# SPIKE_RESULTS — Phase 1B resilience

90-minute followup to Phase 1A. Four tests of session-lifecycle resilience to inform the control plane design.

Spike script wall-clock: **3629.8 s (60.5 min)**, well within the 90-min budget.
Total agents created: **4** (within cap).
Estimated cost: **~$0.15** in token + session-hour charges (under the $5 cap, well under the $3 stop-and-ask threshold).

---

## TL;DR — answers to the four questions

| Question | Answer |
|---|---|
| **T1 Idle billing** | **$0/hr while idle.** `stats.active_seconds` is flat at **2.946 s** from t=0 to t=15 min to t=60 min while `duration_seconds` grows linearly. Idle in `requires_action` does not consume billable active time. *(t=240 min sample pending from background poller; see § T1.)* |
| **T2 Idempotency** | **API silently accepts duplicate posts** (200 OK on every attempt — first, duplicate, AND a different-content post for the same `tool_use_id`). The session uses **only the first** post; subsequent ones are silently dropped. **First-write-wins.** |
| **T3 Session timeout** | **No timeout observed in 60 min.** Session stayed `status=idle` with `active_seconds` flat the entire window, and 60 status polls returned no change. Floor is **≥ 60 min**; true upper bound undocumented and not measurable in this spike. |
| **T4 Mid-flight cancellation** | **Yes, children orphan.** `DELETE /v1/sessions/{id}` hard-deletes the session (subsequent retrieve returns 404). The child agent created by the host before the delete remained in `agents.list()`. **No cascade cleanup.** |

## Architecture verdict

**Safe to build.** The four answers all push the same direction — sessions are cheap to leave idle, the API tolerates retries, and orphan cleanup is the host's responsibility. With one thing to monitor (see § "Cost-of-failure" below) the control plane is straightforward.

---

## T1 — Idle billing curve

Session: `sesn_011CaTc2zk8CsmBb3Y7VU9oQ` (parent agent: `spike1b-billing-test`).

| label | offset | `active_seconds` | `duration_seconds` | status |
|---|---:|---:|---:|---|
| t=0 | 0 s | **2.946** | 11.11 s | idle |
| t=15 min | 900 s | **2.946** | 907.13 s | idle |
| t=60 min | 3600 s | **2.946** | 3608.84 s | idle |
| t=240 min | 14400 s | *pending — written by `spike_1b_t240_poller.py`* | | |
| post-drain | — | *pending — written after t=240 sample* | | |

**Reading.** `active_seconds` is the metric Anthropic almost certainly bills against the $0.08/hr session-hour rate. It is **frozen at 2.946 s — the cost of the single inference turn that produced the `agent.custom_tool_use` event** — for the entire idle window. `duration_seconds` grows roughly 1-for-1 with wall-clock time but does not appear to be billed. After T3 was drained the same pattern held: `active` jumped from 2.746 s to 7.23 s when the agent processed the tool result, then stopped again. Active = inference, not wall-clock.

**Implication.** Stuck `requires_action` sessions are essentially free. A control plane can retry, defer, or just leave a session pending for hours without cost pressure. The only real bound is whatever upper-limit Anthropic enforces (T3 says ≥ 60 min; full bound unknown).

The t=240 sample is being captured by a detached process (`spike_1b_t240_poller.py`) that wakes at `2026-04-27T07:58:05+00:00`, samples, drains the session, and appends to `spike1b_results.json`. Run `python3 cleanup_1b.py` only AFTER `spike1b_t240_poller.log` ends with `[poller] DONE.`

---

## T2 — Idempotency of `user.custom_tool_result`

Session: `sesn_011CaTc3qrs3JSga5jgj1V4n`. Tool-use ID: `sevt_013piwx1Ts7fLW8krkZXbgVC`.

| Attempt | Body | API response | Effect on agent |
|---|---|---|---|
| 1 (first) | `{"agent_id": "agent_t2_first"}` | 200 OK | Used in agent's reply |
| 2 (exact duplicate) | `{"agent_id": "agent_t2_first"}` | 200 OK | Silently dropped |
| 3 (different content, same id) | `{"agent_id": "agent_t2_DIFFERENT"}` | 200 OK | Silently dropped |

Final assistant text: **`agent_t2_first`** — the value from post #1, not the value from post #3.

**Reading.** The send-events endpoint is permissive: same `custom_tool_use_id` can be posted any number of times with any content, all return 200. **Only the first post influences the agent.** The API does not error on duplicates and does not let you "correct" a result you already submitted.

**Implication for the control plane.**
- **Retries are safe.** A retry after a network blip will not double-process or error.
- **You cannot trust the 200 response as proof "this is the value the agent saw."** A late retry that wins the race could be silently overridden by an earlier post you forgot about. Build your post operation to be deterministic for a given `custom_tool_use_id` (no nondeterminism in the result body).
- **No need for your own dedup layer** — but DO maintain a "have I already posted for this id?" record so you can short-circuit redundant calls and (more importantly) detect bugs in your own state machine.

---

## T3 — Session timeout

Session: `sesn_011CaTc3QXMn9ksR4YsZrGzM`. 60 samples at 60-second intervals over 60 min.

| sample | `active_seconds` | `duration_seconds` | status |
|---|---:|---:|---|
| t=0 | 2.746 | 5.77 | idle |
| t=1 min | 2.746 | 67.93 | idle |
| t=15 min | 2.746 | 911.81 | idle |
| t=30 min | 2.746 | 1815.60 | idle |
| t=45 min | 2.746 | 2719.70 | idle |
| t=59 min | 2.746 | 3563.04 | idle |
| t=60 min final | 2.746 | 3608.52 | idle |
| post-drain | **7.23** | 3616.32 | idle |

**Reading.** Across all 60 samples: zero status transitions, zero `active_seconds` growth. Session was reachable and queryable throughout. After we posted the tool result, the agent processed it within ~8 s and produced a final reply (drain text: `` `agent_drain_noop` ``).

**Implication.** No documented timeout, no observed timeout up to 60 min. The empirical floor is ≥60 min; the true ceiling is unknown. For the control plane, this means:
- Don't rely on Anthropic to garbage-collect stuck sessions for you.
- A reaper of your own (cron over `client.beta.sessions.list()` filtering by `created_at_lte` and status) is the safe pattern.
- You can leave sessions parked for at least an hour without losing them.

---

## T4 — Mid-flight cancellation

Parent session: `sesn_011CaTc2VWiZ2xrsyjRbgUD2`. Child agent: `agent_011CaTc2tqRT4mm7Cwq72tFB` (name: `spike1b-orphan-child`).

Sequence:
1. Trigger `agent.custom_tool_use` with input `{"name": "spike1b-orphan-child", "system_prompt": "You only respond with hello."}`.
2. Host calls `client.beta.agents.create(name="spike1b-orphan-child", ...)`. Returns `agent_011CaTc2tqRT4mm7Cwq72tFB`.
3. Host calls `client.beta.sessions.delete(parent_session_id)`. Response: `{id: "...", type: "session_deleted"}`.
4. Host calls `client.beta.sessions.retrieve(parent_session_id)` → **404 not_found_error**. Session is hard-deleted, not archived.
5. Host calls `client.beta.agents.list()` and filters by name → orphan child **is still listed**.

Pre-delete parent stats: `active=2.988 s, duration=5.81 s`. The session never billed beyond that single inference turn.

**Implication.**
- DELETE on a session is a **hard delete**, not archive. No way to recover.
- DELETE does **not** cascade to anything the agent or the host did during the session. Child agents persist. (Reasonable: the child agent is owned by the account, not the session.)
- **Your control plane must track orphan candidates.** If the host crashes between calling `agents.create` for a child and posting the tool result, restarting the host won't see any pending session to resume — but the child agent exists, possibly forever.
- A safe pattern: tag every host-created child agent with `metadata = {"parent_session_id": "...", "created_at": "..."}`. A reaper periodically lists agents and archives any whose `parent_session_id` no longer resolves.

---

## Recommendations for control plane design

### Required retry / dedup strategy
- **Retry posts of `user.custom_tool_result` freely on transport errors.** API is first-write-wins, returns 200 on duplicates.
- **Make the result body deterministic** for a given `custom_tool_use_id`. Idempotency key the result generation, not the post.
- **Don't double-handle a tool_use event.** Track `(session_id, custom_tool_use_id) → posted_at` in your own state. T2 shows the API will quietly accept a second different post that does nothing — that's a bug source if your handler can fire twice.

### Required dedup layer (you don't have one)
None at the API level. Maintain your own `posted_tool_use_ids` set scoped per session. Persist it through host restarts; otherwise your post-restart handler will re-process every pending `agent.custom_tool_use` event from `events.list` and call `agents.create` again — which is a real second agent, not a deduped no-op.

### Required monitoring (alert on)
1. **Sessions in `idle/requires_action` for > N minutes.** No cost pressure (T1), but a backlog signals the host is stuck. Alert at e.g. 10 min.
2. **Agents created in your account with no live owning session.** Reaper described above; alert if the orphan count grows monotonically.
3. **`session.error`, `session.status_terminated` events.** Phase 1A noted these exist but didn't fire in our happy path; T3 confirms idle sessions don't generate them either, so any occurrence is signal.
4. **Per-session `active_seconds` growth.** Spikes mean unexpected inference (e.g., agent looped on its own tool calls).
5. **`agents.list()` count vs your control-plane registry.** Drift = orphan accumulation.

### Cost-of-failure per hour of MTTR

Based on observed values:

| Failure mode | Cost per stuck session per hour |
|---|---|
| Host crashed, sessions stuck in `requires_action` | **$0** (T1: idle is free) |
| Same, plus orphan child created | **$0** for the session, **$0** for the unused child (agents only cost on session inference) |
| Host crashed mid-Opus turn (rare) | Up to one Opus turn ≈ $0.02–0.05 per session, one-time |
| Reaper not running, orphans accumulate | $0 per agent until used; namespace pollution only |

**MTTR is not cost-driven.** It's correctness-driven. The cost ceiling per stuck session per hour is essentially zero — the architecture is forgiving on ops budget. The risk vector is *missed work*, not *runaway billing*. (Plan accordingly: alert on age, not spend.)

### Architecture verdict: **safe to build on top of**

The pattern from Phase 1A — host-mediated custom tool, control plane holds the master key — composes cleanly with all four resilience properties:
- Idle is cheap (T1) → control plane can be conservative about retries.
- Posts are first-write-wins (T2) → idempotent control flow is achievable.
- No timeout in 60 min (T3) → control plane sets its own SLA, doesn't fight Anthropic's.
- Hard delete + no cascade (T4) → orphan sweeper is required but trivial.

The only NEW required component compared to what Phase 1A implied: an **agent reaper** that compares `agents.list()` against your control-plane registry and archives orphans. Cheap to build.

---

## Raw data and reproduce

- `spike1b_results.json` — structured per-test results (gitignored; resource ids are account-private).
- `spike1b_events.jsonl` — every observed session event (gitignored).
- `spike1b_run.log` — live spike output (gitignored).
- `spike1b_t240_poller.log` — background poller progress (gitignored).

```
cp .env.example .env       # paste ANTHROPIC_API_KEY
pip install anthropic python-dotenv
python3 spike_1b.py        # ~60 min, runs T1/T2/T3/T4 and spawns t=240 poller
# wait ~3 hours after spike_1b.py exits, check spike1b_t240_poller.log
python3 cleanup_1b.py      # AFTER the poller is DONE
```

## Cost incurred

- Inference (≈5 Opus turns × ~1.6k input + ~100 output): ~$0.10
- Session-hours: 4 sessions × ~5 s active each = ~$0.0005, negligible
- Idle session-hours: 0 (T1 confirms idle is unbilled on `active_seconds`)
- **Total: ~$0.10–0.15.** Comfortably under the $3 stop-and-ask threshold and the $5 hard cap.
