import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";

import { boundaryZones } from "./eslint.boundaries.mjs";

// FE-02: the web app holds no business logic, server secrets, or direct
// database / payment-provider access. Those imports are banned outright.
const forbiddenImports = {
  patterns: [
    { group: ["stripe", "@stripe/*"], message: "Billing goes through the Python API." },
    {
      group: ["@supabase/*", "pg", "postgres", "@prisma/*", "drizzle-orm"],
      message: "The browser never talks to the database (DEC-008).",
    },
    {
      group: ["openai", "@anthropic-ai/*", "ai"],
      message: "LLM providers are called only by the Python API.",
    },
  ],
};

const config = [
  ...nextVitals,
  ...nextTs,
  {
    ignores: [
      ".next/**",
      "node_modules/**",
      "playwright-report/**",
      "test-results/**",
      "src/lib/api/schema.d.ts",
      "next-env.d.ts",
    ],
  },
  {
    rules: {
      "no-restricted-imports": ["error", forbiddenImports],
      "no-restricted-syntax": [
        "error",
        {
          selector:
            "MemberExpression[object.object.name='process'][object.property.name='env'][property.name=/^(?!NEXT_PUBLIC_|NODE_ENV$).*/]",
          message: "Only NEXT_PUBLIC_* variables may be read in application code (ENV-07).",
        },
      ],
      // Layer boundaries for app/, features/, ui/, and lib/ (see eslint.boundaries.mjs).
      "import/no-restricted-paths": [
        "error",
        { basePath: import.meta.dirname, zones: boundaryZones(import.meta.dirname) },
      ],
      "@typescript-eslint/no-explicit-any": "error",
      "react/no-danger": "error",
    },
  },
  {
    files: [
      "next.config.ts",
      "playwright.config.ts",
      "src/instrumentation.ts",
      "src/sentry.*.ts",
      "e2e/**",
      "scripts/**",
    ],
    rules: { "no-restricted-syntax": "off" },
  },
];

export default config;
