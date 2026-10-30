import {
  expect,
  mockApi,
  REAL_BACKEND,
  registerAndLogin,
  test,
} from "./fixtures";

/**
 * T-072: чат отправляет выбранный полуход как контекст (`selected_ply`).
 *
 * Сценарий — mocked-режим: разбираем короткую партию 1.e4 e5 2.Nf3 Nc6,
 * выбираем в списке ходов полуход 2 (e5) и задаём вопрос. POST
 * /v1/chat/stream должен содержать `selected_ply: 2`; для стартовой позиции
 * (ply 0) поля быть не должно — backend трактует отсутствие как «без
 * контекста». SSE-ответ чата мокается здесь же: в fixtures.ts есть только
 * мок /v1/commentary/stream.
 */

const CHAT_DELTAS = ["Пешка e5 — ", "это тестовый ответ E2E."];
const CHAT_ANSWER = CHAT_DELTAS.join("");

/** Тело SSE-ответа в формате `streamRequest`/`parseSseBlock` (web/src/api.ts). */
function sseBody(events: Array<{ event: string; data: unknown }>): string {
  return events
    .map(({ event, data }) => `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`)
    .join("");
}

test.beforeEach(async ({ page }) => {
  await mockApi(page);
  if (!REAL_BACKEND) {
    // Playwright отдаёт приоритет последнему зарегистрированному маршруту —
    // точечный мок /v1/chat/stream перекрывает общий /v1/ из fixtures.
    await page.route(/^https?:\/\/[^/]+\/v1\/chat\/stream$/, (route) =>
      route.fulfill({
        status: 200,
        headers: {
          "content-type": "text/event-stream; charset=utf-8",
          "cache-control": "no-cache",
        },
        body: sseBody([
          ...CHAT_DELTAS.map((text) => ({ event: "delta", data: { text } })),
          {
            event: "done",
            data: { tokens_used: 42, model: "mock-llm", validated: true },
          },
        ]),
      }),
    );
  }
});

test("чат получает выбранный полуход как контекст", async ({ page }) => {
  // Real-backend-режим требует LLM и поддержки selected_ply на backend;
  // сценарий T-072 проверяется в mocked-режиме.
  test.skip(REAL_BACKEND, "chat-context: только mocked-режим");

  await registerAndLogin(page);

  // Короткая партия: mock POST /v1/analyze вернёт те же 4 полухода.
  await page.locator(".pgn-input").fill("1. e4 e5 2. Nf3 Nc6");
  await page.getByRole("button", { name: "Разобрать партию" }).click();
  await expect(page).toHaveURL(/\/analysis\/[^/]+$/, { timeout: 15_000 });
  await expect(page.locator(".moves__grid .move").first()).toBeVisible();

  // Полуход 2 (e5): после анализа выбран последний полуход (4).
  const moveE5 = page.locator('.moves__grid .move[title^="e5:"]');
  await moveE5.click();
  await expect(moveE5).toHaveClass(/move--active/);

  // Контекст под заголовком чата: ply 2 — первый ход чёрных, movePrefix(2) = "1...".
  const context = page.getByTestId("chat-context");
  await expect(context).toBeVisible();
  await expect(context).toHaveText("Контекст: 1... e5");

  // Вопрос уходит в /v1/chat/stream с выбранным полуходом.
  const chatRequestPromise = page.waitForRequest(
    (request) =>
      request.method() === "POST" &&
      new URL(request.url()).pathname === "/v1/chat/stream",
  );
  await page.getByTestId("chat-message-input").fill("Почему это плохо?");
  await page.getByRole("button", { name: "Спросить" }).click();
  const chatRequest = await chatRequestPromise;
  expect(chatRequest.postDataJSON()).toMatchObject({
    analysis_id: 42,
    message: "Почему это плохо?",
    selected_ply: 2,
  });

  // Ответ из SSE-мока отрисовался в чате.
  await expect(
    page.locator(".chat__message--assistant").filter({ hasText: CHAT_ANSWER }),
  ).toBeVisible({ timeout: 15_000 });

  // Стартовая позиция (ply 0): контекст скрыт, поля selected_ply в теле нет.
  await page.locator(".moves__grid .move.move--start").click();
  await expect(context).toHaveCount(0);

  const startRequestPromise = page.waitForRequest(
    (request) =>
      request.method() === "POST" &&
      new URL(request.url()).pathname === "/v1/chat/stream",
  );
  await page.getByTestId("chat-message-input").fill("А что было в начале?");
  await page.getByRole("button", { name: "Спросить" }).click();
  const startRequest = await startRequestPromise;
  expect(startRequest.postDataJSON()).not.toHaveProperty("selected_ply");
});
