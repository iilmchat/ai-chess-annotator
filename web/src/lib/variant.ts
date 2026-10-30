import { Chess } from "chess.js";

/** Ход, который пользователь сделал на доске в режиме варианта. */
export interface UserMoveResult {
  /** Позиция после хода. */
  fen: string;
  /** Ход в нотации SAN — для строки варианта. */
  san: string;
  /** Ход в UCI-нотации (`e2e4`, `e7e8q`) — для подсветки последнего хода. */
  uci: string;
}

/**
 * Применить ход пользователя к позиции `fen`.
 *
 * Возвращает `null`, если ход нелегален (или FEN не разобрался): вызывающий
 * код в этом случае просто не принимает перетаскивание и фигура возвращается
 * на исходную клетку.
 *
 * Превращение по умолчанию — ферзь: у доски нет выбора фигуры, а ферзь —
 * самый частый и однозначный вариант для быстрого разбора.
 */
export function applyUserMove(
  fen: string,
  from: string,
  to: string,
  promotion = "q",
): UserMoveResult | null {
  try {
    const chess = new Chess(fen);
    const move = chess.move({ from, to, promotion });
    return {
      fen: chess.fen(),
      san: move.san,
      // UCI собираем из from/to/promotion, а не из `lan`: формат совпадает с
      // тем, что ожидает `uciSquares` в lib/board.ts (подсветка последнего хода).
      uci: `${move.from}${move.to}${move.promotion ?? ""}`,
    };
  } catch {
    return null;
  }
}

/**
 * Собрать человекочитаемую строку варианта от указанного полухода.
 *
 * `startPly` — число полуходов основной линии до первого хода варианта
 * (0-based, то есть `selectedPly` доски). Чётное значение — ход белых
 * (`15. Nf3 Nc6`), нечётное — ход чёрных (`15... Nf3`); далее номера
 * подставляются только перед ходами белых, как в шахматной литературе.
 * Пустой список ходов даёт пустую строку.
 */
export function formatVariantLine(startPly: number, sans: string[]): string {
  const moves = sans.map((san) => san.trim()).filter((san) => san.length > 0);
  if (moves.length === 0) {
    return "";
  }

  const start = Number.isFinite(startPly) ? Math.max(0, Math.floor(startPly)) : 0;

  const parts = moves.map((san, index) => {
    const ply = start + index;
    // Номер полухода отсчитывается от начала партии: чётный — ход белых.
    if (ply % 2 === 0) {
      return `${ply / 2 + 1}. ${san}`;
    }
    // Ход чёрных внутри варианта идёт без номера, кроме первого хода
    // варианта — его номер нужен, чтобы понять, к какому ходу относится линия.
    return index === 0 ? `${Math.floor(ply / 2) + 1}... ${san}` : san;
  });

  return parts.join(" ");
}
