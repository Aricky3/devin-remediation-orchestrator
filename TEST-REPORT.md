# Live End-to-End Test Report — Devin Remediation Orchestrator

**Result: PASS.** All 4 curated Apache Superset issues were remediated end-to-end by real Devin API v3 organization sessions. Each issue produced a PR that closes it. 100% success rate, ~2.9 min median time-to-PR.

## What was tested

The full event-driven pipeline against the **real Devin v3 API** (not the dry-run simulator):

1. Orchestrator boots, reconciles the already-dispatched issue #3 session, and the periodic scanner picks up the remaining labeled issues.
2. For each issue, the orchestrator creates a Devin v3 org session with a structured-output contract, labels the issue `devin-in-progress`, and comments a tracking link.
3. Devin autonomously investigates `Aricky3/superset`, writes a minimal fix, and opens a PR with `Closes #N`.
4. The poller reconciles each session, derives `completed`, records the PR + ACUs, swaps the label to `devin-remediated`, and comments the PR link.
5. The dashboard and `/metrics` reflect the live state.

## Evidence

### Live dashboard — 4/4 completed, 100% success

![Live dashboard](https://app.devin.ai/attachments/e4e9da86-f254-4db6-8490-298db3b7dbb9/dashboard.png)

### A real Devin-authored fix (security PR #7: `yaml.Loader` → `yaml.safe_load`)

![PR #7 diff](https://app.devin.ai/attachments/ef116bab-d1d3-4356-b114-748fe62a9480/screenshot_07365b1c67014301a33d14ef120bdd3c.png)

## Results table

| Issue | Category | Devin session verdict | PR (closes issue) |
|---|---|---|---|
| [#1](https://github.com/Aricky3/superset/issues/1) deprecated `datetime.utcnow()` | code quality | completed | [superset#8](https://github.com/Aricky3/superset/pull/8) |
| [#2](https://github.com/Aricky3/superset/issues/2) unsafe `yaml.Loader` (Bandit B506) | security | completed | [superset#7](https://github.com/Aricky3/superset/pull/7) |
| [#4](https://github.com/Aricky3/superset/issues/4) "Github" → "GitHub" | docs | completed | [superset#6](https://github.com/Aricky3/superset/pull/6) |
| [#3](https://github.com/Aricky3/superset/issues/3) duplicated word "to to" | docs | completed | [superset#5](https://github.com/Aricky3/superset/pull/5) |

`/metrics` snapshot at end of run:

```json
{"totals": {"tasks": 4, "active": 0, "completed": 4, "failed": 0},
 "by_state": {"completed": 4, "failed": 0, ...},
 "success_rate": 1.0, "median_time_to_pr_minutes": 2.9, "total_acus_consumed": 0.0}
```

## Automated checks
- 22 unit tests (state machine, PR-URL parsing, dispatch/reconcile, metrics) — all pass.
- `ruff check` — clean.
- CI (GitHub Actions): `test` + `docker` smoke test — both green on the latest commit.

## Notes
- ACUs reported as 0.0 by the API for this take-home org (no metering observed), even though sessions did real work and opened PRs.
- Two correctness fixes were made once real API responses were available: the PR-URL parser (`pull_requests[].pr_url`) and the `derive_state` status mapping; both covered by new tests.
