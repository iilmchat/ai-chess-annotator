import { expect, mockApi, registerAndLogin, test } from "./fixtures";

/**
 * Billing (T-083/T-084): кнопка «Оформить подписку» из модалки лимита и
 * профиля, редирект на Stripe Checkout, страницы success/cancel и
 * «Управлять подпиской» (Customer Portal).
 *
 * Stripe в mocked-режиме заменён на внутренние URL: проверяется именно
 * редирект браузера и контракт запросов, без внешней сети.
 */

async function openProfileFromMenu(
  page: Parameters<typeof registerAndLogin>[0],
  email: string,
): Promise<void> {
  await page.getByRole("button", { name: `Вы вошли как ${email}` }).click();
  await page.getByTestId("profile-menu-link").click();
  await expect(page).toHaveURL(/\/profile$/);
}

test("billing: Free → «Оформить подписку» редиректит на Stripe Checkout", async ({
  page,
}) => {
  await mockApi(page);
  await registerAndLogin(page);

  const badge = page.getByRole("button", { name: /Free:/ });
  await expect(badge).toBeVisible({ timeout: 15_000 });
  await badge.click();

  const dialog = page.getByRole("dialog");
  await expect(dialog).toBeVisible();

  const checkoutRequest = page.waitForRequest(
    (request) =>
      request.method() === "POST" &&
      new URL(request.url()).pathname ===
        "/v1/billing/create-checkout-session",
  );
  await dialog.getByTestId("upgrade-button").click();
  await checkoutRequest;

  await expect(page).toHaveURL(/\/billing\/success$/);
  await expect(
    page.getByRole("heading", { name: "Подписка оформлена", level: 1 }),
  ).toBeVisible();
});

test("billing: профиль Free показывает тариф и ведёт в Checkout", async ({
  page,
}) => {
  await mockApi(page);
  const email = await registerAndLogin(page);
  await openProfileFromMenu(page, email);

  await expect(page.getByTestId("subscription-tier")).toHaveText(
    "Текущий тариф: Free",
  );
  const upgrade = page.getByTestId("profile-upgrade");
  await expect(upgrade).toBeVisible();
  await expect(page.getByTestId("manage-subscription")).toHaveCount(0);

  const checkoutRequest = page.waitForRequest(
    (request) =>
      request.method() === "POST" &&
      new URL(request.url()).pathname ===
        "/v1/billing/create-checkout-session",
  );
  await upgrade.click();
  await checkoutRequest;
  await expect(page).toHaveURL(/\/billing\/success$/);
});

test("billing: Pro показывает тариф и «Управлять подпиской» (Portal)", async ({
  page,
}) => {
  await mockApi(page, { tier: "pro" });
  const email = await registerAndLogin(page);
  await openProfileFromMenu(page, email);

  await expect(page.getByTestId("subscription-tier")).toHaveText(
    "Текущий тариф: Pro",
  );
  await expect(page.getByTestId("profile-upgrade")).toHaveCount(0);

  const portalRequest = page.waitForRequest(
    (request) =>
      request.method() === "POST" &&
      new URL(request.url()).pathname ===
        "/v1/billing/create-portal-session",
  );
  await page.getByTestId("manage-subscription").click();
  await portalRequest;

  await expect(page).toHaveURL(/\/profile\?billing=portal$/);
});

test("billing: страница отмены объясняет, что тариф не изменился", async ({
  page,
}) => {
  await mockApi(page);
  await page.goto("/billing/cancel");

  await expect(
    page.getByRole("heading", { name: "Оплата отменена", level: 1 }),
  ).toBeVisible();
  await expect(page.getByText(/Тариф остался прежним/)).toBeVisible();
});
