import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The build is embedded into the launcher binary (go:embed).
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "../launcher/internal/web/dist",
    emptyOutDir: true,
    assetsInlineLimit: 0,
    sourcemap: false,
  },
  server: {
    // tokens.css lives in docs/design, outside this folder.
    fs: { allow: [".."] },
    proxy: {
      "/api": { target: "https://127.0.0.1:8443", secure: false, changeOrigin: false },
    },
  },
});
