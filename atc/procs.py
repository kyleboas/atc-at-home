"""CIFP procedures compiled into flyable legs (true courses, local x/y positions).

A leg is a dict:
  pt        ARINC 424 path terminator: IF TF CF DF RF CA VA VI VM FM HM HA HF (others are
            flown as the nearest equivalent: FA/FC/FD as CF-like, CD/CI/VD as VA/VI)
  fix, pos  terminating fix name and (x, y), None for CA/VA/VI/VM
  crs       true course/heading (CF CA VA VI VM FM HM), None otherwise
  turn      'L' / 'R' / None (required turn direction); flyover True for flyover fixes
  amin/amax altitude window at the fix (ft), None = no limit;  alt_term for CA/VA (ft)
  spd, sptype  speed limit at the fix (kt) and 'at' | 'max' | 'min'
  radius, center  RF legs;  hold = {crs, turn, leg_min, leg_nm} on HM/HA/HF
  tag       'star' | 'sid' | 'app' (approach transition) | 'final' | 'missed'
  vangle    vertical path angle (deg) on final legs; role (IAF, IF, FAF, MAP ...)
NOT FOR REAL-WORLD NAVIGATION.
"""
import math

from . import world as W
from .world import K

APPROACH_IDS = {  # runway -> {kind: CIFP id}  (kinds: ils, gps = RNAV (GPS), rnav = RNAV (RNP))
    "28L": {"ils": "I28L", "gps": "R28L"},
    "28R": {"ils": "I28R", "gps": "R28RZ", "rnav": "H28RY"},
    "19L": {"ils": "I19L", "gps": "R19L"},
    "19R": {"gps": "R19RY"},                     # no ILS
    "10L": {"gps": "R10L"},                      # no ILS
    "10R": {"gps": "R10RY", "rnav": "H10RZ"},    # no ILS
}


def default_approach(runway):
    """The approach a runway gets when the requested one is not published for it (ILS, else GPS)."""
    ids = APPROACH_IDS.get(runway, {})
    return "ils" if "ils" in ids else "gps"
APPROACH_NAMES = {pid: K.PROCEDURES[pid]["name"] for r in APPROACH_IDS.values() for pid in r.values()}


def _alt(leg):
    a = leg.get("altitude")
    if not a:
        return None, None
    t = a.get("type")
    lo, hi = a.get("min_ft"), a.get("max_ft")
    if t == "at":
        lo = hi = a["alt1"]["ft"]
    return lo, hi


def compile_leg(raw, tag, proc, ils_course=None):
    pt = raw["path_term"]
    crs = raw.get("course_deg")
    if crs is not None and not raw.get("course_is_true"):
        crs = W.mag2true(crs)
    if ils_course is not None and pt == "CF" and tag == "final":
        crs = ils_course
    lo, hi = _alt(raw)
    leg = {"pt": pt, "fix": raw.get("fix"), "pos": K.leg_pos(raw), "crs": crs,
           "turn": {"left": "L", "right": "R"}.get(raw.get("turn")), "flyover": bool(raw.get("flyover")),
           "amin": lo, "amax": hi, "spd": raw.get("speed_kt"),
           "sptype": {"at": "at", "at or below": "max", "at or above": "min"}.get(raw.get("speed_type"), "at")
           if raw.get("speed_kt") else None,
           "tag": tag, "proc": proc, "role": raw.get("role"), "vangle": raw.get("vertical_angle_deg"),
           "dist": raw.get("distance_nm"), "alt_term": None}
    if pt in ("CA", "VA", "FA") and lo is not None:
        leg["alt_term"] = lo
    if pt == "RF":
        c = K.FIX_RECORDS.get(raw.get("arc_center_ref") or "") or {}
        cpos = (c["x"], c["y"]) if c.get("x") is not None else K.FIXES.get(raw.get("arc_center"))
        leg["center"], leg["radius"] = cpos, raw.get("arc_radius_nm")
    if pt in ("HM", "HA", "HF"):
        leg["hold"] = {"crs": crs, "turn": leg["turn"] or "R", "leg_min": raw.get("hold_time_min"),
                       "leg_nm": raw.get("distance_nm") if raw.get("hold_time_min") is None else None}
    if leg["fix"] and leg["fix"].startswith("RW") and tag == "final":
        leg["map"] = True
        leg["amin"] = leg["amax"] = None       # threshold crossing height: flown on the glidepath
    if raw.get("role") == "FAF":
        leg["faf"] = True
    return leg


def compile_legs(raws, tag, proc, ils_course=None):
    return [compile_leg(r, tag, proc, ils_course) for r in raws]


# ------------------------------------------------------------------ routes
def _seg(pid, segment, name=None, runway=None):
    for t in K.PROCEDURES[pid]["transitions"]:
        if t["segment"] != segment:
            continue
        if name is not None and t["transition"] != name:
            continue
        if runway is not None and not (t.get("runways") and runway in t["runways"]):
            continue
        return t
    return None


def join(*legsets):
    out = []
    for legs in legsets:
        for leg in legs or []:
            if out and leg["pt"] == "IF" and out[-1].get("fix") == leg.get("fix"):
                # same fix: keep the stricter constraint of the two
                prev = out[-1]
                for k in ("amin",):
                    if leg[k] is not None:
                        prev[k] = max(prev[k] or leg[k], leg[k])
                for k in ("amax",):
                    if leg[k] is not None:
                        prev[k] = min(prev[k] or leg[k], leg[k])
                if leg.get("spd") and not prev.get("spd"):
                    prev["spd"], prev["sptype"] = leg["spd"], leg["sptype"]
                continue
            out.append(dict(leg))
    return out


def star_legs(star, enroute=None, runway=None):
    p = K.PROCEDURES[star]
    parts = []
    if enroute:
        t = _seg(star, "enroute_transition", enroute)
        parts.append(compile_legs(t["legs"], "star", star) if t else [])
    c = _seg(star, "common")
    parts.append(compile_legs(c["legs"], "star", star) if c else [])
    if runway and p.get("runway_transitions"):
        t = _seg(star, "runway_transition", runway=runway)
        parts.append(compile_legs(t["legs"], "star", star) if t else [])
    return join(*parts)


def sid_legs(sid, runway, enroute=None):
    parts = []
    t = _seg(sid, "runway_transition", runway=runway)
    parts.append(compile_legs(t["legs"], "sid", sid) if t else [])
    c = _seg(sid, "common")
    parts.append(compile_legs(c["legs"], "sid", sid) if c else [])
    if enroute:
        t = _seg(sid, "enroute_transition", enroute)
        parts.append(compile_legs(t["legs"], "sid", sid) if t else [])
    legs = join(*parts)
    # an enroute transition that restarts at the airport (GAPP7: IF KSFO, DF fix) keeps only the DF
    return [leg for leg in legs if not (leg["pt"] == "IF" and leg.get("fix") == "KSFO")]


def approach_kind(pid):
    return {"I": "ils", "R": "gps", "H": "rnav", "L": "loc"}.get(pid[0], "ils")


def approach_runway(pid):
    return K.PROCEDURES[pid]["runway"]


def approach_transitions(pid):
    return [t["transition"] for t in K.PROCEDURES[pid]["transitions"] if t["segment"] == "transition"]


def approach_legs(pid, transition=None):
    """Transition (tag 'app') + final segment to the MAP (tag 'final') + missed approach ('missed')."""
    rwy = W.RUNWAYS[approach_runway(pid)]
    ils_course = rwy.loc_course if pid[0] in ("I", "L") else None
    parts = []
    if transition:
        t = _seg(pid, "transition", transition)
        parts.append(compile_legs(t["legs"], "app", pid) if t else [])
    f = _seg(pid, "final")
    legs, missed = [], False
    for raw in f["legs"]:
        missed = missed or bool(raw.get("missed"))
        legs.append(compile_leg(raw, "missed" if missed else "final", pid, ils_course))
    for i, leg in enumerate(legs):              # everything after the MAP is the missed approach
        if leg.get("map"):
            for m in legs[i + 1:]:
                m["tag"] = "missed"
            break
    return join(*parts, legs)


def transition_for(pid, fix):
    """The approach transition that starts at `fix` (the STAR's last fix), if any."""
    for t in K.PROCEDURES[pid]["transitions"]:
        if t["segment"] == "transition" and t["legs"] and t["legs"][0].get("fix") == fix:
            return t["transition"]
    return None


def final_legs(pid):
    return [leg for leg in approach_legs(pid) if leg["tag"] == "final"]


def intermediate_fix(pid):
    """The fix where the final approach course starts (IF / FACF) and its altitude."""
    f = final_legs(pid)
    return f[0]["fix"], f[0]["pos"], f[0]["amin"]


def faf(pid):
    for leg in approach_legs(pid):
        if leg.get("faf"):
            return leg
    return None


def glide_angle(pid):
    rwy = W.RUNWAYS[approach_runway(pid)]
    if pid[0] == "I":
        return rwy.gs_angle
    for leg in approach_legs(pid):
        if leg["tag"] == "final" and leg.get("vangle"):
            return abs(leg["vangle"])
    return 3.0


# ------------------------------------------------------------------ geometry
def arc_points(center, r, a0, a1, turn, n=None):
    """Points on an arc about `center` from bearing a0 to a1 (from the centre), turning L/R."""
    s = 1 if turn == "R" else -1
    span = ((a1 - a0) * s) % 360
    n = n or max(2, int(span / 5) + 1)
    return [(center[0] + r * math.sin(math.radians(a0 + s * span * i / n)),
             center[1] + r * math.cos(math.radians(a0 + s * span * i / n))) for i in range(n + 1)]


def leg_geometry(prev, leg, nxt=None):
    """Nominal path of a leg from point `prev` as a polyline (for drawing and track miles)."""
    pt = leg["pt"]
    if pt == "RF" and leg.get("center") and prev is not None:
        c, r = leg["center"], leg["radius"]
        return arc_points(c, r, W.bearing(c, prev), W.bearing(c, leg["pos"]), leg["turn"] or "R")
    if pt in ("CA", "VA", "FA") and prev is not None:
        d = 1.2
        u = W.unit(leg["crs"])
        return [prev, (prev[0] + u[0] * d, prev[1] + u[1] * d)]
    if pt == "VI" and prev is not None and nxt is not None and nxt.get("pos") and nxt.get("crs") is not None:
        p = intersect_ray(prev, leg["crs"], nxt["pos"], nxt["crs"])
        return [prev, p] if p else [prev]
    if pt in ("VM", "FM") and prev is not None:
        u = W.unit(leg["crs"])
        return [prev, (prev[0] + u[0] * 6, prev[1] + u[1] * 6)]
    if leg.get("pos") is not None:
        return [prev, leg["pos"]] if prev is not None else [leg["pos"]]
    return [prev] if prev is not None else []


def intersect_ray(p, h, q, c):
    """Point where a ray from p on heading h meets the line through q with course c."""
    ux, uy = W.unit(h)
    vx, vy = W.unit(c)
    det = ux * (-vy) - uy * (-vx)
    if abs(det) < 1e-9:
        return None
    t = ((q[0] - p[0]) * (-vy) - (q[1] - p[1]) * (-vx)) / det
    return (p[0] + t * ux, p[1] + t * uy) if t > 0 else None


def route_polyline(legs, start=None):
    pts, prev = [], start
    for i, leg in enumerate(legs):
        if leg["pt"] in ("HM", "HA", "HF"):
            continue
        g = leg_geometry(prev, leg, legs[i + 1] if i + 1 < len(legs) else None)
        if pts and g and g[0] == pts[-1]:
            g = g[1:]
        pts.extend(g)
        if g:
            prev = g[-1]
    return pts


def polyline_len(pts):
    return sum(W.dist(pts[i], pts[i + 1]) for i in range(len(pts) - 1))


def hold_racetrack(fix, crs, turn, leg_nm=None, leg_min=1.0, gs=230.0):
    """Racetrack points for drawing a hold (inbound course crs true)."""
    r = (gs / 3600) / math.radians(min(3.0, math.degrees(9.80665 / 1852 * math.tan(math.radians(25)) / (gs / 3600))))
    L = leg_nm or gs / 60 * (leg_min or 1.0)
    s = 1 if turn == "R" else -1
    ux, uy = W.unit(crs)
    rx, ry = uy * s, -ux * s                  # toward the holding side
    p0 = fix
    p1 = (fix[0] + 2 * r * rx, fix[1] + 2 * r * ry)
    p2 = (p1[0] - ux * L, p1[1] - uy * L)
    p3 = (fix[0] - ux * L, fix[1] - uy * L)
    c1 = (fix[0] + r * rx, fix[1] + r * ry)
    c2 = (p3[0] + r * rx, p3[1] + r * ry)
    a1 = arc_points(c1, r, W.bearing(c1, p0), W.bearing(c1, p1), turn)
    a2 = arc_points(c2, r, W.bearing(c2, p2), W.bearing(c2, p3), turn)
    return a1 + [p2] + a2 + [p0]


# ------------------------------------------------------------------ scenario helpers
def star_runway_transition(star, runway):
    p = K.PROCEDURES[star]
    if not p.get("runway_transitions"):
        return None
    t = _seg(star, "runway_transition", runway=runway)
    return t["transition"] if t else None


def star_end(legs):
    """Last fix of a STAR route and whether it ends in vectors (FM/VM)."""
    vec = bool(legs) and legs[-1]["pt"] in ("FM", "VM")
    fixes = [leg for leg in legs if leg.get("pos") is not None and leg["pt"] not in ("FM", "VM")]
    return (fixes[-1]["fix"] if fixes else None), vec


def descent_profile(legs, bottom=None, grad=310.0):
    """Ideal 'descend via' altitude at each leg end, computed backward from the last
    constraint at ~3 degrees and clamped into each altitude window."""
    pts = route_polyline(legs)
    n = len(legs)
    alts = [None] * n
    # distance from each leg end to the route end
    ends, prev, cum = [], None, 0.0
    for i, leg in enumerate(legs):
        g = leg_geometry(prev, leg, legs[i + 1] if i + 1 < n else None)
        cum += polyline_len(g) if len(g) > 1 else 0.0
        ends.append(cum)
        if g:
            prev = g[-1]
    total = cum
    alt = bottom
    for i in range(n - 1, -1, -1):
        leg = legs[i]
        if alt is None:
            alt = leg["amax"] if leg["amax"] is not None else leg["amin"]
        else:
            nxt_d = ends[i + 1] - ends[i] if i + 1 < n else 0.0
            alt = alt + nxt_d * grad
        if leg["amax"] is not None:
            alt = min(alt, leg["amax"]) if alt is not None else leg["amax"]
        if leg["amin"] is not None:
            alt = max(alt, leg["amin"]) if alt is not None else leg["amin"]
        alts[i] = alt
    return alts, ends, total, pts
