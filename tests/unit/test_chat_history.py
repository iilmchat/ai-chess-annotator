"""История чата: промпт и prompt_hash (T-038)."""

from __future__ import annotations

from app.cache.semantic import make_prompt_hash
from app.chat.service import build_chat_prompt, format_chat_history

HISTORY = [
    {"role": "user", "content": "Кто играл белыми?"},
    {"role": "assistant", "content": "Svidler."},
]


def test_format_history() -> None:
    assert format_chat_history(HISTORY) == (
        "user: Кто играл белыми?\nassistant: Svidler."
    )
    assert format_chat_history([]) == ""


def test_prompt_contains_history_and_question() -> None:
    prompt = build_chat_prompt("FACTS", "А кто чёрными?", HISTORY)

    assert "FACTS" in prompt
    assert "# История диалога" in prompt
    assert "user: Кто играл белыми?" in prompt
    assert "assistant: Svidler." in prompt
    assert "# Вопрос пользователя" in prompt
    assert "А кто чёрными?" in prompt


def test_prompt_english_scaffolding() -> None:
    """T-106/KI-058: для en заголовки промпта и инструкция — английские."""
    prompt = build_chat_prompt(
        "FACTS", "Who played white?", HISTORY, language="en"
    )

    assert "# Conversation history" in prompt
    assert "# User question" in prompt
    assert "Answer briefly and to the point" in prompt
    assert "# История диалога" not in prompt
    assert "# Вопрос пользователя" not in prompt
    assert "Ответь кратко" not in prompt



def test_prompt_hash_depends_on_history() -> None:
    base = make_prompt_hash(
        "club", "m", "sys", question="q", history=None
    )
    with_history = make_prompt_hash(
        "club",
        "m",
        "sys",
        question="q",
        history=format_chat_history(HISTORY),
    )

    assert base != with_history
