# Foundry — Phase 2A setup

The web app is in `web/`. Phase 2A ships only the chassis: Next.js 15, Postgres on Neon via Drizzle, single-user Clerk auth, deployable on Vercel. No agent logic.

The chassis code is done. To get a working deployed URL you need to do three external setup steps — Neon, Clerk, Vercel — that I cannot do for you (they need your accounts and a browser). The whole sequence below should take ~15 minutes.

## Local prerequisites

- Node ≥ 20
- npm ≥ 10
- A GitHub account that can authorize Vercel

## 1. Create the Neon project

1. https://console.neon.tech/app/projects → **New Project**
   - Name: `foundry`
   - Postgres version: latest available
   - Region: pick the same region you'll deploy Vercel in
2. Once created, copy the **pooled** connection string from the dashboard (it includes `-pooler` in the host). Looks like:
   `postgresql://user:pw@ep-xxx-pooler.region.aws.neon.tech/foundry?sslmode=require`
3. Save it for step 4.

## 2. Create the Clerk app

1. https://dashboard.clerk.com → **Create application**
   - Name: `Foundry`
   - Sign-in options: **Email** only (turn off Google/GitHub/etc unless you want them — single-user, so it's a matter of taste)
2. **Restrict signups to one user.** In Clerk dashboard → **User & authentication** → **Restrictions**:
   - Set **Sign-up mode** to **Restricted**.
   - Add your email to the **Allowlist**.
   - Disable **Sign-up** entirely if you'd prefer: Configure → Email, phone, username → toggle off "Sign-up". You can create your user manually in Users → **+ Create user** and they can sign in but no one else can sign up.
3. Copy the **Publishable key** and **Secret key** from API keys.
4. Save both for step 4.

The app code in `src/lib/auth.ts` enforces a second check (`ALLOWED_EMAIL` must match the signed-in user's primary email). This is defense-in-depth in case the Clerk allowlist is misconfigured.

## 3. Push the schema to Neon

Locally, before deploying:

```bash
cd web
cp .env.local.example .env.local   # then edit it with your real values
npm install
npm run db:push                    # runs drizzle-kit push against Neon
```

`db:push` applies `drizzle/0000_initial.sql` to the database — six empty tables. Verify in the Neon dashboard → **Tables**: you should see `agents`, `sessions`, `tool_use_events`, `evals`, `templates`, `runs`.

If you'd rather use migrations instead of `push`, run `npm run db:migrate` — same effect, but the migration file is recorded in a `__drizzle_migrations` table. Either works for a single-user app; `push` is simpler and matches "boring choices."

## 4. Run locally to verify

```bash
cd web
npm run dev   # http://localhost:3000
```

- `/` should redirect to `/sign-in`
- After signing in with the allowlisted email, you land on `/dashboard` and see **"No agents yet"**
- If you sign in with any other email, you land on `/forbidden` (defense-in-depth gate fires)
- `GET /api/health` returns `{"status":"ok"}` without authentication

## 5. Deploy to Vercel

1. Push this repo to GitHub if you haven't already. The current branch is `claude/agent-factory-x6Lhr`.
2. https://vercel.com/new → **Import Git Repository** → select `routerjoe/agent-factory`.
3. **Root directory**: `web` (the Next.js app lives in a subdirectory; Vercel needs to know).
4. **Framework preset**: Next.js (auto-detected).
5. **Environment variables** — paste these all into the Vercel project's Environment Variables panel (Production, Preview, Development can share these for the single-user app):

   | Name | Value |
   |---|---|
   | `DATABASE_URL` | Neon pooled connection string |
   | `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY` | from Clerk |
   | `CLERK_SECRET_KEY` | from Clerk |
   | `NEXT_PUBLIC_CLERK_SIGN_IN_URL` | `/sign-in` |
   | `NEXT_PUBLIC_CLERK_SIGN_IN_FALLBACK_REDIRECT_URL` | `/dashboard` |
   | `NEXT_PUBLIC_CLERK_SIGN_UP_FALLBACK_REDIRECT_URL` | `/dashboard` |
   | `ALLOWED_EMAIL` | your email (must match what you allowlisted in Clerk, case-insensitive) |

6. **Deploy.** First build takes ~1 minute.
7. Open the Vercel-assigned URL → sign in → dashboard says "No agents yet".

## 6. Post-deploy: tell Clerk about your production URL

In Clerk dashboard → **Domains**, add your Vercel deployment domain. Without this, Clerk's session cookies won't be set on the deployed origin.

If you wire up a custom domain later, add that too.

## What's where

```
web/
├── src/
│   ├── app/
│   │   ├── api/health/route.ts          health check (public)
│   │   ├── dashboard/                   protected pages
│   │   │   ├── layout.tsx               header + auth gate
│   │   │   └── page.tsx                 "no agents yet" empty state
│   │   ├── forbidden/page.tsx           single-user denial page
│   │   ├── sign-in/[[...sign-in]]/      Clerk sign-in
│   │   ├── layout.tsx                   ClerkProvider, fonts
│   │   ├── page.tsx                     redirect → /dashboard
│   │   └── globals.css                  Tailwind 4 + theme tokens
│   ├── components/ui/                   shadcn primitives (button, card)
│   ├── lib/
│   │   ├── auth.ts                      requireAllowedUser() gate
│   │   ├── utils.ts                     cn() helper
│   │   └── db/
│   │       ├── index.ts                 lazy Drizzle client
│   │       └── schema.ts                all 6 tables
│   └── middleware.ts                    Clerk middleware
├── drizzle/
│   └── 0000_initial.sql                 generated migration
├── drizzle.config.ts                    drizzle-kit config
├── components.json                      shadcn config
└── .env.local.example                   env var template
```

## Useful commands

| Command | What it does |
|---|---|
| `npm run dev` | Local dev server with Turbopack |
| `npm run build` | Production build (run by Vercel) |
| `npm run typecheck` | `tsc --noEmit` — fast, no bundling |
| `npm run lint` | ESLint via `eslint-config-next` |
| `npm run db:generate` | Diff schema → emit a new SQL migration in `drizzle/` |
| `npm run db:push` | Apply schema directly to Neon (skip migration history) |
| `npm run db:migrate` | Apply pending migrations from `drizzle/` |
| `npm run db:studio` | Open Drizzle Studio (web UI) at `local.drizzle.studio` |

## When things break

- **`Database connection string format for neon() should be...`** at build time → `DATABASE_URL` is missing or malformed. The Neon string must include `?sslmode=require` and use the `-pooler` host.
- **`Publishable key not valid`** → `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY` is set in `.env.local` but you haven't restarted the dev server. Stop/start `npm run dev`.
- **Sign in succeeds, then redirects to `/forbidden`** → `ALLOWED_EMAIL` doesn't match your Clerk primary email. The match is case-insensitive but must otherwise be exact.
- **Sign in succeeds, page hangs at "Loading..."** → check the Vercel function logs; usually `DATABASE_URL` is wrong. Health check at `/api/health` should still return 200.
- **Drizzle migrations and DB drift** → `npm run db:push` re-applies the schema (no down-migrations on a single-user app).
