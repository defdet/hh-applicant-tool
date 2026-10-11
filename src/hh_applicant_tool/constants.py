from __future__ import annotations

from . import utils

CONFIG_DIR = utils.get_config_path() / "hh-applicant-tool"
CONFIG_FILENAME = "config.json"
LOG_FILENAME = "log.txt"
DATABASE_FILENAME = "data"
COOKIES_FILENAME = "cookies.txt"
# Картинка с капчей, если терминал не умеет её показывать
CAPTCHA_FILENAME = "captcha.png"
# Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36
DESKTOP_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/151.0.0.0 Safari/537.36"
)
# Общий таймаут запроса к OpenAI: соединение + чтение ответа
DEFAULT_OPENAI_TIMEOUT = 30.0
# Отдельно на установку соединения, чтобы недоступный сервер не съедал
# весь таймаут
DEFAULT_OPENAI_CONNECT_TIMEOUT = 5.0


# --- Claude Code CLI (`claude -p`) ---
# Исполняемый файл CLI. Ищется в PATH, можно указать абсолютный путь
DEFAULT_CLAUDE_COMMAND = "claude"
# Sonnet 5. Можно указать алиас ("sonnet") или полный идентификатор модели
DEFAULT_CLAUDE_MODEL = "claude-sonnet-5"
# claude -p поднимает процесс и ходит в сеть, поэтому таймаут больше, чем у HTTP
DEFAULT_CLAUDE_TIMEOUT = 180.0
# Аргументы, превращающие интерактивный claude в обычный текстовый генератор:
# без инструментов, без записи сессий на диск и без пользовательских
# CLAUDE.md/хуков/плагинов, которые могли бы испортить ответ
DEFAULT_CLAUDE_ARGS = (
    "--print",
    "--output-format",
    "text",
    "--tools",
    "",
    "--no-session-persistence",
    "--safe-mode",
)
# Переменные окружения, через которые проксируется дочерний процесс claude
PROXY_ENV_VARS = (
    "HTTP_PROXY",
    "http_proxy",
    "HTTPS_PROXY",
    "https_proxy",
    "ALL_PROXY",
    "all_proxy",
)
