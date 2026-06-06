import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import path from "node:path";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const controllerPort = env.AEO_PORT ?? "8790";
  const proxyTarget = env.AEO_API_TARGET ?? `http://127.0.0.1:${controllerPort}`;
  return {
    plugins: [react(), tailwindcss()],
    resolve: {
      alias: { "@": path.resolve(__dirname, "app") },
    },
    server: {
      proxy: { "/api": proxyTarget },
    },
    build: { outDir: "dist" },
  };
});
