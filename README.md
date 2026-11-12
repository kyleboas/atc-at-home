# ATC autoresearch

**An open, distributed experiment to build an AI air traffic controller that follows the
[Infinite Flight ATC manual](https://infiniteflight.com/guide/atc-manual) — at San Francisco
(KSFO), on real FAA procedures.** Anyone can run experiments on their own machine (by hand or
with their own AI agent), and every improvement that beats the current best on the shared,
frozen scoring becomes the new baseline for everyone. Think *ATC@home*.

- **Live dashboard:** <https://atc.kyleboas.com> — every experiment, the current best, replays.
- **Leaderboard:** [`leaderboard/`](leaderboard/) — `best.json` is the controller to beat.

> Not affiliated with or endorsed by Infinite Flight LLC. **Not for real-world navigation.**

## What is being simulated

- **KSFO, West Plan:** arrivals on 28L/28R (parallel approaches), departures on 01L/01R, which
  cross the 28s. Real FAA CIFP procedures (AIRAC 2611): every STAR, SID and ILS/RNAV approach,
  flown leg by leg including RNP curved (RF) legs and published holds; the real taxiway network
  (OpenStreetMap) and real NorCal minimum vectoring altitudes. See [`ksfo/README.md`](ksfo/README.md).
- **Realistic aircraft:** bank-limited turns, racetrack holds, ramped climbs and descents, 3°
  glidepaths with flare and touchdown, realistic taxi and pushback, wake categories, pilots
  with reaction delays and the occasional mistake.
- **Four positions, one controller:** Ground, Tower, Approach and Departure in
  [`controller.py`](controller.py), using only Infinite Flight's command menus
  ([`atc/commands.py`](atc/commands.py)).
- **Scored against the manual:** a rules monitor ([`atc/rules.py`](atc/rules.py)) cites the
  manual section for every violation — runway separation (3.1.1), parallel approaches
  (6.11.7–6.11.8), intercepts (6.11.3–6.11.5), handoffs (6.14), runway crossings (2.3), taxiing
  (2.2), radar separation (6.2), terrain (6.2.1) — plus delays and go-arounds.

## Join with one command

```bash
curl -fsSL https://raw.githubusercontent.com/kyleboas/atc-autoresearch/main/tools/join.sh | bash
```

It forks and clones the repo, checks the simulator, starts you from the current best and, if
[Claude Code](https://claude.com/claude-code) is installed, runs an agent for 10 experiments
(`tools/join.sh 30` for more). Needs `git`, the [GitHub CLI](https://cli.github.com) (logged in)
and Python 3.14. You pay for your own machine and AI usage.

Everyone's experiments are coordinated through issues: each one is claimed before it starts and
its numbers and diff are published when it ends, so nobody repeats a tried idea
([`collab.md`](collab.md), [`tools/collab.py`](tools/collab.py)). See who is trying what with
`python3 tools/collab.py status`, or on the [issues page](https://github.com/kyleboas/atc-autoresearch/issues).

## How to contribute an experiment by hand

```bash
git clone https://github.com/<you>/atc-autoresearch && cd atc-autoresearch   # your fork
python3 tools/fetch_manual.py          # optional: a local text copy of the ATC manual to search
python3 evaluate.py --split smoke      # quick sanity run (Python 3.14, standard library only)
python3 tools/diagnose.py --top 20     # what is failing, grouped by rule, position and flight leg
python3 run_demo.py --seed 3           # radio transcript + an HTML radar replay in out/
```

Then the loop (it is exactly what the AI agents do too — see [`program.md`](program.md)):

1. `python3 tools/experiment.py start "<idea, cite the manual section>"`
2. edit **`controller.py`** (the only file a submission may change)
3. `python3 tools/experiment.py finish` — scores the training scenarios and keeps or reverts
   your change with the official rule
4. when you have something better: `python3 tools/submit.py`, then open a pull request

Want an AI agent to do it? `tools/run_autoresearch.sh 30` runs Claude Code headless on your own
account and machine, following [`program.md`](program.md).

Details, rules and what happens to a pull request: [`CONTRIBUTING.md`](CONTRIBUTING.md).

## The rule that decides what counts as better

A controller beats the current best only if **all** of these hold
([`tools/verdict.py`](tools/verdict.py)):

1. no more **hard** violations (loss of separation, collision alarms, near misses, terrain);
2. no more **manual intercept / handoff / taxi** violations (6.11, 6.14, 2.2) — never traded away;
3. **lower cost** (hard × 100 + rule × 10 + service × 2 + go-arounds × 3 + delay minutes).

The official score is the one the GitHub check posts on your pull request. A pull request that
beats the best is then scored on held-out scenarios that change every week and are never
published, against `main` on the same scenarios, so tuning to the public training seeds
won't help.

## Infinite Flight resources

The simulator follows these; they are Infinite Flight's documents and are linked, not copied:

- [ATC Manual](https://infiniteflight.com/guide/atc-manual) — the rulebook every controller decision is checked against
- [ATC Guide](https://infiniteflight.com/guide/atc-guide) — how ATC works in Infinite Flight
- [Flying Guide](https://infiniteflight.com/guide/flying-guide) — the pilot side: procedures, approaches, ATC communication
- [Infinite Flight User Guide](https://infiniteflight.com/guide) — everything else
- [Infinite Flight ATC command charts](https://www.helpathand.nl/janpolet/infinite-flight-atc-commands/) by Jan Polet — the full controller and pilot menus that [`atc/commands.py`](atc/commands.py) implements
- [Infinite Flight Community](https://community.infiniteflight.com/) — IFATC, tutorials and discussion

## Repository layout

| Path | What | Contributors may change? |
|---|---|---|
| `controller.py` | the AI controller (Ground, Tower, Approach, Departure) | **yes — the only file** |
| `atc/` | simulator, flight model, procedures, commands, rules monitor, scenarios | no (hash-checked) |
| `ksfo/` | KSFO data (FAA CIFP, OSM, MVA) and a procedures map | no (hash-checked) |
| `evaluate.py`, `FROZEN.sha256` | the scorer and its integrity check | no |
| `tools/` | experiment loop, diagnosis, verification, submit, data builders | no |
| `leaderboard/` | `best.json` and the history of merged controllers (written by CI) | no |

## Licence

Code: [MIT](LICENSE). Data: FAA products are public domain; the taxiway network is © OpenStreetMap
contributors, ODbL. See [`NOTICE.md`](NOTICE.md).
