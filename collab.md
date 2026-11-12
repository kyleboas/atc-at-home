# Working together: protocol for contributors and their agents

Many people (and their AI agents) run experiments at once. To avoid repeating each other, every
experiment is announced and its result published as an issue on
[kyleboas/atc-at-home](https://github.com/kyleboas/atc-at-home/issues), all through
`tools/collab.py`. Read [`program.md`](program.md) first: it has the rules for `controller.py`.
This file only adds the shared part.

## The loop

1. **Look around.** `python3 tools/collab.py status` shows the current best, what others are
   trying right now, ideas waiting for someone, and recent results (kept, reverted, crashed).
   Read the recent results before choosing: a reverted idea is a finding too. Open an issue to
   read its diff and numbers (`gh issue view N --repo kyleboas/atc-at-home --comments`).
2. **Start from the best.** `python3 tools/collab.py pull-best` puts you on branch `exp` at
   upstream `main` and scores the baseline. Do this at the start and whenever `status` shows a
   newer best than yours, unless you have a kept change that beats it (submit that first).
3. **Claim one idea.** `python3 tools/collab.py start "what you will change (FAA 7110.65 paragraph)"`.
   It refuses ideas that are already claimed or were tried; pick something else rather than
   adding `--force`, unless yours really differs (say how in the description).
4. **Edit `controller.py` only**, following program.md.
5. **Finish.** `python3 tools/collab.py finish` scores the training scenarios, keeps or reverts
   your change, and publishes the numbers and the diff to the issue. Always finish what you
   start, including crashes; an unfinished claim expires after 45 minutes.
6. **Submit when you beat upstream.** If `finish` says the result beats the upstream best, run
   `python3 tools/collab.py submit`. It checks again and opens a pull request from your fork.
   CI scores it, then runs a held-out check on scenarios nobody has seen (they change weekly).
7. **Leave ideas you will not try** with `python3 tools/collab.py idea "..."`.

Then go back to 1.

## Etiquette

- One idea per experiment, described in one sentence that cites the FAA 7110.65 paragraph.
- No tuning to particular scenarios or seeds; the held-out check catches it anyway.
- Never edit anything but `controller.py`; never run `--split holdout`.
- Results are public, including your diffs. That is the point: others build on them.
- You pay for your own compute and AI usage. Stop when you want; nothing is lost.
