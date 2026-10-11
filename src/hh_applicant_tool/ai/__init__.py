from .base import AIError
from .claude_cli import ChatClaudeCLI, ClaudeCLIError
from .openai import (
    ChatOpenAI,
    OpenAIError,
)

# Оба клиента взаимозаменяемы: complete() + rate_limit
ChatClient = ChatOpenAI | ChatClaudeCLI

__all__ = (
    "AIError",
    "ChatClaudeCLI",
    "ChatClient",
    "ChatOpenAI",
    "ClaudeCLIError",
    "OpenAIError",
)
