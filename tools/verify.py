"""Measure the sim against the procedures and the manual: how closely aircraft track the coded
STAR / SID / approach legs (incl. the RF arcs of RNAV (RNP) Y 28R), whether altitude and speed
constraints are met at the fixes, parallel approaches (6.11.7-6.11.8), runway crossings (2.3.2),
and frame-to-frame continuity (no jumps beyond the aircraft's speed / turn / climb limits).

    python3 tools/verify.py [--split train] [--seeds 0,3,5] [--controller controller]

Track error is the perpendicular distance from the leg's nominal path: TF = the line between its
fixes, CF = the coded course into the fix, RF = |distance to the arc centre - radius|. Samples in
the first/last 15 % of a leg (where a fly-by turn joins or leaves it) are reported separately
as 'turns'. Altitude constraints are checked only while the pilot is descending/climbing via
(no ATC altitude, not vectored); tolerance 150 ft. Speeds: tolerance 10 kt.
"""
import argparse
import importlib
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from atc import flight as F   # noqa: E402
from atc import scenarios      # noqa: E402
from atc import world as W     # noqa: E402
from atc.sim import Sim        # noqa: E402

ALT_TOL, SPD_TOL = 150, 10


def pct(v, q):
    if not v:
        return float("nan")
    v = sorted(v)
    return v[min(len(v) - 1, int(q * len(v)))]


class VSim(Sim):
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.track = {}           # (pt, tag) -> [xtk]
        self.turns = {}
        self.rf = []              # (cs, fix, xtk, along fraction)
        self.via = {}             # cs -> descending/climbing via right now
        self.fixchk = []          # (cs, fix, proc, tag, alt, amin, amax, ias, spd, sptype, via)
        self.prev = {}
        self.disturbed = {}
        self.jumps = []           # (t, cs, what, value, limit)
        self.xings = []           # dict per crossing
        self.est_pairs = {}
        self.par_min = {}         # pair -> min vertical while < 3 nm and not both established
        self.side = set()         # pairs established side by side within 3 nm
        mon = self.monitor
        orig_tick, orig_event = mon.on_tick, mon.on_event

        def tick():
            orig_tick()
            self.sample()

        def event(ev, ac, **kw):
            orig_event(ev, ac, **kw)
            if ev == "cross_enter":
                self.xings.append({"cs": ac.cs, "rwy": kw.get("rwy"), "t_in": self.t, "t_out": None,
                                   "u": W.RUNWAYS[kw.get("rwy")].u((ac.x, ac.y))})
            elif ev == "crossed":
                for c in reversed(self.xings):
                    if c["cs"] == ac.cs and c["rwy"] == kw.get("rwy") and c["t_out"] is None:
                        c["t_out"] = self.t
                        break
            elif ev == "threshold":
                for c in self.xings:
                    if c["rwy"] == ac.runway and c["t_out"] is not None and c.get("next") is None and self.t >= c["t_out"]:
                        c["next"] = self.t - c["t_out"]
                    elif c["rwy"] == ac.runway and c["t_out"] is None and self.t >= c["t_in"]:
                        c["occupied_at_threshold"] = True
        mon.on_tick, mon.on_event = tick, event

    def sequence_leg(self, ac, lg):
        via = ac.alt_assigned is None and ac.nav == "lnav" and not ac.holdp and not ac.missed
        if lg.get("fix") and lg.get("pos") is not None and lg["pt"] not in ("CA", "VA", "VI", "VM", "FM") \
                and lg["tag"] != "missed":
            self.fixchk.append((ac.cs, lg["fix"], lg["proc"], lg["tag"], ac.alt, lg.get("amin"), lg.get("amax"),
                                ac.ias, lg.get("spd") if ac.spd is None else None, lg.get("sptype"),
                                via and not ac.appr_cleared or (via and lg["tag"] in ("app", "final")), ac.intent))
        return super().sequence_leg(ac, lg)

    def sample(self):
        for ac in self.aircraft.values():
            if ac.m["done"]:
                self.prev.pop(ac.cs, None)
                continue
            p = self.prev.get(ac.cs)
            if p and p[0] == self.t - 1:
                _, x, y, alt, hdg, gs, air = p
                d = math.hypot(ac.x - x, ac.y - y) * 3600
                lim = max(gs, ac.gs) * 1.05 + 3
                if d > lim:
                    self.jumps.append((self.t, ac.cs, "position", round(d, 1), round(lim, 1), ac.phase))
                if air and ac.airborne:
                    dh = abs(W.ang_diff(hdg, ac.hdg))
                    lim_h = F.max_turn_rate(min(gs, ac.gs)) + 0.05
                    if dh > lim_h:
                        self.jumps.append((self.t, ac.cs, "heading", round(dh, 2), round(lim_h, 2), ac.phase))
                    dz = abs(ac.alt - alt) * 60
                    if dz > ac.perf["climb"] * 1.6 + 200:
                        self.jumps.append((self.t, ac.cs, "vertical", round(dz), ac.perf["climb"] * 1.6, ac.phase))
            self.prev[ac.cs] = (self.t, ac.x, ac.y, ac.alt, ac.hdg, ac.gs, ac.airborne)
            if ac.airborne and (ac.nav != "lnav" or ac.holdp is not None or ac.turn360):
                self.disturbed[ac.cs] = self.t
            if not ac.airborne or ac.nav != "lnav" or ac.holdp is not None or ac.turn360 or not ac.legs or ac.climbout:
                continue
            if self.t - self.disturbed.get(ac.cs, -999) < 240:
                continue                      # rejoining the route after a 360, a hold or vectors
            lg = ac.legs[0]
            pt, pos = lg["pt"], lg.get("pos")
            if pt == "RF" and lg.get("center") and ac.leg_from is not None:
                c, r = lg["center"], lg["radius"]
                s = 1 if lg["turn"] == "R" else -1
                a0, a1, an = W.bearing(c, ac.leg_from), W.bearing(c, pos), W.bearing(c, (ac.x, ac.y))
                span = ((a1 - a0) * s) % 360
                done = ((an - a0) * s) % 360
                f = done / span if span > 1 else 0
                xtk = abs(math.hypot(ac.x - c[0], ac.y - c[1]) - r)
                self.rf.append((ac.cs, lg["fix"], xtk, f))
                (self.track if 0.15 <= f <= 0.85 else self.turns).setdefault((pt, lg["tag"]), []).append(xtk)
                continue
            if pos is None or pt not in ("TF", "CF") or ac.leg_from is None:
                continue
            if pt == "TF":
                A = ac.leg_from
                if W.dist(A, pos) < 0.5:
                    continue
                crs = W.bearing(A, pos)
                L = W.dist(A, pos)
            else:
                crs = lg["crs"]
                A = (pos[0] - W.unit(crs)[0] * 30, pos[1] - W.unit(crs)[1] * 30)
                L = W.dist(ac.leg_from, pos)
            ux, uy = W.unit(crs)
            xtk = abs((ac.x - A[0]) * uy - (ac.y - A[1]) * ux)
            along = (pos[0] - ac.x) * ux + (pos[1] - ac.y) * uy
            f = 1 - along / L if L > 0 else 1
            (self.track if 0.15 <= f <= 0.85 else self.turns).setdefault((pt, lg["tag"]), []).append(xtk)
        # parallel approaches (6.11.7-6.11.8)
        arr = [a for a in self.aircraft.values() if a.airborne and not a.m["done"] and a.intent == "arrival"
               and a.runway in W.ARR_RUNWAYS and a.appr_cleared and a.alt > 300]
        for i, a in enumerate(arr):
            for b in arr[i + 1:]:
                if W.CSPR.get(a.runway) != b.runway:
                    continue
                d = math.hypot(a.x - b.x, a.y - b.y)
                if d >= 3.0:
                    continue
                key = tuple(sorted((a.cs, b.cs)))
                if a.established and b.established:
                    self.side.add(key)
                else:
                    self.par_min[key] = min(self.par_min.get(key, 1e9), abs(a.alt - b.alt))


def run(seed, module):
    ctrl = importlib.import_module(module).Controller()
    return VSim(scenarios.make(seed), ctrl).run()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="train")
    ap.add_argument("--seeds", default=None)
    ap.add_argument("--controller", default="controller")
    args = ap.parse_args()
    seeds = [int(s) for s in args.seeds.split(",")] if args.seeds else scenarios.SPLITS[args.split]
    track, turns, rf, fixchk, jumps, cross, par, side = {}, {}, [], [], [], [], {}, set()
    for seed in seeds:
        s = run(seed, args.controller)
        for k, v in s.track.items():
            track.setdefault(k, []).extend(v)
        for k, v in s.turns.items():
            turns.setdefault(k, []).extend(v)
        rf += s.rf
        fixchk += s.fixchk
        jumps += [(seed,) + j for j in s.jumps]
        cross += s.xings
        par.update({(seed,) + k: v for k, v in s.par_min.items()})
        side |= {(seed,) + k for k in s.side}
    ft = W.FT_NM
    print(f"seeds {seeds}\n\nTRACK ERROR on the coded legs (ft; mid-leg samples, 1 s apart)")
    print(f"  {'leg':14}{'n':>8}{'mean':>8}{'p95':>8}{'max':>8}   turns: n, p95, max")
    for k in sorted(track, key=lambda k: (k[1], k[0])):
        v, tv = track[k], turns.get(k, [])
        print(f"  {k[0] + ' ' + k[1]:14}{len(v):8}{sum(v) / len(v) * ft:8.0f}{pct(v, .95) * ft:8.0f}{max(v) * ft:8.0f}"
              f"   {len(tv)}, {pct(tv, .95) * ft:.0f}, {max(tv) * ft if tv else 0:.0f}")
    if rf:
        by = {}
        for cs, fix, x, f in rf:
            by.setdefault(fix, []).append(x)
        print("  RF legs (RNAV (RNP) Y 28R): " + ", ".join(
            f"to {f}: {len(v)} samples, mean {sum(v) / len(v) * ft:.0f} ft, max {max(v) * ft:.0f} ft (radius "
            f"{'2.92' if f == 'JOSUF' else '2.83'} nm)" for f, v in sorted(by.items())))
    print("\nCONSTRAINTS at fix passage (pilot flying the procedure, no ATC altitude)")
    n_alt = bad_alt = n_spd = bad_spd = 0
    worst = []
    for cs, fix, proc, tag, alt, lo, hi, ias, spd, sptype, via, intent in fixchk:
        if via and (lo is not None or hi is not None):
            n_alt += 1
            err = (lo - alt) if lo is not None and alt < lo else ((alt - hi) if hi is not None and alt > hi else 0)
            if err > ALT_TOL:
                bad_alt += 1
                worst.append((err, cs, fix, proc, int(alt), lo, hi))
        if spd and sptype in ("at", "max") and via:
            n_spd += 1
            if ias > spd + SPD_TOL:
                bad_spd += 1
    print(f"  altitude windows: {n_alt} checked, {bad_alt} outside by > {ALT_TOL} ft")
    for w in sorted(worst, reverse=True)[:6]:
        print(f"    {w[1]} {w[3]} {w[2]}: {w[4]} ft, window {w[5]}..{w[6]} ({w[0]:.0f} ft off)")
    print(f"  speed limits: {n_spd} checked, {bad_spd} faster by > {SPD_TOL} kt")
    print("\nPARALLEL APPROACHES 28L/28R (6.11.7-6.11.8)")
    lost = [v for v in par.values() if v < 1000]
    print(f"  pairs within 3 nm before both established: {len(par)}; of these with < 1,000 ft: {len(lost)} "
          f"(min vertical {min(par.values()) if par else '-'} ft)")
    print(f"  pairs side by side (< 3 nm) once both established: {len(side)}")
    print("\nRUNWAY CROSSINGS (2.3.2)")
    done = [c for c in cross if c["t_out"] is not None]
    occ = [c for c in cross if c.get("occupied_at_threshold")]
    nxt = [c["next"] for c in done if c.get("next") is not None]
    print(f"  crossings: {len(cross)} ({len(done)} completed); time on the runway: mean "
          f"{sum(c['t_out'] - c['t_in'] for c in done) / max(1, len(done)):.0f} s, max "
          f"{max([c['t_out'] - c['t_in'] for c in done] or [0])} s")
    print(f"  an arrival crossed the threshold while a crosser was on the runway: {len(occ)}")
    print(f"  next arrival over the threshold after the crosser was clear: min {min(nxt) if nxt else '-'} s, "
          f"median {sorted(nxt)[len(nxt) // 2] if nxt else '-'} s")
    print("\nCONTINUITY (1 s steps): jumps beyond speed / turn rate / climb limits")
    by = {}
    for j in jumps:
        by.setdefault(j[3], []).append(j)
    if not jumps:
        print("  none")
    for k, v in by.items():
        print(f"  {k}: {len(v)}  e.g. {v[:3]}")


if __name__ == "__main__":
    main()
