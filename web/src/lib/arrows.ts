import { Chess } from "chess.js";

import type { AnalyzedMove } from "../types";

/**
 * Стрелка на доске. `from`/`to` — поля в алгебраической нотации (`g1`, `f3`),
 * `color` — одна из двух ролей: рекомендация/хороший ход (зелёная) или
 * ошибка (красная).
 */
export interface BoardArrow {
  from: string;
  to: string;
  color: "green" | "red";
}

/**
 * SAN-ход в тексте: рокировка, ход фигурой/пешкой, взятие, превращение, шах.
 *
 * Семантика повторяет `SAN_PATTERN` backend (`app/engine/validator.py`), но
 * пешечные ходы (`e4`) нам нужны специально: именно они дают стрелки на
 * доске. Границы `(?<![A-Za-z0-9])…(?![A-Za-z0-9])` не позволяют ловить
 * «ходы» внутри слов.
 */
const SAN_PATTERN =
  /(?<![A-Za-z0-9])(?:O-O-O|O-O|0-0-0|0-0|[KQRBN][a-h]?[1-8]?x?[a-h][1-8](?:=[QRBN])?[+#]?|[a-h](?:x[a-h])?[1-8](?:=[QRBN])?[+#]?)(?![A-Za-z0-9])/g;

/**
 * Классы, при которых ход красим в красный. Остальные
 * (`good`/`best`/`brilliant` и неизвестные значения) по умолчанию зелёные.
 */
const RED_CLASSIFICATIONS = new Set(["blunder", "mistake", "inaccuracy"]);

/** Сколько стрелок из комментария показываем, если `limit` не задан. */
const DEFAULT_LIMIT = 3;

/** Первые четыре символа UCI — откуда и куда идёт ход. */
const UCI_SQUARES = /^([a-h][1-8])([a-h][1-8])/i;

/**
 * Извлечь SAN-ходы из текста комментария в порядке упоминания.
 *
 * Рокировка в «нулевой» нотации (`0-0`) нормализуется в `O-O`, повторы
 * отбрасываются (первое вхождение сохраняет позицию в списке). Markdown
 * вокруг хода (`**e4**`) не мешает: звёздочки — не буквы и не цифры.
 */
export function extractSanMoves(text: string): string[] {
  const moves: string[] = [];
  const seen = new Set<string>();
  for (const match of text.matchAll(SAN_PATTERN)) {
    const san = match[0].replaceAll("0", "O");
    if (!seen.has(san)) {
      seen.add(san);
      moves.push(san);
    }
  }
  return moves;
}

export interface BuildArrowsOptions {
  /** FEN текущей (отображаемой) позиции. */
  fen: string;
  /** Разобранные ходы основной линии. */
  moves: AnalyzedMove[];
  /** Выбранный полуход основной линии (0 — начальная позиция). */
  selectedPly: number;
  /** Текст сгенерированного/отредактированного комментария. */
  commentaryText?: string | null;
  /**
   * Максимум стрелок из комментария, не считая базовой рекомендации.
   * По умолчанию 3.
   */
  limit?: number;
}

/**
 * Собрать стрелки для доски: базовая рекомендация выбранного полухода плюс
 * легальные ходы, упомянутые в комментарии.
 *
 * Комментарий пишется для всей партии, поэтому SAN-ходы проверяются на
 * **текущей** позиции: нелегальный там ход просто пропускается (он мог
 * относиться к другому моменту партии). Цвет: рекомендация — всегда зелёная;
 * упомянутый ход — красный, если он совпадает с ходом основной линии,
 * классифицированным как `blunder`/`mistake`/`inaccuracy`, иначе зелёный.
 * Дубликаты по паре from/to схлопываются (первая стрелка выигрывает).
 *
 * Невалидный FEN не бросает исключение: остаётся только базовая стрелка
 * рекомендации (если она есть).
 */
export function buildArrows({
  fen,
  moves,
  selectedPly,
  commentaryText,
  limit = DEFAULT_LIMIT,
}: BuildArrowsOptions): BoardArrow[] {
  const arrows: BoardArrow[] = [];
  const seen = new Set<string>();

  function add(arrow: BoardArrow): boolean {
    const key = `${arrow.from}${arrow.to}`;
    if (seen.has(key)) {
      return false;
    }
    seen.add(key);
    arrows.push(arrow);
    return true;
  }

  function squares(uci: string): { from: string; to: string } | null {
    const match = UCI_SQUARES.exec(uci);
    if (!match) {
      return null;
    }
    const from = match[1].toLowerCase();
    const to = match[2].toLowerCase();
    return from === to ? null : { from, to };
  }

  // 1. Базовая рекомендация относится к выбранному полуходу; для ply 0 её нет.
  const recommendation =
    selectedPly > 0 ? (moves[selectedPly - 1]?.recommended_move ?? null) : null;
  const recommendedUci = recommendation?.uci.toLowerCase() ?? null;
  if (recommendation) {
    const target = squares(recommendation.uci);
    if (target) {
      add({ ...target, color: "green" });
    }
  }

  // 2. Ходы из комментария резолвим на текущей позиции через chess.js.
  const sanMoves = extractSanMoves(commentaryText ?? "");
  if (sanMoves.length === 0) {
    return arrows;
  }

  const max = Number.isFinite(limit)
    ? Math.max(0, Math.floor(limit))
    : DEFAULT_LIMIT;
  if (max === 0) {
    return arrows;
  }

  const played = selectedPly > 0 ? moves[selectedPly - 1] : undefined;
  const classificationByUci = new Map<string, string>();
  for (const move of moves) {
    const uci = move.uci.toLowerCase();
    if (!classificationByUci.has(uci)) {
      classificationByUci.set(uci, move.classification);
    }
  }
  // Сыгранный ход выбранного полухода важнее одноимённого хода из другого
  // места партии: «упомянут именно тот ход, который был сделан».
  function classificationFor(uci: string): string | null {
    if (played && played.uci.toLowerCase() === uci) {
      return played.classification;
    }
    return classificationByUci.get(uci) ?? null;
  }

  let added = 0;
  for (const san of sanMoves) {
    if (added >= max) {
      break;
    }
    let from: string;
    let to: string;
    let uci: string;
    try {
      // Каждый ход проверяем от исходной позиции: комментарий перечисляет
      // альтернативы, а не последовательность.
      const move = new Chess(fen).move(san);
      from = move.from;
      to = move.to;
      uci = `${move.from}${move.to}${move.promotion ?? ""}`;
    } catch {
      // Нелегальный/неразбираемый SAN — не повод ломать отрисовку доски.
      continue;
    }

    if (seen.has(`${from}${to}`)) {
      continue;
    }
    const classification = classificationFor(uci);
    const color: BoardArrow["color"] =
      recommendedUci !== null && uci === recommendedUci
        ? "green"
        : classification !== null && RED_CLASSIFICATIONS.has(classification)
          ? "red"
          : "green";
    if (add({ from, to, color })) {
      added += 1;
    }
  }

  return arrows;
}
