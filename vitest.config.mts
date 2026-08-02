import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import path from "node:path";
import { fileURLToPath } from "node:url";

// `.mts` (not `.ts`) and `import.meta.url` (not `__dirname`) on purpose: this
// file uses `import`/`export default`, real ESM syntax, but the project has no
// `"type": "module"` in package.json, so Node loaded a plain `.ts` version of
// this file as CommonJS despite that syntax. `__dirname` only worked by
// accident of that CJS load; Vite's native config loader flags the mismatch
// (ESM syntax loaded as CommonJS) as deprecated. The `.mts` extension tells
// Node and Vite unambiguously that this file is a module, and `import.meta.url`
// is the real-ESM equivalent of `__dirname` that a true module load supports.
const dirname = path.dirname(fileURLToPath(import.meta.url));

export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    setupFiles: ["./vitest.setup.ts"],
  },
  resolve: {
    alias: {
      "@": path.resolve(dirname, "."),
    },
  },
});
