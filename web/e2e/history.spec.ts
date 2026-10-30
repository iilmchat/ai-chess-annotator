import {
  expect,
  mockApi,
  REAL_BACKEND,
  registerAndLogin,
  test,
  uniqueEmail,
} from "./fixtures";

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

test("история: неподтверждённый пользователь видит placeholder без списка", async ({
  page,
}) => {
  // Сценарий завязан на mock-соглашение fixtures.ts: email с префиксом
  // `unverified-` считается неподтверждённым.
  test.skip(
    REAL_BACKEND,
    "в real-backend режиме подтверждение email зависит от настроек backend",
  );

  const gamesRequests: string[] = [];
  page.on("request", (request) => {
    if (new URL(request.url()).pathname === "/v1/games") {
      gamesRequests.push(request.url());
    }
  });

  const email = uniqueEmail("unverified");
  await registerAndLogin(page, email);

  // Открываем /history «с нуля»: unverified-профиль восстанавливается из мока
  // refresh-cookie, поэтому страница рендерится без общего экрана AuthGate.
  await page.goto("/history");
  await expect(page).toHaveURL(/\/history$/);

  const placeholder = page.getByRole("heading", {
    name: "Подтвердите email, чтобы увидеть историю",
  });
  await expect(placeholder).toBeVisible();
  await expect(
    page.getByText(`Ссылка для подтверждения отправлена на ${email}`),
  ).toBeVisible();
  await expect(page.getByText(/нажмите «Проверить»/)).toBeVisible();
  // Ни таблицы, ни строк партий, ни заголовка списка — ничего не намекает на данные.
  await expect(page.getByRole("table")).toHaveCount(0);
  await expect(page.getByRole("link", { name: /Svidler/ })).toHaveCount(0);
  await expect(
    page.getByRole("heading", { name: "История партий" }),
  ).toHaveCount(0);
  expect(gamesRequests).toHaveLength(0);

  // «Проверить»: backend по-прежнему не подтвердил email — placeholder остаётся,
  // история не запрашивается.
  await page.getByRole("button", { name: "Проверить" }).click();
  await expect(
    page.getByText(/Email пока не подтверждён/),
  ).toBeVisible();
  await expect(placeholder).toBeVisible();
  await expect(page.getByRole("table")).toHaveCount(0);
  expect(gamesRequests).toHaveLength(0);

  // А если backend уже подтвердил email, «Проверить» подхватывает это и
  // загружает историю без перезагрузки страницы (переопределяем только
  // `/auth/me` в рамках теста, fixtures.ts не меняем).
  await page.route(/\/auth\/me$/, (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json; charset=utf-8",
      body: JSON.stringify({
        id: 1,
        email,
        level: "club",
        role: "user",
        tier: "free",
        is_active: true,
        is_verified: true,
        created_at: "2026-10-28T10:00:00Z",
      }),
    }),
  );
  await page.getByRole("button", { name: "Проверить" }).click();
  await expect(
    page.getByRole("heading", { name: "История партий" }),
  ).toBeVisible();
  await expect(page.getByRole("link", { name: /Svidler/ })).toBeVisible();
  await expect(placeholder).toHaveCount(0);
  expect(gamesRequests.length).toBeGreaterThan(0);
});
