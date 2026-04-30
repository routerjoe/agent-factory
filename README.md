# Foundry

A personal Claude Managed Agents factory. Single-user.

## Layout

```
.
├── web/                            Next.js 15 app (Phase 2A chassis)
├── SETUP.md                        End-to-end setup: Neon, Clerk, Vercel
├── SPIKE_RESULTS.md                Phase 1A: agent-creates-agent works
├── SPIKE_RESULTS_PHASE_1B.md       Phase 1B: resilience properties
├── spike.py / spike_1b.py          Spike test runners
└── cleanup.py / cleanup_1b.py      Spike resource teardown
```

## Phases

- **Phase 1A** (done): proved agent-creates-agent via custom_tool_use events.
- **Phase 1B** (done): proved idle is free, no observed timeout ≥ 78.5 hours, hard-delete sessions don't cascade, posts are first-write-wins.
- **Phase 2A** (this branch): web chassis — Next.js, Postgres, auth, deploy. No agent logic.
- Phase 2B (next): control plane / polling worker that services tool-use events.
- Phase 2C: Foundry meta-agent that interviews the user and creates new agents.

## Getting started

See [`SETUP.md`](SETUP.md).
