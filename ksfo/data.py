"""KSFO real-world data, loaded from ksfo/data/*.json, in a shape close to the
fictional airport in if-ai-atc-full/atc/world.py.

Local frame: nautical miles from the KSFO airport reference point, x east, y true
north at the ARP (azimuthal equidistant, see ksfo/geo.py). Altitudes in ft MSL
(KSFO field elevation 13 ft). NOT FOR REAL-WORLD NAVIGATION.

Quick use:
    from ksfo import data as K
    K.RUNWAYS["28L"]["threshold"]           # (x, y)
    K.FIXES["DUYET"]                         # (x, y)
    K.star_route("SERFR4", None, "28L")      # list of legs (dicts)
    K.STARS["BDEGA4"]                        # {transition: [(fix, (min_ft, max_ft)), ...]}
    K.TAXI_NODES, K.TAXI_EDGES, K.STANDS     # surface graph
    K.mva(x, y)                              # NCT minimum vectoring altitude (ft)
"""
import json
import math
from pathlib import Path

_DIR = Path(__file__).resolve().parent / "data"


def _load(name):
    with open(_DIR / name) as fh:
        return json.load(fh)


AIRPORT = _load("airport.json")
_PROCS = _load("procedures.json")
_FIXES = _load("fixes.json")
_HOLDS = _load("holds.json")
SURFACE = _load("surface.json")
try:
    _MVA = _load("mva.json")
except FileNotFoundError:
    _MVA = None

FIELD = "San Francisco International (KSFO)"
ARP = (AIRPORT["lat"], AIRPORT["lon"])
ELEVATION_FT = AIRPORT["elevation_ft"]
MAGVAR = AIRPORT["magvar"]                    # degrees east (+); true = magnetic + MAGVAR
CYCLE = AIRPORT["source"]["cycle"]
EFFECTIVE = AIRPORT["source"]["effective"]
TRANSITION_ALT = AIRPORT["transition_altitude_ft"]
TOWER = tuple(SURFACE["tower"]) if SURFACE.get("tower") else None

# ------------------------------------------------------------------ runways
RUNWAYS = {}
for _r in AIRPORT["runways"]:
    RUNWAYS[_r["ident"]] = {
        "threshold": (_r["threshold_x"], _r["threshold_y"]),            # landing threshold (displaced if any)
        "runway_end": (_r.get("runway_end_x"), _r.get("runway_end_y")),  # start of pavement
        "true_heading": _r.get("true_bearing"),
        "magnetic_heading": _r["magnetic_bearing"],
        "length_ft": _r["length_ft"],
        "width_ft": _r["width_ft"],
        "displaced_threshold_ft": _r["displaced_threshold_ft"],
        "elevation_ft": _r["threshold_elevation_ft"],
        "tch_ft": _r["tch_ft"],
        "opposite": _r.get("opposite"),
        "localizer": _r.get("localizer"),
        "lat": _r["threshold_lat"], "lon": _r["threshold_lon"],
    }
ILS = {}
for _l in AIRPORT["localizers"]:
    ILS[_l["runway"]] = {
        "ident": _l["ident"], "freq": _l["freq_mhz"], "category": _l["ils_category"],
        "loc_antenna": (_l["loc_x"], _l["loc_y"]),
        "gs_antenna": (_l.get("gs_x"), _l.get("gs_y")),
        "course_mag": _l["loc_magnetic_bearing"], "course_true": _l.get("true_course"),
        "gs_angle": _l["gs_angle_deg"], "tch_ft": _l["gs_tch_ft"], "loc_width_deg": _l["loc_width_deg"],
    }

# ------------------------------------------------------------------ fixes
FIX_RECORDS = _FIXES["fixes"]                   # key "IDENT/SECTION" -> full record
FIXES = {}                                       # ident -> (x, y); terminal wpts win on name clashes
for _pref in ("D ", "EA", "PC"):
    for _k, _f in FIX_RECORDS.items():
        if _f.get("section") == _pref and _f.get("x") is not None:
            FIXES[_f["ident"]] = (_f["x"], _f["y"])
for _k, _f in FIX_RECORDS.items():
    if _f.get("x") is not None:
        FIXES.setdefault(_f["ident"], (_f["x"], _f["y"]))

# ------------------------------------------------------------------ procedures
PROCEDURES = {p["id"]: p for p in _PROCS["procedures"]}
SID_IDS = sorted(k for k, p in PROCEDURES.items() if p["kind"] == "SID")
STAR_IDS = sorted(k for k, p in PROCEDURES.items() if p["kind"] == "STAR")
APPROACH_IDS = sorted(k for k, p in PROCEDURES.items() if p["kind"] == "APPROACH")
HOLDS = _HOLDS["holds"]


def leg_pos(leg):
    """(x, y) of a leg's terminating fix, or None (heading/course legs)."""
    ref = leg.get("fix_ref")
    f = FIX_RECORDS.get(ref) if ref else None
    return (f["x"], f["y"]) if f and f.get("x") is not None else None


def _band(leg):
    a = leg.get("altitude")
    return (a.get("min_ft"), a.get("max_ft")) if a else (None, None)


def _simple(legs):
    """[(fix, (min_ft, max_ft))] for legs that end at a fix (like world.STARS)."""
    out = []
    for leg in legs:
        if leg.get("fix") and leg["path_term"] not in ("HM", "HA", "HF"):
            if out and out[-1][0] == leg["fix"]:
                continue
            out.append((leg["fix"], _band(leg)))
    return out


def _by_kind(kind):
    res = {}
    for pid in sorted(PROCEDURES):
        p = PROCEDURES[pid]
        if p["kind"] != kind:
            continue
        res[pid] = {(t["transition"] or "COMMON"): _simple(t["legs"]) for t in p["transitions"]}
    return res


STARS = _by_kind("STAR")          # {star: {transition: [(fix, (min, max))]}}
SIDS = _by_kind("SID")


def _find(pid, segment, name=None, runway=None):
    for t in PROCEDURES[pid]["transitions"]:
        if t["segment"] != segment:
            continue
        if name is not None and t["transition"] != name:
            continue
        if runway is not None and not (t.get("runways") and runway in t["runways"]):
            continue
        return t
    return None


def _join(*parts):
    legs = []
    for t in parts:
        if not t:
            continue
        for leg in t["legs"]:
            # the first IF of a following segment repeats the previous end fix
            if legs and leg["path_term"] == "IF" and legs[-1].get("fix") == leg.get("fix"):
                continue
            legs.append(leg)
    return legs


def star_route(star, enroute=None, runway=None):
    """Full leg list: enroute transition + common route + runway transition."""
    return _join(_find(star, "enroute_transition", enroute) if enroute else None,
                 _find(star, "common"),
                 _find(star, "runway_transition", runway=runway) if runway else None)


def sid_route(sid, runway, enroute=None):
    """Full leg list: runway transition + common route + enroute transition."""
    return _join(_find(sid, "runway_transition", runway=runway),
                 _find(sid, "common"),
                 _find(sid, "enroute_transition", enroute) if enroute else None)


def approach_route(app, transition=None, missed=True):
    """Approach transition (e.g. 'ARCHI') + final segment (+ missed approach legs)."""
    p = PROCEDURES[app]
    trans = None
    if transition:
        trans = next(t for t in p["transitions"] if t["segment"] == "transition" and t["transition"] == transition)
    final = next(t for t in p["transitions"] if t["segment"] == "final")
    legs = _join(trans, final)
    return legs if missed else [l for l in legs if not l.get("missed")]


def approach_fixes(app):
    """{'IAF': [...], 'IF': [...], 'FAF': fix, 'MAP': fix, 'FAF_ALT': ft} for one approach."""
    out = {"IAF": [], "IF": [], "FAF": None, "MAP": None, "FAF_ALT": None}
    for t in PROCEDURES[app]["transitions"]:
        for leg in t["legs"]:
            role = leg.get("role") or ""
            if role.startswith("IAF") and leg["fix"] not in out["IAF"]:
                out["IAF"].append(leg["fix"])
            elif role in ("IF", "final approach course fix") and leg["fix"] not in out["IF"]:
                out["IF"].append(leg["fix"])
            elif role == "FAF":
                out["FAF"] = leg["fix"]
                out["FAF_ALT"] = _band(leg)[0]
            elif role == "missed approach point":
                out["MAP"] = leg["fix"]
    return out


# ------------------------------------------------------------------ surface
TAXI_NODES = {k: (v["x"], v["y"]) for k, v in SURFACE["nodes"].items()}
TAXI_EDGES = [(e["a"], e["b"], e["name"] or "") for e in SURFACE["edges"]]
TAXI_EDGE_INFO = SURFACE["edges"]               # full records: kind, length_nm, geometry, osm_way
TAXIWAYS = SURFACE["taxiway_names"]
STANDS = {s["ref"]: s["node"] for s in SURFACE["stands"] if s.get("ref") and s.get("node")}
HOLD_POINTS = {k: v["holding_position"] for k, v in SURFACE["nodes"].items() if "holding_position" in v}


# ------------------------------------------------------------------ MVA
def _in_ring(x, y, ring):
    inside = False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i]
        xj, yj = ring[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def mva_sector(x, y, chart="FUS3"):
    """The NCT MVA sector containing (x, y), or None outside the chart."""
    if not _MVA:
        return None
    for c in _MVA["charts"]:
        if chart not in c["chart"]:
            continue
        for s in c["sectors"]:
            if _in_ring(x, y, s["exterior_xy"]) and not any(_in_ring(x, y, h) for h in s["holes_xy"]):
                return s
    return None


def mva(x, y):
    """Minimum vectoring altitude (ft MSL) at a point: FUS3 chart, then FUS5, else
    the 25 nm KSFO MSA as a conservative fallback."""
    for chart in ("FUS3", "FUS5"):
        s = mva_sector(x, y, chart)
        if s:
            return s["mva_ft"]
    return MSA_FT


MSA_FT = next((m["sectors"][0]["altitude_ft"] for m in AIRPORT["msa"] if m["center"] == "KSFO"), 5100)


# ------------------------------------------------------------------ helpers (same as world.py)
def dist(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


def bearing(frm, to):
    """Grid bearing in the local frame (= true bearing near the ARP)."""
    return (math.degrees(math.atan2(to[0] - frm[0], to[1] - frm[1])) + 360) % 360


def true_to_mag(t):
    return (t - MAGVAR) % 360


def mag_to_true(m):
    return (m + MAGVAR) % 360
