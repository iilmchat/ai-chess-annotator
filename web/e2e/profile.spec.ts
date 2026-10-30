import type { Page } from "@playwright/test";

import {
  expect,
  mockApi,
  PASSWORD,
  registerAndLogin,
  test,
} from "./fixtures";
import type { AuthUser } from "../src/types";

/**
 * Профиль (T-082): пункт меню → /profile, сохранение имени/уровня через
 * `PATCH /auth/me`, смена пароля через `POST /auth/change-password`.
 *
 * Как и остальные спеки: до логина работает `mockApi`, а конкретные
 * профильные эндпоинты переопределяем `page.route` уже в тесте —
 * последний зарегистрированный маршрут выигрывает (fixtures.mockApi не знает
 * про PATCH /auth/me и POST /auth/change-password).
 */

const NEW_PASSWORD = "NewPassword456!";
const WRONG_PASSWORD = "WrongPassword123";

function authUser(email: string, overrides: Partial<AuthUser> = {}): AuthUser {
  return {
    id: 1,
    email,
    level: "club",
    role: "user",
    tier: "free",
    is_active: true,
    is_verified: true,
    created_at: "2026-10-28T10:00:00Z",
    ...overrides,
  };
}

/** Тело PATCH /auth/me: возвращает «сервер», эхом применяющий изменения. */
async function mockProfilePatch(
  page: Page,
  email: string,
): Promise<Array<Record<string, unknown>>> {
  const bodies: Array<Record<string, unknown>> = [];
  await page.route(/\/auth\/me$/, async (route) => {
    const request = route.request();
    if (request.method() !== "PATCH") {
      const body = JSON.stringify(authUser(email));
      await route.fulfill({
        status: 200,
        contentType: "application/json; charset=utf-8",
        body,
      });
      return;
    }
    const patch = request.postDataJSON() as Record<string, unknown>;
    bodies.push(patch);
    const updated = authUser(email, {
      level: (patch.level as AuthUser["level"] | undefined) ?? "club",
      display_name:
        (patch.display_name as string | null | undefined) ?? null,
    });
    await route.fulfill({
      status: 200,
      contentType: "application/json; charset=utf-8",
      body: JSON.stringify(updated),
    });
  });
  return bodies;
}

/** Открыть /profile через выпадающее меню профиля в шапке. */
async function openProfile(page: Page, email: string): Promise<void> {
  await page.getByRole("button", { name: `Вы вошли как ${email}` }).click();
  await page.getByTestId("profile-menu-link").click();
  await expect(page).toHaveURL(/\/profile$/);
  await expect(
    page.getByRole("heading", { name: "Профиль", level: 1 }),
  ).toBeVisible();
}

test.beforeEach(async ({ page }) => {
  await mockApi(page);
});

test("профиль: пункт меню открывает страницу с формами", async ({ page }) => {
  const email = await registerAndLogin(page);

  await openProfile(page, email);

  await expect(page.getByText("Имя, предпочитаемый уровень и пароль")).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Смена пароля" }),
  ).toBeVisible();
  await expect(page.getByLabel("Имя", { exact: true })).toBeVisible();
  await expect(page.getByLabel("Предпочитаемый уровень")).toBeVisible();
});

test("профиль: смена уровня шлёт PATCH без display_name и показывает успех", async ({
  page,
}) => {
  const email = await registerAndLogin(page);
  const patchBodies = await mockProfilePatch(page, email);
  await openProfile(page, email);

  const requestPromise = page.waitForRequest(
    (request) =>
      request.method() === "PATCH" &&
      new URL(request.url()).pathname === "/auth/me",
  );
  await page.getByLabel("Предпочитаемый уровень").selectOption("gm");
  await page.getByRole("button", { name: "Сохранить" }).click();
  const request = await requestPromise;

  // Ждём ответ мока: он гарантирует, что обработчик page.route уже отработал.
  await expect(page.getByTestId("profile-saved")).toHaveText(
    "Профиль сохранён.",
  );

  // Имя не трогали — поле display_name в теле отсутствует.
  expect(request.postDataJSON()).toEqual({ level: "gm" });
  await expect.poll(() => patchBodies.length).toBe(1);
  expect(patchBodies[0]).not.toHaveProperty("display_name");

  // authStore обновился ответом сервера — выбранный уровень сохраняется.
  await expect(page.getByLabel("Предпочитаемый уровень")).toHaveValue("gm");
});

test("профиль: новое имя уходит в display_name и сохраняется", async ({
  page,
}) => {
  const email = await registerAndLogin(page);
  const patchBodies = await mockProfilePatch(page, email);
  await openProfile(page, email);

  const requestPromise = page.waitForRequest(
    (request) =>
      request.method() === "PATCH" &&
      new URL(request.url()).pathname === "/auth/me",
  );
  await page.getByLabel("Имя", { exact: true }).fill("Магнус Карлсен");
  await page.getByRole("button", { name: "Сохранить" }).click();
  const request = await requestPromise;

  await expect(page.getByTestId("profile-saved")).toBeVisible();
  expect(request.postDataJSON()).toMatchObject({
    display_name: "Магнус Карлсен",
  });
  await expect.poll(() => patchBodies.length).toBe(1);
  // После сохранения форма показывает имя из ответа сервера.
  await expect(page.getByLabel("Имя", { exact: true })).toHaveValue(
    "Магнус Карлсен",
  );
});

test("профиль: несовпадение и короткий пароль не отправляют запрос", async ({
  page,
}) => {
  const email = await registerAndLogin(page);
  let passwordRequests = 0;
  await page.route(/\/auth\/change-password$/, async (route) => {
    passwordRequests += 1;
    await route.fulfill({
      status: 200,
      contentType: "application/json; charset=utf-8",
      body: "{}",
    });
  });
  await openProfile(page, email);

  await page.getByLabel("Текущий пароль", { exact: true }).fill(PASSWORD);
  await page.getByLabel("Новый пароль", { exact: true }).fill(NEW_PASSWORD);
  await page
    .getByLabel("Повторите новый пароль", { exact: true })
    .fill("DifferentPassword789!");
  await page.getByRole("button", { name: "Сменить пароль" }).click();

  await expect(page.getByTestId("password-error")).toHaveText(
    "Пароли не совпадают.",
  );
  expect(passwordRequests).toBe(0);

  // Слишком короткий новый пароль — тот же inline-блок и никакого запроса.
  await page.getByLabel("Новый пароль", { exact: true }).fill("short");
  await page
    .getByLabel("Повторите новый пароль", { exact: true })
    .fill("short");
  await page.getByRole("button", { name: "Сменить пароль" }).click();

  await expect(page.getByTestId("password-error")).toHaveText(
    "Пароль должен быть не короче 8 символов.",
  );
  expect(passwordRequests).toBe(0);
});

test("профиль: успешная смена пароля шлёт текущий и новый пароль", async ({
  page,
}) => {
  const email = await registerAndLogin(page);
  const requestBodies: Array<Record<string, unknown>> = [];
  await page.route(/\/auth\/change-password$/, async (route) => {
    requestBodies.push(route.request().postDataJSON() as Record<string, unknown>);
    await route.fulfill({
      status: 200,
      contentType: "application/json; charset=utf-8",
      body: JSON.stringify({ detail: "Пароль изменён" }),
    });
  });
  await openProfile(page, email);

  await page.getByLabel("Текущий пароль", { exact: true }).fill(PASSWORD);
  await page.getByLabel("Новый пароль", { exact: true }).fill(NEW_PASSWORD);
  await page
    .getByLabel("Повторите новый пароль", { exact: true })
    .fill(NEW_PASSWORD);

  const responsePromise = page.waitForResponse(
    (response) =>
      response.request().method() === "POST" &&
      new URL(response.url()).pathname === "/auth/change-password",
  );
  await page.getByRole("button", { name: "Сменить пароль" }).click();
  const response = await responsePromise;

  expect(response.status()).toBe(200);
  expect(requestBodies).toHaveLength(1);
  expect(requestBodies[0]).toEqual({
    current_password: PASSWORD,
    new_password: NEW_PASSWORD,
  });
  await expect(page.getByTestId("password-changed")).toHaveText(
    "Пароль изменён.",
  );
  await expect(page.getByLabel("Текущий пароль", { exact: true })).toHaveValue(
    "",
  );
});

test("профиль: неверный текущий пароль показывает понятную ошибку", async ({
  page,
}) => {
  const email = await registerAndLogin(page);
  let passwordRequests = 0;
  await page.route(/\/auth\/change-password$/, async (route) => {
    passwordRequests += 1;
    await route.fulfill({
      status: 401,
      contentType: "application/json; charset=utf-8",
      body: JSON.stringify({
        detail: "Неверный текущий пароль",
        code: "invalid_credentials",
      }),
    });
  });
  await openProfile(page, email);

  await page.getByLabel("Текущий пароль", { exact: true }).fill(WRONG_PASSWORD);
  await page.getByLabel("Новый пароль", { exact: true }).fill(NEW_PASSWORD);
  await page
    .getByLabel("Повторите новый пароль", { exact: true })
    .fill(NEW_PASSWORD);
  await page.getByRole("button", { name: "Сменить пароль" }).click();

  await expect(page.getByTestId("password-error")).toHaveText(
    "Текущий пароль неверен.",
  );
  // 401 здесь — неверный пароль, а не истёкший access-токен: refresh и
  // повторная отправка пароля не выполняются.
  expect(passwordRequests).toBe(1);
});
