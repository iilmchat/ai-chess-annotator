import react from "@vitejs/plugin-react";
import { loadEnv } from "vite";
import { VitePWA } from "vite-plugin-pwa";
import { configDefaults, defineConfig } from "vitest/config";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const apiTarget = env.VITE_API_PROXY || "http://127.0.0.1:8000";

  return {
    plugins: [
      react(),
      // T-081: PWA — устанавливаемый манифест + service worker для статики.
      VitePWA({
        registerType: "autoUpdate",
        injectRegister: "auto",
        // SW не нужен в dev: он мешает HMR и кэширует устаревшие модули.
        devOptions: { enabled: false },
        manifest: {
          name: "ChessAnnotator",
          short_name: "ChessAnnotator",
          description:
            "Комментарий к шахматной партии в стиле гроссмейстера: разбор, оценка позиции и объяснения для новичка, клуба и гроссмейстера.",
          lang: "ru",
          start_url: "/",
          scope: "/",
          display: "standalone",
          // Палитра из web/src/styles.css (:root): --bg, --panel, --text.
          theme_color: "#0b1220",
          background_color: "#0b1220",
          icons: [
            { src: "/icons/icon-192.png", sizes: "192x192", type: "image/png" },
            { src: "/icons/icon-512.png", sizes: "512x512", type: "image/png" },
            {
              src: "/icons/icon-maskable-512.png",
              sizes: "512x512",
              type: "image/png",
              purpose: "maskable",
            },
          ],
        },
        workbox: {
          globPatterns: ["**/*.{js,css,html,svg,png,ico,woff2}"],
          navigateFallback: "/index.html",
          navigateFallbackDenylist: [
            /^\/v1\//,
            /^\/auth\//,
            /^\/admin/,
            /^\/health/,
          ],
          cleanupOutdatedCaches: true,
          // Только статика: API-ответы (/v1/*, /auth/*) кэшировать нельзя —
          // иначе пользователь увидит устаревшие данные, чужие сессии и т.п.
          // Поэтому runtimeCaching для них намеренно отсутствует.
        },
      }),
    ],
    // Windows + antivirus иногда блокирует временный файл esbuild-transpile;
    // для современных браузеров downlevel не нужен.
    build: { target: "esnext", minify: "terser" },
    esbuild: { target: "esnext" },
    server: {
      port: 5173,
      proxy: {
        "/v1": apiTarget,
        // T-028: JWT-эндпоинты /auth/* живут вне /v1.
        "/auth": apiTarget,
      },
    },
    // `E2E_PREVIEW=1` (Windows/DSH): preview раздаёт dist и проксирует API
    // так же, как dev-сервер — real-backend E2E продолжает работать.
    preview: {
      port: 5173,
      proxy: {
        "/v1": apiTarget,
        "/auth": apiTarget,
      },
    },
    test: {
      // E2E Playwright-спеки (web/e2e/**) не должны запускаться в Vitest
      // (T-036b): у них другой раннер и нет jsdom-окружения.
      exclude: [...configDefaults.exclude, "e2e/**"],
    },
  };
});
