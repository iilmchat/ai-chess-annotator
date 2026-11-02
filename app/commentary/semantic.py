"""Семантическая валидация комментариев LLM (KI-023, итерация 37).

SAN-валидатор (:mod:`app.engine.validator`) проверяет только **легальность
ходов**. Утверждения о позиции он пропускает, если ходы в них легальны или
отсутствуют: «слон на e6», когда слона там нет, «связка пешки f7», которой
не существует. Этот модуль добавляет второй, семантический слой:

1. :func:`extract_semantic_claims` регулярными выражениями (RU + EN)
   извлекает утверждения двух видов:

   * ``piece_on_square`` — «слон на e6», «чёрный конь на d4»,
     «the white rook on a1»;
   * ``pin`` — «связка пешки f7», «связка коня на d2»,
     «pin of the f7 pawn».

2. :func:`validate_semantic_claims` строит множество позиций партии
   (`mainline_san`) и PV-линий движка (`extra_fens`) — так же, как это делает
   SAN-валидатор, — и отбраковывает утверждение, если описанной в нём фигуры
   **нет на указанном поле ни в одной** известной позиции. Требовать
   совпадения во всех позициях нельзя: комментарий описывает конкретный
   момент партии, и такая проверка давала бы массовые ложные срабатывания.

3. На последней попытке генерации (:mod:`app.commentary.service`) вместо
   ошибки offending-предложения вырезаются
   :func:`strip_invalid_claims`, а :func:`semantic_retry_prompt` формулирует
   подсказку для повтора.

4. KI-060 (итерация 49): отдельный языковой слой. Мультиязычные модели
   (Qwen2.5) иногда подмешивают китайские иероглифы в русский текст.
   :func:`has_cjk` находит символы CJK Unified Ideographs (U+4E00–U+9FFF),
   :func:`cjk_warning` даёт подсказку для одного retry, а
   :func:`sanitize_cjk` на повторной ошибке вырезает предложения с
   иероглифами или отклоняет ответ, если их слишком много.

Границы проверки (важно для ревью): проверяется **существование** фигуры на
поле, а не тактическая корректность. Геометрия связки (кто кого связывает и
куда направлен удар), оценки, мотивы, «лучший ход» и атрибуция вариантов не
сверяются с пакетом фактов — см. KI-023. Извлечение эвристическое: один
паттерн на предложение даёт не более одного утверждения своего вида, а
наивная сегментация на предложения по ``.!?…`` ошибается на сокращениях и
числах вроде «+120.5».
"""

from __future__ import annotations

import bisect
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

import chess
from opentelemetry.trace import Status, StatusCode

from app.ingestion.parser import parse_fen
from app.observability import get_tracer

__all__ = [
    "CJK_MAX_SHARE",
    "CJK_PATTERN",
    "CjkSanitizeResult",
    "SemanticClaim",
    "cjk_share",
    "cjk_warning",
    "count_cjk",
    "extract_semantic_claims",
    "has_cjk",
    "sanitize_cjk",
    "semantic_retry_prompt",
    "semantic_warning",
    "strip_cjk_sentences",
    "strip_invalid_claims",
    "validate_semantic_claims",
]

#: Вид семантического утверждения: фигура на поле или связка.
ClaimKind = Literal["piece_on_square", "pin"]

#: Стемы названий фигур для RU-паттернов: «слона», «конём», «пешки» и т. п.
_RU_PIECES: tuple[tuple[str, chess.PieceType], ...] = (
    ("слон", chess.BISHOP),
    ("кон", chess.KNIGHT),
    ("ладь", chess.ROOK),
    ("ферз", chess.QUEEN),
    ("корол", chess.KING),
    ("пешк", chess.PAWN),
)

#: Названия фигур для EN-паттернов.
_EN_PIECES: tuple[tuple[str, chess.PieceType], ...] = (
    ("bishop", chess.BISHOP),
    ("knight", chess.KNIGHT),
    ("rook", chess.ROOK),
    ("queen", chess.QUEEN),
    ("king", chess.KING),
    ("pawn", chess.PAWN),
)

_RU_PIECE_ALT = "|".join(stem for stem, _ in _RU_PIECES)
_EN_PIECE_ALT = "|".join(name for name, _ in _EN_PIECES)

#: Границы предложения: тело до терминатора/перевода строки + сами
#: терминаторы. Перевод строки — тоже граница, поэтому утверждение не
#: «переползает» из одного предложения в другое. Китайские терминаторы
#: (``。！？``) добавлены для вырезания CJK-предложений (KI-060).
_SENTENCE_TOKEN = re.compile(r"[^.!?…。！？\n]+[.!?…。！？]*")

#: Разделитель абзацев: пустая строка (возможно, с пробелами).
_PARAGRAPH_SPLIT = re.compile(r"(?:[ \t]*\r?\n){2,}")

#: CJK Unified Ideographs (U+4E00–U+9FFF): китайские иероглифы. В диапазон
#: попадают и японские кандзи — для русского комментария это такой же дрейф.
CJK_PATTERN = re.compile(r"[\u4e00-\u9fff]")

#: Доля иероглифов среди непустых символов, после которой ответ отклоняется
#: целиком (вырезать почти весь комментарий бессмысленно). Один случайный
#: иероглиф в длинном тексте порог не превышает — вырезается одно предложение.
CJK_MAX_SHARE = 0.10

#: «Фигура на поле»: «слон на e6», «чёрный конь на d4»,
#: «the white rook on a1». Требуется предлог «на/on» — иначе фразу
#: «слон e6» невозможно отличить от обрывка хода/нотации.
_RU_ON_SQUARE = re.compile(
    r"(?:(?P<color>бел\w*|чёрн\w*|черн\w*)[ \t]+)?"
    r"(?P<piece>" + _RU_PIECE_ALT + r")\w*[ \t]+"
    r"(?:(?:стоит|стоят|находится|находятся|расположен\w*)[ \t]+)?"
    r"на[ \t]+(?P<square>[a-h][1-8])",
    re.IGNORECASE,
)

#: «Связка [цвета] [фигуры] [на] поле»: «связка пешки f7»,
#: «связка коня на d2», «связка на d2».
_RU_PIN = re.compile(
    r"связк\w*[ \t]+"
    r"(?:(?P<color>бел\w*|чёрн\w*|черн\w*)[ \t]+)?"
    r"(?:(?P<piece>" + _RU_PIECE_ALT + r")\w*[ \t]+)?"
    r"(?:на[ \t]+)?"
    r"(?P<square>[a-h][1-8])",
    re.IGNORECASE,
)

#: EN «bishop on e6», «the white rook on a1», «bishop is on e6».
_EN_ON_SQUARE = re.compile(
    r"\b(?:the[ \t]+)?(?:(?P<color>white|black)[ \t]+)?"
    r"(?P<piece>" + _EN_PIECE_ALT + r")s?[ \t]+"
    r"(?:(?:is|stands|sits|lies)[ \t]+)?on[ \t]+"
    r"(?P<square>[a-h][1-8])\b",
    re.IGNORECASE,
)

#: EN «pin of the f7 pawn»: поле идёт перед фигурой.
_EN_PIN_SQUARE_FIRST = re.compile(
    r"\bpin\w*[ \t]+(?:of[ \t]+|on[ \t]+)?(?:the[ \t]+)?"
    r"(?P<square>[a-h][1-8])[ \t]+"
    r"(?:(?P<color>white|black)[ \t]+)?"
    r"(?P<piece>" + _EN_PIECE_ALT + r")s?\b",
    re.IGNORECASE,
)

#: EN «pin of the knight on d2»: фигура идёт перед полем.
_EN_PIN_PIECE_FIRST = re.compile(
    r"\bpin\w*[ \t]+(?:of[ \t]+|on[ \t]+)?(?:the[ \t]+)?"
    r"(?:(?P<color>white|black)[ \t]+)?"
    r"(?:(?P<piece>" + _EN_PIECE_ALT + r")s?[ \t]+(?:on[ \t]+)?)?"
    r"(?P<square>[a-h][1-8])\b",
    re.IGNORECASE,
)

#: EN «the e6 bishop is pinned»: поле перед фигурой.
_EN_PIN_PASSIVE_SQUARE_FIRST = re.compile(
    r"\b(?:the[ \t]+)?(?:(?P<color>white|black)[ \t]+)?"
    r"(?P<square>[a-h][1-8])[ \t]+"
    r"(?P<piece>" + _EN_PIECE_ALT + r")s?[ \t]+"
    r"(?:is|was|are|were)[ \t]+pinned\b",
    re.IGNORECASE,
)

#: EN «the bishop on e6 is pinned»: фигура перед полем.
_EN_PIN_PASSIVE_PIECE_FIRST = re.compile(
    r"\b(?:the[ \t]+)?(?:(?P<color>white|black)[ \t]+)?"
    r"(?P<piece>" + _EN_PIECE_ALT + r")s?[ \t]+"
    r"(?:on[ \t]+)?(?P<square>[a-h][1-8])[ \t]+"
    r"(?:is|was|are|were)[ \t]+pinned\b",
    re.IGNORECASE,
)

#: Паттерны в порядке приоритета: при совпадении с одного места выигрывает
#: более специфичный (ранний) паттерн.
_CLAIM_PATTERNS: tuple[tuple[re.Pattern[str], ClaimKind], ...] = (
    (_RU_ON_SQUARE, "piece_on_square"),
    (_EN_ON_SQUARE, "piece_on_square"),
    (_RU_PIN, "pin"),
    (_EN_PIN_SQUARE_FIRST, "pin"),
    (_EN_PIN_PIECE_FIRST, "pin"),
    (_EN_PIN_PASSIVE_SQUARE_FIRST, "pin"),
    (_EN_PIN_PASSIVE_PIECE_FIRST, "pin"),
)


@dataclass(frozen=True, slots=True)
class SemanticClaim:
    """Извлечённое из текста утверждение о позиции.

    Attributes:
        sentence: предложение, из которого взято утверждение (целиком).
        piece: тип фигуры или ``None``, если фигура в тексте не названа
            (например, «связка на d2»).
        color: цвет фигуры или ``None``, если он не назван.
        square: поле, о котором утверждает текст (``chess.Square`` — int 0..63).
        kind: ``piece_on_square`` или ``pin``.
        text: совпавший фрагмент текста (используется в сообщениях об ошибке).
    """

    sentence: str
    piece: chess.PieceType | None
    color: chess.Color | None
    square: chess.Square
    kind: ClaimKind
    text: str


def extract_semantic_claims(text: str) -> list[SemanticClaim]:
    """Извлечь семантические утверждения из текста комментария.

    Быстрый путь: текст без утверждений (обычный разбор ходов, «ход на e4»,
    голая клетка) даёт пустой список. Утверждения «фигура на поле»,
    попавшие внутрь фразы о связке, не дублируются.

    Args:
        text: текст ответа LLM.

    Returns:
        Утверждения в порядке появления в тексте.
    """
    if not text or not text.strip():
        return []
    spans = _sentence_spans(text)
    if not spans:
        return []
    starts = [start for start, _ in spans]

    candidates: list[tuple[int, int, int, SemanticClaim]] = []
    for priority, (pattern, kind) in enumerate(_CLAIM_PATTERNS):
        for match in pattern.finditer(text):
            sentence = _sentence_for_match(match.start(), text, spans, starts)
            if sentence is None:
                continue
            claim = _build_claim(match, kind, sentence)
            if claim is not None:
                candidates.append((match.start(), match.end(), priority, claim))
    if not candidates:
        return []

    pin_spans = [
        (start, end)
        for start, end, _, claim in candidates
        if claim.kind == "pin"
    ]
    claims: list[SemanticClaim] = []
    seen: set[tuple[object, ...]] = set()
    for start, end, _, claim in sorted(
        candidates, key=lambda item: (item[0], item[2])
    ):
        # «Связка коня на d2» содержит ещё и «конь на d2»: это одно
        # утверждение, а не два.
        if claim.kind == "piece_on_square" and _inside_any(start, end, pin_spans):
            continue
        if claim.kind == "pin":
            # Для связки одного поля в одном предложении достаточно: разные
            # EN-паттерны описывают одну и ту же фразу.
            key: tuple[object, ...] = (
                "pin",
                claim.square,
                _normalize_text(claim.sentence),
            )
        else:
            key = ("piece_on_square", claim.square, claim.piece, claim.color)
        if key in seen:
            continue
        seen.add(key)
        claims.append(claim)
    return claims


def validate_semantic_claims(
    text: str,
    initial_fen: str,
    *,
    mainline_san: Sequence[str] | None = None,
    extra_fens: Sequence[str] | None = None,
) -> tuple[bool, list[str]]:
    """Проверить, что утверждения текста согласуются с позициями партии.

    Утверждение считается корректным, если описанная в нём фигура стоит на
    указанном поле **хотя бы в одной** известной позиции (стартовая, позиции
    после ходов mainline, позиции PV-линий движка). Для связок проверяется
    только наличие фигуры на поле — геометрия связки не проверяется.

    Args:
        text: текст ответа LLM.
        initial_fen: стартовая позиция партии.
        mainline_san: ходы партии (SAN) — по ним строятся позиции.
        extra_fens: дополнительные позиции (PV-линии движка).

    Returns:
        ``(все_корректны, список_проблем)``: проблемы — короткие фрагменты
        текста («слон на e6»), без повторов, в порядке появления.

    Raises:
        InvalidFenError: `initial_fen` или один из `extra_fens` некорректен.
    """
    claims = extract_semantic_claims(text)
    if not claims:
        return (True, [])

    tracer = get_tracer()
    with tracer.start_as_current_span("semantic.validate") as span:
        # Атрибуты — только счётчики, без текста LLM и FEN (без PII).
        span.set_attribute("semantic.claims", len(claims))
        if mainline_san is not None:
            span.set_attribute("semantic.mainline_moves", len(mainline_san))
        if extra_fens is not None:
            span.set_attribute("semantic.extra_fens", len(extra_fens))
        try:
            positions = _known_positions(
                initial_fen, mainline_san, extra_fens
            )
            problems: list[str] = []
            seen: set[str] = set()
            for claim in claims:
                if _claim_holds(claim, positions):
                    continue
                label = _problem_label(claim)
                if label and label not in seen:
                    seen.add(label)
                    problems.append(label)
        except Exception as exc:
            span.record_exception(exc)
            span.set_status(Status(StatusCode.ERROR, type(exc).__name__))
            raise
        span.set_attribute("semantic.problems", len(problems))
        span.set_attribute("semantic.valid", not problems)
        span.set_status(Status(StatusCode.OK))
        return (not problems, problems)


def strip_invalid_claims(text: str, problems: Sequence[str]) -> str:
    """Вырезать предложения с невалидными утверждениями.

    Предложения с проблемными фрагментами удаляются целиком (сравнение без
    учёта регистра и лишних пробелов, ``ё``/``е`` не различаются), остальные
    сохраняются. Абзацы (пустые строки) сохраняются, одиночные переводы
    строк внутри абзаца схлопываются в пробел, двойные пробелы убираются.

    Если после вырезания не осталось текста (все предложения оказались
    невалидны или `problems` не встречаются в тексте), возвращается исходный
    текст: функция не имеет права вернуть пустую строку вместо комментария.

    Args:
        text: исходный текст.
        problems: короткие фрагменты из :func:`validate_semantic_claims`.

    Returns:
        Очищенный текст или исходный, если удалять нечего.
    """
    if not text or not problems:
        return text
    needles = [
        normalized
        for normalized in (_normalize_text(problem) for problem in problems)
        if normalized
    ]
    if not needles:
        return text

    removed_any = False
    paragraphs: list[str] = []
    for paragraph in _PARAGRAPH_SPLIT.split(text):
        kept: list[str] = []
        for match in _SENTENCE_TOKEN.finditer(paragraph):
            sentence = " ".join(match.group(0).split())
            if not sentence:
                continue
            haystack = _normalize_text(sentence)
            if any(needle in haystack for needle in needles):
                removed_any = True
                continue
            kept.append(sentence)
        if kept:
            paragraphs.append(" ".join(kept))
    if not removed_any or not paragraphs:
        return text
    return "\n\n".join(paragraphs)


def semantic_warning(problems: Sequence[str], language: str = "ru") -> str:
    """Короткое предупреждение для повторной генерации (без пакета фактов).

    Args:
        problems: фрагменты невалидных утверждений.
        language: ``ru`` или ``en`` — язык подсказки.

    Returns:
        Текст предупреждения без пакета фактов.
    """
    bad = ", ".join(problems)
    if language == "en":
        return (
            "WARNING: the previous answer placed pieces on squares where they "
            f"do not stand in this game: {bad}. Rewrite the commentary and do "
            "not put pieces on squares where they are not."
        )
    return (
        "ВНИМАНИЕ: в предыдущем ответе были утверждения о фигурах, которых нет "
        f"на доске: {bad}. Перепиши комментарий и не приписывай фигуры полям, "
        "где их нет."
    )


def semantic_retry_prompt(
    package: str, problems: Sequence[str], language: str = "ru"
) -> str:
    """Пакет фактов + подсказка переписать комментарий без «чужих» фигур.

    Стиль повторяет :func:`app.commentary.service._moves_warning` для
    нелегальных ходов: сначала пакет фактов, затем предупреждение с
    перечислением проблем.

    Args:
        package: пакет фактов (или любой исходный промпт).
        problems: фрагменты невалидных утверждений.
        language: ``ru`` или ``en`` — язык подсказки.

    Returns:
        Промпт для повторного запроса к LLM.
    """
    return f"{package}\n\n{semantic_warning(problems, language)}"


@dataclass(frozen=True, slots=True)
class CjkSanitizeResult:
    """Итог очистки текста от CJK-иероглифов (KI-060).

    Attributes:
        text: очищенный текст; пустая строка, если ответ отклонён.
        cjk_chars: сколько иероглифов было в исходном тексте.
        removed_sentences: сколько предложений вырезано.
        share: доля иероглифов среди непустых символов исходного текста.
        rejected: ответ отклонён целиком (иероглифов слишком много или после
            вырезания не осталось текста).
    """

    text: str
    cjk_chars: int
    removed_sentences: int
    share: float
    rejected: bool


def count_cjk(text: str) -> int:
    """Число символов CJK Unified Ideographs в тексте."""
    return len(CJK_PATTERN.findall(text))


def has_cjk(text: str) -> bool:
    """Есть ли в тексте хотя бы один китайский иероглиф."""
    return CJK_PATTERN.search(text) is not None


def cjk_share(text: str) -> float:
    """Доля иероглифов среди непустых символов (0.0 для пустого текста)."""
    meaningful = sum(1 for char in text if not char.isspace())
    if meaningful == 0:
        return 0.0
    return count_cjk(text) / meaningful


def strip_cjk_sentences(text: str) -> tuple[str, int]:
    """Вырезать предложения, содержащие иероглифы.

    Абзацы (пустые строки) сохраняются; одиночные переводы строк внутри
    абзаца схлопываются в пробел. Если иероглифов нет или вырезать нечего,
    возвращается исходный текст и ``0``.

    Args:
        text: текст ответа LLM.

    Returns:
        ``(очищенный_текст, число_вырезанных_предложений)``; пустая строка,
        если все предложения оказались с иероглифами.
    """
    if not has_cjk(text):
        return text, 0
    removed = 0
    paragraphs: list[str] = []
    for paragraph in _PARAGRAPH_SPLIT.split(text):
        kept: list[str] = []
        for match in _SENTENCE_TOKEN.finditer(paragraph):
            sentence = match.group(0).strip()
            if not sentence:
                continue
            if has_cjk(sentence):
                removed += 1
                continue
            kept.append(sentence)
        if kept:
            paragraphs.append(" ".join(kept))
    if removed == 0:
        return text, 0
    return "\n\n".join(paragraphs), removed


def sanitize_cjk(
    text: str, *, max_share: float = CJK_MAX_SHARE
) -> CjkSanitizeResult:
    """Очистить ответ LLM от иероглифов или отклонить его.

    Логика повторной ошибки после одного retry (KI-060):

    * иероглифов нет — текст возвращается без изменений;
    * доля иероглифов выше ``max_share`` — ответ отклоняется целиком
      (``rejected=True``), потому что вырезание оставит бессмысленный
      огрызок;
    * иначе вырезаются предложения с иероглифами; если текста не осталось,
      ответ тоже отклоняется.

    Args:
        text: текст ответа LLM.
        max_share: максимальная допустимая доля иероглифов (0..1).

    Returns:
        :class:`CjkSanitizeResult` с очищенным текстом и счётчиками.

    Raises:
        ValueError: ``max_share`` вне диапазона 0..1.
    """
    if not 0.0 <= max_share <= 1.0:
        raise ValueError(f"max_share должен быть в 0..1, получено {max_share}")
    cjk_chars = count_cjk(text)
    share = cjk_share(text)
    if cjk_chars == 0:
        return CjkSanitizeResult(text, 0, 0, 0.0, False)
    if share > max_share:
        return CjkSanitizeResult("", cjk_chars, 0, share, True)
    stripped, removed = strip_cjk_sentences(text)
    if not stripped.strip():
        return CjkSanitizeResult("", cjk_chars, removed, share, True)
    return CjkSanitizeResult(stripped, cjk_chars, removed, share, False)


def cjk_warning(language: str = "ru") -> str:
    """Подсказка для одного retry без китайских иероглифов.

    Args:
        language: ``ru`` или ``en`` — язык подсказки.

    Returns:
        Короткое предупреждение без пакета фактов.
    """
    if language == "en":
        return (
            "WARNING: the previous answer contained Chinese characters. "
            "Answer in English only. Remove all Chinese characters."
        )
    return (
        "ВНИМАНИЕ: в предыдущем ответе были китайские иероглифы. "
        "Отвечай только на русском языке. Убери китайские иероглифы."
    )


def _sentence_spans(text: str) -> list[tuple[int, int]]:
    """Границы предложений (без переводов строк) в исходном тексте."""
    return [
        (match.start(), match.end()) for match in _SENTENCE_TOKEN.finditer(text)
    ]


def _sentence_for_match(
    start: int,
    text: str,
    spans: Sequence[tuple[int, int]],
    starts: Sequence[int],
) -> str | None:
    """Предложение, содержащее позицию `start`, или ``None``."""
    index = bisect.bisect_right(starts, start) - 1
    if index < 0:
        return None
    span_start, span_end = spans[index]
    if not span_start <= start < span_end:
        return None
    return text[span_start:span_end].strip()


def _build_claim(
    match: re.Match[str], kind: ClaimKind, sentence: str
) -> SemanticClaim | None:
    """Собрать :class:`SemanticClaim` из совпадения регулярного выражения."""
    square_raw = match.group("square")
    if not square_raw:
        return None
    try:
        square = chess.parse_square(square_raw.lower())
    except ValueError:
        return None
    return SemanticClaim(
        sentence=sentence,
        piece=_parse_piece(match.groupdict().get("piece")),
        color=_parse_color(match.groupdict().get("color")),
        square=square,
        kind=kind,
        text=" ".join(match.group(0).split()),
    )


def _parse_piece(raw: str | None) -> chess.PieceType | None:
    """Название фигуры (RU/EN, любой падеж) → ``chess.PieceType``."""
    if not raw:
        return None
    word = raw.strip().lower()
    for stem, piece in _RU_PIECES:
        if word.startswith(stem):
            return piece
    for name, piece in _EN_PIECES:
        if word.startswith(name):
            return piece
    return None


def _parse_color(raw: str | None) -> chess.Color | None:
    """Название цвета (RU/EN) → ``chess.Color``."""
    if not raw:
        return None
    word = raw.strip().lower()
    if word.startswith("бел") or word.startswith("white"):
        return chess.WHITE
    if word.startswith(("чёрн", "черн", "black")):
        return chess.BLACK
    return None


def _known_positions(
    initial_fen: str,
    mainline_san: Sequence[str] | None,
    extra_fens: Sequence[str] | None,
) -> list[chess.Board]:
    """Позиции для сверки: старт + mainline + PV-линии движка.

    Повторяет подход :func:`app.engine.validator._mainline_positions`
    (валидатор не модифицируем): стартовая позиция, затем каждая позиция
    после легального хода mainline; ход, который не парсится, обрывает
    построение. Дополнительные FEN добавляются как есть.
    """
    start = parse_fen(initial_fen)
    positions = [start.copy()]
    if mainline_san:
        board = chess.Board(start.fen())
        for san in mainline_san:
            try:
                move = board.parse_san(san)
            except ValueError:
                break
            board.push(move)
            positions.append(board.copy())
    for fen in extra_fens or ():
        positions.append(parse_fen(fen))
    return positions


def _claim_holds(claim: SemanticClaim, positions: Sequence[chess.Board]) -> bool:
    """Верно ли утверждение хотя бы в одной из известных позиций."""
    for board in positions:
        piece = board.piece_at(claim.square)
        if piece is None:
            continue
        if claim.piece is not None and piece.piece_type != claim.piece:
            continue
        if claim.color is not None and piece.color != claim.color:
            continue
        return True
    return False


def _problem_label(claim: SemanticClaim) -> str:
    """Короткая подпись проблемы для ответа/подсказки (без повторов)."""
    return _normalize_text(claim.text)


def _normalize_text(value: str) -> str:
    """Lowercase + схлопывание пробелов, ``ё`` → ``е`` (устойчивое сравнение)."""
    lowered = value.lower().replace("ё", "е")
    return " ".join(lowered.split())


def _inside_any(
    start: int, end: int, spans: Sequence[tuple[int, int]]
) -> bool:
    """Полностью ли диапазон ``[start, end)`` внутри одного из `spans`."""
    return any(span_start <= start and end <= span_end for span_start, span_end in spans)
