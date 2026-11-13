# Autoresearch program: improve the KSFO AI air traffic controller

You are improving `controller.py`: one AI controller running four positions (Ground, Tower,
Approach, Departure) at San Francisco (KSFO) in any of its real runway configurations, on real FAA procedures (CIFP cycle 2611: STARs, SIDs, ILS/RNAV
approaches incl. RF legs, published holds), the real OSM taxiway network and the real NorCal
minimum vectoring altitudes. It must follow FAA Order JO 7110.65
(https://www.faa.gov/air_traffic/publications/atpubs/atc_html/; `python3 tools/fetch_faa.py` saves a
local text copy in `reference/faa-7110.65.txt` for searching) and speak the FAA phraseology
implemented in `atc/commands.py`, where each instruction cites its paragraph.

## Runway configurations: be good in all of them

Every session runs in one real KSFO configuration (shares from a year of ADS-B, `W.CONFIG_SOURCE`):

| configuration | arrivals | departures | share of the year |
|---|---|---|---|
| `west_28s` | 28L, 28R | 28L, 28R | 52 % (Apr-Sep) |
| `west` (West Plan) | 28L, 28R | 01L, 01R (heavies 28s) | 39 % |
| `west_28r` | 28R | 01L, 01R (heavies 28s) | 4 % |
| `west_28l` | 28L | 01L, 01R (heavies 28s) | 1 % |
| `southeast` | 19L | 10L, 10R | 3 % |
| `east` | 10L | 10R | 0.4 % |
| `west_dep28l`, `west_28r_28s` | 28s / 28R | 28L / 28s | 3 % (holdout only) |

The training split is every configuration above (except the holdout-only two) at every traffic
level (light, medium, peak), plus a 3-hour West Plan peak (`scenarios.SCENARIOS`):
`west` 0-2, `west_28s` 3-5, `west_28r` 6-8, `west_28l` 9-11, `southeast` 12-14, `east` 15-17,
long peak 18.

The score is the average over configurations of each configuration's average cost, so every
configuration counts the same, and a change is kept only if no configuration gets more hard
violations (`by_config` in the evaluator output shows each). Two configurations are never
trained on and the holdout includes them, so:

- Work from the runways in use (`W.ARR_RUNWAYS`, `W.DEP_RUNWAYS`, `W.DEP_HEAVY`,
  `W.INTERSECTIONS`, `W.CSPR`, `W.EXITS`, `W.LINEUP`, the runway geometry, each aircraft's
  `runway`, STAR and approach), as a real controller does from the ATIS. Never branch on the
  configuration's name (`tools/ci_guard.py` rejects `W.CONFIG*` and `"config"`) or on a seed.
- Prefer rules that follow from geometry (same runway, parallel, intersecting, crossing a
  departure path) over per-runway tables; when a procedure needs a table (a STAR's downwind fix,
  a hold), key it on the procedure, not the configuration.
- Check a change across configurations: `python3 evaluate.py --per-scenario` prints each seed with
  its configuration.

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
- Prefer general, 7110.65-grounded logic over special cases for particular seeds.

## FAA rules that are enforced (and must not get worse)

- Procedures first: arrivals fly their filed STAR and the published holds. With an ILS/RNAV
  approach on the flight plan, clear it with an altitude to maintain until established (5-9-4)
  instead of vectoring. Issue headings only when needed: delay vectors (then "resume the X
  arrival", 5-6-2), missed approaches, radar-vector legs at the end of a STAR, or separation.
  Separate vertically (1000 ft, stacks, altitude caps) before reaching for headings.
- Intercepts (5-9-1, 5-9-2): vector to join the final approach course no closer than the
  approach gate (1 nm outside the FAF, at least 5 nm from the threshold), at an angle of 30
  degrees or less (20 degrees or less within 2 nm of the gate), at an altitude not above the
  glideslope. Clear for the approach with an altitude until established and transfer to tower
  before the gate (5-9-4, 5-9-5).
- Parallel approaches (5-9-6): aircraft for parallel runways (28L/28R) are 1000 ft apart until both are
  established on their localizers.
- Runways (3-9-6, 3-9-8, 3-10-3, 3-10-4): same-runway and intersecting-runway separation (in the
  West Plan the 01s cross the 28s, in the Southeast Plan the 10s cross 19L, including extended
  centerlines); arrivals and departures on the same runway (the 28s plan) need 3-9-6/3-10-3
  spacing between them, wake spacing on final (5-5-4) and for
  departures. Every landing aircraft needs a landing clearance (3-10-5).
- Runway crossings (3-1-3, 3-7-2): explicit crossing or hold-short instructions; never cross in
  front of a departure or arrival.
- Taxiing (3-7-1, 3-7-2): routes by taxiway name; avoid conflicts (< 150 ft) and nose-to-nose
  stand-offs. Ground resolves conflicts with `C.follow(cs, traffic="<callsign>")`: the pilot
  stops short of that traffic's path while it conflicts and continues behind it once it has
  passed. `hold_position` and `continue_taxi` are also available.
- Speed (5-7): 5 kt steps, nothing above 250 kt below 10,000 ft (5-7-3), no speed adjustments
  inside 5 nm of the threshold or the FAF, or in a hold (5-7-1). `C.approach_speed(cs, kts,
  until_nm=5)` = "maintain N knots until N mile final".
- Holds (4-6-1, 4-6-4): every holding clearance carries an expect-further-clearance time
  (`efc`, session seconds) and an altitude at or above the MVA. Leaving the hold needs a
  clearance beyond the fix (4-6-2, `C.exit_hold`).
- Fuel (2-1-4, 10-2-5): arrivals carry extra fuel for holding (10-25 min narrowbodies, 25-45 min
  widebodies), then 15-25 min of alternate fuel, then the final reserve. Under 5 minutes of extra
  left they call minimum fuel (no priority, 2-1-8 notes it); once the alternate fuel is spent too
  they declare Mayday fuel. A Mayday aircraft gets priority: work it in at once (`direct_to`, `exit_hold`,
  `cleared_approach`, `vector`), or if it cannot land in time clear it to Oakland
  (`C.cleared_to_airport(cs, airport="KOAK", altitude=...)`, 4-2-1). It must land within its
  direct time to land + 5 minutes of the Mayday or be diverted, otherwise it is a hard
  violation. A diversion counts as 60 minutes of delay.
- Go around (3-8-1, 3-10-9): `C.go_around(cs)` (fly runway heading) or
  `C.go_around(cs, then="left_traffic"|"right_traffic", runway="28L")`: a 1,500 ft circuit
  (observation `circuit`, legs `upwind`/`crosswind`/`downwind`/`base`). Either way Tower hands the
  aircraft to Approach within 150 s.
- Aircraft in the air have priority. Ground may hold departures at the gate (answer
  `request_pushback` with `hold_position`, release later with `pushback_approved`) when the
  airport is busy, so delay is absorbed at the gate rather than in the air, on taxiways or at the
  runway. A minute held at the gate costs a quarter of a minute of delay anywhere else.
- Radar: 3 nm / 1000 ft (5-5-4), conflict alert (5-14-6), MVA/terrain (5-6-3).

## Tower: anticipated separation (3-9-6, 3-10-3)

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
   `python3 tools/experiment.py start "<short description, cite the 7110.65 paragraph>"`
3. Edit `controller.py`. Sanity check with `python3 evaluate.py --split smoke`.
4. Score and record it: `python3 tools/experiment.py finish`
   This runs the training scenarios (19 sessions, ~1 min on 12 cores; seed 18 is a 3-hour peak session), KEEPS the change (git commit) only if hard
   violations did not increase, approach/taxi rule violations (5-9, 3-7) did
   not increase, and the score went down; otherwise it reverts `controller.py`. Never commit or
   revert yourself.
5. Append one line to `notes.md`: experiment number, kept/reverted, and what you learned.
6. Repeat.

Every start/finish shows up live on the dashboard (https://atc.kyleboas.com), so always call both.

## Priorities

1. Zero hard violations: runway separation (3-9, 3-10), runway crossings (3-1-3), conflict
   alerts (5-14-6), near misses, MVA/terrain (5-6-3), Mayday fuel not handled (2-1-4).
2. Then rule violations (each cites its 7110.65 paragraph).
3. Then go-arounds and delay.
