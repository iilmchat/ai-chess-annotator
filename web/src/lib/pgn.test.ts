import { Chess } from "chess.js";
import { describe, expect, it } from "vitest";

import type { AnalyzedMove } from "../types";
import {
  buildAnnotatedPgn,
  buildPgnFilename,
  buildPgnFromSan,
  positionsFromPgn,
} from "./pgn";

const PGN = `[Event "Test"]
[Site "ChessAnnotator"]
[Date "2026.10.28"]
[White "Svidler"]
[Black "Shirov"]
[Result "1-0"]
[ECO "C60"]

1. e4 e5 2. Nf3 Nc6 1-0`;

const START_FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";

function analysedMove(
  ply: number,
  san: string,
  overrides: Partial<AnalyzedMove> = {},
): AnalyzedMove {
  return {
    ply,
    san,
    uci: "e2e4",
    fen: START_FEN,
    eval_cp: 0,
    mate: null,
    best_move_san: san,
    delta_cp: 0,
    classification: "good",
    motifs: [],
    recommended_move: null,
    ...overrides,
  };
}

const MOVES: AnalyzedMove[] = [
  analysedMove(1, "e4", { eval_cp: 35, classification: "good" }),
  analysedMove(2, "e5", { eval_cp: 30, classification: "inaccuracy" }),
  analysedMove(3, "Nf3", { eval_cp: 40, classification: "best" }),
  analysedMove(4, "Nc6", { eval_cp: 25, classification: "good" }),
];

describe("positionsFromPgn", () => {
  it("восстанавливает FEN после каждого хода", () => {
    const result = positionsFromPgn("1. e4 e5 2. Nf3 Nc6 1/2-1/2");

    expect(result.positions).toHaveLength(4);
    expect(result.initialFen).toMatch(/^rnbqkbnr\/pppppppp/);
    expect(result.positions[0].san).toBe("e4");
    expect(result.positions[0].uci).toBe("e2e4");
    expect(result.positions[0].fen).toContain("4P3");
    expect(result.positions[3].san).toBe("Nc6");
  });

  it("поддерживает FEN-старт", () => {
    const pgn =
      '[SetUp "1"]\n[FEN "8/8/8/8/8/8/4P3/4K2k w - - 0 1"]\n\n1. e4 Kg2 1/2-1/2';
    const result = positionsFromPgn(pgn);

    expect(result.initialFen).toContain("4K2k");
    expect(result.positions).toHaveLength(2);
  });

  it("отклоняет неразбираемый PGN", () => {
    expect(() => positionsFromPgn("1. Zz9")).toThrow(/PGN/);
  });
});

describe("buildAnnotatedPgn", () => {
  it("сохраняет заголовки исходной партии и добавляет Annotator", () => {
    const result = buildAnnotatedPgn({ pgn: PGN, moves: MOVES });

    expect(result.startsWith("[Event")).toBe(true);
    expect(result).toContain('[Event "Test"]');
    expect(result).toContain('[Site "ChessAnnotator"]');
    expect(result).toContain('[White "Svidler"]');
    expect(result).toContain('[Black "Shirov"]');
    expect(result).toContain('[ECO "C60"]');
    expect(result).toContain('[Result "1-0"]');
    expect(result).toContain('[Annotator "ChessAnnotator"]');
  });

  it("не перезаписывает существующий Annotator", () => {
    const pgn = PGN.replace(
      '[ECO "C60"]',
      '[ECO "C60"]\n[Annotator "Human"]',
    );
    const result = buildAnnotatedPgn({ pgn, moves: MOVES });

    expect(result).toContain('[Annotator "Human"]');
    expect(result).not.toContain('[Annotator "ChessAnnotator"]');
  });

  it("добавляет комментарий с оценкой и классификацией к каждому ходу", () => {
    const result = buildAnnotatedPgn({ pgn: PGN, moves: MOVES });

    expect(result).toContain("1. e4 {+0.35, good} e5 {+0.30, inaccuracy}");
    expect(result).toContain("2. Nf3 {+0.40, best} Nc6 {+0.25, good}");
    expect(result.match(/\{/g)).toHaveLength(4);
    expect(result.trimEnd().endsWith("1-0")).toBe(true);
  });

  it("пишет мат в комментарий вместо cp-оценки", () => {
    const moves: AnalyzedMove[] = [
      analysedMove(1, "e4", { eval_cp: null, mate: 3 }),
      analysedMove(2, "e5", { eval_cp: null, mate: -2 }),
    ];
    const result = buildAnnotatedPgn({ pgn: PGN, moves });

    expect(result).toContain("{+M3, good}");
    expect(result).toContain("{-M2, good}");
  });

  it("дописывает комментарий партии после результата и санитизирует его", () => {
    const result = buildAnnotatedPgn({
      pgn: PGN,
      moves: MOVES,
      commentary: "Ключ {опасность}\nвторая строка",
    });

    expect(result.trimEnd().endsWith("1-0 {Ключ (опасность) вторая строка}")).toBe(
      true,
    );
    expect(result).not.toContain("{опасность}");
    // 4 комментария к ходам + комментарий партии.
    expect(result.match(/\{/g)).toHaveLength(5);
    expect(result.match(/\}/g)).toHaveLength(5);
  });

  it("не даёт скобкам и переводам строк сломать PGN", () => {
    const moves: AnalyzedMove[] = [
      analysedMove(1, "e4", { classification: "bad}\n1. h4 {" }),
    ];
    const result = buildAnnotatedPgn({ pgn: PGN, moves });

    expect(result).not.toContain("bad}\n");
    expect(result).toContain("{+0.00, bad) 1. h4 (}");
    // Главное: результат остаётся валидным PGN.
    const replayed = new Chess();
    replayed.loadPgn(result);
    expect(replayed.history()).toEqual(["e4", "e5", "Nf3", "Nc6"]);
  });

  it("возвращает PGN, который chess.js разбирает обратно", () => {
    const result = buildAnnotatedPgn({ pgn: PGN, moves: MOVES });
    const replayed = new Chess();
    replayed.loadPgn(result);

    expect(replayed.history()).toEqual(["e4", "e5", "Nf3", "Nc6"]);
    expect(replayed.getHeaders().Annotator).toBe("ChessAnnotator");
    expect(replayed.getHeaders().Result).toBe("1-0");
  });

  it("отклоняет пустой и неразбираемый PGN", () => {
    expect(() => buildAnnotatedPgn({ pgn: "   ", moves: [] })).toThrow(/PGN/);
    expect(() => buildAnnotatedPgn({ pgn: "1. Zz9", moves: [] })).toThrow(/PGN/);
  });
});

describe("buildPgnFromSan", () => {
  it("собирает PGN из SAN со заголовками и результатом", () => {
    const result = buildPgnFromSan(["e4", "e5", "Nf3"]);

    expect(result.startsWith('[Event "ChessAnnotator"]')).toBe(true);
    expect(result).toContain('[Date "');
    expect(result).toContain("[Result \"*\"]");
    expect(result).toContain("\n\n1. e4 e5 2. Nf3 *\n");

    const replayed = new Chess();
    replayed.loadPgn(result);
    expect(replayed.history()).toEqual(["e4", "e5", "Nf3"]);
  });

  it("поддерживает FEN-старт через SetUp/FEN", () => {
    const fen = "8/8/8/8/8/8/4P3/4K2k w - - 0 1";
    const result = buildPgnFromSan(["e4", "Kg2"], fen);

    expect(result).toContain('[SetUp "1"]');
    expect(result).toContain(`[FEN "${fen}"]`);
    expect(result).toContain("\n\n1. e4 Kg2 *\n");

    const replayed = new Chess();
    replayed.loadPgn(result);
    expect(replayed.history()).toEqual(["e4", "Kg2"]);
  });

  it("сохраняет FEN исходной позиции аннотированного PGN", () => {
    const fen = "8/8/8/8/8/8/4P3/4K2k w - - 0 1";
    const source = buildPgnFromSan(["e4", "Kg2"], fen);
    const annotated = buildAnnotatedPgn({
      pgn: source,
      moves: [analysedMove(1, "e4", { eval_cp: 120 }), analysedMove(2, "Kg2")],
    });

    expect(annotated).toContain('[FEN "8/8/8/8/8/8/4P3/4K2k w - - 0 1"]');
    expect(annotated).toContain("1. e4 {+1.20, good} Kg2 {+0.00, good}");
    const replayed = new Chess();
    replayed.loadPgn(annotated);
    expect(replayed.history()).toEqual(["e4", "Kg2"]);
  });
});

describe("buildPgnFilename", () => {
  it("подставляет analysis_id или game", () => {
    expect(buildPgnFilename("42")).toBe("chessannotator-42.pgn");
    expect(buildPgnFilename(7)).toBe("chessannotator-7.pgn");
    expect(buildPgnFilename(undefined)).toBe("chessannotator-game.pgn");
    expect(buildPgnFilename(null)).toBe("chessannotator-game.pgn");
  });

  it("не пропускает разделители путей в имя файла", () => {
    expect(buildPgnFilename("a/b c")).toBe("chessannotator-a-b-c.pgn");
    expect(buildPgnFilename("..\\../etc")).toBe("chessannotator-etc.pgn");
  });
});
