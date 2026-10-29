import {
  expect,
  MOCK_EDITED_TEXT,
  mockApi,
  REAL_BACKEND,
  registerAndLogin,
  test,
} from "./fixtures";

test.beforeEach(async ({ page }) => {
  await mockApi(page);
});

test("комментарий: SSE-генерация → правка → PATCH /v1/commentaries/:id", async ({
  page,
}) => {
  // В real-backend-режиме генерация через LLM (LM Studio) занимает ~50–60 с.
  test.setTimeout(REAL_BACKEND ? 240_000 : 60_000);

  await registerAndLogin(page);

  // Анализ партии — комментарий строится по его analysis_id.
  await page.getByRole("button", { name: "Разобрать партию" }).click();
  await expect(page).toHaveURL(/\/analysis\/[^/]+$/, {
    timeout: REAL_BACKEND ? 150_000 : 15_000,
  });
  await expect(page.locator(".board-wrap")).toBeVisible();

  // Генерация комментария через POST /v1/commentary/stream (SSE).
  const generate = page.getByRole("button", {
    name: "Сгенерировать",
    exact: true,
  });
  await expect(generate).toBeVisible();
  await generate.click();

  // После события done панель переключается на «Сгенерировать заново».
  await expect(
    page.getByRole("button", { name: "Сгенерировать заново", exact: true }),
  ).toBeVisible({ timeout: REAL_BACKEND ? 180_000 : 15_000 });
  await expect(page.locator(".commentary-text")).toBeVisible({
    timeout: REAL_BACKEND ? 180_000 : 15_000,
  });
  await expect(page.locator(".commentary-text")).not.toBeEmpty();

  // Редактируем текст.
  await page.getByRole("button", { name: "Редактировать" }).click();
  const editor = page.getByRole("textbox", { name: "Текст комментария" });
  await expect(editor).toBeVisible();
  await editor.fill(MOCK_EDITED_TEXT);

  // PATCH должен уйти на /v1/commentaries/:id и завершиться успешно.
  const patchResponsePromise = page.waitForResponse((response) => {
    if (response.request().method() !== "PATCH") {
      return false;
    }
    return /^\/v1\/commentaries\/\d+$/.test(new URL(response.url()).pathname);
  });
  await page.getByRole("button", { name: "Сохранить правку" }).click();
  const patchResponse = await patchResponsePromise;
  expect(patchResponse.status()).toBe(200);

  // Правка отражена в панели и показан маркер успеха «edit #<id>».
  await expect(page.locator(".commentary-text")).toContainText("после правки E2E");
  await expect(page.getByText(/edit #\d+/)).toBeVisible();
  await expect(editor).toHaveCount(0);
});
