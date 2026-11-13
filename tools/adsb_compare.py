"""Real KSFO traffic (ADS-B) next to the simulator: final-approach spacing, runway throughput and
where arrivals join final, per runway configuration. A source of experiment ideas, not a score.

    python3 -I tools/adsb_compare.py fetch 2026/07/15 2026/07/16      # adsb.fi heatmap -> cache
    python3 -I tools/adsb_compare.py compare                         # every train seed, report.md
    python3 -I tools/adsb_compare.py compare --seeds 2,5,18 --out /tmp/r.md

Real data: the adsb.fi heatmap archive (globe.adsb.fi/globe_history/YYYY/MM/DD/heatmap/NN.bin.ttf,
48 half-hour slices a day, every aircraft every 30 s, altitude in 25 ft steps, no type or
callsign). Only points within ~23 nm of KSFO are kept, as text lines "t hex lat lon alt25 gs10"
(alt25 -123 = on the ground), one file per slice in --data.

Both sides go through the same code: simulator tracks (recorded every 2 s) are resampled to the
heatmap's 30 s, runway and threshold times are detected from the track (not taken from the sim),
and each real half hour is labelled with the configuration its runway use matches. Real wake
categories are unknown, so spacing is compared over all pairs.
"""
import argparse
import bisect
import collections
import json
import math
import os
import statistics
import struct
import sys
import urllib.request
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from atc import world as W          # noqa: E402
from ksfo import data as K          # noqa: E402
from ksfo.geo import LocalFrame     # noqa: E402

CACHE = os.path.expanduser("~/.cache/atc-adsb/ksfo")
URL = "https://globe.adsb.fi/globe_history/{day}/heatmap/{half:02d}.bin.ttf"
MAGIC = 0x0e7f7c9d
BOX = (37.2e6, 38.0e6, -122.9e6, -121.9e6)      # lat/lon * 1e6 kept from each slice
SAMPLE_S = 30
RING_NM = 20.0                                  # entry ring (the heatmap box reaches ~23 nm)
PAIR_S = 240                                    # consecutive landings further apart are not "in trail"
FRAME = LocalFrame(*K.ARP)
GROUND = {"clear", "exiting", "gate_req", "holding_short", "landing_roll", "lined_up", "lining_up", "pending",
          "push_hold", "pushed", "pushing", "rolling", "rto", "taxi_in", "taxiing"}   # sim phases on the ground
LAND = ("01L", "01R", "10L", "10R", "19L", "19R", "28L", "28R")


# ------------------------------------------------------------------ fetch
def fetch_slice(out, day, half):
    fn = os.path.join(out, f"{day.replace('/', '-')}_{half:02d}.txt")
    if os.path.exists(fn):
        return "have", fn
    err = None
    for _ in range(3):
        try:
            req = urllib.request.Request(URL.format(day=day, half=half), headers={"User-Agent": "atc-adsb-compare"})
            b = urllib.request.urlopen(req, timeout=180).read()
            break
        except Exception as e:  # noqa: BLE001
            err = e
    else:
        return f"FAIL {err}", fn
    rows, t = [], None
    for i in range(len(b) // 16):
        h, la, lo, a, g = struct.unpack_from("<iiihh", b, i * 16)
        if h == MAGIC:
            hi, lo32 = struct.unpack_from("<iI", b, i * 16 + 4)
            t = (hi * 4294967296 + lo32) / 1000
            continue
        if t is not None and BOX[0] < la < BOX[1] and BOX[2] < lo < BOX[3]:
            rows.append(f"{t:.0f} {h & 0xffffff:06x} {la / 1e6:.6f} {lo / 1e6:.6f} {a} {g}")
    with open(fn + ".tmp", "w") as f:
        f.write("\n".join(rows))
    os.replace(fn + ".tmp", fn)
    return f"ok {len(rows)}", fn


def cmd_fetch(a):
    os.makedirs(a.data, exist_ok=True)
    jobs = [(d, h) for d in a.days for h in range(48)]
    with ThreadPoolExecutor(4) as ex:
        for status, fn in ex.map(lambda j: fetch_slice(a.data, *j), jobs):
            print(status, fn, flush=True)


# ------------------------------------------------------------------ tracks
def load_real(data):
    """hex -> [(t, x, y, alt_ft, gs_kt)] for aircraft that come near the field low."""
    pts = collections.defaultdict(list)
    for fn in sorted(os.listdir(data)):
        if not fn.endswith(".txt"):
            continue
        with open(os.path.join(data, fn)) as f:
            for line in f:
                p = line.split()
                if len(p) == 6:
                    pts[p[1]].append((int(p[0]), float(p[2]), float(p[3]), int(p[4]), int(p[5])))
    lat0, lon0 = K.ARP
    near = []
    for h, tr in pts.items():
        # cheap prefilter: within ~3 nm of the ARP at some point
        if any(abs(la - lat0) < 0.05 and abs(lo - lon0) < 0.065 for _, la, lo, _, _ in tr):
            near.append(h)
    tracks = {}
    for h in near:
        tr = sorted(set(pts[h]))
        out = []
        for t, la, lo, a25, g10 in tr:
            x, y = FRAME.to_xy(la, lo)
            gnd = a25 == -123
            out.append((t, x, y, W.ELEV if gnd else a25 * 25, g10 / 10, gnd))
        tracks[h] = out
    return tracks


def segments(tr, gap=900):
    seg = []
    for p in tr:
        if seg and p[0] - seg[-1][0] > gap:
            yield seg
            seg = []
        seg.append(p)
    if seg:
        yield seg


def interp(p0, p1, f):
    """Point between p0 and p1 (t, x, y, alt, gs; the ground flag is p1's)."""
    return tuple(a + (b - a) * f for a, b in zip(p0[:5], p1[:5])) + (p1[5],)


def adiff(a, b):
    return (b - a + 540) % 360 - 180


def trk(p0, p1):
    return math.degrees(math.atan2(p1[1] - p0[1], p1[2] - p0[2])) % 360


# ------------------------------------------------------------------ detection (same for real and sim)
def landing(seg):
    """(i, runway, t_thr) of the landing in a track segment, or None: the leg that crosses a
    landing threshold heading down the runway, low, nearest the centreline."""
    best = None
    for i in range(1, len(seg)):
        p0, p1 = seg[i - 1], seg[i]
        if p1[0] - p0[0] > 3 * SAMPLE_S or p0[3] > W.ELEV + 1500 or math.hypot(p0[1], p0[2]) > 8:
            continue
        if p0[5] or p0[4] < 80:                     # airborne and flying before the threshold
            continue
        if math.hypot(p1[1] - p0[1], p1[2] - p0[2]) < 0.2:
            continue
        tk = trk(p0, p1)
        for r in LAND:
            R = W.RUNWAYS[r]
            if abs(adiff(R.hdg, tk)) > 20:
                continue
            rem0, _ = R.to_thr(p0[1], p0[2])
            rem1, _ = R.to_thr(p1[1], p1[2])
            if not (0 < rem0 < 4 and rem1 <= 0):
                continue
            f = rem0 / (rem0 - rem1)
            q = interp(p0, p1, f)
            v = abs(R.to_thr(q[1], q[2])[1])
            if v < 0.1 and (best is None or v < best[3]):
                best = (i, r, q[0], v)
    return best and best[:3]


def takeoff(seg):
    """(runway, t) of a departure: the leg that passes the far end of a runway, climbing."""
    best = None
    for i in range(1, len(seg)):
        p0, p1 = seg[i - 1], seg[i]
        if p1[0] - p0[0] > 3 * SAMPLE_S or p1[3] > W.ELEV + 4000 or p1[3] <= p0[3]:
            continue
        if math.hypot(p1[1] - p0[1], p1[2] - p0[2]) < 0.2:
            continue
        tk = trk(p0, p1)
        for r in LAND:
            R = W.RUNWAYS[r]
            if abs(adiff(R.hdg, tk)) > 25:
                continue
            u0, v0 = R.uv(p0[1], p0[2])
            u1, v1 = R.uv(p1[1], p1[2])
            if not (u0 < R.len_nm <= u1 and u0 > -0.2):
                continue
            f = (R.len_nm - u0) / (u1 - u0)
            q = interp(p0, p1, f)
            v = abs(R.uv(q[1], q[2])[1])
            if v < 0.25 and (best is None or v < best[2]):
                best = (r, q[0], v)
    return best and best[:2]


def rem_at(seg, R, t):
    """Distance to R's threshold (along course) at time t, interpolated, or None."""
    ts = [p[0] for p in seg]
    i = bisect.bisect_left(ts, t)
    if i == 0 or i >= len(seg):
        return None
    p0, p1 = seg[i - 1], seg[i]
    if p1[0] - p0[0] > 3 * SAMPLE_S:
        return None
    q = interp(p0, p1, (t - p0[0]) / (p1[0] - p0[0]))
    return R.to_thr(q[1], q[2])[0]


def arrival_info(seg, i, r, t_thr):
    """Geometry of one arrival up to its threshold crossing."""
    R = W.RUNWAYS[r]
    pts = seg[:i]                           # points before the threshold
    info = {"rwy": r, "t": t_thr}
    # join final: going back from the threshold, the earliest point from which every point is on
    # the extended centreline (|v| < 0.3 nm + 5 % of range) and every leg tracks the runway (+-15 deg)
    j = len(pts) - 1
    while j > 0:
        rem, v = R.to_thr(pts[j - 1][1], pts[j - 1][2])
        if not (0 < rem < 30 and abs(v) < 0.3 + 0.05 * rem):
            break
        if abs(adiff(R.hdg, trk(pts[j - 1], pts[j]))) > 15:
            break
        j -= 1
    rem_j = R.to_thr(pts[j][1], pts[j][2])[0] if pts else None
    if j > 0 and rem_j is not None:
        info["join_nm"] = rem_j
        info["join_alt"] = pts[j][3]
        info["intercept"] = abs(adiff(R.hdg, trk(pts[j - 1], pts[j])))
        if j > 1:   # the turn onto final usually spans two samples
            info["intercept"] = max(info["intercept"], abs(adiff(R.hdg, trk(pts[j - 2], pts[j - 1]))))
    # downwind: legs flown within 35 deg of the reciprocal, before joining, inside the ring
    dw = []
    for k in range(1, j + 1):
        p0, p1 = pts[k - 1], pts[k]
        if math.hypot(p1[1], p1[2]) < RING_NM and abs(adiff((R.hdg + 180) % 360, trk(p0, p1))) < 35:
            dw.append(R.to_thr(p1[1], p1[2]))
    if len(dw) >= 2:
        info["dw_offset"] = statistics.median(v for _, v in dw)          # + right of the final course
        info["dw_end"] = max(rem for rem, _ in dw)                        # how far out the base turn is
    # from the entry ring: entry bearing, track miles, time
    for k in range(len(pts) - 1, 0, -1):
        d0, d1 = math.hypot(pts[k - 1][1], pts[k - 1][2]), math.hypot(pts[k][1], pts[k][2])
        if d0 >= RING_NM > d1:
            f = (d0 - RING_NM) / (d0 - d1)
            q = interp(pts[k - 1], pts[k], f)
            tail = [q] + list(pts[k:]) + [interp(seg[i - 1], seg[i], 0)]
            info["entry_brg"] = math.degrees(math.atan2(q[1], q[2])) % 360
            info["ring_nm"] = sum(math.hypot(b[1] - a[1], b[2] - a[2]) for a, b in zip(tail, tail[1:]))
            info["ring_min"] = (t_thr - q[0]) / 60
            info["ring_alt"] = q[3]
            break
    # ground speed 4 nm and 1 nm out
    for nm, key in ((4, "gs4"), (1, "gs1")):
        for k in range(len(pts) - 1, 0, -1):         # the last crossing, on the final course
            (r0, v0), (r1, v1) = R.to_thr(pts[k - 1][1], pts[k - 1][2]), R.to_thr(pts[k][1], pts[k][2])
            if r0 >= nm > r1 and abs(v0) < 1 and abs(v1) < 1:
                info[key] = interp(pts[k - 1], pts[k], (r0 - nm) / (r0 - r1))[4]
                break
    return info


def analyse(tracks):
    """Arrivals (with geometry and the segment they came from) and departures from tracks."""
    arrs, deps = [], []
    for key, tr in tracks.items():
        for seg in segments(tr):
            ld = landing(seg)
            if ld:
                i, r, t = ld
                a = arrival_info(seg, i, r, t)
                a["id"] = key
                a["seg"] = seg
                arrs.append(a)
            to = takeoff(seg)
            if to and (not ld or to[1] < ld[2]):
                deps.append({"id": key, "rwy": to[0], "t": to[1]})
    arrs.sort(key=lambda a: a["t"])
    deps.sort(key=lambda d: d["t"])
    return arrs, deps


# ------------------------------------------------------------------ configuration of a half hour
def label(arr_c, dep_c):
    def used(c):
        n = sum(c.values())
        return {r for r, v in c.items() if v >= 2 and v >= 0.12 * n}
    a, d = used(arr_c), used(dep_c)
    if not a:
        return None
    if a == {"28L", "28R"}:
        return "west" if d & {"01L", "01R"} else "west_28s" if d and d <= {"28L", "28R"} else None
    if a == {"28R"}:
        return "west_28r" if d & {"01L", "01R"} else "west_28r_28s" if d and d <= {"28L", "28R"} else None
    if a == {"28L"}:
        return "west_28l" if d & {"01L", "01R"} else "west_dep28l" if d == {"28L"} else None
    if a == {"19L"}:
        return "southeast"
    if a == {"10L"}:
        return "east"
    return None


def windows(arrs, deps):
    w = collections.defaultdict(lambda: (collections.Counter(), collections.Counter()))
    for a in arrs:
        w[int(a["t"] // 1800)][0][a["rwy"]] += 1
    for d in deps:
        w[int(d["t"] // 1800)][1][d["rwy"]] += 1
    return w


# ------------------------------------------------------------------ measurements
def measure(arrs, deps, cfg_of, win_s=1800):
    """Per configuration: lists of samples for every metric. cfg_of(t) -> configuration or None."""
    M = collections.defaultdict(lambda: collections.defaultdict(list))
    for a in arrs:
        c = cfg_of(a["t"])
        if c is None:
            continue
        for k in ("join_nm", "join_alt", "intercept", "ring_nm", "ring_min", "ring_alt", "gs4", "gs1"):
            if k in a:
                M[c][k].append(a[k])
        if "dw_offset" in a:
            M[c]["dw_offset_abs"].append(abs(a["dw_offset"]))
            M[c]["dw_end"].append(a["dw_end"])
    # in-trail pairs on the same runway: time apart, and the follower's distance to the threshold
    # when the leader crosses it
    by_rwy = collections.defaultdict(list)
    for a in arrs:
        by_rwy[a["rwy"]].append(a)
    for r, lst in by_rwy.items():
        R = W.RUNWAYS[r]
        for lead, fol in zip(lst, lst[1:]):
            dt = fol["t"] - lead["t"]
            c = cfg_of(lead["t"])
            if c is None or dt > PAIR_S:
                continue
            M[c]["gap_s"].append(dt)
            d = rem_at(fol["seg"], R, lead["t"])
            if d is not None and d > 0:
                M[c]["gap_nm"].append(d)
    # parallel landings (28L/28R): time between a landing and the nearest one on the other runway
    for r, o in (("28L", "28R"), ("28R", "28L")):
        ot = [a["t"] for a in by_rwy.get(o, [])]
        for a in by_rwy.get(r, []):
            c = cfg_of(a["t"])
            if c is None or not ot:
                continue
            i = bisect.bisect_left(ot, a["t"])
            near = min(abs(a["t"] - ot[k]) for k in (i - 1, i) if 0 <= k < len(ot))
            if near <= PAIR_S:
                M[c]["parallel_s"].append(near)
    # departures: interval on the same runway and between any two departures
    dr = collections.defaultdict(list)
    for d in deps:
        dr[d["rwy"]].append(d["t"])
    for r, ts in dr.items():
        for t0, t1 in zip(ts, ts[1:]):
            c = cfg_of(t0)
            if c is not None and t1 - t0 <= PAIR_S:
                M[c]["dep_same_s"].append(t1 - t0)
    for d0, d1 in zip(deps, deps[1:]):
        c = cfg_of(d0["t"])
        if c is not None and d1["t"] - d0["t"] <= PAIR_S:
            M[c]["dep_any_s"].append(d1["t"] - d0["t"])
    # throughput per half hour (as hourly rates)
    for k, (ac, dc) in windows(arrs, deps).items():
        c = cfg_of(k * 1800 + 900)
        if c is None:
            continue
        na, nd = sum(ac.values()), sum(dc.values())
        M[c]["arr_per_h"].append(na * 3600 / win_s)
        M[c]["dep_per_h"].append(nd * 3600 / win_s)
        M[c]["mov_per_h"].append((na + nd) * 3600 / win_s)
    return M


# ------------------------------------------------------------------ simulator
def sim_tracks(seed):
    import importlib
    from atc import scenarios
    from atc.sim import Sim
    ctrl = importlib.import_module("controller").Controller()
    sc = scenarios.make(seed)
    sim = Sim(sc, ctrl, record=True).run()
    raw = collections.defaultdict(list)
    for t, rows in sim.frames:
        for r in rows:
            raw[r[0]].append((t, r[1], r[2], r[3], r[4], r[5] in GROUND))
    # resample at the heatmap's rate, each aircraft with its own phase like the real reports
    tracks = {}
    for cs, tr in raw.items():
        ph = sum(map(ord, cs)) % SAMPLE_S
        tracks[cs] = [p for p in tr if (p[0] - ph) % SAMPLE_S in (0, 1)]
    truth = {a.cs: (a.intent, a.runway, a.m["t_landed"] is not None, a.m["t_diverted"] is not None)
             for a in sim.aircraft.values()}
    return seed, sc.get("config", "west"), sim.duration, tracks, truth


def sim_measure(seeds, jobs):
    M = collections.defaultdict(lambda: collections.defaultdict(list))
    check = collections.Counter()
    fate = collections.defaultdict(collections.Counter)
    with ProcessPoolExecutor(jobs) as ex:
        for seed, cfg, dur, tracks, truth in ex.map(sim_tracks, seeds):
            arrs, deps = analyse(tracks)
            got = {a["id"]: a["rwy"] for a in arrs}
            for cs, (intent, rwy, landed, diverted) in truth.items():
                if intent != "arrival":
                    continue
                fate[cfg]["landed" if landed else "diverted" if diverted else "still airborne at the end"] += 1
                if not landed:
                    check["arrivals not landed by the end of the run"] += 1
                else:
                    check["landed, found" if got.get(cs) == rwy else "landed, other runway" if cs in got
                          else "landed, not found"] += 1
            m = measure(arrs, deps, lambda t, c=cfg: c)
            for k, v in m[cfg].items():
                if not k.endswith("_per_h"):
                    M[cfg][k].extend(v)
            # throughput: sliding 30 min windows (5 min steps) from the first to the last movement
            ta, td = [a["t"] for a in arrs], [d["t"] for d in deps]
            ev = sorted(ta + td)
            for t0 in range(int(ev[0]) if ev else 0, int(ev[-1]) - 1799 if ev else 0, 300):
                na = sum(1 for t in ta if t0 <= t < t0 + 1800)
                nd = sum(1 for t in td if t0 <= t < t0 + 1800)
                M[cfg]["arr_per_h"].append(2 * na)
                M[cfg]["dep_per_h"].append(2 * nd)
                M[cfg]["mov_per_h"].append(2 * (na + nd))
            print(f"  sim seed {seed} {cfg}: {len(arrs)} landings, {len(deps)} departures", file=sys.stderr, flush=True)
    return M, check, fate


# ------------------------------------------------------------------ report
ROWS = [  # key, title, unit, which percentiles to show
    ("gap_nm", "In-trail spacing at the leader's threshold crossing (same runway)", "nm", "low"),
    ("gap_s", "Time between landings on the same runway (pairs <= 4 min)", "s", "low"),
    ("parallel_s", "28L/28R: time to the nearest landing on the other parallel", "s", "low"),
    ("arr_per_h", "Landings per hour (each half hour)", "/h", "high"),
    ("dep_per_h", "Departures per hour (each half hour)", "/h", "high"),
    ("mov_per_h", "Movements per hour (each half hour)", "/h", "high"),
    ("dep_same_s", "Time between departures off the same runway (<= 4 min)", "s", "low"),
    ("dep_any_s", "Time between any two departures (<= 4 min)", "s", "low"),
    ("join_nm", "Joins final at (distance to threshold)", "nm", "mid"),
    ("join_alt", "Altitude when joining final", "ft", "mid"),
    ("intercept", "Intercept angle onto final", "deg", "mid"),
    ("dw_offset_abs", "Downwind lateral offset from the final course", "nm", "mid"),
    ("dw_end", "Downwind extends to (distance out along the final course)", "nm", "mid"),
    ("ring_nm", f"Track miles from the {RING_NM:.0f} nm ring to the threshold", "nm", "mid"),
    ("ring_min", f"Minutes from the {RING_NM:.0f} nm ring to the threshold", "min", "mid"),
    ("ring_alt", f"Altitude crossing the {RING_NM:.0f} nm ring", "ft", "mid"),
    ("gs4", "Ground speed 4 nm out", "kt", "mid"),
    ("gs1", "Ground speed 1 nm out", "kt", "mid"),
]
CONFIGS = ["west", "west_28s", "west_28r", "west_28l", "southeast", "east", "west_dep28l", "west_28r_28s"]


def pct(xs, q):
    xs = sorted(xs)
    if not xs:
        return None
    k = (len(xs) - 1) * q
    f = int(k)
    return xs[f] + (xs[min(f + 1, len(xs) - 1)] - xs[f]) * (k - f)


def summ(xs, kind):
    if not xs:
        return "-", None
    qs = {"low": (0.1, 0.5), "high": (0.5, 0.9), "mid": (0.1, 0.5, 0.9)}[kind]
    vals = [pct(xs, q) for q in qs]
    fmt = (lambda v: f"{v:.0f}") if max(abs(v) for v in vals) >= 20 else (lambda v: f"{v:.1f}")
    lab = {"low": "p10 / med", "high": "med / p90", "mid": "p10 / med / p90"}[kind]
    extra = f", max {max(xs):.0f}" if kind == "high" else f", min {min(xs):.1f}" if kind == "low" else ""
    return f"{' / '.join(fmt(v) for v in vals)} (n={len(xs)}{extra})", (lab, vals)


def gaps(real, sim):
    """Plain-language differences big enough to be worth an experiment."""
    out = []
    for c in CONFIGS:
        R, S = real.get(c, {}), sim.get(c, {})

        def med(d, k):
            return statistics.median(d[k]) if len(d.get(k, [])) >= 5 else None

        def p(d, k, q):
            return pct(d[k], q) if len(d.get(k, [])) >= 5 else None
        for k, thr, what in (("gap_nm", 0.5, "in-trail spacing at the threshold"),
                             ("gap_s", 15, "time between landings on one runway"),
                             ("dep_same_s", 15, "time between departures off one runway"),
                             ("join_nm", 2.0, "distance at which arrivals join final"),
                             ("ring_nm", 3.0, "track miles from the ring"),
                             ("ring_min", 1.5, "minutes from the ring"),
                             ("gs4", 10, "ground speed 4 nm out")):
            r, s = med(R, k), med(S, k)
            if r is not None and s is not None and abs(s - r) >= thr:
                out.append(f"- **{c}**: median {what} is {s:.1f} in the sim vs {r:.1f} real "
                           f"({'sim higher' if s > r else 'sim lower'}).")
        for k, what in (("arr_per_h", "landing rate"), ("dep_per_h", "departure rate")):
            r, s = p(R, k, 0.9), p(S, k, 0.9)
            if r is not None and s is not None and abs(s - r) >= 4:
                aar = f" (the sim meters arrivals to an AAR of {W.CONFIGS[c]['aar']}/h)" if k == "arr_per_h" else ""
                out.append(f"- **{c}**: busy-period {what} (p90 of half hours) is {s:.0f}/h in the sim vs "
                           f"{r:.0f}/h real{aar}.")
    return out


def cmd_compare(a):
    print("loading real tracks...", file=sys.stderr, flush=True)
    tracks = load_real(a.data)
    arrs, deps = analyse(tracks)
    w = windows(arrs, deps)
    lab = {k: label(ac, dc) for k, (ac, dc) in w.items()}
    real = measure(arrs, deps, lambda t: lab.get(int(t // 1800)))
    days = sorted({fn[:10] for fn in os.listdir(a.data) if fn.endswith(".txt")})
    hours = collections.Counter(v for v in lab.values() if v)
    print(f"real: {len(arrs)} landings, {len(deps)} departures over {len(days)} days", file=sys.stderr, flush=True)

    from atc import scenarios
    seeds = sorted(scenarios.SPLITS["train"]) if a.seeds is None else [int(s) for s in a.seeds.split(",")]
    print(f"simulating seeds {seeds}...", file=sys.stderr, flush=True)
    sim, check, fate = sim_measure(seeds, a.jobs)

    L = ["# Real KSFO (ADS-B) vs the simulator", "",
         f"Real: adsb.fi heatmap, {len(days)} days ({', '.join(days)}), {len(arrs)} landings and "
         f"{len(deps)} departures detected; half hours per configuration: "
         + ", ".join(f"{c} {n / 2:.1f} h" for c, n in hours.most_common()) + ".",
         f"Sim: seeds {', '.join(map(str, seeds))} with the current controller.py, resampled to {SAMPLE_S} s. "
         f"Landings found on sim tracks against the sim's own record: {dict(check)}.", "",
         "Throughput rows are hourly rates of each half hour (sim: sliding 30 min windows, 5 min steps). "
         "The sim's rates are capped by the traffic it is given (arrivals/h, departures/h per level: "
         + ", ".join(f"{k} {v[0][0]}-{v[0][1]} / {v[1][0]}-{v[1][1]}" for k, v in scenarios.LEVELS.items()) + "), so a lower sim rate "
         "is a gap only if its arrivals are being delayed. Real spacing "
         "mixes all wake pairs (the heatmap has no aircraft type).", ""]
    gl = [f"- **{c}**: of {sum(f.values())} sim arrivals, {f['landed']} landed, {f['diverted']} diverted and "
          f"{f['still airborne at the end']} were still airborne when the run stopped."
          for c, f in fate.items() if f["landed"] < sum(f.values())] + gaps(real, sim)
    L += ["## Gaps worth an experiment", ""] + (gl or ["- none above the thresholds"]) + [""]
    for c in CONFIGS:
        if c not in real and c not in sim:
            continue
        L += [f"## {c}", "", "| metric | real | sim |", "|---|---|---|"]
        for k, title, unit, kind in ROWS:
            rs, rl = summ(real.get(c, {}).get(k, []), kind)
            ss, sl = summ(sim.get(c, {}).get(k, []), kind)
            if rl is None and sl is None:
                continue
            labl = (rl or sl)[0]
            L.append(f"| {title} ({unit}, {labl}) | {rs} | {ss} |")
        L.append("")
    text = "\n".join(L)
    with open(a.out, "w") as f:
        f.write(text)
    print(text)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch")
    f.add_argument("days", nargs="+", help="YYYY/MM/DD (UTC)")
    f.add_argument("--data", default=CACHE)
    c = sub.add_parser("compare")
    c.add_argument("--data", default=CACHE)
    c.add_argument("--seeds", help="comma-separated (default: the train split)")
    c.add_argument("--jobs", type=int, default=os.cpu_count())
    c.add_argument("--out", default="adsb_report.md")
    a = ap.parse_args()
    (cmd_fetch if a.cmd == "fetch" else cmd_compare)(a)


if __name__ == "__main__":
    main()
