import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The Node server (server.mjs) serves the built `dist/` and the /api routes in
// production. In dev, proxy /api to that same server so token minting works.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": "http://localhost:8090",
    },
  },
  build: {
    outDir: "dist",
  },
});
