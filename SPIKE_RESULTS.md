# SPIKE_RESULTS — agent-creates-agent

## Verdict: **PASS**

Agent A (a Managed Agent in this account) drove creation of a second Managed Agent (`spike-child`) end-to-end. `spike-child` was found in `client.beta.agents.list()` under the same `agent_id` returned by the host's `agents.create()` call. End-to-end latency was 11.5 s, first try.

## Recommendation: **agent-creates-agent IS viable**, with one architectural constraint.

**You cannot give an agent its own bash + the SDK + a key and let it self-manage.** Per `managed-agents-client-patterns.md`: *"secrets currently hold MCP credentials only — they are not exposed to the container's shell."* Vaults can't inject `ANTHROPIC_API_KEY` into the sandbox. Every "agent creates agent" interaction has to go through either:

- **(a) A long-running host that intercepts custom-tool events** — the pattern this spike proves. Build a control plane that holds the master key and handles `agent.custom_tool_use` events out-of-band. This is what Foundry should do. Note the implication: "fire-and-forget" sessions that need to spawn agents won't work without that host running.
- **(b) An MCP server you operate** that wraps `agents.create` and lives at a URL the sandbox can reach.

If your project plan assumed (a), proceed. If it assumed agents would self-bootstrap inside their own sandbox, redesign.

## What worked

- Beta header `managed-agents-2026-04-01` is auto-applied by `anthropic` 0.97.0 on `client.beta.*` — no manual header needed.
- `client.beta.environments.create(name=..., config={"type":"cloud","networking":{"type":"unrestricted"}})` returned `env_*` immediately.
- `client.beta.agents.create(name=..., model="claude-opus-4-7", system=..., tools=[<one custom tool>])` returned `agent_*` immediately. `claude-opus-4-7` is in the SDK's accepted `Model` literal.
- Custom-tool config is `{type: "custom", name, description, input_schema}` — works as expected.
- `client.beta.sessions.create(agent=agent_id, environment_id=env_id, title=...)` accepts a bare string for `agent` (not just an object form) and returns `sesn_*`.
- `client.beta.sessions.events.send(session_id, events=[{"type":"user.message", "content":[{"type":"text","text":"..."}]}])` worked with the keyword shape Python convention dictates. (TS docs show positional+body-dict; Python is positional `session_id` + `events=` kwarg.)
- `client.beta.sessions.events.list(session_id, order="asc")` is a `SyncPageCursor` that auto-paginates when iterated. Polling loop dedupes by `ev.id`.
- Event flow ran exactly as the SDK Pydantic types predicted.

### Observed event sequence (from `spike_events.jsonl`)

```
1.  user.message                                                  (host -> session)
2.  session.status_running
3.  span.model_request_start
4.  agent.custom_tool_use      name=create_managed_agent          <-- the meta-call
5.  span.model_request_end
6.  session.status_idle        stop_reason.type=requires_action
                               stop_reason.event_ids=[<id of #4>]
7.  user.custom_tool_result    custom_tool_use_id=<id of #4>      (host -> session)
8.  session.status_running
9.  span.model_request_start
10. agent.message              content=[TextBlock("agent_011...")]
11. span.model_request_end
12. session.status_idle        stop_reason.type=end_turn          <-- DONE
```

### Confirmed event shapes (from live run, not docs)

```jsonc
// agent.custom_tool_use — FLAT, not nested under "tool_use"
{
  "id": "sevt_012KSsjuMqrJ9iCADnutedQv",
  "type": "agent.custom_tool_use",
  "name": "create_managed_agent",
  "input": { "name": "spike-child", "system_prompt": "..." },
  "processed_at": "2026-04-27T00:37:55.868000Z"
}

// session.status_idle — discriminator on stop_reason.type
{
  "id": "sevt_01FTT6E84EWkYTCqEyfjrwyS",
  "type": "session.status_idle",
  "stop_reason": {
    "type": "requires_action",
    "event_ids": ["sevt_012KSsjuMqrJ9iCADnutedQv"]
  }
}
{ "stop_reason": { "type": "end_turn" } }   // simpler shape

// agent.message — content is a flat List[TextBlock] on the event itself
{
  "id": "sevt_01HHegXKikkP3z29jAL3r36z",
  "type": "agent.message",
  "content": [{ "type": "text", "text": "agent_011CaT..." }]
}
```

The `id` of an `agent.custom_tool_use` event **is** the value the API expects as `custom_tool_use_id` in the reply. (No separate tool_use_id.) This wasn't quoted in any docs page I could find — confirmed only by reading the SDK Pydantic types and then by the live run.

## What didn't (verbatim)

One real bug, found by `cleanup.py`:

```
list sessions failed: Error code: 400 - {'type': 'error', 'error':
{'type': 'invalid_request_error',
 'message': 'limit: value must be greater than or equal to 1 and less than or equal to 100'},
 'request_id': 'req_011CaTLoU3K1MF1VadszzC7g'}
```

The events doc says `events.list` accepts `limit default 1000`. For `agents.list` and `sessions.list` the cap is **100**. Fix in cleanup.py: use `limit=100` (or omit and let it default). Re-run succeeded:

```
[sessions]
  delete session sesn_011CaTLmqnTHGErsgYAouzSu title='spike-session'
[agents]
  archive agent agent_011CaTLnG6CF4t2VWH2QyTvK name='spike-child'
  archive agent agent_011CaTLmpSbB85NbHcZzufx9 name='spike-agent-a'
```

`agents` are archived (no DELETE endpoint exists); `environments` and `sessions` are hard-deleted.

## Quirks and surprises

1. **`agent.custom_tool_use.id` doubles as the resolution key.** The same value goes back as `custom_tool_use_id`. Cleaner than the messages API's separate `tool_use_id`.
2. **`session.status_idle` is overloaded.** Same event type for "agent finished its turn" and "agent is blocked waiting for me". Discriminator is `stop_reason.type`. Treating *any* idle as turn-end (which my pre-run draft did) would have caused the spike to "complete" before the host ever resolved the tool call.
3. **`spans` events fire around inference.** `span.model_request_start` / `span.model_request_end` bracket each inference call. The events doc lists these but the overview implies cleaner Claude/user/MCP grouping; the spans interleave throughout. Filter them out for any user-facing UX.
4. **Output cap = sane.** Agent A's "Be terse" system prompt produced exactly the agent_id literal as the final message. No "Here is the new agent's ID:" preamble. With `claude-opus-4-7`, terse instructions stick.
5. **No `delete` for agents.** Cleanup uses `archive`. Archived agents still appear in `agents.list(include_archived=True)` and presumably can't be reused for new sessions; budget for the archived-agent count if you spawn lots.
6. **List `limit` cap is 100, not 1000** as the events doc claims. (Per-resource list endpoints.)
7. **`limit=1000` on `environments.list` did NOT 400** in the same run — silently ignored. Inconsistency.
8. **Total resources used:** 1 environment + 2 agents + 1 session. Within the 3-agent cap.

## Cost incurred

Approximate, based on Anthropic public pricing:

- Session-hour: 11.5 s × ($0.08/hr) ≈ **$0.0003**
- Token costs (one Opus-4.7 turn with tool-call + one tiny response): ~1.5–2k input tokens, ~10 output tokens ≈ **$0.02–0.03**
- **Total ≈ $0.02–0.04.**

Plus a small amount for the docs research / SDK install / aborted cleanup attempt: rounding up, **call it $0.05 total for the spike run**.

## Reproduce

```
cp .env.example .env       # paste ANTHROPIC_API_KEY
pip install anthropic python-dotenv
python3 spike.py           # ~12 s end-to-end
python3 cleanup.py         # archives spike-* agents, deletes spike-* sessions/envs
```

Raw events from the run: `spike_events.jsonl` (gitignored).

## What to build on top

Before scaling this pattern, the open questions worth a follow-up spike:

- How does the host receive `agent.custom_tool_use` in production? Polling is fine for one session; for many concurrent sessions use `client.beta.sessions.events.stream()` (SSE) — the SDK exposes it. Webhook story isn't documented in what I read.
- What happens if the host crashes between receiving the tool-use event and posting the result? Is there a TTL on `requires_action`? The session is just sitting idle — does `$0.08/hr` accrue while idle? That's the cost-control question before going to production.
- Multi-tenant safety: an Agent A custom tool that creates agents will, by default, do so in YOUR account using YOUR key. If the larger project is meant for end users, the control plane needs per-tenant key isolation (or a separate Anthropic org per tenant).
