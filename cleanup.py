#!/usr/bin/env python3
"""
cleanup.py - archive/delete every Managed Agents resource named with "spike-".

Agents have no delete endpoint (only archive); environments support delete.
Sessions get deleted too if their title starts with "spike-".

Run AFTER spike.py. Idempotent.
"""
from __future__ import annotations

import os
import sys
from typing import Any

from dotenv import load_dotenv
import anthropic


PREFIX = "spike-"


def _g(obj: Any, name: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _starts(obj: Any, field: str) -> bool:
    v = _g(obj, field) or ""
    return isinstance(v, str) and v.startswith(PREFIX)


def main() -> None:
    load_dotenv()
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY missing.", file=sys.stderr)
        sys.exit(1)

    client = anthropic.Anthropic()

    # Sessions first (so they don't pin agents/envs).
    print("[sessions]")
    try:
        for s in client.beta.sessions.list(limit=100):
            if _starts(s, "title"):
                sid = _g(s, "id")
                print(f"  delete session {sid} title={_g(s, 'title')!r}")
                try:
                    client.beta.sessions.delete(sid)
                except Exception as e:  # noqa: BLE001
                    print(f"    ! {e}")
    except Exception as e:  # noqa: BLE001
        print(f"  ! list sessions failed: {e}")

    print("[agents]")
    try:
        for a in client.beta.agents.list(limit=100):
            if _starts(a, "name"):
                aid = _g(a, "id")
                print(f"  archive agent {aid} name={_g(a, 'name')!r}")
                try:
                    client.beta.agents.archive(aid)
                except Exception as e:  # noqa: BLE001
                    print(f"    ! {e}")
    except Exception as e:  # noqa: BLE001
        print(f"  ! list agents failed: {e}")

    print("[environments]")
    try:
        for env in client.beta.environments.list(limit=100):
            if _starts(env, "name"):
                eid = _g(env, "id")
                print(f"  delete environment {eid} name={_g(env, 'name')!r}")
                try:
                    client.beta.environments.delete(eid)
                except Exception as e:  # noqa: BLE001
                    print(f"    ! {e}")
    except Exception as e:  # noqa: BLE001
        print(f"  ! list environments failed: {e}")

    print("done.")


if __name__ == "__main__":
    main()
