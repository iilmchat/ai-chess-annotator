import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/v1": "http://localhost:8000",
      "/auth": "http://localhost:8000",
      "/chat": "http://localhost:8000",
      "/runs": "http://localhost:8000",
    },
  },
});