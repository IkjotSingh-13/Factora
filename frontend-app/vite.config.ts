import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    host: "0.0.0.0",
    port: 5173,
    allowedHosts: true,
    // `npm run dev` on its own still talks to the running API, so the UI never needs
    // a second fake data path.
    proxy: { "/api": { target: "http://127.0.0.1:8000", changeOrigin: true } },
  },
  preview: { host: "0.0.0.0", port: 4173, allowedHosts: true },
});
