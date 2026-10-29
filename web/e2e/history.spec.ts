import { expect, mockApi, REAL_BACKEND, registerAndLogin, test } from "./fixtures";

test.beforeEach(async ({ page }) => {
  await mockApi(page);
});

test("история: переход из шапки, /history и открытие партии", async ({ page }) => {
  test.setTimeout(REAL_BACKEND ? 180_000 : 60_000);

  await registerAndLogin(page);

  if (REAL_BACKEND) {
    // У нового аккаунта в реальном backend нет партий — создаём анализ,
    // чтобы в истории появилась хотя бы одна запись.
    await page.getByRole("button", { name: "Разобрать партию" }).click();
    await expect(page).toHaveURL(/\/analysis\/[^/]+$/, { timeout: 150_000 });
  }

  // Клик «История» в шапке.
  await page.getByRole("link", { name: "История" }).click();
  await expect(page).toHaveURL(/\/history$/);
  await expect(
    page.getByRole("heading", { name: "История партий" }),
  ).toBeVisible();

  // Клик по строке партии (у tr role="link", accessible name — из ячеек).
  const row = page.getByRole("link", { name: /Svidler/ });
  await expect(row).toBeVisible({ timeout: 15_000 });
  await row.click();

  await expect(page).toHaveURL(/\/analysis\/[^/]+$/, {
    timeout: REAL_BACKEND ? 60_000 : 15_000,
  });
  await expect(page.locator(".board-wrap")).toBeVisible();
  await expect(page.getByRole("region", { name: "Ходы" })).toBeVisible();
});
