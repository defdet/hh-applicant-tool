"""Клиент к Claude Code CLI.

В отличие от {ChatOpenAI} здесь нет HTTP-запросов: каждый вызов запускает
`claude -p` как дочерний процесс, промпт отдается через stdin, ответ читается
из stdout. Авторизация и выбор провайдера — забота самого CLI (подписка Claude,
`CLAUDE_CODE_OAUTH_TOKEN` или `ANTHROPIC_API_KEY`), поэтому api_key тут не
нужен.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import time
from dataclasses import KW_ONLY, dataclass, field
from pathlib import Path
from threading import Lock

from ..constants import (
    DEFAULT_CLAUDE_ARGS,
    DEFAULT_CLAUDE_COMMAND,
    DEFAULT_CLAUDE_MODEL,
    DEFAULT_CLAUDE_TIMEOUT,
    PROXY_ENV_VARS,
)
from .base import AIError

logger = logging.getLogger(__package__)


class ClaudeCLIError(AIError):
    pass


@dataclass
class ChatClaudeCLI:
    """Генерация текста через `claude -p`."""

    _: KW_ONLY

    system_prompt: str | None = None
    model: str = DEFAULT_CLAUDE_MODEL
    # Исполняемый файл: имя в PATH или абсолютный путь
    command: str = DEFAULT_CLAUDE_COMMAND
    # Дополнительные аргументы CLI, например ["--effort", "low"]
    extra_args: list[str] = field(default_factory=list)
    # claude сам ходит в сеть и ретраит, поэтому таймаут щедрый
    timeout: float = DEFAULT_CLAUDE_TIMEOUT
    max_retries: int = 2
    # количество запросов в минуту (0 = отключено)
    rate_limit: int = 0
    # Прокси только для дочернего процесса. None — наследуем окружение
    proxy_url: str | None = None
    # Вырезать прокси из окружения дочернего процесса
    no_proxy: bool = False
    # Рабочий каталог процесса. Лучше нейтральный, чтобы claude не подцепил
    # чужой репозиторий
    cwd: Path | None = None

    # Эти поля нужны только для совместимости с {ChatOpenAI}
    temperature: float = 0.0
    max_completion_tokens: int = 1000

    _previous_request_time: float = field(default=0.0, init=False)
    _lock: Lock = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._lock = Lock()

    @property
    def _min_request_interval(self) -> float:
        return 60.0 / self.rate_limit if self.rate_limit > 0 else 0.0

    def _resolve_executable(self) -> str:
        """Путь до claude. Под Windows сам подставит .cmd/.exe."""
        return shutil.which(self.command) or self.command

    def _build_args(self) -> list[str]:
        args = [self._resolve_executable(), *DEFAULT_CLAUDE_ARGS]

        if self.model:
            args += ["--model", self.model]

        if self.system_prompt:
            args += ["--system-prompt", self.system_prompt]

        args += self.extra_args
        return args

    def _build_env(self) -> dict[str, str]:
        env = dict(os.environ)

        # docker-compose охотно подставляет пустые строки, а claude принимает
        # их за заданную авторизацию и потом жалуется на невалидный токен
        for name in ("CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY"):
            if not env.get(name):
                env.pop(name, None)

        if self.no_proxy:
            for name in PROXY_ENV_VARS:
                env.pop(name, None)
        elif self.proxy_url:
            env["HTTP_PROXY"] = env["http_proxy"] = self.proxy_url
            env["HTTPS_PROXY"] = env["https_proxy"] = self.proxy_url

        return env

    def _wait_rate_limit(self) -> None:
        if not self._previous_request_time:
            return

        delay = (
            self._min_request_interval
            - time.monotonic()
            + self._previous_request_time
        )
        if delay > 0:
            logger.debug("Wait %.2fs before claude call", delay)
            time.sleep(delay)

    def _run(self, args: list[str], prompt: str) -> subprocess.CompletedProcess:
        with self._lock:
            self._wait_rate_limit()
            try:
                return subprocess.run(
                    args,
                    input=prompt,
                    # Ответ и ошибки забираем себе, stdin занят промптом,
                    # поэтому claude не сможет свалиться в интерактив
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=self.timeout,
                    env=self._build_env(),
                    cwd=str(self.cwd) if self.cwd else None,
                )
            finally:
                self._previous_request_time = time.monotonic()

    @staticmethod
    def _clean_output(text: str) -> str:
        """Ответ иногда завернут в markdown-блок, а его слать нельзя."""
        text = text.strip()

        if text.startswith("```"):
            lines = text.splitlines()
            # Сносим открывающий ```lang и закрывающий ```
            lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines).strip()

        return text

    def complete(self, message: str) -> str:
        args = self._build_args()

        if logger.isEnabledFor(logging.DEBUG):
            logger.debug("claude системный промпт: %s", self.system_prompt)
            logger.debug("claude запрос: %s", message)

        last_error: str = "неизвестная ошибка"

        for attempt in range(self.max_retries + 1):
            try:
                proc = self._run(args, message)
            except FileNotFoundError as ex:
                raise ClaudeCLIError(
                    f"Не найден исполняемый файл {self.command!r}. "
                    "Установите Claude Code (https://claude.ai/install.sh) "
                    "или задайте 'command' в секции claude конфига."
                ) from ex
            except subprocess.TimeoutExpired:
                last_error = f"превышен таймаут {self.timeout:g}с"
                logger.warning(
                    "claude не ответил за %.0fс (попытка %d/%d)",
                    self.timeout,
                    attempt + 1,
                    self.max_retries + 1,
                )
                continue
            except OSError as ex:
                raise ClaudeCLIError(f"Не удалось запустить claude: {ex}") from ex

            stderr = (proc.stderr or "").strip()

            if proc.returncode != 0:
                # Про "Not logged in" и прочие свои беды claude рассказывает
                # в stdout, а не в stderr
                last_error = (
                    stderr
                    or (proc.stdout or "").strip()
                    or f"код возврата {proc.returncode}"
                )
                logger.warning(
                    "claude завершился с ошибкой (попытка %d/%d): %s",
                    attempt + 1,
                    self.max_retries + 1,
                    # Трейсы бывают длинные, а в лог идет все подряд
                    last_error[-512:],
                )
                continue

            if stderr:
                logger.debug("claude stderr: %s", stderr[-512:])

            result = self._clean_output(proc.stdout or "")

            if not result:
                last_error = "пустой ответ"
                logger.warning(
                    "claude вернул пустой ответ (попытка %d/%d)",
                    attempt + 1,
                    self.max_retries + 1,
                )
                continue

            logger.debug("claude ответ: %s", result)
            return result

        raise ClaudeCLIError(f"claude не справился: {last_error}")

    def solve_captcha(self, image_data: bytes) -> str:
        raise ClaudeCLIError(
            "Распознавание капчи через claude -p не поддерживается, "
            "настройте секцию openai_captcha"
        )
