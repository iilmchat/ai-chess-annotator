import { describe, expect, it } from "vitest";

import { positionsFromPgn } from "./pgn";

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
