import { describe, expect, it } from "vitest";

import {
  clampEval,
  evalSeries,
  evalToWhiteShare,
  EVAL_LIMIT_CP,
  mateLabel,
} from "./eval";
import type { AnalyzedMove } from "../types";

/** Минимальный полуход: остальные поля для проверок не нужны. */
function move(ply: number, evalCp: number | null, mate: number | null): AnalyzedMove {
  return {
    ply,
    san: `m${ply}`,
    uci: "a2a3",
    fen: "",
    eval_cp: evalCp,
    mate,
    best_move_san: "a2a3",
    delta_cp: 0,
    classification: "good",
    motifs: [],
    recommended_move: null,
  };
}

describe("clampEval", () => {
  it("прижимает оценку к ±1000 cp", () => {
    expect(clampEval(0)).toBe(0);
    expect(clampEval(154)).toBe(154);
    expect(clampEval(5000)).toBe(EVAL_LIMIT_CP);
    expect(clampEval(-5000)).toBe(-EVAL_LIMIT_CP);
  });
});

describe("evalToWhiteShare", () => {
  it("даёт 1 для мата белым и 0 для мата чёрным", () => {
    expect(evalToWhiteShare(null, 3)).toBe(1);
    expect(evalToWhiteShare(null, -2)).toBe(0);
  });

  it("считает долю белых по сантипешкам", () => {
    expect(evalToWhiteShare(0, null)).toBe(0.5);
    expect(evalToWhiteShare(200, null)).toBe(0.6);
    expect(evalToWhiteShare(-200, null)).toBe(0.4);
  });

  it("не схлопывает шкалу на краях: 0.02..0.98", () => {
    expect(evalToWhiteShare(5000, null)).toBe(0.98);
    expect(evalToWhiteShare(-5000, null)).toBe(0.02);
  });

  it("трактует отсутствие оценки как равенство", () => {
    expect(evalToWhiteShare(null, null)).toBe(0.5);
  });
});

describe("evalSeries", () => {
  it("строит ряд полуходов и кодирует мат как ±1000", () => {
    const series = evalSeries([
      move(1, 30, null),
      move(2, null, 4),
      move(3, null, -2),
      move(4, -120, null),
    ]);
    expect(series).toEqual([
      { ply: 1, value: 30 },
      { ply: 2, value: 1000 },
      { ply: 3, value: -1000 },
      { ply: 4, value: -120 },
    ]);
  });

  it("прижимает крайние оценки и заменяет null нулём", () => {
    const series = evalSeries([
      move(1, 99999, null),
      move(2, null, null),
      move(3, -99999, null),
    ]);
    expect(series).toEqual([
      { ply: 1, value: 1000 },
      { ply: 2, value: 0 },
      { ply: 3, value: -1000 },
    ]);
  });

  it("возвращает пустой ряд для пустого списка ходов", () => {
    expect(evalSeries([])).toEqual([]);
  });
});

describe("mateLabel", () => {
  it("подписывает мат в обе стороны", () => {
    expect(mateLabel(3)).toBe("M3");
    expect(mateLabel(-2)).toBe("M-2");
    expect(mateLabel(1)).toBe("M1");
  });

  it("возвращает null, если мата нет", () => {
    expect(mateLabel(null)).toBeNull();
  });
});
