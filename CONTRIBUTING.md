# Contributing an experiment

Thanks for helping! Every contribution is an experiment on `controller.py`: one idea, measured
on the same frozen simulator and scoring as everyone else's.

## Rules

1. **Change only `controller.py`.** The simulator, data, scorer and tools are hash-checked
   (`FROZEN.sha256`); the GitHub check rejects a pull request that touches anything else.
2. **Follow the [ATC manual](https://infiniteflight.com/guide/atc-manual) and Infinite Flight's
   command menus** (`atc/commands.py`). Cite the manual section your change relies on in the
   pull request title or description.
3. **Procedures first:** aircraft fly their STARs, SIDs and published holds and are cleared for
   approaches on their flight plan; give headings only when needed (delay vectors, missed
   approaches, separation). Prefer vertical separation. See [`program.md`](program.md).
4. **Deterministic, standard library only.** No randomness, no wall clock, no network, no
   reading files outside the repo, no tricks that detect the scenario seed.
5. **One idea per pull request**, described in a sentence.

## Fastest way

`tools/join.sh` (see the README) sets everything up and can run an agent for you. Agents and
people follow [`collab.md`](collab.md): `tools/collab.py start "idea"`, edit, `tools/collab.py
finish`, and `tools/collab.py submit` when you beat the upstream best. The steps below are the
same thing by hand.

## Workflow

1. Fork, clone, and run `python3 evaluate.py --split smoke` to check your setup (Python 3.14).
2. Iterate locally with `tools/experiment.py start "..."` / `finish` (keeps or reverts with the
   official rule against your local best; history goes to `results.jsonl`, which is not
   committed).
3. Get the latest best: `git fetch upstream && git checkout upstream/main -- leaderboard/`.
4. `python3 tools/submit.py` — scores your controller and compares it with `leaderboard/best.json`.
5. Open a pull request against `main`.

## What happens to your pull request

1. **evaluate** (GitHub Actions) checks that only `controller.py` changed, runs the training
   scenarios and applies the rule in [`tools/verdict.py`](tools/verdict.py). It runs your code
   with no secrets and a read-only token.
2. **comment** posts the scores on your pull request: ✅ beats the current best, or ❌ with the
   reason.
3. If it beats the best, **holdout** scores it again in a sandbox (no network, no secrets) and
   on held-out scenarios that nobody has seen; they are derived from a secret and change every
   week. It compares your controller with `main` on the same scenarios and posts the result.
   It is labelled `ready-for-review`, and a maintainer reviews the code and merges it if it is better on both (or it is merged
   automatically, if the maintainers have turned that on). `controller.py` may only import
   `math`, `heapq` and a few other pure modules plus the `atc` package, and may not change the
   simulator's data (`tools/ci_guard.py`).
4. After the merge, **leaderboard** re-scores `main` and records it in `leaderboard/` as the new
   best — the next experiment has to beat yours. It also shows up on <https://atc.kyleboas.com>.

Local numbers can differ slightly from CI's (floating point differs between machines); the
official numbers are CI's.
