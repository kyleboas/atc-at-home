"""The one rule that decides whether a controller beats the current best.

Used by tools/experiment.py (local loop), tools/submit.py (before you open a PR) and
tools/ci_score.py (the official check on every pull request).

A candidate beats the best only if ALL of these hold:
  - hard violations did not increase (separation, collision alarms, near misses, terrain);
  - manual intercept / handoff / taxi violations (6.11, 6.14, 2.2) did not increase;
  - cost went down.
"""
MANUAL = ("6.11", "6.14", "2.2")

REASONS = {
    "hard": "more hard violations (separation, collision alarms, near misses, terrain) than the current best",
    "manual": "more manual intercept / handoff / taxi violations (6.11, 6.14, 2.2) than the current best",
    "cost": "cost is not lower than the current best",
}


def manual(r):
    return sum(v for k, v in (r.get("violations_by_rule") or {}).items() if k.startswith(MANUAL))


def verdict(new, best):
    """(beats_best, [reason codes for failing])."""
    if not best:
        return True, []
    fails = []
    if new["hard"] > best["hard"]:
        fails.append("hard")
    if manual(new) > manual(best):
        fails.append("manual")
    if not new["cost"] < best["cost"]:
        fails.append("cost")
    return not fails, fails
