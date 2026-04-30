#!/usr/bin/env python3
"""
spike.py - 2-hour verification: can a Managed Agent create another Managed Agent?

Approach (chosen): host-mediated custom tool.

Agent A is configured with one custom tool, `create_managed_agent`. When Claude
invokes it, the session emits an `agent.custom_tool_use` event and goes
`session.status_idle` with `stop_reason.type == "requires_action"`. THIS script
intercepts that, calls `client.beta.agents.create()` itself, and posts the
result back as a `user.custom_tool_result`. The master API key never enters
the sandbox.

Run: python3 spike.py
Cleanup afterward: python3 cleanup.py

Hard caps:
  - Total agents created: 2 (Agent A + spike-child). User cap is 3.
  - Session timeout: 5 minutes.
  - All resources prefixed with "spike-" for cleanup.

Event flow we expect (event types confirmed from anthropic 0.97.0 SDK types):
  send: user.message
   <- session.status_running
   <- agent.custom_tool_use         (id, name, input)
   <- session.status_idle           (stop_reason.type == "requires_action",
                                     stop_reason.event_ids == [tool_use.id])
  send: user.custom_tool_result     (custom_tool_use_id == that id)
   <- session.status_running
   <- agent.message                 (content: [TextBlock])
   <- session.status_idle           (stop_reason.type == "end_turn")  -> DONE
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
import anthropic


PREFIX = "spike-"
MODEL = "claude-opus-4-7"
TOOL_NAME = "create_managed_agent"
CHILD_NAME = f"{PREFIX}child"
CHILD_SYSTEM = "You only respond with the word hello."
TEST_MESSAGE = (
    f"Create a new agent named '{CHILD_NAME}' with the system prompt "
    f"'{CHILD_SYSTEM}'. Return the new agent's ID."
)
AGENT_A_SYSTEM = (
    "You are a meta-agent. When asked to create a new agent, "
    f"use the {TOOL_NAME} tool to do so and return the new agent's ID. "
    "Be terse."
)

SESSION_TIMEOUT_S = 300
POLL_INTERVAL_S = 2
EVENT_LOG_PATH = Path(__file__).resolve().parent / "spike_events.jsonl"


def fail(msg: str) -> None:
    print("\n=== FAIL ===")
    print(msg)
    sys.exit(1)


def log_event(ev) -> None:
    """Append every event we observe to spike_events.jsonl for forensics."""
    try:
        payload = ev.model_dump(mode="json") if hasattr(ev, "model_dump") else dict(ev)
    except Exception as e:  # noqa: BLE001
        payload = {"_dump_error": str(e), "_repr": repr(ev)}
    with EVENT_LOG_PATH.open("a") as f:
        f.write(json.dumps(payload) + "\n")


def main() -> None:
    t0 = time.time()
    load_dotenv()
    if not os.environ.get("ANTHROPIC_API_KEY"):
        fail("ANTHROPIC_API_KEY missing. Copy .env.example to .env and fill it in.")
    if EVENT_LOG_PATH.exists():
        EVENT_LOG_PATH.unlink()

    client = anthropic.Anthropic()

    # 1. environment
    print("[1/6] Creating environment (unrestricted egress)...")
    env = client.beta.environments.create(
        name=f"{PREFIX}env",
        config={"type": "cloud", "networking": {"type": "unrestricted"}},
    )
    print(f"      env_id={env.id}")

    # 2. agent A
    print("[2/6] Creating Agent A (meta-agent)...")
    custom_tool = {
        "type": "custom",
        "name": TOOL_NAME,
        "description": (
            "Create a new Managed Agent in this Anthropic account. "
            "Returns the new agent's ID as JSON."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Name for the new agent."},
                "system_prompt": {
                    "type": "string",
                    "description": "System prompt for the new agent.",
                },
            },
            "required": ["name", "system_prompt"],
        },
    }
    agent_a = client.beta.agents.create(
        name=f"{PREFIX}agent-a",
        model=MODEL,
        system=AGENT_A_SYSTEM,
        tools=[custom_tool],
    )
    print(f"      agent_a_id={agent_a.id}")

    # 3. session
    print("[3/6] Creating session...")
    session = client.beta.sessions.create(
        agent=agent_a.id,
        environment_id=env.id,
        title=f"{PREFIX}session",
    )
    print(f"      session_id={session.id}")

    # 4. test message
    print("[4/6] Sending test message...")
    client.beta.sessions.events.send(
        session_id=session.id,
        events=[
            {
                "type": "user.message",
                "content": [{"type": "text", "text": TEST_MESSAGE}],
            }
        ],
    )

    # 5. poll, handle the custom-tool callback, capture final assistant text
    print("[5/6] Polling session events...")
    seen: set[str] = set()
    pending_tool_calls: dict[str, tuple[str, dict]] = {}  # event_id -> (name, input)
    resolved_tool_calls: set[str] = set()
    child_agent_id: str | None = None
    final_text_parts: list[str] = []
    deadline = time.time() + SESSION_TIMEOUT_S
    done = False
    last_stop_reason: str | None = None

    while not done and time.time() < deadline:
        for ev in client.beta.sessions.events.list(session_id=session.id, order="asc"):
            if ev.id in seen:
                continue
            seen.add(ev.id)
            log_event(ev)
            print(f"      <- {ev.type}  (id={ev.id})")

            if ev.type == "agent.custom_tool_use":
                # Flat shape: ev.id (== custom_tool_use_id), ev.name, ev.input
                pending_tool_calls[ev.id] = (ev.name, ev.input)

            elif ev.type == "agent.message":
                for block in ev.content:
                    if getattr(block, "type", None) == "text" and block.text:
                        final_text_parts.append(block.text)

            elif ev.type == "session.status_idle":
                last_stop_reason = ev.stop_reason.type
                print(f"         stop_reason={last_stop_reason}")
                if last_stop_reason == "end_turn":
                    done = True
                    break
                if last_stop_reason == "retries_exhausted":
                    fail(f"Session retries exhausted. event={ev!r}")
                if last_stop_reason == "requires_action":
                    # Resolve every pending tool call listed in event_ids.
                    for tu_id in ev.stop_reason.event_ids:
                        if tu_id in resolved_tool_calls:
                            continue
                        if tu_id not in pending_tool_calls:
                            fail(
                                f"Session requires action on {tu_id} but we never "
                                f"saw the corresponding agent.custom_tool_use event."
                            )
                        name, tool_input = pending_tool_calls[tu_id]
                        if name != TOOL_NAME:
                            fail(f"Unexpected tool: {name!r} (input={tool_input!r})")

                        req_name = (tool_input or {}).get("name", "child")
                        child_name = (
                            req_name
                            if req_name.startswith(PREFIX)
                            else f"{PREFIX}{req_name}"
                        )
                        child_system = (tool_input or {}).get("system_prompt", "")
                        print(f"      -> host: creating child agent '{child_name}'")
                        child = client.beta.agents.create(
                            name=child_name,
                            model=MODEL,
                            system=child_system,
                        )
                        child_agent_id = child.id
                        print(f"      -> child_agent_id={child_agent_id}")

                        client.beta.sessions.events.send(
                            session_id=session.id,
                            events=[
                                {
                                    "type": "user.custom_tool_result",
                                    "custom_tool_use_id": tu_id,
                                    "content": [
                                        {
                                            "type": "text",
                                            "text": json.dumps({"agent_id": child.id}),
                                        }
                                    ],
                                }
                            ],
                        )
                        resolved_tool_calls.add(tu_id)

            elif ev.type == "session.status_terminated":
                fail(f"Session terminated: {ev!r}")
            elif ev.type == "session.error":
                fail(f"Session error: {ev!r}")

        if not done:
            time.sleep(POLL_INTERVAL_S)

    if not done:
        fail(
            f"Timed out after {SESSION_TIMEOUT_S}s. "
            f"last_stop_reason={last_stop_reason!r} "
            f"resolved={len(resolved_tool_calls)}/{len(pending_tool_calls)} "
            f"final_text={''.join(final_text_parts)!r}"
        )

    # 6. verify
    print("[6/6] Verifying child via agents.list()...")
    matched = [
        a for a in client.beta.agents.list() if a.name == CHILD_NAME
    ]
    elapsed = time.time() - t0
    final_text = "".join(final_text_parts)

    print()
    print(f"  agent_a_id     = {agent_a.id}")
    print(f"  child_agent_id = {child_agent_id}")
    print(f"  matched in list: {[a.id for a in matched]}")
    print(f"  agent A final text: {final_text!r}")
    print(f"  elapsed: {elapsed:.1f}s")
    print(f"  raw events: {EVENT_LOG_PATH}")

    if (
        matched
        and child_agent_id
        and any(a.id == child_agent_id for a in matched)
    ):
        print("\n=== PASS ===")
        print("Agent A drove creation of spike-child via the host-mediated")
        print("custom tool, and spike-child appears in agents.list().")
        print("\nRun `python3 cleanup.py` to archive/delete spike-* resources.")
    else:
        fail("spike-child not found in agents.list() under expected ID.")


if __name__ == "__main__":
    main()
