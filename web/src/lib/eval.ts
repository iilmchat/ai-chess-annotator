import type { AnalyzedMove } from "../types";

/**
 * Предел шкалы оценки в сантипешках: ±10 пешек.
 *
 * Всё, что выходит за предел, прижимается к границе — иначе один «выигранный»
 * ход растягивал бы шкалу и график на всю высоту.
 */
export const EVAL_LIMIT_CP = 1000;

/** Минимальная доля белых на шкале, чтобы полоса никогда не исчезала. */
const MIN_SHARE = 0.02;

/** Максимальная доля белых на шкале (симметрично MIN_SHARE). */
const MAX_SHARE = 0.98;

/** Ограничить оценку диапазоном ±EVAL_LIMIT_CP. */
export function clampEval(evalCp: number): number {
  return Math.max(-EVAL_LIMIT_CP, Math.min(EVAL_LIMIT_CP, evalCp));
}

/**
 * Доля белых на вертикальной шкале оценки (0..1, сверху — белые).
 *
 * - мат в пользу белых → 1, мат в пользу чёрных → 0 (полная заливка);
 * - нет оценки → 0.5 (равная позиция);
 * - сантипешки прижимаются к ±1000 и пересчитываются в долю, но не выходят
 *   за пределы 0.02..0.98, чтобы шкала оставалась видимой.
 */
export function evalToWhiteShare(
  evalCp: number | null,
  mate: number | null,
): number {
  if (mate !== null) {
    return mate > 0 ? 1 : 0;
  }
  if (evalCp === null) {
    return 0.5;
  }
  const share = 0.5 + clampEval(evalCp) / (2 * EVAL_LIMIT_CP);
  return Math.min(MAX_SHARE, Math.max(MIN_SHARE, share));
}

/**
 * Ряд для графика оценки: X — полуход, Y — оценка в сантипешках.
 *
 * Мат кодируется как ±EVAL_LIMIT_CP, оценка прижимается к тому же пределу,
 * отсутствие оценки трактуется как 0 (равная позиция).
 */
export function evalSeries(
  moves: AnalyzedMove[],
): Array<{ ply: number; value: number }> {
  return moves.map((move) => ({
    ply: move.ply,
    value:
      move.mate !== null
        ? move.mate > 0
          ? EVAL_LIMIT_CP
          : -EVAL_LIMIT_CP
        : clampEval(move.eval_cp ?? 0),
  }));
}

/**
 * Подпись мата: «M3» для мата белым, «M-2» для мата чёрным.
 *
 * `null`, если мата в оценке нет.
 */
export function mateLabel(mate: number | null): string | null {
  if (mate === null) {
    return null;
  }
  return mate > 0 ? `M${mate}` : `M-${Math.abs(mate)}`;
}
