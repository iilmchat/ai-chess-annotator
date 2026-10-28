import { describe, expect, it } from "vitest";

import {
  classificationLabel,
  formatEval,
  formatTokens,
  levelLabel,
  movePrefix,
} from "./format";

describe("formatEval", () => {
  it("показывает сантипешки со знаком", () => {
    expect(formatEval(154, null)).toBe("+1.54");
    expect(formatEval(-32, null)).toBe("-0.32");
    expect(formatEval(0, null)).toBe("+0.00");
  });

  it("показывает мат", () => {
    expect(formatEval(null, 3)).toBe("+M3");
    expect(formatEval(null, -2)).toBe("-M2");
  });

  it("показывает прочерк без оценки", () => {
    expect(formatEval(null, null)).toBe("—");
  });
});

describe("подписи", () => {
  it("переводит классы ходов", () => {
    expect(classificationLabel("blunder")).toBe("Зевок");
    expect(classificationLabel("mistake")).toBe("Ошибка");
    expect(classificationLabel("unknown")).toBe("unknown");
  });

  it("переводит уровни", () => {
    expect(levelLabel("club")).toBe("Клуб");
    expect(levelLabel("gm")).toBe("Гроссмейстер");
  });

  it("нумерует полуходы", () => {
    expect(movePrefix(1)).toBe("1.");
    expect(movePrefix(2)).toBe("1...");
    expect(movePrefix(23)).toBe("12.");
  });

  it("форматирует токены", () => {
    expect(formatTokens(1950)).toMatch(/1\s?950/);
  });
});
