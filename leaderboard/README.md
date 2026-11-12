# Leaderboard

Written by the **leaderboard** GitHub workflow after every merge to `main` — don't edit by hand.

- `best.json` — the scores of the current best controller (the one on `main`). A submission
  has to beat these under the rule in [`tools/verdict.py`](../tools/verdict.py).
- `results.jsonl` — one line per merged controller: commit, author, note, scores.
