import { existsSync, readdirSync } from "node:fs";
import path from "node:path";

/**
 * Import boundaries between the layers of src/ (DEC-007, ARCH-01), as zones
 * for eslint-plugin-import's `no-restricted-paths` (bundled with eslint-config-next):
 *
 *   app/       routes and layouts; composes features
 *   features/  one folder per feature; public surface only through its index.ts
 *   ui/        shared presentational components with no feature knowledge
 *   lib/       infrastructure: API client, analytics, CSP, redaction, hooks
 *
 * Zones are checked on resolved file paths, so aliased and relative imports
 * are treated alike. Feature folders are discovered, so a new feature is
 * covered without editing this file.
 *
 * @param {string} root the web app directory (zones are relative to it)
 */
export function boundaryZones(root) {
  const src = path.join(root, "src");
  const featuresDir = path.join(src, "features");
  const features = existsSync(featuresDir)
    ? readdirSync(featuresDir, { withFileTypes: true })
        .filter((entry) => entry.isDirectory())
        .map((entry) => entry.name)
    : [];
  const publicIndex = (feature) => `./${feature}/index.ts`;
  // lib/ and ui/ may not import features at all. Everything else (routes, root
  // instrumentation, any other top-level entry, e2e) may use public indexes only.
  // Top-level entries are listed when the config loads, which covers every file
  // `eslint .` lints; an editor may need an ESLint server restart to cover a new one.
  const featureConsumers = [
    ...readdirSync(src)
      .filter((name) => !["features", "lib", "ui"].includes(name))
      .map((name) => `./src/${name}`),
    "./e2e",
  ];

  return [
    {
      target: "./src/lib",
      from: ["./src/ui", "./src/features", "./src/app"],
      message: "lib/ is infrastructure and must not import ui/, features/, or app/.",
    },
    {
      target: "./src/ui",
      from: ["./src/features", "./src/app"],
      message: "ui/ is shared presentation and must not import features/ or app/.",
    },
    {
      target: "./src/features",
      from: "./src/app",
      message: "Features must not import routes; app/ composes features.",
    },
    ...features.map((feature) => ({
      target: `./src/features/${feature}`,
      from: "./src/features",
      except: [`./${feature}`, ...features.filter((other) => other !== feature).map(publicIndex)],
      message: 'Use another feature only through its public index: import from "@/features/<name>".',
    })),
    {
      target: featureConsumers,
      from: "./src/features",
      except: features.map(publicIndex),
      message: 'Import a feature only through its public index: "@/features/<name>".',
    },
  ];
}
