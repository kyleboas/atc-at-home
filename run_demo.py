"""Run one scenario, print the radio transcript, and write an HTML replay.

    python3 run_demo.py --seed 7
    python3 run_demo.py --seed 7 --quiet          # summary only
    python3 run_demo.py --seed 7 --controller my_controller
    python3 run_demo.py --seed 7 --out out/x.html --commit abc123 --back ../index.html
"""
import argparse
import importlib
import json
import math
import os

from atc import procs as P
from atc import scenarios
from atc import world as W
from atc.sim import Sim
from evaluate import metrics

HERE = os.path.dirname(os.path.abspath(__file__))


def r3(p):
    return [round(p[0], 3), round(p[1], 3)]


def world_data():
    """Everything the scope draws: runways, procedures as flown (RF arcs, holds), fixes, MVA,
    OSM taxiways and stands."""
    K = W.K
    runways = []
    for p in W.PAVEMENTS:
        a, b = p.split("/")
        ra, rb = W.RUNWAYS[a], W.RUNWAYS[b]
        runways.append({"name": p, "a": a, "b": b, "start": r3(ra.start), "end": r3(ra.far), "width_ft": ra.width_ft,
                        "thr": {a: r3(ra.thr), b: r3(rb.thr)}, "in_use": [i for i in (a, b) if i in W.IN_USE.values()]})
    procs, fixes = [], set()

    seen = set()

    def add(name, kind, legs, start=None, group=None):
        if name in seen:
            return None
        seen.add(name)
        pts = P.route_polyline(legs, start)
        main = [lg for lg in legs if lg["tag"] != "missed"]
        miss = [lg for lg in legs if lg["tag"] == "missed"]
        line = P.route_polyline(main, start)
        out = {"name": name, "kind": kind, "group": group or name, "pts": [r3(p) for p in line], "holds": []}
        if miss:
            prev = main[-1]["pos"] if main and main[-1].get("pos") else None
            out["missed"] = [r3(p) for p in P.route_polyline(miss, prev)]
        for lg in legs:
            if lg.get("fix") and lg.get("pos") is not None and lg["pt"] not in ("FM", "VM"):
                fixes.add(lg["fix"])
            if lg["pt"] in ("HM", "HA", "HF") and lg.get("pos"):
                h = lg["hold"]
                out["holds"].append([r3(p) for p in P.hold_racetrack(lg["pos"], h["crs"], h["turn"], h["leg_nm"], h["leg_min"] or 1.0)])
        labels = []
        for lg in legs:
            if lg.get("fix") and lg.get("pos") is not None and (lg["amin"] or lg["amax"] or lg.get("spd")):
                lo, hi = lg["amin"], lg["amax"]
                txt = (f"{lo // 100:03d}" if lo == hi and lo else
                       (f"{hi // 100:03d}-" if hi and not lo else (f"{lo // 100:03d}+" if lo and not hi else
                                                                   (f"{lo // 100:03d}-{hi // 100:03d}" if lo else ""))))
                if lg.get("spd"):
                    txt += f" {lg['spd']}{'-' if lg['sptype'] == 'max' else ''}K"
                labels.append([lg["fix"], txt.strip()])
        out["labels"] = labels
        procs.append(out)
        return pts

    for star, trans, _, rwys, _ in scenarios.STARS:
        for tr in trans:
            for r in rwys:
                add(f"{star}{'.' + tr if tr else ''}", "star", P.star_legs(star, tr, r), group=star)
    for rwy, kinds in P.APPROACH_IDS.items():
        for kind, pid in kinds.items():
            for tr in P.approach_transitions(pid):
                legs = [lg for lg in P.approach_legs(pid, tr) if lg["tag"] == "app"]
                add(f"{pid}.{tr}", "transition", legs, group=pid)
            add(pid, "approach", P.approach_legs(pid), group=pid)
    for sid, _ in scenarios.SIDS:
        for r in W.DEP_RUNWAYS:
            for tr in W.K.PROCEDURES[sid]["enroute_transitions"]:
                add(f"{sid}.{tr}.{r}", "sid", P.sid_legs(sid, r, tr), W.RUNWAYS[r].xy(W.RUNWAYS[r].len_nm), group=sid)
    fx = {f: r3(K.FIXES[f]) for f in sorted(fixes) if f in K.FIXES and not f.startswith("RW")}
    mva = []
    import ksfo.data as KD
    for ch in KD._load("mva.json")["charts"]:
        if "FUS3" not in ch["chart"]:            # the terminal-area chart (3 nm separation)
            continue
        for s in ch["sectors"]:
            ring = [r3(p) for p in s["exterior_xy"]]
            if min(math.hypot(*p) for p in ring) > 60:
                continue
            mva.append({"name": s["name"], "mva": s["mva_ft"], "ring": ring})
    edges = []
    for a, b, name, kind, L, g in W.EDGES:
        if kind in ("runway", "runway_displaced"):
            continue
        edges.append([name, {"taxiway": 0, "taxilane": 1, "parking_position": 2, "stand_link": 2}.get(kind, 1),
                      [[round(p[0], 4), round(p[1], 4)] for p in g]])
    stands = {s["ref"]: [round(s["x"], 4), round(s["y"], 4)] for s in W.STANDS.values()}
    return {"field": f"{W.ICAO} {W.FIELD}", "config": W.CONFIG, "arr": W.ARR_RUNWAYS, "dep": W.DEP_RUNWAYS,
            "runways": runways, "procs": procs, "fixes": fx, "mva": mva, "taxi": edges, "stands": stands,
            "tower": r3(W.TOWER), "airspace_nm": W.APPROACH_NM, "tower_nm": W.TOWER_NM, "cycle": W.CYCLE,
            "magvar": W.MAGVAR}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--controller", default="controller")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--out", default=None)
    ap.add_argument("--commit", default=None)
    ap.add_argument("--back", default=None)
    args = ap.parse_args()

    ctrl = importlib.import_module(args.controller).Controller()
    sc = scenarios.make(args.seed)
    sim = Sim(sc, ctrl, record=True).run()
    m = metrics(sim)

    if not args.quiet:
        for line in sim.transcript:
            t = f"{line['t'] // 60:02d}:{line['t'] % 60:02d}"
            print(f"{t}  {line['who']:<8} {line['text']}")
        print()
    print(json.dumps({k: m[k] for k in ("seed", "level", "cost", "hard", "rule", "go_arounds", "delay_min",
                                        "movements", "by_rule")}))

    data = {"seed": args.seed, "level": sc["level"], "summary": m, "world": world_data(), "commit": args.commit,
            "back": args.back, "frames": sim.frames, "transcript": sim.transcript}
    html = open(os.path.join(HERE, "atc", "replay.html")).read().replace("/*DATA*/null", json.dumps(data, separators=(",", ":")))
    out = args.out or os.path.join(HERE, "out", f"replay-seed{args.seed}.html")
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    open(out, "w").write(html)
    print(f"replay: {out}")


if __name__ == "__main__":
    main()
