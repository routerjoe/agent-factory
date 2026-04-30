# Foundry web app (Phase 2A chassis)

Next.js 15 + Tailwind 4 + shadcn/ui + Drizzle ORM (Neon) + Clerk auth, deployed on Vercel. Single-user.

## Quick start

```bash
cp .env.local.example .env.local   # fill in the values
npm install
npm run db:push                    # apply schema to Neon
npm run dev                        # http://localhost:3000
```

For the full setup walkthrough (creating Neon/Clerk/Vercel accounts, env vars, deploy), see [`../SETUP.md`](../SETUP.md).

## Scripts

- `npm run dev` — local dev (Turbopack)
- `npm run build` — production build
- `npm run typecheck` — `tsc --noEmit`
- `npm run lint` — ESLint
- `npm run db:generate` — diff schema → new SQL migration
- `npm run db:push` — apply schema directly
- `npm run db:migrate` — apply pending migrations
- `npm run db:studio` — Drizzle Studio web UI
