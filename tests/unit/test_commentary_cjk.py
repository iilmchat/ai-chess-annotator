"""Unit-тесты CJK-петли retry в сервисе комментария (KI-060, итерация 49).

Сервис проверяется напрямую (`_generate_with_validation`), без БД и HTTP:
нас интересует порядок «детект → retry → вырезание/отклонение» и текст
подсказок, а не сохранение комментария.
"""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from app.commentary.service import _generate_with_validation
from app.errors import LLMValidationError
from app.ingestion import ParsedGame, parse_pgn
from app.llm.base import BaseLLMClient, LLMResult, LLMUsage

PGN = "1. d4 Nf6 2. c4 e6 1/2-1/2"
CLEAN_TEXT = "Партия началась с хода d4, затем последовало Nf6."
DRIFTED_TEXT = "Партия была интересной. Конь идет на 马e4."
BARE_CJK_TEXT = "Конь идет на 马e4"
ILLEGAL_AND_CJK = "Белые сыграли Qxf7. 马"
ALL_CJK_TEXT = "马马马 这是中文"


class ScriptedLLM(BaseLLMClient):
    """LLM, отдающая заранее заданные ответы по очереди."""

    def __init__(self, texts: Sequence[str]) -> None:
        super().__init__(model="scripted-model")
        self._texts = list(texts)
        self.prompts: list[str] = []

    @property
    def provider(self) -> str:
        return "scripted"

    async def complete(
        self, prompt: str, system_prompt: str | None = None
    ) -> LLMResult:
        self.prompts.append(prompt)
        index = min(len(self.prompts) - 1, len(self._texts) - 1)
        return LLMResult(
            text=self._texts[index],
            usage=LLMUsage(
                provider="scripted",
                model="scripted-model",
                tokens_in=10,
                tokens_out=5,
            ),
        )


def _parsed() -> ParsedGame:
    return parse_pgn(PGN)


async def _run(
    llm: BaseLLMClient,
    *,
    max_retries: int,
    language: str = "ru",
    semantic_validation_enabled: bool = True,
) -> tuple[str, int]:
    text, _usage, attempts = await _generate_with_validation(
        llm,
        "ПАКЕТ ФАКТОВ",
        "СИСТЕМНЫЙ ПРОМПТ",
        _parsed(),
        [],
        max_retries,
        language=language,
        semantic_validation_enabled=semantic_validation_enabled,
    )
    return text, attempts


# --- retry ------------------------------------------------------------------


async def test_cjk_drift_triggers_russian_only_retry() -> None:
    llm = ScriptedLLM([DRIFTED_TEXT, CLEAN_TEXT])

    text, attempts = await _run(llm, max_retries=2)

    assert text == CLEAN_TEXT
    assert attempts == 2
    assert len(llm.prompts) == 2
    assert "Отвечай только на русском языке" in llm.prompts[1]
    assert "Убери китайские иероглифы" in llm.prompts[1]
    # Пакет фактов остаётся в промпте, а не заменяется подсказкой.
    assert llm.prompts[1].startswith("ПАКЕТ ФАКТОВ")


async def test_clean_text_needs_single_attempt() -> None:
    llm = ScriptedLLM([CLEAN_TEXT])

    text, attempts = await _run(llm, max_retries=2)

    assert text == CLEAN_TEXT
    assert attempts == 1
    assert len(llm.prompts) == 1


async def test_cjk_retry_hint_is_localized() -> None:
    llm = ScriptedLLM([DRIFTED_TEXT, CLEAN_TEXT])

    await _run(llm, max_retries=1, language="en")

    assert "English only" in llm.prompts[1]
    assert "Chinese characters" in llm.prompts[1]


async def test_illegal_move_and_cjk_are_combined_in_hint() -> None:
    llm = ScriptedLLM([ILLEGAL_AND_CJK, CLEAN_TEXT])

    text, attempts = await _run(llm, max_retries=2)

    assert text == CLEAN_TEXT
    assert attempts == 2
    assert "Qxf7" in llm.prompts[1]
    assert "Отвечай только на русском языке" in llm.prompts[1]


# --- последняя попытка ------------------------------------------------------


async def test_last_attempt_strips_drifted_sentence() -> None:
    llm = ScriptedLLM([DRIFTED_TEXT])

    text, attempts = await _run(llm, max_retries=0)

    assert text == "Партия была интересной."
    assert attempts == 1
    assert "马" not in text


async def test_last_attempt_rejects_mostly_cjk_answer() -> None:
    llm = ScriptedLLM([ALL_CJK_TEXT])

    with pytest.raises(LLMValidationError, match="китайские иероглифы"):
        await _run(llm, max_retries=0)


async def test_cjk_retry_exhausted_then_rejects() -> None:
    llm = ScriptedLLM([BARE_CJK_TEXT])

    with pytest.raises(LLMValidationError, match="KI-060"):
        await _run(llm, max_retries=1)

    assert len(llm.prompts) == 2
    assert "Отвечай только на русском языке" in llm.prompts[1]


async def test_cjk_check_runs_even_with_semantic_validation_off() -> None:
    llm = ScriptedLLM([DRIFTED_TEXT, CLEAN_TEXT])

    text, attempts = await _run(
        llm, max_retries=2, semantic_validation_enabled=False
    )

    assert text == CLEAN_TEXT
    assert attempts == 2
    assert "Отвечай только на русском языке" in llm.prompts[1]
