import { expect, mockApi, registerAndLogin, test, uniqueEmail } from "./fixtures";

test.beforeEach(async ({ page }) => {
  await mockApi(page);
});

test("регистрация и вход: email в шапке и кнопка «Выйти»", async ({ page }) => {
  const email = uniqueEmail("auth");

  // Приватный маршрут без сессии уводит на /login (AuthGate).
  await page.goto("/");
  await expect(page).toHaveURL(/\/login$/);

  await registerAndLogin(page, email);

  // В шапке — email пользователя (auth.loggedInAs) и кнопка выхода.
  await expect(page.getByText(`Вы вошли как ${email}`)).toBeVisible();
  const logout = page.getByRole("button", { name: "Выйти" });
  await expect(logout).toBeVisible();

  // Выход чистит сессию и возвращает на страницу входа.
  await logout.click();
  await expect(page).toHaveURL(/\/login$/);
  await expect(
    page.getByRole("heading", { name: "Вход в ChessAnnotator" }),
  ).toBeVisible();
  await expect(page.getByText(`Вы вошли как ${email}`)).toHaveCount(0);
});
