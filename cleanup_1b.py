#!/usr/bin/env python3
"""
cleanup_1b.py - delete every Managed Agents resource named with "spike1b-".

Includes the orphan child created during Test 4. Uses limit=100 (the real cap
discovered in Phase 1A; the events doc claims 1000 but agents.list /
sessions.list reject anything above 100).

Idempotent.
"""
from __future__ import annotations

import os
import sys

from dotenv import load_dotenv
import anthropic


PREFIX = "spike1b-"


def main() -> None:
    load_dotenv()
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY missing.", file=sys.stderr)
        sys.exit(1)
    client = anthropic.Anthropic()

    print("[sessions]")
    try:
        for s in client.beta.sessions.list(limit=100):
            title = s.title or ""
            if title.startswith(PREFIX):
                print(f"  delete session {s.id} title={title!r}")
                try:
                    client.beta.sessions.delete(s.id)
                except Exception as e:  # noqa: BLE001
                    print(f"    ! {e}")
    except Exception as e:  # noqa: BLE001
        print(f"  ! list sessions failed: {e}")

    print("[agents]")
    try:
        for a in client.beta.agents.list(limit=100):
            if (a.name or "").startswith(PREFIX):
                print(f"  archive agent {a.id} name={a.name!r}")
                try:
                    client.beta.agents.archive(a.id)
                except Exception as e:  # noqa: BLE001
                    print(f"    ! {e}")
    except Exception as e:  # noqa: BLE001
        print(f"  ! list agents failed: {e}")

    print("[environments]")
    try:
        for env in client.beta.environments.list(limit=100):
            if (env.name or "").startswith(PREFIX):
                print(f"  delete environment {env.id} name={env.name!r}")
                try:
                    client.beta.environments.delete(env.id)
                except Exception as e:  # noqa: BLE001
                    print(f"    ! {e}")
    except Exception as e:  # noqa: BLE001
        print(f"  ! list environments failed: {e}")

    print("done.")


if __name__ == "__main__":
    main()
