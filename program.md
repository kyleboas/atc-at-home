# Autoresearch program: improve the KSFO AI air traffic controller

You are improving `controller.py`: one AI controller running four positions (Ground, Tower,
Approach, Departure) at San Francisco (KSFO) in the West Plan (arrivals 28L/28R, departures
01L/01R, which cross the 28s), on real FAA procedures (CIFP cycle 2611: STARs, SIDs, ILS/RNAV
approaches incl. RF legs, published holds), the real OSM taxiway network and the real NorCal
minimum vectoring altitudes. It must follow the Infinite Flight ATC manual
(https://infiniteflight.com/guide/atc-manual; `python3 tools/fetch_manual.py` saves a local text copy for searching) and use only the
Infinite Flight command menus as implemented in `atc/commands.py` (charts: https://www.helpathand.nl/janpolet/infinite-flight-atc-commands/). More Infinite
Flight documentation is linked in README.md.

Working with others? Also follow [`collab.md`](collab.md): it claims each idea and publishes each
result as an issue so contributors don't repeat each other.

## Rules

- Edit **only** `controller.py`. Never edit `atc/`, `ksfo/`, `evaluate.py`, `FROZEN.sha256`,
  `tools/`, `results.*` or `status.json`. The evaluator refuses to run if frozen files change.
- Never run `--split holdout`. Humans score that.
- Deterministic controller: no randomness, no wall clock.
- Imports: only `math`, `heapq`, `bisect`, `collections`, `itertools`, `functools`, `dataclasses`,
  `enum`, `typing`, `statistics`, `operator` and the `atc` package; no `getattr`/`eval`/file access,
  no dunder attributes, and never assign into `W.*`, `P.*` or `C.*` (checked by `tools/ci_guard.py`).
- Prefer general, manual-grounded logic over special cases for particular seeds.

## Manual rules that are enforced (and must not get worse)

- Procedures first: arrivals fly their filed STAR and the published holds. With an ILS/RNAV
  approach on the flight plan, clear it on the flight plan with an altitude until established
  (6.9.3 "continue as filed, expect ...", 6.10.2, 6.11.5) instead of vectoring. Approach,
  Departure and Center issue headings only when they have to: delay vectors (then "continue as
  filed", 6.9.5), missed approaches, radar-vector legs at the end of a STAR, or separation.
  Separate vertically (1000 ft, stacks, altitude caps) before reaching for headings.
- Intercepts (6.11.3-6.11.5): when vectoring to a localizer, ~30 degree intercept (never 10 or
  less), clear only once settled on the previous vector, altitude below the glideslope at the
  join point, held until established. Hand to tower only once established (6.14).
- Parallel approaches (6.11.7-6.11.8): aircraft for 28L and 28R are level and 1000 ft apart
  until both are established on their localizers.
- Runways (3.1.1): same-runway and intersecting-runway separation (the 01s cross the 28s,
  including extended centerlines), wake spacing on final and for departures.
- Runway crossings (2.3): refer to the runway in the direction in use; cross only when the
  conditions in 2.3.2 hold; anticipated crossings are encouraged (2.3.3).
- Taxiing (2.2): routes by taxiway name; avoid routes that meet opposite traffic (2.2.1).
- Only Infinite Flight commands (atc/commands.py): the IF ATC menus plus what the IFATC manual
  documents in the app (radar contact, drag & taxi, drag & vector approach clearances, holds at a
  fix, direct-to, go-around "fly runway heading"). Nothing else exists: no callsign in a give way,
  no hold short of a taxiway, no turn direction on a vector, sequencing says where the traffic to
  follow is (`follow="final"`), not who it is.
- Go around (3.5.4): `C.go_around(cs)` (fly runway heading) or
  `C.go_around(cs, then="left_traffic"|"right_traffic", runway="28L")`: a 1,500 ft circuit
  (observation `circuit`, legs `upwind`/`crosswind`/`downwind`/`base`). Approach is always open, so
  either way Tower hands the aircraft to Approach within 150 s; sequencing can then say
  `follow="left_downwind"` etc.
- Give way (2.2.1): Ground resolves taxi conflicts with `C.give_way(cs, to="left|right|ahead")` or
  `C.give_way(cs, to="exiting", runway="28L")`. The pilot picks the traffic on that side when the
  call is heard, stops short of its path and continues on their own once it has passed. When a new
  taxi route meets moving traffic, give way first, then the taxi clearance. `hold_position` to stop
  taxiing traffic counts as a 2.2.1 violation unless a collision is imminent (closest approach
  < ~120 ft within 25 s, not in trail). Observation fields: `give_way`, `giving_way_now`.
- Aircraft in the air have priority. Ground may hold departures at the gate (answer
  `request_pushback` with `hold_position`, release later with `pushback_approved`) when the
  airport is busy, so delay is absorbed at the gate rather than in the air, on taxiways or at the
  runway. A minute held at the gate costs a quarter of a minute of delay anywhere else.
- Radar: 3 nm / 1000 ft (6.2.2), MVA/terrain (6.2.1), speeds (6.15).

## Tower: anticipated separation (manual 3.1.2, 3.2.3)

Tower may clear the next departure for takeoff (or line it up) while the previous one is still
rolling, when the previous one will be airborne -- and past the wake time and the radar gap --
by the time the next one actually starts its roll. Separation is judged at the moment of the
roll / threshold crossing, which is exactly what the monitor checks. The same applies to landing
clearances. Experiment 17 (operator) tried this in `Tower.departure_ok` by projecting the leader
forward to the follower's predicted roll start: departure delay fell 79.9 -> 74.7 min/scenario
with no runway separation losses, but collision alarms at Approach rose 9 -> 12 and taxi
conflicts 45 -> 52, so the keep rule reverted it. Worth retrying together with Departure /
Approach / Ground changes that absorb the earlier departures.

## Loop (one experiment at a time)

1. Look at what is failing: `python3 tools/diagnose.py --top 20` groups violations by rule,
   position and flight legs, with a seed/time example. `python3 run_demo.py --seed N` prints a
   full radio transcript. `python3 tools/verify.py` checks procedure tracking.
2. Pick ONE hypothesis. Announce it before editing:
   `python3 tools/experiment.py start "<short description, cite the manual section>"`
3. Edit `controller.py`. Sanity check with `python3 evaluate.py --split smoke`.
4. Score and record it: `python3 tools/experiment.py finish`
   This runs the training scenarios (~35 s), KEEPS the change (git commit) only if hard
   violations did not increase, intercept/handoff/taxi rule violations (6.11, 6.14, 2.2) did
   not increase, and cost went down; otherwise it reverts `controller.py`. Never commit or
   revert yourself.
5. Append one line to `notes.md`: experiment number, kept/reverted, and what you learned.
6. Repeat.

Every start/finish shows up live on the dashboard (https://atc.kyleboas.com), so always call both.

## Priorities

1. Zero hard violations: runway separation (3.1.1), collision alarms (6.2.4), near misses,
   MVA/terrain (6.2.1).
2. Then rule violations (each cites its manual section).
3. Then go-arounds and delay.
