"""Prompt-injection guardrails для чата (T-033, итерация 18).

Слой намеренно простой и детерминированный: нормализация Unicode,
удаление управляющих/zero-width/bidi символов, лимит 2000 символов и
набор regex-правил для типовых атак. Сырой текст в аудит не пишется —
только `rule_ids`, длина и sha256.

Это не замена семантическому классификатору (T-033-бонус), а дешёвый
первый барьер: он ловит «забудь инструкции», «выведи системный промпт»,
role-теги и jailbreak-фразы до вызова LLM.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass

__all__ = [
    "MAX_MESSAGE_CHARS",
    "GuardrailDecision",
    "inspect_user_input",
    "sanitize_user_input",
]

#: Жёсткий лимит на вопрос пользователя (совпадает с Pydantic-схемой).
MAX_MESSAGE_CHARS = 2_000

#: (rule_id, pattern). Паттерны ищутся по нормализованному тексту.
_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "ignore_instructions",
        re.compile(
            r"\b(ignore|disregard|forget|override)\b.{0,40}"
            r"\b(instructions?|prompts?|rules?)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "ignore_instructions_ru",
        re.compile(
            r"\b(игнорируй|забудь|не\s+следуй|обойди)\b.{0,40}"
            r"\b(инструкц\w*|правил\w*|промпт\w*|ограничен\w*)",
            re.IGNORECASE,
        ),
    ),
    (
        "prompt_leak",
        re.compile(
            r"\b(reveal|show|print|repeat|output|leak|выведи|покажи|"
            r"повтори|раскрой|напечатай)\b.{0,40}"
            r"\b(prompt|instructions?|промпт\w*|инструкц\w*)",
            re.IGNORECASE,
        ),
    ),
    (
        "role_override",
        re.compile(
            r"\b(you\s+are\s+now|act\s+as|from\s+now\s+on|ты\s+теперь|"
            r"представь,?\s+что\s+ты|с\s+этого\s+момента\s+ты)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "jailbreak",
        re.compile(
            r"\b(jailbreak|DAN|developer\s+mode|режим\s+разработчика|"
            r"обойди\s+ограничения)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "role_tags",
        re.compile(
            r"(<\s*/?\s*system\s*>|###\s*system|\[\s*system\s*\])",
            re.IGNORECASE,
        ),
    ),
)


@dataclass(frozen=True, slots=True)
class GuardrailDecision:
    """Результат проверки одного сообщения."""

    text: str
    blocked: bool
    rule_ids: tuple[str, ...]
    original_len: int
    message_hash: str


def _normalize(text: str) -> str:
    """NFKC + удаление управляющих/zero-width/bidi + лимит длины."""
    normalized = unicodedata.normalize("NFKC", text)
    cleaned = "".join(
        char
        for char in normalized
        if char in "\n\t" or unicodedata.category(char)[0] != "C"
    )
    cleaned = re.sub(r"[ \t]+", " ", cleaned).strip()
    return cleaned[:MAX_MESSAGE_CHARS]


def sanitize_user_input(text: str) -> str:
    """Очистить текст: нормализация, лимит и удаление injection-фраз.

    Возвращает безопасный текст. Для блокировки используйте
    `inspect_user_input`: sanitize сам по себе не сообщает, было ли
    совпадение.
    """
    sanitized = _normalize(text)
    for _rule_id, pattern in _RULES:
        sanitized = pattern.sub("[удалено]", sanitized)
    sanitized = re.sub(r"(?:\s*\[удалено\]\s*)+", " [удалено] ", sanitized)
    return sanitized.strip()[:MAX_MESSAGE_CHARS]


def inspect_user_input(text: str) -> GuardrailDecision:
    """Проверить сообщение и вернуть решение для API/аудита."""
    normalized = _normalize(text)
    matches = tuple(
        rule_id for rule_id, pattern in _RULES if pattern.search(normalized)
    )
    return GuardrailDecision(
        text=sanitize_user_input(text),
        blocked=bool(matches),
        rule_ids=matches,
        original_len=len(text),
        message_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
    )
