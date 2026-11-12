# atc-at-home

**An open experiment to build an AI air traffic controller for San Francisco (KSFO) that
follows [FAA Order JO 7110.65](https://www.faa.gov/air_traffic/publications/atpubs/atc_html/)
and flies real FAA procedures.** Anyone can run experiments on their own machine, by hand or with an AI
agent. When someone's controller beats the current best on the shared scoring, it becomes the
starting point for everyone else.

- **Live dashboard:** <https://atc.kyleboas.com> shows every experiment, the current best and replays.
- **Leaderboard:** [`leaderboard/best.json`](leaderboard/) is the controller to beat.

## What is autoresearch?

The idea comes from Andrej Karpathy's [autoresearch](https://github.com/karpathy/autoresearch):
an AI agent improves a program on its own. It makes one small change, runs a fixed test, keeps
the change if the score improved and drops it if not, then tries something else. In Karpathy's
version the program is the training code for a small language model. Here it's the controller
(`controller.py`), and the score measures how safely and smoothly it runs KSFO under FAA
rules. Doing this as a group, with everyone building on the latest best, is borrowed from
[autoresearch-at-home](https://github.com/mutable-state-inc/autoresearch-at-home).

> Not affiliated with or endorsed by the FAA. **Not for real-world navigation.**

## What is being simulated

- **KSFO, West Plan.** Arrivals land on 28L/28R (parallel approaches) and departures leave from
  01L/01R, crossing the 28s. Procedures come from the FAA CIFP (AIRAC 2611): every STAR, SID and
  ILS/RNAV approach, flown leg by leg, including RNP curved (RF) legs and published holds. The
  taxiway network is from OpenStreetMap and the minimum vectoring altitudes are NorCal's. More in
  [`ksfo/README.md`](ksfo/README.md).
- **Aircraft.** Bank-limited turns, racetrack holds, gradual climbs and descents, 3° glidepaths
  with flare and touchdown, taxi and pushback, and wake categories. Pilots take a moment to
  respond and occasionally get things wrong. Arrivals carry limited holding fuel: they call
  minimum fuel, then Mayday fuel, and must be worked in with priority or diverted to Oakland.
- **One controller for four positions.** Ground, Tower, Approach and Departure all live in
  [`controller.py`](controller.py) and speak FAA phraseology
  ([`atc/commands.py`](atc/commands.py), each instruction citing its 7110.65 paragraph).
- **Scoring.** A rules monitor ([`atc/rules.py`](atc/rules.py)) flags each violation with its
  7110.65 paragraph: runway separation (3-9-6, 3-9-8, 3-10-3), runway crossings (3-1-3),
  taxiing (3-7), intercepts and approach clearances (5-9-1, 5-9-2, 5-9-4), parallel approaches
  (5-9-6), speed control (5-7), radar and wake separation (5-5-4), conflict alerts (5-14-6),
  minimum vectoring altitudes (5-6-3) and fuel emergencies (2-1-4, 10-2-5). Delays, go-arounds
  and diversions count too.

## Join with one command

```bash
curl -fsSL https://raw.githubusercontent.com/kyleboas/atc-at-home/main/tools/join.sh | bash
```

This forks and clones the repo, checks that the simulator runs and starts you from the current
best. If you have [Claude Code](https://claude.com/claude-code) installed, it also runs an agent
for 10 experiments (`tools/join.sh 30` for more). You need `git`, the
[GitHub CLI](https://cli.github.com) (logged in) and Python 3.14. Compute and AI usage are on
your own machine and account.

Experiments are coordinated through GitHub issues. You claim an idea before starting and post
the numbers and diff when you're done, so nobody repeats something that's already been tried
([`collab.md`](collab.md), [`tools/collab.py`](tools/collab.py)). Run
`python3 tools/collab.py status` or check the [issues page](https://github.com/kyleboas/atc-at-home/issues)
to see what people are working on.

## Contributing an experiment by hand

```bash
git clone https://github.com/<you>/atc-at-home && cd atc-at-home   # your fork
python3 tools/fetch_faa.py             # optional: a local text copy of FAA JO 7110.65 to search
python3 evaluate.py --split smoke      # quick sanity run (Python 3.14, standard library only)
python3 tools/diagnose.py --top 20     # what is failing, grouped by rule, position and flight leg
python3 run_demo.py --seed 3           # radio transcript + an HTML radar replay in out/
```

Each experiment follows the same steps the AI agents use ([`program.md`](program.md)):

1. `python3 tools/experiment.py start "<idea, cite the 7110.65 paragraph>"`
2. Edit **`controller.py`**. It's the only file a submission may change.
3. `python3 tools/experiment.py finish` scores the training scenarios, then keeps or reverts
   your change using the official rule.
4. Once you have an improvement, run `python3 tools/submit.py` and open a pull request.

To have an AI agent do this instead, `tools/run_autoresearch.sh 30` runs Claude Code headless
on your account and machine, following [`program.md`](program.md).

Rules, details and what happens to a pull request after you open it are in
[`CONTRIBUTING.md`](CONTRIBUTING.md).

## What counts as better

A controller beats the current best only if all three of these hold
([`tools/verdict.py`](tools/verdict.py)):

1. It has no more **hard** violations (loss of separation, collision alarms, near misses, terrain).
2. It has no more **approach or taxi** violations (7110.65 5-9 and 3-7: intercepts, approach
   clearances, transfer to tower, taxiing). Lowering cost doesn't make up for an increase here.
3. Its **cost** is lower, where cost = hard × 100 + rule × 10 + service × 2 + go-arounds × 3 +
   delay minutes (a diversion counts as 60), per 40 minutes of each scenario.

The official score is the one the GitHub check posts on your pull request. If a pull request
beats the best, it's then run against `main` on a set of held-out scenarios. Those change
every week and are never published, so overfitting to the public training seeds won't get a
change merged.

## FAA references

- [FAA Order JO 7110.65, Air Traffic Control](https://www.faa.gov/air_traffic/publications/atpubs/atc_html/):
  the rules and phraseology every controller decision is checked against
  (`python3 tools/fetch_faa.py` saves chapters 2, 3, 4, 5, 7 and 10 as text)
- [Aeronautical Information Manual](https://www.faa.gov/air_traffic/publications/atpubs/aim_html/): the pilot side
- [FAA CIFP](https://www.faa.gov/air_traffic/flight_info/aeronav/digital_products/cifp/): the procedures the aircraft fly

## Repository layout

| Path | What | Contributors may change? |
|---|---|---|
| `controller.py` | the AI controller (Ground, Tower, Approach, Departure) | **yes, and nothing else** |
| `atc/` | simulator, flight model, procedures, commands, rules monitor, scenarios | no (hash-checked) |
| `ksfo/` | KSFO data (FAA CIFP, OSM, MVA) and a procedures map | no (hash-checked) |
| `evaluate.py`, `FROZEN.sha256` | the scorer and its integrity check | no |
| `tools/` | experiment loop, diagnosis, verification, submit, data builders | no |
| `leaderboard/` | `best.json` and the history of merged controllers (written by CI) | no |

## Licence

Code: [MIT](LICENSE). Data: FAA products are public domain. The taxiway network is
© OpenStreetMap contributors, ODbL. See [`NOTICE.md`](NOTICE.md).
