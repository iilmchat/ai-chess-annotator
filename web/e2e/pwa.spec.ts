import type { Page } from "@playwright/test";

import { expect, mockApi, REAL_BACKEND, registerAndLogin, test } from "./fixtures";

/**
 * T-081: PWA.
 *
 * Манифест и service worker появляются только в собранном `dist`, поэтому
 * спека осмысленна лишь в preview-режиме:
 *   E2E_PREVIEW=1 pnpm --dir web run test:e2e -- pwa
 *
 * В обычном dev-режиме (`pnpm dev`) плагин PWA выключен (devOptions.enabled:
 * false) — тест пропускается. Против реального backend PWA-проверки не
 * гоняем: они не зависят от API и экономят время.
 */
const PREVIEW = process.env.E2E_PREVIEW === "1";

type PwaIcon = {
  src: string;
  sizes?: string;
  type?: string;
  purpose?: string;
};

type PwaManifest = {
  name?: string;
  short_name?: string;
  description?: string;
  lang?: string;
  start_url?: string;
  scope?: string;
  display?: string;
  theme_color?: string;
  background_color?: string;
  icons?: PwaIcon[];
};

/** Число зарегистрированных SW; -1, если serviceWorker недоступен. */
async function swRegistrationCount(page: Page): Promise<number> {
  return page.evaluate(async () => {
    if (!("serviceWorker" in navigator)) return -1;
    const registrations = await navigator.serviceWorker.getRegistrations();
    return registrations.length;
  });
}

test.beforeEach(async ({ page }) => {
  await mockApi(page);
});

test("PWA: манифест, иконки, theme-color и регистрация service worker", async ({
  page,
}) => {
  test.skip(REAL_BACKEND, "PWA-проверки не выполняются против реального backend");
  test.skip(
    !PREVIEW,
    "Манифест и SW есть только в собранном dist: запускайте с E2E_PREVIEW=1",
  );
  test.setTimeout(60_000);

  // Заодно проверяем, что приложение поднимается в preview с моками API.
  await registerAndLogin(page);
  await page.goto("/");

  // 1. <link rel="manifest"> и резолв его href.
  const manifestLink = page.locator('link[rel="manifest"]');
  await expect(manifestLink).toHaveCount(1);
  const href = await manifestLink.getAttribute("href");
  expect(href, "у link[rel=manifest] должен быть непустой href").toBeTruthy();
  const manifestUrl = new URL(href!, page.url()).toString();

  // 2. Манифест отдаётся preview-сервером как JSON с корректными полями.
  const manifestResponse = await page.request.get(manifestUrl);
  expect(manifestResponse.status(), `GET ${manifestUrl}`).toBe(200);
  const manifest = (await manifestResponse.json()) as PwaManifest;

  expect(manifest.name).toBe("ChessAnnotator");
  expect(manifest.short_name).toBe("ChessAnnotator");
  expect(manifest.lang).toBe("ru");
  expect(manifest.start_url).toBe("/");
  expect(manifest.scope).toBe("/");
  expect(manifest.display).toBe("standalone");
  expect(manifest.theme_color).toBe("#0b1220");
  expect(manifest.background_color).toBe("#0b1220");

  // 3. Иконки: 192, 512 и maskable 512; каждая реально отдаётся сервером.
  const icons = manifest.icons ?? [];
  expect(icons.length).toBeGreaterThan(0);
  for (const size of ["192x192", "512x512"]) {
    expect(
      icons.some((icon) => icon.sizes === size),
      `в манифесте нет иконки ${size}`,
    ).toBe(true);
  }
  expect(
    icons.some((icon) => icon.sizes === "512x512" && icon.purpose === "maskable"),
    "в манифесте нет maskable-иконки 512x512",
  ).toBe(true);

  for (const icon of icons) {
    expect(icon.type ?? "image/png").toBe("image/png");
    const iconUrl = new URL(icon.src, manifestUrl).toString();
    const iconResponse = await page.request.get(iconUrl);
    expect(iconResponse.status(), `GET ${iconUrl}`).toBe(200);
    expect(
      iconResponse.headers()["content-type"] ?? "",
      `content-type иконки ${icon.src}`,
    ).toContain("image/png");
  }

  // 4. theme-color, description и iOS-мета-теги (в <head> из index.html).
  const themeColor = page.locator('meta[name="theme-color"]');
  await expect(themeColor).toHaveCount(1);
  expect(await themeColor.getAttribute("content")).toBe("#0b1220");

  await expect(page.locator('meta[name="description"]')).toHaveCount(1);
  await expect(
    page.locator('meta[name="apple-mobile-web-app-capable"]'),
  ).toHaveAttribute("content", "yes");
  await expect(
    page.locator('meta[name="apple-mobile-web-app-status-bar-style"]'),
  ).toHaveAttribute("content", "default");
  await expect(
    page.locator('meta[name="apple-mobile-web-app-title"]'),
  ).toHaveAttribute("content", "ChessAnnotator");

  const appleTouchIcon = page.locator('link[rel="apple-touch-icon"]');
  await expect(appleTouchIcon).toHaveCount(1);
  const appleHref = await appleTouchIcon.getAttribute("href");
  expect(appleHref, "у apple-touch-icon должен быть href").toBeTruthy();
  const appleIconResponse = await page.request.get(
    new URL(appleHref!, page.url()).toString(),
  );
  expect(appleIconResponse.status()).toBe(200);

  // 5. Service worker. Регистрация асинхронная (registerSW.js после загрузки),
  // поэтому до 5 с ждём ненулевого количества регистраций вместо sleep.
  // В Playwright/Chromium на 127.0.0.1 SW доступен (secure context).
  const swSupported = await page.evaluate(() => "serviceWorker" in navigator);
  if (!swSupported) {
    // Честно фиксируем среду без SW, не превращая проверку во флейк.
    test.info().annotations.push({
      type: "warning",
      description:
        "navigator.serviceWorker недоступен в контексте Playwright — регистрация SW не проверена",
    });
    return;
  }

  await expect
    .poll(() => swRegistrationCount(page), {
      message: "service worker не зарегистрировался за 5 с в preview-сборке",
      timeout: 5_000,
      intervals: [200, 300, 500, 1_000],
    })
    .toBeGreaterThan(0);

  const swInfo = await page.evaluate(async () => {
    const registrations = await navigator.serviceWorker.getRegistrations();
    const registration = registrations[0];
    const worker =
      registration?.active ?? registration?.waiting ?? registration?.installing;
    return {
      scope: registration?.scope ?? null,
      scriptURL: worker?.scriptURL ?? null,
    };
  });
  expect(swInfo.scope).toBe(new URL("/", page.url()).toString());
  expect(swInfo.scriptURL).toMatch(/\/sw\.js$/);
});
