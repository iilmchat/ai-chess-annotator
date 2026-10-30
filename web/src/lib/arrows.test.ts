import { describe, expect, it } from "vitest";

import type { AnalyzedMove, RecommendedMove } from "../types";
import { buildArrows, extractSanMoves } from "./arrows";

const START_FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";
const FEN_AFTER_E4 = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq e3 0 1";
const FEN_AFTER_E5 = "rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq e6 0 2";
const FEN_AFTER_NC6 = "r1bqkbnr/pppp1ppp/2n5/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R w KQkq - 2 3";

function analyzedMove(
  ply: number,
  san: string,
  uci: string,
  classification = "good",
  recommended: RecommendedMove | null = null,
): AnalyzedMove {
  return {
    ply,
    san,
    uci,
    fen: "",
    eval_cp: 0,
    mate: null,
    best_move_san: san,
    delta_cp: 0,
    classification,
    motifs: [],
    recommended_move: recommended,
  };
}

/** Основная линия 1.e4 e5 2.Nf3 Nc6 с рекомендацией для 2...Nc6. */
function mainline(): AnalyzedMove[] {
  return [
    analyzedMove(1, "e4", "e2e4"),
    analyzedMove(2, "e5", "e7e5"),
    analyzedMove(3, "Nf3", "g1f3"),
    analyzedMove(4, "Nc6", "b8c6", "good", {
      san: "Bb5",
      uci: "f1b5",
      eval_cp: 35,
      mate: null,
    }),
  ];
}

describe("extractSanMoves", () => {
  it("извлекает пешечные и фигурные ходы в порядке упоминания", () => {
    expect(extractSanMoves("1. e4 e5 2. Nf3 Nc6 3. Bb5 a6")).toEqual([
      "e4",
      "e5",
      "Nf3",
      "Nc6",
      "Bb5",
      "a6",
    ]);
  });

  it("распознаёт рокировку в обеих нотациях и нормализует нули", () => {
    expect(
      extractSanMoves("Можно O-O, а можно 0-0-0 — и даже O-O+."),
    ).toEqual(["O-O", "O-O-O"]);
  });

  it("распознаёт взятия, шах и превращения", () => {
    expect(extractSanMoves("exd5 Qxf7+ Rxe8# e8=Q+ exd8=N")).toEqual([
      "exd5",
      "Qxf7+",
      "Rxe8#",
      "e8=Q+",
      "exd8=N",
    ]);
  });

  it("не ловит SAN-подобные куски внутри слов", () => {
    expect(
      extractSanMoves("e4life, Bc4x и слово h4cker, но ход Bc4 — настоящий."),
    ).toEqual(["Bc4"]);
  });

  it("дедуплицирует ходы, сохраняя порядок первого упоминания", () => {
    expect(extractSanMoves("e4 Nf3 e4 e5 Nf3 e4")).toEqual(["e4", "Nf3", "e5"]);
  });

  it("не считает номера ходов и оценки ходами", () => {
    expect(extractSanMoves("Ход 12. e4! лучше, чем 12... c5?")).toEqual([
      "e4",
      "c5",
    ]);
  });
});

describe("buildArrows", () => {
  it("добавляет зелёную стрелку рекомендации выбранного полухода", () => {
    const arrows = buildArrows({
      fen: FEN_AFTER_NC6,
      moves: mainline(),
      selectedPly: 4,
    });

    expect(arrows).toEqual([{ from: "f1", to: "b5", color: "green" }]);
  });

  it("не добавляет рекомендацию для ply 0", () => {
    const moves = [
      analyzedMove(1, "e4", "e2e4", "good", {
        san: "e4",
        uci: "e2e4",
        eval_cp: 30,
        mate: null,
      }),
    ];

    const arrows = buildArrows({
      fen: START_FEN,
      moves,
      selectedPly: 0,
      commentaryText: "Начнём с e4.",
    });

    // Стрелка из комментария есть, а базовой рекомендации на старте нет.
    expect(arrows).toEqual([{ from: "e2", to: "e4", color: "green" }]);
  });

  it("резолвит SAN из комментария на текущей позиции", () => {
    const arrows = buildArrows({
      fen: FEN_AFTER_NC6,
      moves: mainline().map((move) => ({
        ...move,
        recommended_move: null,
      })),
      selectedPly: 4,
      commentaryText: "Лучше было Bc4, а затем d3 — инициатива у белых.",
    });

    expect(arrows).toEqual([
      { from: "f1", to: "c4", color: "green" },
      { from: "d2", to: "d3", color: "green" },
    ]);
  });

  it("пропускает нелегальные для текущей позиции SAN", () => {
    const arrows = buildArrows({
      fen: FEN_AFTER_NC6,
      moves: [],
      selectedPly: 0,
      // Qh5 здесь невозможен: диагональ d1-h5 перекрыта конём f3.
      commentaryText: "Qh5 было бы плохо, а вот Bc4 — другое дело.",
    });

    expect(arrows).toEqual([{ from: "f1", to: "c4", color: "green" }]);
  });

  it("красит в красный сыгранный ход основной линии с классом blunder", () => {
    const moves = [
      analyzedMove(1, "e4", "e2e4"),
      analyzedMove(2, "e5", "e7e5"),
      analyzedMove(3, "Bc4", "f1c4", "blunder"),
    ];

    const arrows = buildArrows({
      fen: FEN_AFTER_NC6,
      moves,
      selectedPly: 3,
      commentaryText: "Здесь Bc4 — зевок.",
    });

    expect(arrows).toEqual([{ from: "f1", to: "c4", color: "red" }]);
  });

  it("оставляет зелёным ход с классом best", () => {
    const moves = [
      analyzedMove(1, "e4", "e2e4"),
      analyzedMove(2, "e5", "e7e5"),
      analyzedMove(3, "Bb5", "f1b5", "best"),
    ];

    const arrows = buildArrows({
      fen: FEN_AFTER_NC6,
      moves,
      selectedPly: 3,
      commentaryText: "Bb5 — лучший ход.",
    });

    expect(arrows).toEqual([{ from: "f1", to: "b5", color: "green" }]);
  });

  it("дедуплицирует стрелку рекомендации и упомянутый ход", () => {
    const arrows = buildArrows({
      fen: FEN_AFTER_NC6,
      moves: mainline(),
      selectedPly: 4,
      commentaryText: "Лучше было Bb5.",
    });

    expect(arrows).toEqual([{ from: "f1", to: "b5", color: "green" }]);
  });

  it("ограничивает число стрелок из комментария через limit", () => {
    const arrows = buildArrows({
      fen: FEN_AFTER_NC6,
      moves: [],
      selectedPly: 0,
      commentaryText: "Bc4 d3 Be2 h3 — все ходы хороши.",
      limit: 2,
    });

    expect(arrows).toEqual([
      { from: "f1", to: "c4", color: "green" },
      { from: "d2", to: "d3", color: "green" },
    ]);
  });

  it("без комментария и рекомендации стрелок нет", () => {
    expect(
      buildArrows({
        fen: START_FEN,
        moves: [],
        selectedPly: 0,
        commentaryText: "",
      }),
    ).toEqual([]);

    expect(
      buildArrows({
        fen: START_FEN,
        moves: mainline(),
        selectedPly: 0,
        commentaryText: null,
      }),
    ).toEqual([]);
  });

  it("не падает на невалидном FEN и нелегальном комментарии", () => {
    expect(
      buildArrows({
        fen: "8/8/8 w - - 0 1",
        moves: [],
        selectedPly: 0,
        commentaryText: "e4",
      }),
    ).toEqual([]);
  });

  it("строит стрелку из комментария для позиции после 1.e4", () => {
    const arrows = buildArrows({
      fen: FEN_AFTER_E4,
      moves: [analyzedMove(1, "e4", "e2e4")],
      selectedPly: 1,
      commentaryText: "Чёрным стоит ответить e5.",
    });

    expect(arrows).toEqual([{ from: "e7", to: "e5", color: "green" }]);
  });

  it("не путает ход основной линии из другой позиции с сыгранным", () => {
    // Nf3 уже был в основной линии (ply 3, класс best), но сейчас ход белых
    // после 1.e4 e5 — Nf3 снова легален и остаётся зелёным.
    const arrows = buildArrows({
      fen: FEN_AFTER_E5,
      moves: [
        analyzedMove(1, "e4", "e2e4"),
        analyzedMove(2, "e5", "e7e5"),
        analyzedMove(3, "Nf3", "g1f3", "best"),
      ],
      selectedPly: 2,
      commentaryText: "Nf3 развивает коня.",
    });

    expect(arrows).toEqual([{ from: "g1", to: "f3", color: "green" }]);
  });
});
