import "dotenv/config";

import { defineConfig } from "drizzle-kit";

// `drizzle-kit generate` does not need a live database; only push/migrate do.
// Use a placeholder when DATABASE_URL isn't set so codegen still works locally.
const url = process.env.DATABASE_URL ?? "postgresql://placeholder";

export default defineConfig({
  out: "./drizzle",
  schema: "./src/lib/db/schema.ts",
  dialect: "postgresql",
  dbCredentials: { url },
  verbose: true,
  strict: true,
});
