import { positionsFromPgn } from "../src/lib/pgn";
import type { AnalyzeResponse, AnalyzedMove } from "../src/types";
import { expect, mockApi, REAL_BACKEND, registerAndLogin, test } from "./fixtures";

/**
 * T-073: стрелки на доске разбора.
 *
 * Сценарий — mocked-режим. `POST /v1/analyze` подменяется разбором короткой
 * партии «1. e4 e5 2. Nf3 Nc6», у последнего полухода появляется
 * `recommended_move` (Bb5). После загрузки разбора на доске должна быть
 * зелёная стрелка рекомендации, а после SSE-генерации комментария — ещё и
 * стрелки для упомянутых в тексте легальных ходов.
 *
 * DOM-селектор стрелок: react-chessboard 5.12.1 рисует их отдельным `<svg>`
 * с `viewBox="0 0 2048 2048"` (у фигур viewBox `0 0 45 45`), каждая стрелка —
 * `<path stroke="<color>">`; в `id` маркера-наконечника входят поля
 * `startSquare`/`endSquare`, поэтому по нему видно, куда ведёт стрелка.
 */

const MOCK_ANALYSIS_ID = "42";
const ARROWS_LAYER = 'svg[viewBox="0 0 2048 2048"]';

/**
 * Комментарий упоминает:
 * - `Nc6` — уже сделанный ход чёрных, в текущей позиции нелегален → стрелки нет;
 * - `Bb5` — совпадает с рекомендацией, стрелка не дублируется;
 * - `Bc4` — легальная альтернатива, появляется своя стрелка.
 */
const COMMENTARY_TEXT =
  "Чёрные сыграли Nc6, но лучше было Bb5, а затем Bc4 — белые сохраняют инициативу.";

/** SSE-ответ в формате `web/src/api.ts` (parseSseBlock). */
function sseBody(): string {
  const events = [
    `event: meta\ndata: ${JSON.stringify({ model: "mock-llm", level: "club" })}\n\n`,
    `event: delta\ndata: ${JSON.stringify({ text: COMMENTARY_TEXT })}\n\n`,
    `event: done\ndata: ${JSON.stringify({
      commentary_id: 101,
      tokens_used: 42,
      model: "mock-llm",
      validated: true,
    })}\n\n`,
  ];
  return events.join("");
}

function analysisWithRecommendation(): AnalyzeResponse {
  const { initialFen, positions } = positionsFromPgn("1. e4 e5 2. Nf3 Nc6");
  const lastPly = positions.length;
  const moves: AnalyzedMove[] = positions.map((position) => ({
    ply: position.ply,
    san: position.san,
    uci: position.uci,
    fen: position.fen,
    eval_cp: 20 + position.ply,
    mate: null,
    best_move_san: position.san,
    delta_cp: 0,
    classification: "good",
    motifs: [],
    // Рекомендация для последнего полухода: 3.Bb5 (легальна в этой позиции).
    recommended_move:
      position.ply === lastPly
        ? { san: "Bb5", uci: "f1b5", eval_cp: 35, mate: null }
        : null,
  }));

  return {
    analysis_id: MOCK_ANALYSIS_ID,
    status: "completed",
    meta: { white: "Svidler", black: "Shirov", eco: "C60" },
    initial_fen: initialFen,
    analysis_level: "club",
    moves_analyzed: positions.length,
    time_taken_ms: 1234,
    summary: { blunders: 0, mistakes: 0, inaccuracies: 0 },
    created_at: "2026-10-30T12:00:00Z",
    moves,
  };
}

test.beforeEach(async ({ page }) => {
  // Общие моки (auth/usage/games) + точечные override'ы:
  // Playwright отдаёт приоритет последнему зарегистрированному маршруту.
  await mockApi(page);
  if (REAL_BACKEND) {
    return;
  }
  await page.route(/^https?:\/\/[^/]+\/v1\/analyze$/, (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json; charset=utf-8",
      body: JSON.stringify(analysisWithRecommendation()),
    }),
  );
  await page.route(/^https?:\/\/[^/]+\/v1\/commentary\/stream$/, (route) =>
    route.fulfill({
      status: 200,
      headers: {
        "content-type": "text/event-stream; charset=utf-8",
        "cache-control": "no-cache",
      },
      body: sseBody(),
    }),
  );
});

test("стрелки доски: рекомендация и ходы из комментария", async ({ page }) => {
  test.skip(
    REAL_BACKEND,
    "T-073: сценарий проверяет mocked-разбор с recommended_move и мок-SSE",
  );
  test.setTimeout(60_000);

  await registerAndLogin(page);

  // Короткая партия: последний полуход — 2...Nc6, ход белых.
  await page.locator(".pgn-input").fill("1. e4 e5 2. Nf3 Nc6");
  await page.getByRole("button", { name: "Разобрать партию" }).click();
  await expect(page).toHaveURL(/\/analysis\/[^/]+$/, { timeout: 15_000 });
  await expect(page.locator(".board-wrap")).toBeVisible();

  const arrowsLayer = page.locator(ARROWS_LAYER);
  const arrowPaths = arrowsLayer.locator("path");

  // --- Базовая рекомендация: одна зелёная стрелка f1-b5 -------------------
  await expect(arrowsLayer).toHaveCount(1);
  await expect(arrowPaths).toHaveCount(1);
  await expect(arrowPaths.first()).toHaveAttribute("stroke", "green");
  await expect(
    arrowsLayer.locator('marker[id$="f1-b5"] polygon'),
  ).toHaveAttribute("fill", "green");

  // --- Генерация комментария: добавляются стрелки упомянутых ходов -------
  await page.getByRole("button", { name: "Сгенерировать", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "Сгенерировать заново", exact: true }),
  ).toBeVisible({ timeout: 15_000 });
  await expect(page.locator(".commentary-text")).toContainText("Bc4");

  // Рекомендация Bb5 не дублируется, нелегальный Nc6 пропущен, Bc4 — новая
  // стрелка. Итого: f1-b5 (рекомендация) + f1-c4 (комментарий).
  await expect(arrowPaths).toHaveCount(2);
  await expect(
    arrowsLayer.locator('marker[id$="f1-b5"] polygon'),
  ).toHaveAttribute("fill", "green");
  await expect(
    arrowsLayer.locator('marker[id$="f1-c4"] polygon'),
  ).toHaveAttribute("fill", "green");
  await expect(arrowPaths.nth(0)).toHaveAttribute("stroke", "green");
  await expect(arrowPaths.nth(1)).toHaveAttribute("stroke", "green");
});
