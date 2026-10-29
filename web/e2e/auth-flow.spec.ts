import {
  expect,
  mockApi,
  registerAndLogin,
  test,
  uniqueEmail,
} from "./fixtures";

test.beforeEach(async ({ page }) => {
  await mockApi(page);
});

test("verify-email: успешное подтверждение и переход на главную", async ({
  page,
}) => {
  await page.goto("/verify-email?token=valid-token");

  await expect(
    page.getByText("Email успешно подтвержден!"),
  ).toBeVisible();
  await expect(
    page.getByRole("link", { name: "На главную" }),
  ).toBeVisible();
  await expect(
    page.getByRole("status").getByRole("link", { name: "Войти" }),
  ).toBeVisible();
});

test("verify-email: недействительный токен показывает понятную ошибку", async ({
  page,
}) => {
  await page.goto("/verify-email?token=invalid");

  await expect(page.getByText("Не удалось подтвердить email")).toBeVisible();
  await expect(
    page.getByText(/Токен недействителен или истёк/),
  ).toBeVisible();
});

test("verify-email: ссылка без токена не делает запрос", async ({ page }) => {
  await page.goto("/verify-email");

  await expect(page.getByText("Не удалось подтвердить email")).toBeVisible();
  await expect(page.getByText(/В ссылке нет токена/)).toBeVisible();
});

test("неподтверждённый пользователь видит экран «Подтвердите email»", async ({
  page,
}) => {
  const email = uniqueEmail("unverified");
  await registerAndLogin(page, email);

  await expect(
    page.getByRole("heading", { name: "Подтвердите email" }),
  ).toBeVisible();
  await expect(page.getByText(`Мы отправили ссылку на ${email}`)).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Я подтвердил(а) — проверить снова" }),
  ).toBeVisible();
});

test("forgot-password: нейтральное сообщение после отправки", async ({
  page,
}) => {
  await page.goto("/login");
  await page.getByRole("link", { name: "Забыли пароль?" }).click();
  await expect(page).toHaveURL(/\/forgot-password$/);

  await page.getByLabel("Email").fill("user@example.com");
  await page.getByRole("button", { name: "Отправить ссылку" }).click();

  await expect(page.getByText("Письмо отправлено")).toBeVisible();
  await expect(
    page.getByText(/Если такой email существует, мы отправили ссылку/),
  ).toBeVisible();
});

test("reset-password: успешная смена ведёт на /login с сообщением", async ({
  page,
}) => {
  const email = uniqueEmail("reset");
  // Сброс может открыть уже авторизованный пользователь: локальная сессия
  // должна очиститься вместе с refresh-сессией на backend.
  await registerAndLogin(page, email);
  await page.goto("/reset-password?token=valid-token");

  await page.getByLabel("Новый пароль").fill("NewPassword456!");
  await page.getByLabel("Повторите пароль").fill("NewPassword456!");
  await page.getByRole("button", { name: "Сохранить пароль" }).click();

  await expect(page).toHaveURL(/\/login$/);
  await expect(
    page.getByText("Пароль обновлён. Войдите с новым паролем."),
  ).toBeVisible();
  await expect(page.getByText(`Вы вошли как ${email}`)).toHaveCount(0);
});

test("reset-password: несовпадение паролей не отправляет форму", async ({
  page,
}) => {
  await page.goto("/reset-password?token=valid-token");

  await page.getByLabel("Новый пароль").fill("NewPassword456!");
  await page.getByLabel("Повторите пароль").fill("DifferentPassword789!");
  await page.getByRole("button", { name: "Сохранить пароль" }).click();

  await expect(page.getByText("Пароли не совпадают.")).toBeVisible();
  await expect(page).toHaveURL(/\/reset-password/);
});

test("reset-password: использованный токен показывает ошибку", async ({
  page,
}) => {
  await page.goto("/reset-password?token=invalid");

  await page.getByLabel("Новый пароль").fill("NewPassword456!");
  await page.getByLabel("Повторите пароль").fill("NewPassword456!");
  await page.getByRole("button", { name: "Сохранить пароль" }).click();

  await expect(
    page.getByText(/Ссылка недействительна или уже использована/),
  ).toBeVisible();
});

test("logout-all: пункт меню вызывает API и очищает сессию", async ({
  page,
}) => {
  const email = uniqueEmail("auth-all");
  await registerAndLogin(page, email);

  await page
    .getByRole("button", { name: `Вы вошли как ${email}` })
    .click();
  const requestPromise = page.waitForRequest(
    (request) =>
      request.url().endsWith("/auth/logout-all") &&
      request.method() === "POST",
  );
  await page
    .getByRole("menuitem", { name: "Выйти со всех устройств" })
    .click();
  await requestPromise;

  await expect(page).toHaveURL(/\/login$/);
  await expect(
    page.getByRole("heading", { name: "Вход в ChessAnnotator" }),
  ).toBeVisible();
  await expect(page.getByText(`Вы вошли как ${email}`)).toHaveCount(0);
});
