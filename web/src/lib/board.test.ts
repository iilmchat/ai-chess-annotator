import { describe, expect, it } from "vitest";

import { parseFen, uciSquares } from "./board";
import { START_FEN } from "./format";

describe("parseFen", () => {
  it("разбирает начальную позицию", () => {
    const board = parseFen(START_FEN);

    expect(board).toHaveLength(8);
    expect(board[0][0]).toMatchObject({
      square: "a8",
      piece: "♜",
      light: true,
    });
    expect(board[7][4]).toMatchObject({ square: "e1", piece: "♔" });
    expect(board[4][4]).toMatchObject({ square: "e4", piece: null });
  });

  it("ставит пешку e4 и освобождает e2", () => {
    const fen = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1";
    const board = parseFen(fen);

    expect(board[4][4].piece).toBe("♙");
    expect(board[6][4].piece).toBeNull();
  });

  it("отклоняет FEN с неверным числом горизонталей", () => {
    expect(() => parseFen("8/8/8 w - - 0 1")).toThrow(/8 горизонталей/);
  });

  it("отклоняет FEN с неизвестной фигурой", () => {
    expect(() => parseFen("8/8/8/8/8/8/8/7X w - - 0 1")).toThrow(/неизвестная фигура/);
  });
});

describe("uciSquares", () => {
  it("разбирает обычный ход", () => {
    expect(uciSquares("e2e4")).toEqual({ from: "e2", to: "e4" });
  });

  it("разбирает превращение", () => {
    expect(uciSquares("e7e8q")).toEqual({ from: "e7", to: "e8" });
  });

  it("возвращает null для мусора", () => {
    expect(uciSquares("zz")).toBeNull();
  });
});
