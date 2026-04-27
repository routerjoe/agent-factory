#!/usr/bin/env python3
"""
spike_1b.py - Phase 1B resilience spike. Four tests:

  T1 IDLE BILLING: sample stats.{active_seconds,duration_seconds} at
                   t=0/15/60 min from session entering requires_action;
                   t=240 captured by background poller (see end of script).
  T2 IDEMPOTENCY:  post user.custom_tool_result twice for the same id;
                   then post a DIFFERENT result for the same id.
  T3 SESSION TIMEOUT: leave a session in requires_action for ~60 min,
                      sample status periodically, document any timeout.
  T4 MID-FLIGHT CANCEL: trigger tool_use, host calls real agents.create
                        for spike1b-orphan-child, then DELETE parent
                        session BEFORE posting the result. Verify the
                        child still exists.

Resources (4 agents, hard cap):
  - spike1b-billing-test  (Tests 1 and 2; explicit "or" in spec)
  - spike1b-timeout-test  (Test 3)
  - spike1b-cancel-test   (Test 4 parent)
  - spike1b-orphan-child  (created during Test 4 by the host)

Cost cap $5; expected $0.20-$0.60.

Outputs:
  spike1b_results.json      - structured per-test results
  spike1b_events.jsonl      - every observed session event (forensics)
  spike1b_t240_pending.json - handoff to the background t=240 poller
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
import anthropic


PREFIX = "spike1b-"
MODEL = "claude-opus-4-7"
TOOL_NAME = "create_managed_agent"
SYSTEM = (
    "You are a meta-agent. When asked to create a new agent, "
    f"use the {TOOL_NAME} tool to do so and return the new agent's ID. "
    "Be terse."
)
TOOL = {
    "type": "custom",
    "name": TOOL_NAME,
    "description": (
        "Create a new Managed Agent in this Anthropic account. "
        "Returns the new agent's ID as JSON."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "system_prompt": {"type": "string"},
        },
        "required": ["name", "system_prompt"],
    },
}

ROOT = Path(__file__).resolve().parent
RESULTS_PATH = ROOT / "spike1b_results.json"
EVENTS_PATH = ROOT / "spike1b_events.jsonl"
T240_PENDING = ROOT / "spike1b_t240_pending.json"

# Sample offsets for Test 1, in seconds, that fit within the spike window.
T1_INSPIKE_OFFSETS_S = [15 * 60, 60 * 60]
T1_OFFLINE_OFFSET_S = 240 * 60  # captured by the background poller

# Test 3: poll the timeout-test session every minute for ~60 min.
T3_DURATION_S = 60 * 60
T3_POLL_INTERVAL_S = 60

# Hard timeouts that protect us if something hangs.
TRIGGER_TIMEOUT_S = 90
MIN_BETWEEN_SAMPLES_S = 5


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def log_event(test: str, ev) -> None:
    try:
        payload = ev.model_dump(mode="json") if hasattr(ev, "model_dump") else dict(ev)
    except Exception as e:  # noqa: BLE001
        payload = {"_dump_error": str(e), "_repr": repr(ev)}
    payload["_test"] = test
    payload["_logged_at"] = now_iso()
    with EVENTS_PATH.open("a") as f:
        f.write(json.dumps(payload) + "\n")


def trigger_tool_use(client, agent_id: str, env_id: str, prompt: str, test_label: str):
    """Create a session, send `prompt`, poll until requires_action with one
    custom_tool_use queued, return (session_id, tool_use_event_id, tool_input)."""
    session = client.beta.sessions.create(
        agent=agent_id,
        environment_id=env_id,
        title=f"{PREFIX}{test_label}-session",
    )
    client.beta.sessions.events.send(
        session_id=session.id,
        events=[
            {
                "type": "user.message",
                "content": [{"type": "text", "text": prompt}],
            }
        ],
    )
    seen: set[str] = set()
    pending: dict[str, tuple[str, dict]] = {}
    deadline = time.time() + TRIGGER_TIMEOUT_S
    while time.time() < deadline:
        for ev in client.beta.sessions.events.list(session.id, order="asc"):
            if ev.id in seen:
                continue
            seen.add(ev.id)
            log_event(test_label, ev)
            if ev.type == "agent.custom_tool_use":
                pending[ev.id] = (ev.name, ev.input)
            elif ev.type == "session.status_idle":
                if ev.stop_reason.type == "requires_action":
                    if not ev.stop_reason.event_ids:
                        continue
                    # take the first pending tool_use_id we know about
                    for tu_id in ev.stop_reason.event_ids:
                        if tu_id in pending:
                            return session.id, tu_id, pending[tu_id]
                if ev.stop_reason.type == "end_turn":
                    raise RuntimeError(
                        f"Session ended without triggering tool: {test_label}"
                    )
        time.sleep(2)
    raise RuntimeError(
        f"Timed out waiting for tool_use trigger in {test_label} ({TRIGGER_TIMEOUT_S}s)"
    )


def sample_session(client, session_id: str) -> dict:
    s = client.beta.sessions.retrieve(session_id)
    return {
        "sampled_at": now_iso(),
        "status": s.status,
        "duration_seconds": s.stats.duration_seconds,
        "active_seconds": s.stats.active_seconds,
        "input_tokens": s.usage.input_tokens,
        "output_tokens": s.usage.output_tokens,
        "cache_read_input_tokens": s.usage.cache_read_input_tokens,
        "updated_at": s.updated_at.isoformat() if s.updated_at else None,
    }


def post_tool_result(client, session_id: str, tu_id: str, payload: dict, is_error: bool = False):
    """Post a user.custom_tool_result. Returns (ok, response_or_exception)."""
    try:
        resp = client.beta.sessions.events.send(
            session_id=session_id,
            events=[
                {
                    "type": "user.custom_tool_result",
                    "custom_tool_use_id": tu_id,
                    "content": [{"type": "text", "text": json.dumps(payload)}],
                    "is_error": is_error,
                }
            ],
        )
        return True, resp
    except Exception as e:  # noqa: BLE001
        return False, e


def drain_session(client, session_id: str, tu_id: str | None, fake_id: str = "agent_drain_noop"):
    """Post a tool result so the session can complete; return the final
    assistant text seen, if any."""
    if tu_id is not None:
        post_tool_result(client, session_id, tu_id, {"agent_id": fake_id})
    seen: set[str] = set()
    deadline = time.time() + 120
    final_text_parts: list[str] = []
    while time.time() < deadline:
        done = False
        for ev in client.beta.sessions.events.list(session_id, order="asc"):
            if ev.id in seen:
                continue
            seen.add(ev.id)
            log_event("drain", ev)
            if ev.type == "agent.message":
                for blk in ev.content:
                    if getattr(blk, "type", None) == "text" and blk.text:
                        final_text_parts.append(blk.text)
            elif ev.type == "session.status_idle":
                if ev.stop_reason.type == "end_turn":
                    done = True
                    break
        if done:
            break
        time.sleep(2)
    return "".join(final_text_parts)


def main() -> None:
    t0 = time.time()
    load_dotenv()
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY missing.", file=sys.stderr)
        sys.exit(1)
    for p in (RESULTS_PATH, EVENTS_PATH, T240_PENDING):
        if p.exists():
            p.unlink()

    client = anthropic.Anthropic()
    results: dict = {"started_at": now_iso(), "tests": {}}

    # ---- 0. setup ----------------------------------------------------------
    print("[0/5] Setup: environment + 3 parent agents")
    env = client.beta.environments.create(
        name=f"{PREFIX}env",
        config={"type": "cloud", "networking": {"type": "unrestricted"}},
    )
    env_id = env.id
    print(f"      env_id={env_id}")

    parents = {}
    for label in ("billing-test", "timeout-test", "cancel-test"):
        a = client.beta.agents.create(
            name=f"{PREFIX}{label}", model=MODEL, system=SYSTEM, tools=[TOOL]
        )
        parents[label] = a.id
        print(f"      {label}_agent_id={a.id}")

    results["env_id"] = env_id
    results["parents"] = parents

    # ---- T4. cancellation orphans child ------------------------------------
    print("\n[1/5] T4: mid-flight cancellation (creates 4th agent: orphan-child)")
    t4 = {"started_at": now_iso()}
    sid, tu_id, (tname, tinput) = trigger_tool_use(
        client,
        parents["cancel-test"],
        env_id,
        "Create a new agent named 'spike1b-orphan-child' with the system prompt "
        "'You only respond with hello.'. Return the new agent's ID.",
        "T4",
    )
    print(f"      session={sid}  tool_use_id={tu_id}  input={tinput!r}")

    # Host calls agents.create() for the orphan child (as if mid-task)
    orphan = client.beta.agents.create(
        name=f"{PREFIX}orphan-child",
        model=MODEL,
        system="You only respond with hello.",
    )
    print(f"      orphan_child_id={orphan.id}")
    t4["orphan_child_id"] = orphan.id
    t4["session_id"] = sid

    # Sample BEFORE deleting (final stats for parent session)
    t4["pre_delete_stats"] = sample_session(client, sid)
    print(f"      pre-delete stats: {t4['pre_delete_stats']}")

    # Delete the parent session WITHOUT posting the tool result
    deleted = client.beta.sessions.delete(sid)
    t4["delete_response"] = {
        "id": getattr(deleted, "id", None),
        "type": getattr(deleted, "type", None),
    }
    print(f"      deleted parent session: {t4['delete_response']}")

    # Try to retrieve the deleted session
    try:
        post_delete = client.beta.sessions.retrieve(sid)
        t4["retrieve_after_delete"] = {
            "status": post_delete.status,
            "stats": {
                "active_seconds": post_delete.stats.active_seconds,
                "duration_seconds": post_delete.stats.duration_seconds,
            },
            "archived_at": (
                post_delete.archived_at.isoformat() if post_delete.archived_at else None
            ),
        }
    except Exception as e:  # noqa: BLE001
        t4["retrieve_after_delete"] = {"error": repr(e)}
    print(f"      retrieve after delete: {t4['retrieve_after_delete']}")

    # Verify orphan still exists in agents.list
    listed = [a for a in client.beta.agents.list(limit=100) if a.name == f"{PREFIX}orphan-child"]
    t4["orphan_still_listed"] = bool(listed)
    t4["orphan_listed_ids"] = [a.id for a in listed]
    print(f"      orphan in list: {t4['orphan_still_listed']} {t4['orphan_listed_ids']}")
    t4["finished_at"] = now_iso()
    results["tests"]["T4_cancellation"] = t4

    # ---- T1 + T3 idle sessions kicked off in parallel ----------------------
    print("\n[2/5] Starting T1 (billing) and T3 (timeout) idle sessions...")
    sid_t1, tu_t1, _ = trigger_tool_use(
        client,
        parents["billing-test"],
        env_id,
        "Create a new agent named 'spike1b-fake-t1' with system prompt 'hi'.",
        "T1",
    )
    t1_start_wall = time.time()
    print(f"      T1 session={sid_t1}  tool_use={tu_t1}")

    sid_t3, tu_t3, _ = trigger_tool_use(
        client,
        parents["timeout-test"],
        env_id,
        "Create a new agent named 'spike1b-fake-t3' with system prompt 'hi'.",
        "T3",
    )
    t3_start_wall = time.time()
    print(f"      T3 session={sid_t3}  tool_use={tu_t3}")

    # Initial samples (t=0)
    t1 = {
        "session_id": sid_t1,
        "tool_use_id": tu_t1,
        "started_at": now_iso(),
        "samples": [{"label": "t=0", "offset_s": 0, **sample_session(client, sid_t1)}],
    }
    t3 = {
        "session_id": sid_t3,
        "tool_use_id": tu_t3,
        "started_at": now_iso(),
        "samples": [{"label": "t=0", "offset_s": 0, **sample_session(client, sid_t3)}],
    }

    # ---- T2 idempotency ----------------------------------------------------
    print("\n[3/5] T2: idempotency on a fresh billing-test session")
    t2 = {"started_at": now_iso()}
    sid2, tu2, _ = trigger_tool_use(
        client,
        parents["billing-test"],
        env_id,
        "Create a new agent named 'spike1b-fake-t2' with system prompt 'hi'.",
        "T2",
    )
    t2["session_id"] = sid2
    t2["tool_use_id"] = tu2
    print(f"      T2 session={sid2}  tool_use={tu2}")

    # First post — happy path
    ok1, r1 = post_tool_result(client, sid2, tu2, {"agent_id": "agent_t2_first"})
    t2["post_1"] = {"ok": ok1, "error": None if ok1 else repr(r1)}
    print(f"      post #1: ok={ok1}")
    time.sleep(1)

    # Duplicate post — same id, same content
    ok2, r2 = post_tool_result(client, sid2, tu2, {"agent_id": "agent_t2_first"})
    t2["post_2_duplicate"] = {"ok": ok2, "error": None if ok2 else repr(r2)}
    print(f"      post #2 duplicate: ok={ok2} err={None if ok2 else repr(r2)[:200]}")
    time.sleep(1)

    # Different content for same id
    ok3, r3 = post_tool_result(client, sid2, tu2, {"agent_id": "agent_t2_DIFFERENT"})
    t2["post_3_different_content_same_id"] = {
        "ok": ok3,
        "error": None if ok3 else repr(r3),
    }
    print(f"      post #3 different content: ok={ok3} err={None if ok3 else repr(r3)[:200]}")

    # Drain the session and read the final assistant text — does it reflect
    # the first content, the third content, or something else?
    t2["final_text"] = drain_session(client, sid2, None)
    t2["finished_at"] = now_iso()
    print(f"      T2 final text: {t2['final_text']!r}")
    results["tests"]["T2_idempotency"] = t2

    # ---- T1 + T3 in-spike sampling ----------------------------------------
    print("\n[4/5] Sampling T1 and T3 every minute (T3) and at 15/60 min (T1)...")
    next_t1_idx = 0
    next_t3_sample_at = t3_start_wall + T3_POLL_INTERVAL_S
    end_at = max(
        t1_start_wall + T1_INSPIKE_OFFSETS_S[-1],
        t3_start_wall + T3_DURATION_S,
    )

    while time.time() < end_at:
        now = time.time()
        if next_t1_idx < len(T1_INSPIKE_OFFSETS_S):
            target = t1_start_wall + T1_INSPIKE_OFFSETS_S[next_t1_idx]
            if now >= target:
                offset = T1_INSPIKE_OFFSETS_S[next_t1_idx]
                samp = sample_session(client, sid_t1)
                t1["samples"].append(
                    {"label": f"t={offset // 60}min", "offset_s": offset, **samp}
                )
                print(f"      T1 t={offset // 60}min  active={samp['active_seconds']:.2f}s  "
                      f"duration={samp['duration_seconds']:.2f}s  status={samp['status']}")
                # Persist incrementally so a crash doesn't lose data.
                results["tests"]["T1_idle_billing"] = t1
                RESULTS_PATH.write_text(json.dumps(results, indent=2))
                next_t1_idx += 1

        if now >= next_t3_sample_at:
            samp = sample_session(client, sid_t3)
            offset = int(now - t3_start_wall)
            t3["samples"].append(
                {"label": f"t={offset // 60}min{offset % 60}s", "offset_s": offset, **samp}
            )
            print(f"      T3 t={offset // 60}min  status={samp['status']}  "
                  f"active={samp['active_seconds']:.2f}s")
            results["tests"]["T3_session_timeout"] = t3
            RESULTS_PATH.write_text(json.dumps(results, indent=2))
            next_t3_sample_at = now + T3_POLL_INTERVAL_S

        time.sleep(MIN_BETWEEN_SAMPLES_S)

    # Final in-spike sample for T3
    samp = sample_session(client, sid_t3)
    offset = int(time.time() - t3_start_wall)
    t3["samples"].append(
        {"label": f"t={offset // 60}min", "offset_s": offset, **samp}
    )
    t3["finished_at_inspike"] = now_iso()
    t3["timed_out_within_window"] = samp["status"] in ("terminated",)
    print(f"      T3 final in-spike sample: status={samp['status']}")

    # Drain T3 (post the tool result, complete the session, capture final stats)
    t3["drain_text"] = drain_session(client, sid_t3, tu_t3)
    t3["post_drain_stats"] = sample_session(client, sid_t3)
    print(f"      T3 post-drain stats: {t3['post_drain_stats']}")
    results["tests"]["T3_session_timeout"] = t3

    # ---- T1 hand-off to t=240 background poller ---------------------------
    print("\n[5/5] Persisting T1 state and launching detached t=240 poller")
    t240_target = t1_start_wall + T1_OFFLINE_OFFSET_S
    pending = {
        "session_id": sid_t1,
        "tool_use_id": tu_t1,
        "started_at": t1["started_at"],
        "started_wall_s": t1_start_wall,
        "t240_target_wall_s": t240_target,
        "t240_target_iso": datetime.fromtimestamp(
            t240_target, tz=timezone.utc
        ).isoformat(),
        "results_path": str(RESULTS_PATH),
    }
    T240_PENDING.write_text(json.dumps(pending, indent=2))

    poller = ROOT / "spike1b_t240_poller.py"
    log_path = ROOT / "spike1b_t240_poller.log"
    # Detach completely so the poller survives this script ending.
    subprocess.Popen(
        ["python3", str(poller)],
        stdout=log_path.open("a"),
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
        cwd=str(ROOT),
    )
    print(f"      detached poller launched. log={log_path}")
    print(f"      will sample T1 at {pending['t240_target_iso']}, then drain.")

    results["tests"]["T1_idle_billing"] = t1
    results["finished_at"] = now_iso()
    results["elapsed_s"] = round(time.time() - t0, 1)
    RESULTS_PATH.write_text(json.dumps(results, indent=2))

    print(f"\nElapsed: {results['elapsed_s']}s")
    print(f"Results so far: {RESULTS_PATH}")
    print(f"Run cleanup_1b.py AFTER the t=240 poller completes "
          f"(check {log_path}).")


if __name__ == "__main__":
    main()
