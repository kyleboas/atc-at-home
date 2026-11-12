"""Frozen evaluator. Runs the controller over a scenario split and scores it.

    python3 evaluate.py                 # train split (what autoresearch uses): 12 x 40 min
    python3 evaluate.py --split smoke   # 2 quick scenarios (20 min)
    python3 evaluate.py --split holdout # humans only, before merging a change

Lower cost is better. Cost is the average over scenarios of each scenario's cost per 40 minutes, so
the 3-hour session (seed 12) counts like one train scenario at its rate, not 4.5 of them.
Any hard violation (loss of runway separation per manual 3.1.1 incl.
intersecting runways, a crossing against traffic per 2.3.2, a collision alarm per 6.2.4, an
airborne near miss, or terrain per 6.2.1) marks the run FAIL regardless of cost.
"""
import argparse
import hashlib
import importlib
import json
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from atc import scenarios  # noqa: E402
from atc.sim import Sim    # noqa: E402

FROZEN = ["atc/__init__.py", "atc/world.py", "atc/procs.py", "atc/flight.py", "atc/commands.py", "atc/sim.py",
          "atc/rules.py", "atc/scenarios.py", "evaluate.py", "ksfo/__init__.py", "ksfo/data.py", "ksfo/geo.py",
          "ksfo/data/airport.json", "ksfo/data/procedures.json", "ksfo/data/fixes.json", "ksfo/data/holds.json",
          "ksfo/data/surface.json", "ksfo/data/mva.json"]
WEIGHTS = {"hard": 100, "rule": 10, "service": 2, "go_around": 3}
SESSION_S = 40 * 60     # cost is reported per 40-minute session (the length of a train scenario)


def check_frozen():
    path = os.path.join(HERE, "FROZEN.sha256")
    if not os.path.exists(path):
        return ["FROZEN.sha256 missing (run tools/freeze.py)"]
    want = dict(line.split()[::-1] for line in open(path) if line.strip())
    bad = []
    for f in FROZEN:
        got = hashlib.sha256(open(os.path.join(HERE, f), "rb").read()).hexdigest()
        if want.get(f) != got:
            bad.append(f)
    return bad


def run_one(args):
    seed, module = args
    ctrl = importlib.import_module(module).Controller()
    sim = Sim(scenarios.make(seed), ctrl).run()
    return metrics(sim)


def metrics(sim):
    acs = list(sim.aircraft.values())
    mon = sim.monitor
    sev = {"hard": 0, "rule": 0, "service": 0}
    by_rule, by_pos = {}, {}
    for v in mon.violations:
        sev[v["severity"]] += 1
        by_rule[v["rule"]] = by_rule.get(v["rule"], 0) + 1
        k = f"{v['pos']}:{v['severity']}"
        by_pos[k] = by_pos.get(k, 0) + 1
    dep_delay = sum(a.m["t_roll"] - a.m["t_hold_short"] for a in acs
                    if a.intent == "departure" and a.m["t_roll"] is not None and a.m["t_hold_short"] is not None)
    push_hold = sum(a.m["t_push_ok"] - a.m["t_push_req"] for a in acs
                    if a.m["t_push_ok"] is not None and a.m["t_push_req"] is not None)
    arr_delay = sum(max(-0.5 * a.m["nominal"], a.m["t_landed"] - a.m["t_spawn"] - a.m["nominal"]) for a in acs
                    if a.intent == "arrival" and a.m["t_landed"] is not None)
    level_s = sum(a.m["level_s"] for a in acs if a.intent == "departure")
    cross_wait = sum(a.m["cross_wait_s"] for a in acs)
    go_arounds = sum(a.m["go_arounds"] for a in acs)
    movements = (sum(1 for a in acs if a.intent == "departure" and a.m["t_airborne"])
                 + sum(1 for a in acs if a.intent == "arrival" and a.m["t_landed"]))
    # aircraft in the air have priority: holding a departure at the gate (a ground stop) costs a quarter
    # of the same delay in the air or at the hold-short line
    delay_min = (dep_delay + push_hold / 4 + arr_delay + mon.req_wait_s + level_s / 2 + cross_wait / 2) / 60
    cost = (WEIGHTS["hard"] * sev["hard"] + WEIGHTS["rule"] * sev["rule"] + WEIGHTS["service"] * sev["service"]
            + WEIGHTS["go_around"] * go_arounds + delay_min)
    return {"seed": sim.seed, "level": sim.sc["level"], "duration": sim.duration, "cost": round(cost, 2), "hard": sev["hard"],
            "rule": sev["rule"], "service": sev["service"], "by_rule": by_rule, "by_pos": by_pos,
            "dep_level_min": round(level_s / 60, 2), "go_arounds": go_arounds,
            "delay_min": round(delay_min, 2), "dep_delay_min": round(dep_delay / 60, 2),
            "arr_delay_min": round(arr_delay / 60, 2), "push_hold_min": round(push_hold / 60, 2),
            "request_wait_min": round(mon.req_wait_s / 60, 2), "cross_wait_min": round(cross_wait / 60, 2),
            "gdp_delay_min": round(sum(a.spec.get("edct_s", 0) for a in acs) / 60, 2),
            "fuel_minimum": sum(1 for a in acs if a.m["fuel_state"] == "minimum"),
            "fuel_emergency": sum(1 for a in acs if a.m["fuel_state"] == "emergency"),
            "alerts": mon.alerts, "movements": movements, "controller_errors": sim.controller_errors,
            "sim_seconds": sim.t}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="train", choices=list(scenarios.SPLITS))
    ap.add_argument("--controller", default="controller", help="module with a Controller class")
    ap.add_argument("--jobs", type=int, default=os.cpu_count())
    ap.add_argument("--per-scenario", action="store_true")
    args = ap.parse_args()

    bad = check_frozen()
    if bad:
        print("FROZEN FILES CHANGED:", ", ".join(bad))
        print("The evaluator and simulator must not be edited. Revert them and try again.")
        sys.exit(2)

    seeds = scenarios.SPLITS[args.split]
    if args.split == "holdout" and os.environ.get("ATC_HOLDOUT_SEEDS"):
        # maintainers score the real held-out set from private seeds (never published)
        seeds = [int(x) for x in os.environ["ATC_HOLDOUT_SEEDS"].split(",") if x.strip()]
    t0 = time.time()
    with ProcessPoolExecutor(max_workers=args.jobs) as ex:
        results = list(ex.map(run_one, [(s, args.controller) for s in seeds]))
    n = len(results)
    tot = lambda k: sum(r[k] for r in results)  # noqa: E731
    by_rule, by_pos = {}, {}
    for r in results:
        for k, v in r["by_rule"].items():
            by_rule[k] = by_rule.get(k, 0) + v
        for k, v in r["by_pos"].items():
            by_pos[k] = by_pos.get(k, 0) + v
    summary = {
        # each session is scored per 40 minutes, so a 3-hour session weighs like a 40-minute one
        "split": args.split, "scenarios": n,
        "cost": round(sum(r["cost"] * SESSION_S / r["duration"] for r in results) / n, 3),
        "status": "PASS" if tot("hard") == 0 else "FAIL",
        "hard": tot("hard"), "rule": tot("rule"), "service": tot("service"), "go_arounds": tot("go_arounds"),
        "delay_min_per_scenario": round(tot("delay_min") / n, 2),
        "dep_delay_min": round(tot("dep_delay_min") / n, 2), "arr_delay_min": round(tot("arr_delay_min") / n, 2),
        "movements": tot("movements"), "controller_errors": tot("controller_errors"),
        "dep_level_min": round(tot("dep_level_min") / n, 2),
        "gdp_delay_min": round(tot("gdp_delay_min") / n, 2),
        "fuel_minimum": tot("fuel_minimum"), "fuel_emergency": tot("fuel_emergency"),
        "violations_by_position": dict(sorted(by_pos.items())),
        "violations_by_rule": dict(sorted(by_rule.items())), "seconds": round(time.time() - t0, 1),
    }
    if args.per_scenario:
        for r in results:
            print(json.dumps({k: r[k] for k in ("seed", "level", "cost", "hard", "rule", "go_arounds", "delay_min", "by_rule")}))
    for k, v in summary.items():
        print(f"{k + ':':26}{v}")
    print("JSON " + json.dumps(summary))


if __name__ == "__main__":
    main()
