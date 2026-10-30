import { describe, expect, it } from "vitest";

import { START_FEN } from "./format";
import { applyUserMove, formatVariantLine } from "./variant";

describe("applyUserMove", () => {
  it("применяет легальный ход и возвращает FEN/SAN/UCI", () => {
    const result = applyUserMove(START_FEN, "e2", "e4");

    expect(result).not.toBeNull();
    expect(result?.san).toBe("e4");
    expect(result?.uci).toBe("e2e4");
    expect(result?.fen).toBe(
      // chess.js 1.4 не пишет e3 в FEN, если взятие на проходе невозможно.
      "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1",
    );
  });

  it("применяет ход чёрных из позиции после 1.e4", () => {
    const result = applyUserMove(
      "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq e3 0 1",
      "e7",
      "e5",
    );

    expect(result?.san).toBe("e5");
    expect(result?.uci).toBe("e7e5");
  });

  it("возвращает null для нелегального хода", () => {
    expect(applyUserMove(START_FEN, "e2", "e5")).toBeNull();
    // Чёрная пешка не может ходить, когда ход белых.
    expect(applyUserMove(START_FEN, "e7", "e6")).toBeNull();
  });

  it("возвращает null для пустой или битой клетки", () => {
    expect(applyUserMove(START_FEN, "e4", "e5")).toBeNull();
    expect(applyUserMove(START_FEN, "zz", "e4")).toBeNull();
  });

  it("возвращает null для невалидного FEN", () => {
    expect(applyUserMove("8/8/8 w - - 0 1", "a1", "a2")).toBeNull();
  });

  it("ставит превращение в UCI с фигурой по умолчанию (ферзь)", () => {
    // Чёрный король на h8: поле e8 свободно, превращение даёт шах по 8-й
    // горизонтали.
    const fen = "7k/4P3/8/8/8/8/8/4K3 w - - 0 1";
    const result = applyUserMove(fen, "e7", "e8");

    expect(result?.san).toBe("e8=Q+");
    expect(result?.uci).toBe("e7e8q");
    expect(result?.fen).toContain("4Q2k");
  });
});

describe("formatVariantLine", () => {
  it("возвращает пустую строку для пустого списка", () => {
    expect(formatVariantLine(0, [])).toBe("");
    expect(formatVariantLine(15, [])).toBe("");
  });

  it("нумерует вариант с хода белых", () => {
    expect(formatVariantLine(0, ["Nf3", "Nc6"])).toBe("1. Nf3 Nc6");
  });

  it("ставит многоточие, если вариант начинается с хода чёрных", () => {
    expect(formatVariantLine(1, ["e5"])).toBe("1... e5");
  });

  it("нумерует несколько ходов внутри середины партии", () => {
    // 29 полуходов сыграно → следующий ход чёрных, 15-й ход партии;
    // следующий за ним ход белых — уже 16-й.
    expect(formatVariantLine(29, ["Nf3", "Nc6", "Bb5"])).toBe(
      "15... Nf3 16. Nc6 Bb5",
    );
  });

  it("ставит номер первому ходу белых в середине партии", () => {
    // 30 полуходов сыграно → ход белых, 16-й ход партии.
    expect(formatVariantLine(30, ["e4", "e5", "Nf3"])).toBe(
      "16. e4 e5 17. Nf3",
    );
  });

  it("не падает на отрицательном startPly", () => {
    expect(formatVariantLine(-3, ["e4"])).toBe("1. e4");
  });
});
