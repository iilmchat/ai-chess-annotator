import react from "@vitejs/plugin-react";
import { loadEnv } from "vite";
import { configDefaults, defineConfig } from "vitest/config";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const apiTarget = env.VITE_API_PROXY || "http://127.0.0.1:8000";

  return {
    plugins: [react()],
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
