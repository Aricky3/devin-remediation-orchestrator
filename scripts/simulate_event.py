#!/usr/bin/env python3
"""Drive the orchestrator with synthetic events (no GitHub / no ACUs needed).

Intended for demos and CI: point it at a running orchestrator started with
DRY_RUN=true and it will inject a few synthetic "remediation" issues, then the
orchestrator's simulator advances them to completed with fake PRs.

    python -m scripts.simulate_event --base-url http://localhost:8000 --count 4
"""

from __future__ import annotations

import argparse
import json
import urllib.request

SAMPLE = [
    ("Add explicit timeouts to outbound HTTP requests", "Bandit B113: requests.* calls without timeout."),
    ("Bump vulnerable dependency flagged by pip-audit", "Upgrade the flagged package to a patched version."),
    ("Remove unused imports and fix lint violations", "ruff/flake8 findings in a small utility module."),
    ("Fix typos in documentation", "Several typos in docs and docstrings."),
    ("Replace assert used for runtime validation", "Asserts are stripped under -O; use explicit checks."),
]


def post(url: str, payload: dict) -> dict:
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://localhost:8000")
    ap.add_argument("--count", type=int, default=4)
    ap.add_argument("--start", type=int, default=9001, help="starting synthetic issue number")
    args = ap.parse_args()

    for i in range(args.count):
        title, body = SAMPLE[i % len(SAMPLE)]
        num = args.start + i
        res = post(f"{args.base_url}/api/simulate/issue", {"number": num, "title": title, "body": body})
        task = res.get("task", {})
        print(f"injected issue #{num}: {title} -> task state={task.get('state')} session={task.get('session_url')}")
    print(f"\nInjected {args.count} synthetic issues. Watch the dashboard at {args.base_url}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
