"""The one rule that decides whether a controller beats the current best.

Used by tools/experiment.py (local loop), tools/submit.py (before you open a PR) and
tools/ci_score.py (the official check on every pull request).

A candidate beats the best only if ALL of these hold:
  - hard violations did not increase (separation, collision alarms, near misses, terrain),
    in total and in every runway configuration on its own (a gain in one configuration does not
    buy a loss in another);
  - approach and taxi violations of FAA JO 7110.65 (5-9 radar approaches: intercepts,
    approach clearance, transfer to tower; 3-7 taxi and ground movement) did not increase;
  - the score (lower is better) went down.
"""
MANUAL = ("5-9", "3-7")  # FAA JO 7110.65 paragraph prefixes

REASONS = {
    "hard": "more hard violations (separation, collision alarms, near misses, terrain) than the current best",
    "config": "more hard violations than the current best in a runway configuration",
    "manual": "more approach / taxi violations (FAA 5-9, 3-7) than the current best",
    "cost": "score is not lower than the current best",
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
    nb, bb = new.get("by_config") or {}, best.get("by_config") or {}
    if any(c in bb and v["hard"] > bb[c]["hard"] for c, v in nb.items()):
        fails.append("config")
    if manual(new) > manual(best):
        fails.append("manual")
    if not new["cost"] < best["cost"]:
        fails.append("cost")
    return not fails, fails
