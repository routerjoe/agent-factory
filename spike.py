#!/usr/bin/env python3
"""
spike.py - 2-hour verification: can a Managed Agent create another Managed Agent?

Approach (chosen by the user): host-mediated custom tool.

Agent A is configured with a single custom tool, `create_managed_agent`. When
Claude inside Agent A's session decides to invoke it, the session emits an
`agent.custom_tool_use` event and idles. THIS script (the host) intercepts that
event, calls `client.beta.agents.create()` itself, and posts the result back
into the session as a `user.custom_tool_result`. The agent never sees the
master API key. This is the same pattern a production "Foundry"-style system
would use; if it works, the foundational architecture is viable.

Run: python3 spike.py
Cleanup afterward: python3 cleanup.py

Hard caps to keep the spike cheap:
  - Total agents created: 2 (Agent A + spike-child). Cap is 3.
  - Session timeout: 5 minutes.
  - All resources are prefixed with "spike-" for easy cleanup.
"""
from __future__ import annotations

import json
import os
import sys
import time
from typing import Any

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


def _g(obj: Any, name: str, default: Any = None) -> Any:
    """Read a field from a Pydantic model OR a dict."""
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _gp(obj: Any, *path: str, default: Any = None) -> Any:
    cur = obj
    for p in path:
        cur = _g(cur, p)
        if cur is None:
            return default
    return cur


def _fail(msg: str) -> None:
    print("\n=== FAIL ===")
    print(msg)
    sys.exit(1)


def main() -> None:
    t0 = time.time()
    load_dotenv()
    if not os.environ.get("ANTHROPIC_API_KEY"):
        _fail("ANTHROPIC_API_KEY missing. Copy .env.example to .env and fill it in.")

    client = anthropic.Anthropic()

    # ---- 1. environment ----------------------------------------------------
    print("[1/6] Creating environment (unrestricted egress)...")
    env = client.beta.environments.create(
        name=f"{PREFIX}env",
        config={"type": "cloud", "networking": {"type": "unrestricted"}},
    )
    env_id = _g(env, "id")
    print(f"      env_id={env_id}")

    # ---- 2. agent A --------------------------------------------------------
    print("[2/6] Creating Agent A (meta-agent)...")
    custom_tool = {
        "type": "custom",
        "name": TOOL_NAME,
        "description": (
            "Create a new Managed Agent in this Anthropic account. "
            "Returns the new agent's ID as JSON: {\"agent_id\": \"agent_...\"}."
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
    agent_a_id = _g(agent_a, "id")
    print(f"      agent_a_id={agent_a_id}")

    # ---- 3. session --------------------------------------------------------
    print("[3/6] Creating session...")
    session = client.beta.sessions.create(
        agent=agent_a_id,
        environment_id=env_id,
        title=f"{PREFIX}session",
    )
    session_id = _g(session, "id")
    print(f"      session_id={session_id}")

    # ---- 4. send the test message ------------------------------------------
    print("[4/6] Sending test message...")
    client.beta.sessions.events.send(
        session_id=session_id,
        events=[
            {
                "type": "user.message",
                "content": [{"type": "text", "text": TEST_MESSAGE}],
            }
        ],
    )

    # ---- 5. poll, handle the custom-tool callback, capture final text ------
    print("[5/6] Polling session events...")
    seen: set[str] = set()
    final_text: str | None = None
    child_agent_id: str | None = None
    handled_tool_call = False
    deadline = time.time() + SESSION_TIMEOUT_S
    done = False

    while not done and time.time() < deadline:
        page = client.beta.sessions.events.list(session_id=session_id, limit=1000)
        events = list(_g(page, "data") or [])

        for ev in events:
            ev_id = _g(ev, "id") or repr(ev)
            if ev_id in seen:
                continue
            seen.add(ev_id)
            ev_type = _g(ev, "type")
            print(f"      <- {ev_type}")

            if ev_type == "agent.custom_tool_use" and not handled_tool_call:
                # Try several shapes the event might take.
                tu = _g(ev, "tool_use") or _g(ev, "custom_tool_use") or ev
                tu_id = _g(tu, "id") or _g(ev, "tool_use_id") or _g(ev, "custom_tool_use_id")
                tu_name = _g(tu, "name") or _g(ev, "name")
                tu_input = _g(tu, "input") or _g(ev, "input") or {}
                if tu_name != TOOL_NAME:
                    print(f"      ! unexpected tool: {tu_name}")
                    continue

                req_name = (tu_input or {}).get("name", "child")
                child_name = req_name if req_name.startswith(PREFIX) else f"{PREFIX}{req_name}"
                child_system = (tu_input or {}).get("system_prompt", "")
                print(f"      -> host: creating child agent '{child_name}'")
                child = client.beta.agents.create(
                    name=child_name,
                    model=MODEL,
                    system=child_system,
                )
                child_agent_id = _g(child, "id")
                print(f"      -> child_agent_id={child_agent_id}")

                client.beta.sessions.events.send(
                    session_id=session_id,
                    events=[
                        {
                            "type": "user.custom_tool_result",
                            "custom_tool_use_id": tu_id,
                            "content": [
                                {
                                    "type": "text",
                                    "text": json.dumps({"agent_id": child_agent_id}),
                                }
                            ],
                        }
                    ],
                )
                handled_tool_call = True

            elif ev_type == "agent.message":
                content = _g(ev, "content") or _gp(ev, "message", "content") or []
                if isinstance(content, list):
                    for blk in content:
                        if _g(blk, "type") == "text":
                            t = _g(blk, "text")
                            if t:
                                final_text = t

            elif ev_type == "session.status_idle":
                if handled_tool_call:
                    done = True
                    break

            elif ev_type in ("session.status_terminated", "session.error"):
                _fail(f"Session ended unexpectedly: {ev_type}\nevent={ev!r}")

        if not done:
            time.sleep(POLL_INTERVAL_S)

    if not done:
        _fail(f"Timed out after {SESSION_TIMEOUT_S}s waiting for session to idle.")

    # ---- 6. verify ---------------------------------------------------------
    print("[6/6] Verifying child via agents.list()...")
    listing = client.beta.agents.list(limit=1000)
    agents = list(_g(listing, "data") or [])
    matched = [a for a in agents if _g(a, "name") == CHILD_NAME]
    elapsed = time.time() - t0

    print()
    print(f"  agent_a_id     = {agent_a_id}")
    print(f"  child_agent_id = {child_agent_id}")
    print(f"  matched in list: {[_g(a, 'id') for a in matched]}")
    print(f"  agent A final text: {final_text!r}")
    print(f"  elapsed: {elapsed:.1f}s")

    if matched and child_agent_id and any(_g(a, "id") == child_agent_id for a in matched):
        print("\n=== PASS ===")
        print("Agent A successfully drove creation of spike-child via the")
        print("host-mediated custom tool, and spike-child appears in agents.list().")
        print("\nRun `python3 cleanup.py` to archive/delete spike-* resources.")
    else:
        _fail("spike-child not found in agents.list() under expected ID.")


if __name__ == "__main__":
    main()
