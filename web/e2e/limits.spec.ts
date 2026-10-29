import { expect, mockApi, REAL_BACKEND, registerAndLogin, test } from "./fixtures";

test.beforeEach(async ({ page }) => {
  await mockApi(page);
});

test("лимиты: usage-badge Free в шапке и модалка дневного лимита", async ({
  page,
}) => {
  await registerAndLogin(page);

  // UsageBadge.compact(0) => "0", compact(50000) => "50k" → "Free: 0/50k".
  // В real-backend-режиме лимит берётся из конфига backend, поэтому
  // проверяем только тир, а точные цифры — в mocked-режиме.
  const badge = page.getByRole("button", {
    name: REAL_BACKEND ? /Free:/ : /Free: 0k?\/50k/,
  });
  await expect(badge).toBeVisible({ timeout: 15_000 });

  // Клик открывает модалку дневного лимита токенов.
  await badge.click();
  const dialog = page.getByRole("dialog");
  await expect(dialog).toBeVisible();
  await expect(
    dialog.getByRole("heading", { name: "Дневной лимит токенов" }),
  ).toBeVisible();

  if (!REAL_BACKEND) {
    await expect(
      dialog.getByText(/Использовано 0 из 50k токенов за сегодня \(UTC\)\./),
    ).toBeVisible();
  }

  await dialog.getByRole("button", { name: "Закрыть" }).click();
  await expect(dialog).toHaveCount(0);
});
