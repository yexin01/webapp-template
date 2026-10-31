import path from "node:path";

import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  resolve: { alias: { "@": path.resolve(import.meta.dirname, "src") } },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./vitest.setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
    coverage: {
      provider: "v8",
      // Terminal reports only: files written to coverage/ would be linted and format-checked.
      reporter: ["text", "text-summary"],
      include: ["src/**/*.{ts,tsx}"],
      exclude: ["src/**/*.test.{ts,tsx}", "src/**/*.testing.{ts,tsx}", "src/**/*.d.ts"],
    },
  },
});
