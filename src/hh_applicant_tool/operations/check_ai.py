from __future__ import annotations

import argparse
import logging
from typing import TYPE_CHECKING

from ..ai import AIError, ChatClaudeCLI
from ..main import BaseNamespace, BaseOperation

if TYPE_CHECKING:
    from ..main import HHApplicantTool


logger = logging.getLogger(__package__)

DEFAULT_PROMPT = (
    "[ВАКАНСИЯ] Название: Python-разработчик, Работодатель: Рога и Копыта; "
    "[РЕЗЮМЕ] Название: Python-разработчик, Имя: Иван, Фамилия: Иванов"
)


class Namespace(BaseNamespace):
    purpose: str
    prompt: str
    system_prompt: str


class Operation(BaseOperation):
    """Проверить настройки AI, не трогая hh.ru."""

    __aliases__ = ("test-ai", "ai-check")

    def setup_parser(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--purpose",
            choices=["cover_letter", "vacancy_filter"],
            default="cover_letter",
            help="Какую секцию конфига проверять",
        )
        parser.add_argument(
            "--system-prompt",
            "--ai-system",
            default="Напиши сопроводительное письмо на 2-3 предложения. Верни только текст письма.",  # noqa: E501
            help="Системный промпт",
        )
        parser.add_argument(
            "--prompt",
            default=DEFAULT_PROMPT,
            help="Текст запроса",
        )

    def run(
        self,
        tool: HHApplicantTool,
        args: Namespace,
    ) -> None | int:
        provider = tool.resolve_ai_provider(args.purpose)
        print("🤖 Провайдер:", provider)

        client = (
            tool.get_cover_letter_ai(args.system_prompt)
            if args.purpose == "cover_letter"
            else tool.get_vacancy_filter_ai(args.system_prompt)
        )

        if isinstance(client, ChatClaudeCLI):
            print("⚙️ Команда:", client.command)
            print("🧠 Модель:", client.model)
            print(
                "🌐 Прокси:",
                "отключен"
                if client.no_proxy
                else (client.proxy_url or "из переменных окружения"),
            )
        else:
            print("🌐 Эндпоинт:", client.base_url)
            print("🧠 Модель:", client.model)
            print("🌐 Прокси:", client.session.proxies or "не задан")

        try:
            result = client.complete(args.prompt)
        except AIError as ex:
            logger.error(ex)
            print("❌ AI не ответил")
            return 1

        print("✅ Ответ:")
        print(result)
        return 0
