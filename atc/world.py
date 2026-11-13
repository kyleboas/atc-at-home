"""KSFO world for the simulator: runways, airport configuration, frequencies,
aircraft performance, the taxi graph and the procedures, built from the real
data package in ksfo/ (FAA CIFP 2611, OSM surface, NCT MVA).

Frame: nautical miles from the KSFO ARP, x east, y north (true north at the
ARP). Headings and courses inside the sim are TRUE; everything the controller
says or the pilots read back (assigned headings, CIFP courses) is MAGNETIC, using
the CIFP airport variation (14.0 E). Altitudes are ft MSL (field 13 ft).

Airport configuration: one of CONFIGS per scenario (set_config). The West Plan is SFO's
normal one: arrivals land 28L and 28R, departures use 01L and 01R (heavies 28R). The 01s
physically cross both 28s about
4,600-5,400 ft past the 28 thresholds, so every 01 departure is an
intersecting-runway operation against every 28 arrival (7110.65 3-9-8 / 3-10-4
"Intersecting"). 28L/28R are 750 ft apart (closely spaced parallels, one
runway for wake purposes); so are 01L/01R.
NOT FOR REAL-WORLD NAVIGATION.
"""
import math
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
from ksfo import data as K  # noqa: E402

FIELD = "San Francisco Intl"
ICAO = "KSFO"
ELEV = K.ELEVATION_FT
MAGVAR = K.MAGVAR                 # 14.0 E (CIFP airport record)
FT_NM = 6076.12
CYCLE = K.CYCLE

FREQS = {"ground": "121.8", "tower": "120.5", "approach": "135.65", "departure": "135.1", "center": "128.55"}
RADAR = ("approach", "departure")
POS = {"ground": "GND", "tower": "TWR", "approach": "APP", "departure": "DEP", "center": "CTR"}

APPROACH_NM = 50.0                # approach airspace 50 nm, surface to FL180
APPROACH_CEILING = 18000
TOWER_NM = 5.0                    # tower ring, up to 5,000 ft AAL
TOWER_CEILING = 5000 + ELEV
SID_TOP = 5000                    # initial climb until radar contact

# Runway configurations seen in real KSFO operations (ADS-B, see CONFIG_SOURCE). Each scenario
# runs one of them: world.set_config(key) switches the active runways and everything derived
# from them (IN_USE, INTERSECTIONS, EXITS, LINEUP, DEP_HOLD, ATIS).
#   arr        landing runways
#   dep        departure runways
#   dep_heavy  where heavies / supers depart (long runway), dep_heavy_share of them; the
#              others use `dep` like everyone else
#   aar        arrival acceptance rate per hour (ground delay program)
CONFIG_SOURCE = ("ADS-B (adsb.fi heatmap archive) at KSFO, 14 Nov 2025 - 13 Nov 2026, three half-hour "
                 "samples a day (1,093 samples, ~34,000 flights); landing / departure runway of every "
                 "flight from its track. Share of the year: 28s 52 % (nearly all of April-September), "
                 "West Plan 39 % (departures 15 % off the 28s), single arrival runway 5 % (28R 4, 28L 1), "
                 "Southeast 3 %, East 0.4 %")
CONFIGS = {
    "west": dict(name="West Plan", arr=("28L", "28R"), dep=("01L", "01R"),
                 dep_heavy=("28L", "28R"), dep_heavy_share=0.6, aar=36,
                 atis="landing runways 28L and 28R, departing runways 01L and 01R, heavy departures "
                 "runways 28L and 28R. Simultaneous parallel ILS and RNAV approaches in use.",
                 wind="290 at 14"),
    "west_28r": dict(name="West Plan, single runway arrivals", arr=("28R",), dep=("01L", "01R"),
                     dep_heavy=("28L", "28R"), dep_heavy_share=0.6, aar=30,
                     atis="landing runway 28R, departing runways 01L and 01R, heavy departures runways "
                     "28L and 28R. ILS runway 28R approach in use.", wind="280 at 8"),
    "west_28l": dict(name="West Plan, single runway arrivals", arr=("28L",), dep=("01L", "01R"),
                     dep_heavy=("28L", "28R"), dep_heavy_share=0.6, aar=30,
                     atis="landing runway 28L, departing runways 01L and 01R, heavy departures runways "
                     "28L and 28R. ILS runway 28L approach in use.", wind="280 at 8"),
    "west_28s": dict(name="West Plan, departures 28L and 28R", arr=("28L", "28R"), dep=("28L", "28R"),
                     aar=36, atis="landing and departing runways 28L and 28R. Simultaneous parallel ILS "
                     "and RNAV approaches in use.", wind="230 at 12"),
    "southeast": dict(name="Southeast Plan", arr=("19L",), dep=("10L", "10R"), aar=30,
                      atis="ILS runway 19L approach in use, landing runway 19L, departing runways 10L "
                      "and 10R. Departing aircraft should be prepared for a short notice runway change.",
                      wind="160 at 16"),
    # two real variants kept out of training (holdout only): does the controller generalise?
    "west_28r_28s": dict(name="West Plan, 28R arrivals, departures 28L and 28R", arr=("28R",), dep=("28L", "28R"),
                         aar=30, atis="landing runway 28R, departing runways 28L and 28R. ILS runway 28R approach "
                         "in use.", wind="250 at 10"),
    "west_dep28l": dict(name="West Plan, departures 28L", arr=("28L", "28R"), dep=("28L",), aar=36,
                        atis="landing runways 28L and 28R, departing runway 28L. Simultaneous parallel ILS and "
                        "RNAV approaches in use.", wind="260 at 12"),
    "east": dict(name="East Plan", arr=("10L",), dep=("10R",), aar=30,
                 atis="RNAV runway 10L approach in use, landing runway 10L, departing runway 10R.",
                 wind="090 at 10"),
}
CONFIG_KEY = None
CONFIG = None
ARR_RUNWAYS = ()
DEP_RUNWAYS = ()
DEP_HEAVY = ()
ATIS = ""
CSPR = {"28L": "28R", "28R": "28L", "01L": "01R", "01R": "01L",      # < 2500 ft apart
        "10L": "10R", "10R": "10L", "19L": "19R", "19R": "19L"}
ATIS_TAIL = ("Hold short of all runways; readback of all hold short instructions required.")


def mag2true(m):
    return (m + MAGVAR) % 360


def true2mag(t):
    return (t - MAGVAR) % 360


def dist(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


def bearing(frm, to):
    return (math.degrees(math.atan2(to[0] - frm[0], to[1] - frm[1])) + 360) % 360


def ang_diff(a, b):
    """Signed smallest difference b - a in degrees, -180..180."""
    return (b - a + 540) % 360 - 180


def unit(h):
    r = math.radians(h)
    return math.sin(r), math.cos(r)


def bearing_name(x, y):
    brg = (math.degrees(math.atan2(x, y)) + 360) % 360
    names = ["north", "northeast", "east", "southeast", "south", "southwest", "west", "northwest"]
    return names[int((brg + 22.5) // 45) % 8]


def gs_from_ias(ias, alt):
    """True airspeed: ground speed grows with altitude (no wind), ~2%/1000 ft."""
    return ias * (1 + 0.018 * max(0.0, alt) / 1000)


def mva(x, y):
    """NorCal TRACON minimum vectoring altitude (FUS3, then FUS5, else the 25 nm MSA)."""
    return K.mva(x, y)


# ------------------------------------------------------------------ runways
class Runway:
    """One runway direction. u = along the runway from the start of the pavement in the
    direction of use, v = to the right of it (nm)."""

    def __init__(self, ident):
        r = K.RUNWAYS[ident]
        o = K.RUNWAYS[r["opposite"]]
        self.ident, self.opposite = ident, r["opposite"]
        self.pavement = "/".join(sorted([ident, r["opposite"]]))
        self.start = tuple(r["runway_end"])          # start of usable pavement in this direction
        self.thr = tuple(r["threshold"])              # landing threshold (displaced where there is one)
        self.far = tuple(o["runway_end"])             # other end of the pavement
        self.hdg = bearing(self.start, self.far)      # true
        self.mag = r["magnetic_heading"]
        self.len_nm = dist(self.start, self.far)
        self.ux, self.uy = unit(self.hdg)
        self.u_thr = self.u(self.thr)
        self.elev = r["elevation_ft"]
        ils = K.ILS.get(ident)
        self.ils = ils
        self.gs_angle = ils["gs_angle"] if ils else 3.0
        self.tch = ils["tch_ft"] if ils else r["tch_ft"]
        self.loc_course = ils["course_true"] if ils else self.hdg
        self.loc_mag = ils["course_mag"] if ils else self.mag
        self.width_ft = r["width_ft"]

    def u(self, p):
        return (p[0] - self.start[0]) * self.ux + (p[1] - self.start[1]) * self.uy

    def uv(self, x, y):
        dx, dy = x - self.start[0], y - self.start[1]
        return dx * self.ux + dy * self.uy, dx * self.uy - dy * self.ux

    def xy(self, u, v=0.0):
        return (self.start[0] + u * self.ux + v * self.uy, self.start[1] + u * self.uy - v * self.ux)

    def to_thr(self, x, y):
        """Along-course distance to the landing threshold (positive before it) and cross track."""
        u, v = self.uv(x, y)
        return self.u_thr - u, v

    def glide_alt(self, rem_nm, angle=None):
        """Glidepath altitude rem_nm before the threshold (TCH at the threshold)."""
        a = self.gs_angle if angle is None else angle
        return self.elev + self.tch + rem_nm * FT_NM * math.tan(math.radians(a))

    def contains(self, x, y, half_ft=165.0, margin_ft=150.0):
        """Inside the runway strip used for occupancy: within half_ft of the centerline and
        between the pavement ends (plus margin). 165 ft = 100 ft half width + tail."""
        u, v = self.uv(x, y)
        return abs(v) * FT_NM <= half_ft and -margin_ft / FT_NM <= u <= self.len_nm + margin_ft / FT_NM


RUNWAYS = {i: Runway(i) for i in sorted(K.RUNWAYS)}
PAVEMENTS = sorted({r.pavement for r in RUNWAYS.values()})
IN_USE = {}                              # pavement -> designator in the direction in use (3-7-2)


def runway_of_pavement(p):
    return RUNWAYS[IN_USE[p]]


def _intersect(r1, r2):
    """Intersection of two runway centerlines -> (u along r1, u along r2) in nm."""
    a, b, ux, uy = r1.start[0], r1.start[1], r1.ux, r1.uy
    c, d, vx, vy = r2.start[0], r2.start[1], r2.ux, r2.uy
    det = ux * (-vy) - uy * (-vx)
    if abs(det) < 1e-9:
        return None
    t = ((c - a) * (-vy) - (d - b) * (-vx)) / det
    p = (a + t * ux, b + t * uy)
    if not (0 <= t <= r1.len_nm) or not (0 <= r2.u(p) <= r2.len_nm):
        return None
    return t, r2.u(p), p


INTERSECTIONS = {}                     # (rwy, other) -> u on rwy (nm), for the active runways
INTERSECTION_PTS = {}


def _intersections(active):
    out = {}
    for a in active:
        for b in active:
            if a < b and b != CSPR.get(a) and RUNWAYS[a].pavement != RUNWAYS[b].pavement:
                x = _intersect(RUNWAYS[a], RUNWAYS[b])
                if x:
                    out[(a, b)] = x[0]
                    out[(b, a)] = x[1]
    return out


# "beyond the intersection" = past the far edge of the crossing runway (half width + margin)
BEYOND_NM = 250.0 / FT_NM


# ------------------------------------------------------------------ aircraft
# cat jet/prop; wake = FAA category (J super, H heavy, L large, S small); speeds IAS kt;
# accel/decel kt/s on the runway; climb/descent fpm; cruise = clean IAS above 10,000 ft.
TYPES = {
    "A320": dict(cat="jet", wake="L", name="Airbus A320", vapp=136, vterm=250, cruise=290, vr=145, accel=4.4, decel=4.0, climb=2600, descent=2000, ceiling=37000),
    "A321": dict(cat="jet", wake="L", name="Airbus A321", vapp=140, vterm=250, cruise=290, vr=150, accel=4.0, decel=4.0, climb=2400, descent=2000, ceiling=37000),
    "A319": dict(cat="jet", wake="L", name="Airbus A319", vapp=132, vterm=250, cruise=290, vr=140, accel=4.6, decel=4.0, climb=2800, descent=2000, ceiling=37000),
    "B738": dict(cat="jet", wake="L", name="Boeing 737-800", vapp=145, vterm=250, cruise=290, vr=150, accel=4.3, decel=4.0, climb=2500, descent=2000, ceiling=39000),
    "B39M": dict(cat="jet", wake="L", name="Boeing 737 MAX 9", vapp=147, vterm=250, cruise=290, vr=152, accel=4.2, decel=4.0, climb=2500, descent=2000, ceiling=39000),
    "B77W": dict(cat="jet", wake="H", name="Boeing 777-300ER", vapp=150, vterm=250, cruise=300, vr=165, accel=3.4, decel=3.5, climb=2000, descent=2000, ceiling=39000),
    "B789": dict(cat="jet", wake="H", name="Boeing 787-9", vapp=145, vterm=250, cruise=300, vr=160, accel=3.6, decel=3.6, climb=2200, descent=2000, ceiling=41000),
    "A359": dict(cat="jet", wake="H", name="Airbus A350-900", vapp=142, vterm=250, cruise=300, vr=158, accel=3.6, decel=3.6, climb=2200, descent=2000, ceiling=41000),
    "B744": dict(cat="jet", wake="H", name="Boeing 747-400", vapp=155, vterm=250, cruise=300, vr=170, accel=3.0, decel=3.4, climb=1800, descent=2000, ceiling=37000),
    "A388": dict(cat="jet", wake="J", name="Airbus A380-800", vapp=145, vterm=250, cruise=300, vr=160, accel=3.0, decel=3.4, climb=1800, descent=2000, ceiling=41000),
    "E175": dict(cat="jet", wake="L", name="Embraer E175", vapp=130, vterm=250, cruise=280, vr=135, accel=4.8, decel=4.2, climb=2800, descent=2000, ceiling=37000),
    "CRJ9": dict(cat="jet", wake="L", name="CRJ-900", vapp=140, vterm=250, cruise=280, vr=145, accel=4.8, decel=4.2, climb=2800, descent=2000, ceiling=37000),
}
WAKE_NAMES = {"J": "super", "H": "heavy", "L": "large", "S": "small"}
# FAA JO 7110.65 5-5-4: minimum radar separation on final behind (leader, follower) in nm.
WAKE_NM = {("J", "J"): 6.0, ("J", "H"): 6.0, ("J", "L"): 7.0, ("J", "S"): 8.0,
           ("H", "H"): 4.0, ("H", "L"): 5.0, ("H", "S"): 6.0}
DEP_WAKE_S = {"J": 180, "H": 120}       # 5-8-3: departures behind a heavy / super (same or CSPR runway)


def wake_nm(leader_wake, follower_wake):
    return WAKE_NM.get((leader_wake, follower_wake), 0.0)


# ------------------------------------------------------------------ surface
SURFACE = K.SURFACE
NODES = {k: (v["x"], v["y"]) for k, v in SURFACE["nodes"].items()}
EDGES = []                                 # (a, b, name, kind, length_nm, geometry)
RUNWAY_EDGE_KINDS = ("runway", "runway_displaced")
FREE_KINDS = ("parking_position", "stand_link", "taxilane")
for _e in SURFACE["edges"]:
    EDGES.append((_e["a"], _e["b"], _e["name"] or "", _e["kind"], _e["length_nm"],
                  [tuple(p) for p in _e["geometry"]]))
ADJ = {n: [] for n in NODES}                # node -> [(other, edge index)]
for _k, (_a, _b, _n, _kind, _l, _g) in enumerate(EDGES):
    ADJ[_a].append((_b, _k))
    ADJ[_b].append((_a, _k))
TAXIWAYS = sorted({e[2] for e in EDGES if e[3] == "taxiway" and e[2]})


def edge_geom(k, frm):
    """Polyline of edge k starting at node frm."""
    a, b, _, _, _, g = EDGES[k]
    return g if frm == a else list(reversed(g))


def edge_free(k):
    """Edges that need not be named in a taxi route: unnamed connectors, stand lines, taxilanes."""
    _, _, name, kind, _, _ = EDGES[k]
    return kind in FREE_KINDS or not name


def zone_runways(x, y, half_ft=165.0):
    """Pavements (by in-use designator) whose runway strip contains the point."""
    out = []
    for p in PAVEMENTS:
        r = RUNWAYS[IN_USE[p]] if p in IN_USE else RUNWAYS[p.split("/")[0]]
        if r.contains(x, y, half_ft):
            out.append(r.ident)
    return out


NODE_ZONE = {n: tuple(zone_runways(*p)) for n, p in NODES.items()}       # updated by set_config


def in_use(ident):
    """The designator in use on the pavement of runway `ident` (ident itself if it is not active)."""
    return IN_USE.get(RUNWAYS[ident].pavement, ident)
HOLD_FT = 260.0           # where a pilot stops short of a runway (hold line ~250-280 ft from the CL)


# stands: airline stands with a ref south of 28L (terminals A-G), cargo stands north of 28R
def _stand_side(p):
    v28l = RUNWAYS["28L"].uv(*p)[1]
    v28r = RUNWAYS["28R"].uv(*p)[1]
    return "north" if v28r > 0 else ("south" if v28l < 0 else "between")


STANDS = {}                   # id -> dict(ref, node, entry, x, y, side, heavy_ok)
_seen = set()
for _i, _s in enumerate(SURFACE["stands"]):
    if not _s.get("connected") or not _s.get("node") or _s["node"] not in NODES:
        continue
    _side = _stand_side((_s["x"], _s["y"]))
    ref = _s.get("ref")
    if _side == "south" and not ref:
        continue
    if _side == "between":
        continue
    sid = ref if ref and ref not in _seen else f"{(ref or 'R')}-{_i}"
    _seen.add(sid)
    STANDS[sid] = {"ref": ref or sid, "node": _s["node"], "entry": _s.get("entry_node"), "x": _s["x"], "y": _s["y"],
                   "side": _side, "terminal": (ref or "cargo")[0] if _side == "south" else "cargo",
                   "heavy_ok": _side == "north" or (ref or " ")[0] in "AG"}
def _stand_line(start):
    """Nodes from a stand along its lead-in line (parking_position / stand_link edges) to the
    first node on a taxiway or taxilane."""
    seen, stack, goal = {start: None}, [start], None
    while stack:
        n = stack.pop()
        if n != start and any(EDGES[k][3] not in ("parking_position", "stand_link") for _, k in ADJ[n]):
            goal = n
            break
        for m, k in ADJ[n]:
            if m not in seen and EDGES[k][3] in ("parking_position", "stand_link"):
                seen[m] = n
                stack.append(m)
    if goal is None:
        goal = ADJ[start][0][0]
        seen[goal] = start
    path = [goal]
    while seen[path[-1]] is not None:
        path.append(seen[path[-1]])
    return list(reversed(path))


STAND_LINES = {s["node"]: _stand_line(s["node"]) for s in STANDS.values()}


def lead_in_nodes(*nodes):
    """Nodes of the stand lead-in lines that start at any of `nodes` (taxi routes may use the
    stand lines of their own start and goal stands only)."""
    out = set()
    for n in nodes:
        out.update(STAND_LINES.get(n, ()))
    return out


PAX_STANDS = sorted(k for k, s in STANDS.items() if s["side"] == "south")
CARGO_STANDS = sorted(k for k, s in STANDS.items() if s["side"] == "north")
TOWER = tuple(SURFACE["tower"]) if SURFACE.get("tower") else (0.0, 0.0)


# ------------------------------------------------------------------ runway exits and line-up points
def _runway_nodes(ident):
    r = RUNWAYS[ident]
    name = "RWY " + r.pavement.replace("01L/19R", "01L/19R")
    nodes = set()
    for a, b, n, kind, _, _ in EDGES:
        if kind in RUNWAY_EDGE_KINDS and set(n.replace("RWY ", "").split("/")) == set(r.pavement.split("/")):
            nodes.add(a)
            nodes.add(b)
    return sorted(nodes, key=lambda k: r.u(NODES[k])), name


def _straightest(n, k_in, h0):
    cands = [(m, kk) for m, kk in ADJ[n] if kk != k_in and EDGES[kk][3] not in RUNWAY_EDGE_KINDS
             and EDGES[kk][3] != "parking_position"]
    if not cands:
        return None
    m, kk = min(cands, key=lambda c: abs(ang_diff(h0, bearing(NODES[n], edge_geom(c[1], n)[1]))))
    if abs(ang_diff(h0, bearing(NODES[n], edge_geom(kk, n)[1]))) > 60:
        return None
    return m, kk


def _walk_off(ident, n0, k0, clear_ft=230.0):
    """From runway node n0 along taxiway edge k0: the polyline until clear of the runway
    (|v| >= clear_ft). If the taxiway then runs straight into another runway strip within two
    more edges, carry on to the last node short of it (the hold line of that runway) and report
    it. Returns (points, end_node, holds_short_of) or None."""
    r = RUNWAYS[ident]
    pts = [NODES[n0]]
    n, k = n0, k0
    for _ in range(12):
        nxt = EDGES[k][1] if EDGES[k][0] == n else EDGES[k][0]
        pts.extend(edge_geom(k, n)[1:])
        n = nxt
        x, y = NODES[n]
        v = abs(r.uv(x, y)[1]) * FT_NM
        if any(RUNWAYS[z].pavement != r.pavement for z in NODE_ZONE[n]):
            return None
        if v >= clear_ft:
            # look ahead: another runway strip straight ahead within two edges -> hold short of it
            m, kk, extra = n, k, []
            for _ in range(2):
                tail = (pts + extra)
                step = _straightest(m, kk, bearing(tail[-2], tail[-1]))
                if step is None:
                    break
                m2, kk2 = step
                zs = [z for z in NODE_ZONE[m2] if RUNWAYS[z].pavement != r.pavement]
                if zs:
                    return pts + extra, m, zs[0]
                extra = extra + edge_geom(kk2, m)[1:]
                if dist(NODES[m2], NODES[n]) > 0.12:
                    break
                m, kk = m2, kk2
            return pts, n, None
        step = _straightest(n, k, bearing(pts[-2], pts[-1]))
        if step is None:
            return None
        k = step[1]
    return None


def _exits(ident):
    """Exits for landings on `ident`: dicts with u (nm from the pavement start), side, angle,
    name, kind (high_speed | right_angle), polyline to a point clear of the runway."""
    r = RUNWAYS[ident]
    nodes, _ = _runway_nodes(ident)
    out = []
    for n in nodes:
        u = r.u(NODES[n])
        for m, k in ADJ[n]:
            a, b, name, kind, _, g = EDGES[k]
            if kind in RUNWAY_EDGE_KINDS or kind == "parking_position":
                continue
            p = edge_geom(k, n)[1]
            ang = ang_diff(r.hdg, bearing(NODES[n], p))
            if not (8 <= abs(ang) <= 100):
                continue
            w = _walk_off(ident, n, k)
            if w is None:
                continue
            pts, end, hs = w
            out.append({"u": u, "node": n, "side": "right" if ang > 0 else "left", "angle": round(abs(ang)),
                        "name": name or "", "kind": "high_speed" if abs(ang) <= 45 else "right_angle",
                        "pts": pts, "end": end, "hold_short": hs})
    out.sort(key=lambda e: e["u"])
    return out


def _lineups(ident):
    """Holding points for full-length departures: taxiway connections within 900 ft of the start
    of the pavement. Each: hold node (where the aircraft waits) and the polyline onto the runway."""
    r = RUNWAYS[ident]
    nodes, _ = _runway_nodes(ident)
    out = []
    for n in nodes:
        u = r.u(NODES[n])
        if u > 900 / FT_NM:
            continue
        for m, k in ADJ[n]:
            if EDGES[k][3] in RUNWAY_EDGE_KINDS:
                continue
            w = _walk_off(ident, n, k, clear_ft=HOLD_FT)
            if w is None:
                continue
            pts, end, _ = w
            out.append({"u": u, "node": n, "hold": end, "name": EDGES[k][2], "pts": list(reversed(pts))})
    out.sort(key=lambda e: e["u"])
    return out


_EXITS = {i: _exits(i) for i in RUNWAYS}              # every runway direction, computed once
_LINEUPS = {i: _lineups(i) for i in RUNWAYS}
_PAX_C = (sum(STANDS[k]["x"] for k in PAX_STANDS) / len(PAX_STANDS),
          sum(STANDS[k]["y"] for k in PAX_STANDS) / len(PAX_STANDS))


def _lineup(i):
    """Full-length holding point for departures: on the left of the runway where that is the
    terminal side (01L/01R: west), otherwise the one nearest the terminals."""
    c = [e for e in _LINEUPS[i] if RUNWAYS[i].uv(*NODES[e["hold"]])[1] < 0]
    if c and (i in ("01L", "01R") or dist(NODES[c[0]["hold"]], _PAX_C) <= min(
            dist(NODES[e["hold"]], _PAX_C) for e in _LINEUPS[i]) + 0.05):
        return c[0]
    return min(_LINEUPS[i], key=lambda e: (round(dist(NODES[e["hold"]], _PAX_C), 2), e["u"]))


EXITS, LINEUPS, LINEUP, DEP_HOLD = {}, {}, {}, {}       # for the active runways (departure runways: RTOs)


def set_config(key):
    """Make `key` (a CONFIGS key) the airport configuration in use."""
    global CONFIG_KEY, CONFIG, ARR_RUNWAYS, DEP_RUNWAYS, DEP_HEAVY, ATIS
    if key == CONFIG_KEY:
        return
    c = CONFIGS[key]
    CONFIG_KEY, CONFIG = key, c["name"]
    ARR_RUNWAYS = tuple(c["arr"])
    dep = tuple(c["dep"]) + tuple(r for r in c.get("dep_heavy", ()) if r not in c["dep"])
    DEP_RUNWAYS = dep
    DEP_HEAVY = tuple(c.get("dep_heavy", ()))
    ATIS = "KSFO information {info}. " + c["name"] + ": " + c["atis"] + " Wind " + c["wind"] + \
        ". Visibility 10. " + ATIS_TAIL
    active = tuple(dict.fromkeys(ARR_RUNWAYS + DEP_RUNWAYS))
    IN_USE.clear()
    for i in active:
        IN_USE[RUNWAYS[i].pavement] = i
    INTERSECTIONS.clear()
    INTERSECTIONS.update(_intersections(active))
    INTERSECTION_PTS.clear()
    for (a, b), u in INTERSECTIONS.items():
        INTERSECTION_PTS[(a, b)] = RUNWAYS[a].xy(u)
    for d in (EXITS, LINEUPS, LINEUP, DEP_HOLD):
        d.clear()
    NODE_ZONE.update({n: tuple(zone_runways(*q)) for n, q in NODES.items()})
    for i in active:
        EXITS[i] = [dict(e, hold_short=in_use(e["hold_short"])) if e["hold_short"] else e for e in _EXITS[i]]
    for i in DEP_RUNWAYS:
        LINEUPS[i] = _LINEUPS[i]
        LINEUP[i] = _lineup(i)
        DEP_HOLD[i] = LINEUP[i]["hold"]


set_config("west")


def procedures():
    from . import procs
    return procs


def summary():
    return {
        "field": FIELD, "icao": ICAO, "elev": ELEV, "magvar": MAGVAR, "cycle": CYCLE, "config": CONFIG,
        "arr_runways": ARR_RUNWAYS, "dep_runways": DEP_RUNWAYS,
        "exits": {k: [(round(e["u"] * FT_NM), e["side"], e["angle"], e["name"], e["hold_short"]) for e in v]
                  for k, v in EXITS.items()},
        "lineups": {k: [(round(e["u"] * FT_NM), e["name"], e["hold"]) for e in v] for k, v in LINEUPS.items()},
        "intersections_ft": {f"{a}x{b}": round(u * FT_NM) for (a, b), u in INTERSECTIONS.items()},
        "stands": (len(PAX_STANDS), len(CARGO_STANDS)),
    }


if __name__ == "__main__":
    import json
    print(json.dumps(summary(), indent=1))
