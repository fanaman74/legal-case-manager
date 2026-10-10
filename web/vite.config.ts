import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The build is served by the web app (services/api/app/web).
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "../services/api/app/web",
    emptyOutDir: true,
    assetsInlineLimit: 0,
    sourcemap: false,
  },
  server: {
    // tokens.css lives in docs/design, outside this folder.
    fs: { allow: [".."] },
    proxy: {
      "/api": { target: "https://127.0.0.1:9443", secure: false, changeOrigin: false },
    },
  },
});
