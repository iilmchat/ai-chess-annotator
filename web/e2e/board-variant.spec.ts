import type { Page } from "@playwright/test";

import { expect, mockApi, REAL_BACKEND, registerAndLogin, test } from "./fixtures";

/**
 * T-070: интерактивная доска разбора.
 *
 * Сценарий — mocked-режим, мок-разбор 1.e4 e5 2.Nf3 Nc6 (последний полуход
 * основной линии, ход белых). Перетаскивание фигур делается настоящими
 * pointer-событиями мыши (`page.mouse`): react-chessboard 5.12.1 работает на
 * dnd-kit, поэтому `dragTo()` локаторов (HTML5 drag&drop) здесь не подходит.
 *
 * Клетки находятся по `data-square` — react-chessboard v5 рендерит у каждой
 * клетки `data-square`/`data-column`/`data-row`, а у фигуры — `data-piece`
 * (например, `wN`). Центр клетки считается из её boundingBox, чтобы тест не
 * зависел от размера доски и mobile-вьюпорта.
 */

test.beforeEach(async ({ page }) => {
  await mockApi(page);
});

/** Центр клетки доски в координатах вьюпорта. */
async function squareCenter(
  page: Page,
  square: string,
): Promise<{ x: number; y: number }> {
  const cell = page.locator(`[data-square="${square}"]`);
  await cell.scrollIntoViewIfNeeded();
  const box = await cell.boundingBox();
  if (!box) {
    throw new Error(`клетка ${square} не найдена на доске`);
  }
  return { x: box.x + box.width / 2, y: box.y + box.height / 2 };
}

/** Перетащить фигуру с клетки `from` на клетку `to` реальными событиями мыши. */
async function dragSquare(
  page: Page,
  from: string,
  to: string,
): Promise<void> {
  const start = await squareCenter(page, from);
  const end = await squareCenter(page, to);

  await page.mouse.move(start.x, start.y);
  await page.mouse.down();
  // dnd-kit-сенсор активируется после минимального сдвига указателя.
  await page.mouse.move(start.x, start.y + 2, { steps: 2 });
  // Плавное движение к центру целевой клетки: collision detection react-
  // chessboard выбирает droppable по координатам указателя (pointerWithin).
  await page.mouse.move(end.x, end.y, { steps: 15 });
  await page.mouse.up();
}

test("вариант на доске: перетаскивание фигуры, сброс и навигация", async ({
  page,
}) => {
  // В real-backend-режиме анализ Stockfish идёт дольше basic-таймаута.
  test.setTimeout(REAL_BACKEND ? 180_000 : 90_000);

  await registerAndLogin(page);

  // Фиксируем короткую партию вместо DEMO_PGN: тогда доска после анализа
  // заведомо стоит после 2.Nc6 (4 полухода, ход белых), а строка варианта
  // начинается с 3-го хода. Этот же PGN совпадает с мок-разбором fixtures.
  await page.locator(".pgn-input").fill("1. e4 e5 2. Nf3 Nc6");
  await page
    .getByRole("button", { name: "Разобрать партию" })
    .click();
  await expect(page).toHaveURL(/\/analysis\/[^/]+$/, {
    timeout: REAL_BACKEND ? 150_000 : 15_000,
  });
  await expect(page.locator(".board-wrap")).toBeVisible();

  // Основная линия выбрана на последний полуход: ход белых, следующий номер
  // хода — 3-й. Пока варианта нет — видна подсказка, строки варианта нет.
  await expect(page.getByTestId("variant-hint")).toBeVisible();
  await expect(page.getByTestId("variant-bar")).toHaveCount(0);

  // Доска интерактивна: конь белых стоит на f3.
  await expect(page.locator('[data-square="f3"] [data-piece="wN"]')).toHaveCount(1);

  // --- Нелегальный ход не создаёт вариант ---------------------------------
  // Ладья a1 заперта пешкой a2: ход a1-a5 нелегален, onPieceDrop вернёт false,
  // поэтому вариант не появляется, а фигура остаётся на a1.
  await dragSquare(page, "a1", "a5");
  await expect(page.getByTestId("variant-bar")).toHaveCount(0);
  await expect(page.locator('[data-square="a1"] [data-piece="wR"]')).toHaveCount(1);

  // --- Легальный ход: перетаскивание коня f3 -> g5 (3. Ng5) --------------
  await dragSquare(page, "f3", "g5");

  const bar = page.getByTestId("variant-bar");
  await expect(bar).toBeVisible();
  await expect(page.getByTestId("variant-line")).toHaveText("3. Ng5");
  await expect(page.getByTestId("variant-hint")).toHaveCount(0);
  // Позиция на доске действительно изменилась: конь теперь на g5.
  await expect(page.locator('[data-square="g5"] [data-piece="wN"]')).toHaveCount(1);
  await expect(page.locator('[data-square="f3"] [data-piece]')).toHaveCount(0);

  // --- Второй ход варианта (уже за чёрных): Nc6 -> d4 --------------------
  await dragSquare(page, "c6", "d4");
  await expect(page.getByTestId("variant-line")).toHaveText("3. Ng5 Nd4");
  await expect(page.locator('[data-square="d4"] [data-piece="bN"]')).toHaveCount(1);

  // --- «Сбросить на партию»: вариант исчезает, позиция восстанавливается --
  await page.getByTestId("variant-reset").click();
  await expect(bar).toHaveCount(0);
  await expect(page.getByTestId("variant-hint")).toBeVisible();
  await expect(page.locator('[data-square="f3"] [data-piece="wN"]')).toHaveCount(1);
  await expect(page.locator('[data-square="g5"] [data-piece]')).toHaveCount(0);

  // --- Навигация по основной линии тоже сбрасывает вариант ----------------
  await dragSquare(page, "f3", "g5");
  await expect(bar).toBeVisible();
  await page.getByTestId("nav-prev").click();
  await expect(bar).toHaveCount(0);
  await expect(page.getByTestId("variant-hint")).toBeVisible();
  // Полуход основной линии переключился на предпоследний (мок: 4 полухода).
  await expect(page.getByTestId("eval-graph")).toHaveAttribute(
    "data-selected-ply",
    "3",
  );
  // Доска показывает основную линию: конь снова на f3.
  await expect(page.locator('[data-square="f3"] [data-piece="wN"]')).toHaveCount(1);
  await expect(page.locator('[data-square="g5"] [data-piece]')).toHaveCount(0);
});
