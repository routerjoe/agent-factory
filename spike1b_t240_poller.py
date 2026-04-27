#!/usr/bin/env python3
"""
spike_1b_t240_poller.py - detached process spawned by spike_1b.py.

Sleeps until the planned t=240 min sample for the T1 idle-billing test,
records stats.{active,duration}_seconds and usage on the still-idle session,
then drains the session by posting a fake user.custom_tool_result, captures
final stats, and appends everything to spike1b_results.json under
tests.T1_idle_billing.

Idempotent: safe to re-run; it just re-samples and re-drains.
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
import anthropic


ROOT = Path(__file__).resolve().parent
RESULTS_PATH = ROOT / "spike1b_results.json"
PENDING_PATH = ROOT / "spike1b_t240_pending.json"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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


def main() -> None:
    load_dotenv()
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY missing.", file=sys.stderr)
        sys.exit(1)
    if not PENDING_PATH.exists():
        print(f"no pending file at {PENDING_PATH}", file=sys.stderr)
        sys.exit(1)

    pending = json.loads(PENDING_PATH.read_text())
    print(f"[poller] pending={pending}")

    target_wall = float(pending["t240_target_wall_s"])
    sleep_s = max(0.0, target_wall - time.time())
    print(f"[poller] sleeping {sleep_s:.0f}s until t=240 target")
    time.sleep(sleep_s)

    client = anthropic.Anthropic()
    sid = pending["session_id"]
    tu_id = pending["tool_use_id"]

    print(f"[poller] sampling T1 session={sid} at {now_iso()}")
    samp_240 = sample_session(client, sid)
    print(f"[poller] t=240 sample: {samp_240}")

    # Drain
    print("[poller] posting fake tool result to drain")
    try:
        client.beta.sessions.events.send(
            session_id=sid,
            events=[
                {
                    "type": "user.custom_tool_result",
                    "custom_tool_use_id": tu_id,
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps(
                                {"agent_id": "agent_t240_drain_noop"}
                            ),
                        }
                    ],
                }
            ],
        )
    except Exception as e:  # noqa: BLE001
        print(f"[poller] drain post failed: {e!r}")

    # Wait briefly for end_turn so post-drain stats are stable
    deadline = time.time() + 90
    while time.time() < deadline:
        try:
            s = client.beta.sessions.retrieve(sid)
            if s.status in ("idle", "terminated"):
                # idle could be requires_action again? unlikely after drain
                break
        except Exception as e:  # noqa: BLE001
            print(f"[poller] retrieve during drain wait failed: {e!r}")
        time.sleep(5)
    samp_final = sample_session(client, sid)
    print(f"[poller] post-drain sample: {samp_final}")

    # Merge into RESULTS_PATH (preserve everything else)
    if RESULTS_PATH.exists():
        results = json.loads(RESULTS_PATH.read_text())
    else:
        results = {"tests": {}}
    t1 = results.setdefault("tests", {}).setdefault("T1_idle_billing", {})
    t1.setdefault("samples", []).append(
        {"label": "t=240min", "offset_s": 240 * 60, **samp_240}
    )
    t1["post_drain_sample"] = samp_final
    t1["t240_completed_at"] = now_iso()
    RESULTS_PATH.write_text(json.dumps(results, indent=2))

    print(f"[poller] DONE. results updated: {RESULTS_PATH}")


if __name__ == "__main__":
    main()
