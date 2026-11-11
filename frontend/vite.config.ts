import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In development the API is proxied so no CORS setup is needed.
export default defineConfig({
  plugins: [react()],
  server: { port: 5173, proxy: { "/api": "http://localhost:8000", "/health": "http://localhost:8000" } },
});
