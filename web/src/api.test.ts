import { describe, expect, it } from "vitest";

import { parseSseBlock } from "./api";

describe("parseSseBlock", () => {
  it("разбирает delta", () => {
    expect(parseSseBlock('event: delta\ndata: {"text":"d4"}')).toEqual({
      event: "delta",
      payload: { text: "d4" },
    });
  });

  it("разбирает многострочный data и игнорирует комментарии", () => {
    expect(
      parseSseBlock(': ping\nevent: delta\ndata: {"text":\ndata: "e4"}'),
    ).toEqual({ event: "delta", payload: { text: "e4" } });
  });

  it("возвращает null для мусора", () => {
    expect(parseSseBlock("event: delta\ndata: not-json")).toBeNull();
    expect(parseSseBlock(": ping")).toBeNull();
  });
});
