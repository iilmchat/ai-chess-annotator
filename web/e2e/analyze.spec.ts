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

  // --- Iteration 36: шкала оценки, график, навигация, скрытие оценок ------

  // Шкала оценки рядом с доской: role="meter" и доля белых в data-white-share.
  const evalBar = page.getByTestId("eval-bar");
  await expect(evalBar).toBeVisible();
  await expect(evalBar).toHaveAttribute("role", "meter");
  await expect(evalBar).toHaveAttribute("aria-valuenow", /^\d{1,3}$/);
  await expect(evalBar).toHaveAttribute("data-white-share", /^(0|1)\.\d{2}$/);

  // График оценки виден и отмечает выбранный полуход (после анализа — последний).
  const graph = page.getByTestId("eval-graph");
  await expect(graph).toBeVisible();
  await expect(graph.locator(".recharts-surface")).toBeVisible();
  const totalPlies = Number(await graph.getAttribute("data-selected-ply"));
  expect(totalPlies).toBeGreaterThan(0);

  // На последнем полуходе «вперёд»/«в конец» недоступны.
  const navStart = page.getByTestId("nav-start");
  const navPrev = page.getByTestId("nav-prev");
  const navNext = page.getByTestId("nav-next");
  const navEnd = page.getByTestId("nav-end");
  await expect(navEnd).toBeDisabled();
  await expect(navNext).toBeDisabled();
  await expect(navStart).toBeEnabled();
  await expect(navPrev).toBeEnabled();

  // В начало: выбор 0, на границе «в начало»/«назад» недоступны, шкала 50/50.
  await navStart.click();
  await expect(graph).toHaveAttribute("data-selected-ply", "0");
  await expect(evalBar).toHaveAttribute("data-white-share", "0.50");
  await expect(navStart).toBeDisabled();
  await expect(navPrev).toBeDisabled();
  await expect(navNext).toBeEnabled();

  // «Вперёд» / «назад» двигают выбор на один полуход.
  await navNext.click();
  await expect(graph).toHaveAttribute("data-selected-ply", "1");
  await navPrev.click();
  await expect(graph).toHaveAttribute("data-selected-ply", "0");

  // «В конец» возвращает на последний полуход, «вперёд»/«в конец» снова off.
  await navEnd.click();
  await expect(graph).toHaveAttribute("data-selected-ply", String(totalPlies));
  await expect(navEnd).toBeDisabled();
  await expect(navNext).toBeDisabled();

  // Клик по графику переключает доску на ближайший полуход.
  await navStart.click();
  await expect(graph).toHaveAttribute("data-selected-ply", "0");
  const surface = graph.locator(".recharts-surface");
  const surfaceBox = await surface.boundingBox();
  expect(surfaceBox).not.toBeNull();
  if (surfaceBox) {
    await surface.click({
      position: {
        x: Math.round(surfaceBox.width * 0.7),
        y: Math.round(surfaceBox.height * 0.5),
      },
    });
  }
  await expect(graph).not.toHaveAttribute("data-selected-ply", "0");

  // Переключатель «Показывать оценки» скрывает и возвращает числовые оценки
  // в списке ходов (классификация/цвет хода при этом остаются).
  const movesGrid = page.locator(".moves__grid");
  const moveButtons = movesGrid.locator(".move:not(.move--start)");
  const moveEvals = movesGrid.locator(".move__eval");
  const movesCount = await moveButtons.count();
  expect(movesCount).toBeGreaterThan(0);
  await expect(moveEvals).toHaveCount(movesCount);

  const toggle = page.getByTestId("toggle-evals");
  await expect(toggle).toBeVisible();
  await expect(toggle).toBeChecked();
  await expect(moveButtons.first()).toHaveAttribute("title", /cp$/);

  await toggle.uncheck();
  await expect(moveEvals).toHaveCount(0);
  await expect(moveButtons.first()).not.toHaveAttribute("title", /cp/);

  await toggle.check();
  await expect(moveEvals).toHaveCount(movesCount);
  await expect(moveButtons.first()).toHaveAttribute("title", /cp$/);
});
