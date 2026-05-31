#!/usr/bin/env python3
"""Part 1 helper: create the curated remediation issues on the watched repo.

Reads issue definitions from ``scripts/remediation_issues.json`` and creates each
one (idempotently) with the remediation label. Run from the project root:

    python -m scripts.create_issues            # uses .env / environment
    GITHUB_REPO=Aricky3/superset python -m scripts.create_issues
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Allow running as a plain script too.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings  # noqa: E402
from app.github_client import GitHubClient  # noqa: E402

ISSUES_FILE = Path(__file__).parent / "remediation_issues.json"


def main() -> int:
    settings = get_settings()
    if not settings.github_token:
        print("ERROR: GITHUB_TOKEN is required to create issues.", file=sys.stderr)
        return 1

    issues = json.loads(ISSUES_FILE.read_text())
    gh = GitHubClient(settings.github_token, settings.github_repo, settings.github_api_url)

    gh.ensure_label_exists(settings.remediate_label, "5319e7", "Queue for autonomous Devin remediation")

    created, skipped = 0, 0
    for spec in issues:
        title = spec["title"]
        existing = gh.find_open_issue_by_title(title)
        if existing:
            print(f"skip (exists): #{existing['number']} {title}")
            skipped += 1
            continue
        labels = spec.get("labels", []) + [settings.remediate_label]
        res = gh.create_issue(title, spec["body"], labels)
        print(f"created: #{res['number']} {title} -> {res['html_url']}")
        created += 1

    print(f"\nDone. created={created} skipped={skipped} repo={settings.github_repo}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
