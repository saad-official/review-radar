import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";

const root = fileURLToPath(new URL("./", import.meta.url));

export default defineConfig({
  resolve: {
    // Mirrors tsconfig "paths": { "@/*": ["./*"] }
    alias: [{ find: /^@\//, replacement: root }],
  },
  test: {
    include: ["tests/**/*.test.ts"],
    environment: "node",
  },
});
