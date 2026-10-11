from __future__ import annotations

import argparse
import logging
import random
from datetime import datetime
from typing import TYPE_CHECKING

from ..ai.base import AIError
from ..api import ApiError, datatypes
from ..main import BaseNamespace, BaseOperation
from ..utils.date import parse_api_datetime
from ..utils.string import rand_text

if TYPE_CHECKING:
    from ..main import HHApplicantTool


try:
    import readline

    readline.add_history("/cancel ")
    readline.add_history("/ban")
    readline.set_history_length(10_000)
except ImportError:
    pass


logger = logging.getLogger(__package__)


class Namespace(BaseNamespace):
    reply_message: str
    max_pages: int
    only_invitations: bool
    only_new: bool
    nudge_unviewed: bool
    dry_run: bool
    use_ai: bool
    system_prompt: str
    message_prompt: str
    period: int


# Если модель решит, что отвечать не на что, она пришлет это слово вместо
# письма. Проверяется по первому слову ответа, регистр не важен
AI_SKIP_TOKEN = "SKIP"

AI_SKIP_INSTRUCTION = (
    "Если последнее сообщение работодателя не требует ответа "
    "(отказ, автоответ, простое уведомление, приглашение без вопросов "
    "или в переписке вообще нет вопроса к тебе), "
    f"ответь одним словом {AI_SKIP_TOKEN} и ничем больше. "
    "В остальных случаях пришли только текст сообщения работодателю."
)


class Operation(BaseOperation):
    """Ответ всем работодателям."""

    __aliases__ = ["reply-empls", "reply-chats", "reall"]

    def setup_parser(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--resume-id",
            help="Идентификатор резюме. Если не указан, то просматриваем чаты для всех резюме",
        )
        parser.add_argument(
            "-m",
            "--reply-message",
            "--reply",
            help="Отправить сообщение во все чаты. Если не передать сообщение, то нужно будет вводить его в интерактивном режиме.",  # noqa: E501
        )
        parser.add_argument(
            "--period",
            type=int,
            help="Игнорировать отклики, которые не обновлялись больше N дней",
        )
        parser.add_argument(
            "-p",
            "--max-pages",
            type=int,
            default=25,
            help="Максимальное количество страниц для проверки",
        )
        parser.add_argument(
            "-oi",
            "--only-invitations",
            help="Отвечать только на приглашения",
            default=False,
            action=argparse.BooleanOptionalAction,
        )
        parser.add_argument(
            "--only-new",
            "--only-unread",
            help="Отвечать только в чатах с непрочитанными сообщениями "
            "(has_new_messages). Учтите, что чтение чата снимает этот флаг "
            "на стороне hh, поэтому повторный запуск такой чат уже не увидит",
            default=False,
            action=argparse.BooleanOptionalAction,
        )
        parser.add_argument(
            "--nudge-unviewed",
            "--remind",
            help="Писать и в те чаты, где работодатель еще не открывал отклик "
            "и никаких сообщений от него не было. Это напоминание о себе, "
            "а не ответ на вопрос, поэтому по умолчанию выключено",
            default=False,
            action=argparse.BooleanOptionalAction,
        )
        parser.add_argument(
            "--dry-run",
            "--dry",
            help="Не отправлять сообщения, а только выводить параметры запроса",
            default=False,
            action=argparse.BooleanOptionalAction,
        )
        parser.add_argument(
            "--use-ai",
            "--ai",
            help="Использовать AI для автоматической генерации ответов",
            action=argparse.BooleanOptionalAction,
        )
        parser.add_argument(
            "--system-prompt",
            "--ai-system",
            help="Системный промпт для AI",
            default="Ты — соискатель на HeadHunter. Отвечай вежливо и кратко.",
        )
        parser.add_argument(
            "--message-prompt",
            "--prompt",
            help="Промпт для генерации сообщения",
            default="Напиши короткий ответ работодателю на основе истории переписки.",
        )

    def run(self, tool: HHApplicantTool, args: Namespace) -> None:
        self.tool = tool
        self.api_client = tool.api_client
        #self.resume_id = tool.first_resume_id() #вместо id первого резюме берем id из аргументов
        self.resume_id = args.resume_id
        self.reply_message = args.reply_message or tool.config.get(
            "reply_message"
        )
        self.max_pages = args.max_pages
        self.dry_run = args.dry_run
        self.only_invitations = args.only_invitations
        self.only_new = args.only_new
        self.nudge_unviewed = args.nudge_unviewed

        self.message_prompt = args.message_prompt
        self.cover_letter_ai = (
            tool.get_cover_letter_ai(args.system_prompt)
            if args.use_ai
            else None
        )
        self.period = args.period

        logger.debug(f"{self.reply_message = }")
        return self.reply_employers()

    def reply_employers(self):
        blacklist = set(self.tool.get_blacklisted())
        me: datatypes.User = self.tool.get_me()
        resumes = self.tool.get_resumes()
        resumes = (
            list(filter(lambda x: x["id"] == self.resume_id, resumes))
            if self.resume_id is not None # добавляем проверку на пустоту
            else resumes
        )
        resumes = list(
            filter(
                lambda resume: resume["status"]["id"] == "published", resumes
            )
        )
        if not resumes:
            logger.error("Нет опубликованных резюме")
            return 1
        return self._reply_chats(user=me, resumes=resumes, blacklist=blacklist)

    @staticmethod
    def _is_ai_skip(message: str) -> bool:
        """Модель сообщила, что отвечать не на что."""
        stripped = (message or "").strip().strip("*_`\"'.!").strip()
        if not stripped:
            return True
        first_word = stripped.split(maxsplit=1)[0].strip("*_`\"'.!,:")
        return first_word.upper() == AI_SKIP_TOKEN

    def _reply_chats(
        self,
        user: datatypes.User,
        resumes: list[datatypes.Resume],
        blacklist: set[str],
    ) -> None:
        resume_map = {r["id"]: r for r in resumes}

        base_placeholders = {
            "first_name": user.get("first_name") or "",
            "last_name": user.get("last_name") or "",
            "email": user.get("email") or "",
            "phone": user.get("phone") or "",
        }

        for negotiation in self.tool.get_negotiations(
            max_pages=self.max_pages
        ):
            try:
                # try:
                #     self.tool.storage.negotiations.save(negotiation)
                # except RepositoryError as e:
                #     logger.exception(e)

                # Ключ resume бывает и просто null, а не отсутствует
                negotiation_resume = negotiation.get("resume") or {}
                if not (
                    resume := resume_map.get(negotiation_resume.get("id"))
                ):
                    continue

                updated_at = parse_api_datetime(negotiation["updated_at"])

                # Пропуск откликов, которые не обновлялись более N дней (при просмотре они обновляются вроде)
                if (
                    self.period
                    and (datetime.now(updated_at.tzinfo) - updated_at).days
                    > self.period
                ):
                    continue

                state_id = negotiation["state"]["id"]
                if state_id == "discard":
                    continue

                if self.only_invitations and not state_id.startswith("inv"):
                    continue

                nid = negotiation["id"]
                vacancy = negotiation["vacancy"]
                employer = vacancy.get("employer") or {}
                salary = vacancy.get("salary") or {}

                # Работодатель мог закрыть чат — сообщение все равно не уйдет,
                # так что не тратим ни запрос к API, ни вызов модели
                messaging_status = negotiation.get("messaging_status")
                if messaging_status and messaging_status != "ok":
                    logger.debug(
                        "Чат %s недоступен для записи (messaging_status=%s)",
                        nid,
                        messaging_status,
                    )
                    continue

                if self.only_new and not negotiation.get("has_new_messages"):
                    logger.debug("В чате %s нет новых сообщений", nid)
                    continue

                if employer.get("id") in blacklist:
                    print(
                        "🚫 Пропускаем заблокированного работодателя",
                        employer.get("alternate_url"),
                    )
                    continue

                placeholders = {
                    "vacancy_name": vacancy.get("name", ""),
                    "employer_name": employer.get("name", ""),
                    "resume_title": resume.get("title") or "",
                    **base_placeholders,
                }

                logger.debug(
                    "Вакансия %(vacancy_name)s от %(employer_name)s"
                    % placeholders
                )

                page: int = 0
                last_message: datatypes.Message | None = None
                message_history: list[str] = []
                while True:
                    messages_res: datatypes.PaginatedItems[
                        datatypes.Message
                    ] = self.api_client.get(
                        f"/negotiations/{nid}/messages", page=page
                    )
                    if not messages_res["items"]:
                        break

                    last_message = messages_res["items"][-1]
                    for message in messages_res["items"]:
                        if not message.get("text"):
                            continue
                        author = (
                            "Работодатель"
                            if message["author"]["participant_type"]
                            == "employer"
                            else "Я"
                        )
                        message_date = parse_api_datetime(
                            message.get("created_at")
                        ).strftime("%d.%m.%Y %H:%M:%S")

                        message_history.append(
                            f"[ {message_date} ] {author}: {message['text']}"
                        )

                    if page + 1 >= messages_res["pages"]:
                        break
                    page += 1

                if not last_message:
                    continue

                is_employer_message = (
                    last_message["author"]["participant_type"] == "employer"
                )

                # Отвечаем только тогда, когда последнее слово за
                # работодателем: иначе мы просто пишем вдогонку собственному
                # отклику, на который еще никто ничего не ответил.
                # Прежнее поведение (напоминание о себе в непросмотренных
                # откликах) осталось за флагом --nudge-unviewed
                needs_reply = is_employer_message or (
                    self.nudge_unviewed
                    and not negotiation.get("viewed_by_opponent")
                )

                if not is_employer_message:
                    logger.debug(
                        "В чате %s последнее сообщение наше, отвечать не на что",
                        nid,
                    )

                if needs_reply:
                    send_message = ""
                    if self.reply_message:
                        send_message = (
                            rand_text(self.reply_message) % placeholders
                        )
                        logger.debug(f"Template message: {send_message}")
                    elif self.cover_letter_ai:
                        try:
                            ai_query = (
                                f"Вакансия: {placeholders['vacancy_name']}\n"
                                f"Работодатель: {placeholders['employer_name']}\n"
                                f"История переписки:\n"
                                + "\n".join(message_history[-10:])
                                + f"\n\nИнструкция: {self.message_prompt}\n"
                                + AI_SKIP_INSTRUCTION
                            )
                            logger.debug("AI query:\n%s", ai_query)
                            send_message = self.cover_letter_ai.complete(
                                ai_query
                            )
                            logger.debug(f"AI message: {send_message}")
                        except AIError as ex:
                            logger.warning(
                                f"Ошибка AI для чата {nid}: {ex}"
                            )
                            continue

                        if self._is_ai_skip(send_message):
                            print(
                                "🤖 Отвечать не на что, пропускаем",
                                vacancy["alternate_url"],
                            )
                            continue
                    else:
                        print("🏢", placeholders["employer_name"])
                        print("💼", placeholders["vacancy_name"])
                        if salary:
                            print(
                                "💵 от",
                                salary.get("from") or salary.get("to") or 0,
                                "до",
                                salary.get("to") or salary.get("from") or 0,
                                salary.get("currency", "RUR"),
                            )

                        print("\nПоследние сообщения чата:")
                        print()
                        for msg in (
                            message_history[-5:]
                            if len(message_history) > 5
                            else message_history
                        ):
                            print(msg)

                        try:
                            print("-" * 40)
                            print("Активное резюме:", resume.get("title") or "")
                            print(
                                "/ban, /cancel необязательное сообщение для отмены"
                            )
                            send_message = input("Ваше сообщение: ").strip()
                        except EOFError:
                            continue

                        if not send_message:
                            print("🚶 Пропускаем чат")
                            continue

                        if send_message.startswith("/ban"):
                            self.api_client.put(
                                f"/employers/blacklisted/{employer['id']}"
                            )
                            blacklist.add(employer["id"])
                            print(
                                "🚫 Работодатель заблокирован",
                                employer.get("alternate_url"),
                            )
                            continue
                        elif send_message.startswith("/cancel"):
                            _, decline_msg = send_message.split("/cancel", 1)
                            self.api_client.delete(
                                f"/negotiations/active/{nid}",
                                with_decline_message=decline_msg.strip(),
                            )
                            print("❌ Отмена заявки", vacancy["alternate_url"])
                            continue

                    # Пустой ответ отправлять бессмысленно, а API еще и вернет
                    # ошибку
                    if not send_message.strip():
                        logger.debug("Пустое сообщение для чата %s", nid)
                        continue

                    # Финальная отправка текста
                    if self.dry_run:
                        logger.debug(
                            "dry-run: отклик на %s: %s",
                            vacancy["alternate_url"],
                            send_message,
                        )
                        continue

                    self.api_client.post(
                        f"/negotiations/{nid}/messages",
                        message=send_message,
                        delay=random.uniform(1, 3),
                    )
                    print(f"📨 Отправлено для {vacancy['alternate_url']}")

            except ApiError as ex:
                logger.error(ex)

        print("📝 Сообщения разосланы!")
