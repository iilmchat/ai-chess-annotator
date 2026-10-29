import { expect, mockApi, REAL_BACKEND, registerAndLogin, test } from "./fixtures";

test.beforeEach(async ({ page }) => {
  await mockApi(page);
});

test("разбор партии: доска и список ходов, URL /analysis/:id", async ({ page }) => {
  // В real-backend-режиме анализ у Stockfish может идти дольше basic-таймаута.
  test.setTimeout(REAL_BACKEND ? 180_000 : 60_000);

  await registerAndLogin(page);

  // PGN предзаполнен DEMO_PGN (analysisStore) — просто запускаем анализ.
  await expect(page.getByRole("button", { name: "Разобрать партию" })).toBeVisible();
  await page.getByRole("button", { name: "Разобрать партию" }).click();

  await expect(page).toHaveURL(/\/analysis\/[^/]+$/, {
    timeout: REAL_BACKEND ? 150_000 : 15_000,
  });

  // Доска (AnalysisWorkspace .board-wrap) и список ходов (MoveList).
  await expect(page.locator(".board-wrap")).toBeVisible();
  await expect(page.getByRole("region", { name: "Ходы" })).toBeVisible();
  await expect(page.locator(".moves__grid .move").first()).toBeVisible();

  // Появилась сводка разбора (секция summary).
  await expect(page.getByRole("heading", { name: "Сводка" })).toBeVisible();
});
