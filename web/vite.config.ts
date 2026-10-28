import react from "@vitejs/plugin-react";
import { defineConfig, loadEnv } from "vite";

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
      },
    },
  };
});
