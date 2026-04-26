# SPIKE_RESULTS — agent-creates-agent

**Status: NOT YET RUN.** spike.py is written but not executed (per user instruction). Fill in the "Run results" section after running it.

---

## Question

Can a Claude Managed Agent call the Anthropic Managed Agents API as a tool to create another Managed Agent?

## Approach (chosen)

**Host-mediated custom tool.** Agent A is configured with one custom tool, `create_managed_agent`. When the agent invokes it, the session emits `agent.custom_tool_use` and idles; spike.py (the host) intercepts that event, calls `client.beta.agents.create()` itself, and posts the result back as `user.custom_tool_result`. The master `ANTHROPIC_API_KEY` never enters the sandbox.

This is a strict reading of the spec — "the agent can drive creation of another agent" — and matches what a production system would actually do. It does NOT prove that an agent's *own bash tool* can call the API; that path is blocked anyway (see "Quirks" below).

## Pre-run findings (from docs)

- Beta header `managed-agents-2026-04-01` is auto-applied by the SDK on `client.beta.{agents,environments,sessions,vaults}.*`.
- Pricing: **$0.08/session-hour** + normal token costs. The spike caps session time at 5 minutes, so the session-hour charge should be ≤ ~$0.007.
- `agent_toolset_20260401` includes `bash`, `read`, `write`, `edit`, `glob`, `grep`, `web_fetch`, `web_search`. **No built-in `anthropic_api` tool.**
- Per `managed-agents-client-patterns.md`: *"secrets currently hold MCP credentials only — they are not exposed to the container's shell."* So vaults cannot inject `ANTHROPIC_API_KEY` as an env var into bash. This rules out the naive "agent installs the SDK and calls the API itself" path unless you embed the key in the prompt, which the docs explicitly warn against.
- Networking: `unrestricted` ("full egress except legal blocklist") vs. `package_managers_and_custom` (with `allowed_hosts`). Spike uses `unrestricted` for simplicity, although it isn't strictly needed for the chosen approach.
- Agents have **no DELETE** — only `archive`. Environments and sessions support `delete`.
- Session lifecycle signal: `session.status_idle` event indicates the agent has finished its turn (or is awaiting a custom tool result).

## Run results

**Fill in after running `python3 spike.py`.**

- Verdict: PASS / FAIL / PARTIAL
- Total elapsed:
- agent_a_id:
- child_agent_id (returned by spike.py):
- child_agent_id (found in `agents.list()`):
- Final assistant text from Agent A:

### What worked

-

### What didn't (paste API errors verbatim)

-

### Iteration notes

The most likely places spike.py will need a small fix on first run:

1. **Exact shape of the `agent.custom_tool_use` event.** The events doc lists the type but not the JSON payload. spike.py probes both `ev.tool_use.{id,name,input}` and the flattened `ev.{tool_use_id,name,input}` shape via the `_g` helper — if both miss, you'll see `! unexpected tool: None` in the log and need to print the raw event.
2. **Field name when posting the tool result.** spike.py uses `custom_tool_use_id` (per `managed-agents-api-reference.md`); if the actual field is `tool_use_id`, the API will return a 422 and the agent will never see the result.
3. **`events.send` signature.** TypeScript example is `send(sessionId, {events})`. Python convention is `send(session_id=..., events=[...])`, which spike.py uses; if the SDK actually uses positional + body dict, swap accordingly.
4. **`events.list` pagination.** spike.py reads everything in one call (`limit=1000`). For long-running sessions you'd want a cursor; not needed for this spike.

## Recommendation

**Fill in after running.** Decision matrix for the larger project:

- **PASS:** agent-creates-agent IS viable via host-mediated custom tools. Build the larger project on this pattern. Note that you will need a long-running host process (or a webhook/SSE listener) to handle `agent.custom_tool_use` events out-of-band; "fire and forget" sessions that need to spawn agents won't work without one.
- **FAIL:** stop and re-evaluate. The two follow-up paths are (a) MCP server proxy — spin up an MCP server that wraps `agents.create()` and put its credentials in a vault, or (b) wait for Anthropic to expose a built-in agent-management tool.

## Cost so far

- Spike not yet run: $0.
- Expected on first successful run: ≤ $0.05 (one ~30s session at $0.08/hr is < $0.001; the Opus turn handling the tool call is the dominant cost — likely a few cents).
