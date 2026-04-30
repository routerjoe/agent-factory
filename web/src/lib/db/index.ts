import { neon } from "@neondatabase/serverless";
import { drizzle, type NeonHttpDatabase } from "drizzle-orm/neon-http";

import * as schema from "./schema";

type Db = NeonHttpDatabase<typeof schema>;

let cachedDb: Db | null = null;

function getDb(): Db {
  if (cachedDb) return cachedDb;

  const url = process.env.DATABASE_URL;
  if (!url) {
    throw new Error(
      "DATABASE_URL is not set. Add it to .env.local (see SETUP.md).",
    );
  }
  const client = neon(url);
  cachedDb = drizzle(client, { schema });
  return cachedDb;
}

/**
 * Drizzle client. Lazily constructed on first property access so importing
 * this module does not throw during build-time page-data collection (Next
 * runs the module graph; only actual queries should require DATABASE_URL).
 */
export const db = new Proxy({} as Db, {
  get(_target, prop, receiver) {
    return Reflect.get(getDb(), prop, receiver);
  },
});

export { schema };
