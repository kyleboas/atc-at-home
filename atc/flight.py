"""Aircraft state and the airborne flight model: physics, LNAV (ARINC leg by leg), VNAV
(altitude/speed constraints), holds, localizer/glidepath, flare.

Physics (generalised from the Demo Field model, no wind, TAS = ground speed):
  turns     standard rate (3 deg/s) capped by a 25 degree bank, w = g tan(25)/TAS, with a
            roll-in/roll-out (turn rate changes by at most 1 deg/s per second)
  vertical  vertical speed ramps 150 fpm/s (450 below 1500 ft for rotation and go-arounds)
            toward the type's climb/descent rate, levelling off without overshoot
  speed     IAS toward a target: +1.5 kt/s (1.0 climbing), -1.0 kt/s (0.5 descending clean)
  final     the runway's own glidepath: TCH at the landing threshold and the published angle
            (ILS 28L 2.85, others 3.0); captured from below, at most ~1500 fpm to rejoin from above
LNAV legs (CIFP path terminators): IF/DF direct, TF great-circle track (straight in the local
frame), CF course to fix, RF constant-radius arc about the centre fix with a feed-forward turn
rate, CA/VA course/heading to an altitude, VI heading to intercept the next leg, VM heading and
FM course from a fix to a manual termination (the aircraft stays on it awaiting vectors), HM/HA/HF
racetrack holds. Fly-by fixes are sequenced at the turn lead R tan(delta/2) + roll-in, fly-over
fixes overhead.
"""
import math
import random

from . import procs as P
from . import world as W

G_NM = 9.80665 / 1852
MAX_BANK = 25.0
STD_RATE = 3.0
ROLL_ACC = 1.0
VS_ACC = 150.0
VS_ACC_LOW = 450.0
FLARE_FT = 30.0
FLARE_TAU = 1.3
CLIMBOUT_FT = 400
DESC_GRAD = 300.0                 # ft per nm a pilot plans a 'descend via' path with (~3 deg)
DECEL_NM = 5.0                    # kt per nm when planning a slow-down to a speed limit (0.5 kt/s in a descent)
HOLD_SPD = (230, 265)             # at/below 14,000 ft, above
LOC_HALF_WIDTH = 2.5              # deg either side: the needle comes alive
HOLD_LEGS = ("HM", "HA", "HF")


def max_turn_rate(gs):
    v = max(gs, 40.0) / 3600
    return min(STD_RATE, math.degrees(G_NM * math.tan(math.radians(MAX_BANK)) / v))


def turn_radius(gs):
    return (max(gs, 40.0) / 3600) / math.radians(max_turn_rate(gs))


def sqrt_law(err, rate_acc, rate_max):
    return math.copysign(min(rate_max, math.sqrt(2 * rate_acc * 0.8 * abs(err))), err)


class Aircraft:
    def __init__(self, spec, seed):
        self.cs = spec["cs"]
        self.type = spec["type"]
        self.perf = W.TYPES[self.type]
        self.cat = self.perf["cat"]
        self.wake = self.perf["wake"]
        self.intent = spec["intent"]
        self.spec = spec
        self.rng = random.Random(f"{seed}:{self.cs}")
        self.sloppy = spec.get("sloppy", False)
        self.reaction = spec.get("reaction", 4)
        self.will_rto = spec.get("rto", False)
        self.ifr = True
        self.stand = spec.get("stand")
        self.cargo = spec.get("cargo", False)
        # flight plan
        self.star, self.star_trans = spec.get("star"), spec.get("star_trans")
        self.sid, self.sid_trans = spec.get("sid"), spec.get("sid_trans")
        self.appr_req = spec.get("approach")          # ils | gps | rnav (arrivals)
        self.runway = spec.get("runway")              # landing or departure runway
        self.appr_exp = None                          # expected approach procedure id
        self.appr_cleared = None                      # cleared approach procedure id
        self.filed_alt = spec.get("filed_alt", 35000)
        # physics
        self.x = self.y = self.alt = self.ias = self.gs = self.hdg = 0.0
        self.vs = 0.0
        self.rate = 0.0
        # navigation
        self.nav = "lnav"                 # lnav | heading
        self.legs = []                    # active route (compiled legs)
        self.exp_legs = []                # expected approach legs (FPL, not yet cleared)
        self.leg_from = None              # start point of the active leg
        self.legs_ver = 0
        self._cum = None
        self.hdg_target = None            # true
        self.vectors_expected = False     # on a VM/FM leg or vectors
        self.holdp = None
        self.turn360 = None
        self.saved_legs = None
        self.climbout = False
        self.gs_cap = False
        self.established = False
        self.loc_armed = False
        self.icpt_angle = None
        self.alt_assigned = None
        self.alt_until = None             # "maintain X until established"
        self.via = True                   # descend/climb via the procedure constraints
        self.proc_spd = None              # speed limit from the last procedure fix passed
        self.spd = None                   # ATC speed assignment
        self.expedite_alt = False
        self.missed = False
        self.radar_contact = False
        self.hold_alt = None
        self.hold_fix = None              # ATC hold at a fix on the route: dict(fix, turns, alt)
        self.exit_hold = False
        self.final_rwy = None             # Runway object of the approach being flown
        self.glide_angle = 3.0
        # ground / runway
        self.phase = "pending"
        self.freq = None
        self.timer = 0.0
        self.hold = False
        self.hold_until = None
        self.give_way = None              # {"cs": traffic, "t0", "clear_s", "still_s"}: "follow" (7110.65 3-7-2)
        self.gw_stop = False              # stopped now, giving way
        self.msg_due = None
        self.gpath = []                   # taxi polyline [(x, y, node|None)]
        self.gk = 0                       # index of the next point
        self.gstops = []                  # [{"k": index, "rwy": ident, "cleared": bool, "done": bool}]
        self.gnode = None
        self.gspeed = 0.0
        self.blocked_s = 0
        self.taxi_total = 1.0
        self.hs_rwy = None                # holding short of this runway (between runways after an exit)
        self.cross_clear = set()          # runways cleared to cross
        self.contact_tower_when_ready = False
        self.takeoff_clear = False
        self.immediate = False
        self.roll_timer = None
        self.rot = None
        self.flare = False
        self.touchdown_u = None
        self.clearance = None             # landing clearance runway
        self.leader = None
        self.circuit = None               # "left" | "right": flying a visual circuit after a go-around
        self.sequence = None
        self.exit_cmd = None
        self.expedite_exit = False
        self.switch_to_ground_when_clear = False
        self.target_exit = None
        self.rto_done = False
        self.flags = set()
        self.zone = ()                    # runway strips the aircraft is in (ground)
        self.t_freq = 0
        self.m = dict(t_spawn=None, t_push_req=None, t_push_ok=None, t_hold_short=None, t_roll=None,
                      t_airborne=None, t_landed=None, nominal=None, go_arounds=0, done=False, rto=False,
                      level_s=0, hold_s=0, cross_wait_s=0, fuel_state=None,
                      t_mayday=None, mayday_limit=None, t_diverted=None)
        # Fuel (arrivals): minutes of delay the aircraft can absorb before it would land on its final
        # reserve (14 CFR 121.639 / ICAO Annex 6). What is left of contingency plus any extra fuel at the
        # edge of the approach airspace: about 10-25 min for narrowbodies and regionals (short trips),
        # 25-45 min for widebodies (long trips, larger 5% contingency). Its own RNG so the rest of the
        # scenario stays the same.
        frng = random.Random(f"{seed}:{self.cs}:fuel")
        self.fuel_extra_s = 60 * (frng.uniform(25, 45) if self.wake in ("H", "J") else frng.uniform(10, 25))

    @property
    def airborne(self):
        return self.phase == "airborne"

    @property
    def on_ground(self):
        return self.phase not in ("airborne", "pending")

    def leg(self):
        if not self.airborne:
            return None
        if self.turn360:
            return "360"
        if self.holdp is not None:
            return "hold"
        if self.nav == "heading":
            return "vectors"
        if not self.legs:
            return None
        lg = self.legs[0]
        if lg["pt"] in ("FM", "VM"):
            return "vectors"
        if self.climbout:
            return "climbout"
        return lg["tag"]

    def next_fix(self):
        for lg in self.legs:
            if lg.get("fix") and lg["pt"] not in HOLD_LEGS and lg["pt"] not in ("FM",):
                return lg["fix"]
        return None

    def say(self, sim, kind, data=None):
        sim.pilot_message(self, kind, data or {})

    def set_legs(self, legs, leg_from=None):
        self.legs = legs
        self.leg_from = leg_from
        self.legs_ver += 1
        self._cum = None
        self.holdp = None


# ------------------------------------------------------------------ physics
def turn(ac, want=None, cmd=None):
    wmax = max_turn_rate(ac.gs)
    if cmd is None:
        e = W.ang_diff(ac.hdg, want)
        cmd = math.copysign(min(wmax, math.sqrt(2 * ROLL_ACC * 0.8 * abs(e)), 0.7 * abs(e)), e)
    cmd = max(-wmax, min(wmax, cmd))
    ac.rate += max(-ROLL_ACC, min(ROLL_ACC, cmd - ac.rate))
    old = ac.hdg
    ac.hdg = (ac.hdg + ac.rate) % 360
    return old


def advance(ac, old_hdg):
    h = math.radians(old_hdg + W.ang_diff(old_hdg, ac.hdg) / 2)
    step = ac.gs / 3600
    ac.x += math.sin(h) * step
    ac.y += math.cos(h) * step


def vertical(ac, ta=None, vs_cmd=None):
    """Ramp the vertical speed toward a target altitude (or hold a commanded VS)."""
    p = ac.perf
    k = 1.5 if ac.expedite_alt else 1.0
    climb, desc = p["climb"] * k, p["descent"] * k
    if ac.alt > 10000:
        climb *= max(0.45, 1 - (ac.alt - 10000) / 45000)
    level = vs_cmd is None
    start = ac.alt
    for _ in range(4):
        if level:
            err = ta - ac.alt
            vs_cmd = 60 * sqrt_law(err, VS_ACC / 60, (climb if err > 0 else desc) / 60) if err else 0.0
        a = (VS_ACC_LOW if (ac.alt < 1500 and vs_cmd > ac.vs) else VS_ACC) / 4
        old_vs, old_alt = ac.vs, ac.alt
        ac.vs += max(-a, min(a, vs_cmd - ac.vs))
        ac.alt += (old_vs + ac.vs) / 2 / 60 / 4
        if level and ta is not None and ta != old_alt and (ta - old_alt) * (ta - ac.alt) <= 0:
            ac.alt, ac.vs = float(ta), 0.0
        ac.alt = max(0.0, ac.alt)
    return ac.alt - start


# ------------------------------------------------------------------ route geometry / track miles
def leg_course_at(prev, leg):
    """Course of a leg at its start, for turn anticipation."""
    pt = leg["pt"]
    if pt == "RF" and leg.get("center") and prev is not None:
        b = W.bearing(leg["center"], prev)
        return (b + (90 if leg["turn"] == "R" else -90)) % 360
    if pt in ("CF", "CA", "VA", "VI", "VM", "FM", "FA", "CI", "VD", "CD", "FC", "FD"):
        return leg["crs"]
    if leg.get("pos") is not None and prev is not None and W.dist(prev, leg["pos"]) > 1e-6:
        return W.bearing(prev, leg["pos"])
    return None


def leg_end(prev, leg):
    if leg.get("pos") is not None:
        return leg["pos"]
    g = P.leg_geometry(prev, leg)
    return g[-1] if g else prev


def cum_lengths(ac):
    """Track miles from the end of each active leg to the landing threshold (arrivals), using the
    nominal geometry; vector legs are estimated as a turn onto a 10 nm final."""
    if ac._cum is not None:
        return ac._cum
    legs = ac.legs + ([] if ac.appr_cleared else ac.exp_legs)
    n = len(ac.legs)
    seg, prev = [], None
    ends = []
    for i, lg in enumerate(legs):
        if lg["pt"] in HOLD_LEGS:
            seg.append(0.0)
            ends.append(prev)
            continue
        if lg["tag"] == "missed":
            seg.append(None)
            ends.append(prev)
            continue
        g = P.leg_geometry(prev, lg, legs[i + 1] if i + 1 < len(legs) else None)
        seg.append(P.polyline_len(g) if len(g) > 1 and prev is not None else 0.0)
        if g:
            prev = g[-1]
        ends.append(prev)
    # from the end of each leg to the threshold
    total = 0.0
    out = [0.0] * len(legs)
    tail_est = 0.0
    if legs and not any(lg.get("map") for lg in legs if lg["tag"] != "missed"):
        rwy = W.RUNWAYS.get(ac.runway) if ac.runway in W.ARR_RUNWAYS else None
        last = next((e for e in reversed(ends) if e is not None), None)
        if rwy is not None and last is not None:
            p10 = rwy.xy(rwy.u_thr - 10.0)
            tail_est = W.dist(last, p10) * 1.15 + 10.0
    total = tail_est
    for i in range(len(legs) - 1, -1, -1):
        out[i] = total
        if seg[i] is not None:
            total += seg[i] if i > 0 else 0.0
    # out[i] = distance from end of leg i to the threshold; legs before i add seg[i]
    res = [0.0] * len(legs)
    acc = tail_est
    for i in range(len(legs) - 1, -1, -1):
        res[i] = acc
        if seg[i] is not None:
            acc += seg[i]
    ac._cum = (res[:n], res, ends)
    return ac._cum


def dist_to_leg_end(ac, leg):
    pos = leg.get("pos")
    if leg["pt"] == "RF" and leg.get("center"):
        c, r = leg["center"], leg["radius"]
        a = W.bearing(c, (ac.x, ac.y))
        b = W.bearing(c, pos)
        s = 1 if leg["turn"] == "R" else -1
        span = ((b - a) * s) % 360
        if span > 300:
            span = 0.0
        return r * math.radians(span)
    if pos is None:
        return 0.0
    if leg["pt"] in ("CF", "TF") and leg["tag"] == "final":
        c = leg["crs"] if leg["pt"] == "CF" else (W.bearing(ac.leg_from, pos) if ac.leg_from else W.bearing((ac.x, ac.y), pos))
        ux, uy = W.unit(c)
        return max(0.0, (pos[0] - ac.x) * ux + (pos[1] - ac.y) * uy)
    return W.dist((ac.x, ac.y), pos)


def remaining(ac):
    """Track miles to the landing threshold (arrivals), None if unknown."""
    if ac.intent != "arrival" or not ac.airborne:
        return None
    rwy = W.RUNWAYS.get(ac.runway)
    if ac.nav == "heading" or ac.turn360 or not ac.legs:
        if rwy is None:
            return None
        a, v = rwy.to_thr(ac.x, ac.y)
        if a > 0:
            return a + abs(v) * 1.15
        return math.hypot(a, v) * 1.3 + 12.0
    cum = cum_lengths(ac)[0]
    if ac.holdp is not None:
        # to the hold fix, then the whole of the next leg (cum[0] starts at its end)
        lg = ac.legs[0]
        first = W.dist(ac.holdp["fix"], lg["pos"]) if lg.get("pos") is not None and lg["pt"] != "RF" else (
            dist_to_leg_end(ac, lg) if lg.get("pos") is not None else 0.0)
        return hold_to_fix(ac) + first + (cum[0] if cum else 0.0)
    lg = ac.legs[0]
    if lg["tag"] == "missed":
        return None
    if lg["pt"] in ("FM", "VM"):
        if rwy is None:
            return None
        p10 = rwy.xy(rwy.u_thr - 10.0)
        return W.dist((ac.x, ac.y), p10) * 1.15 + 10.0
    return dist_to_leg_end(ac, lg) + (cum[0] if cum else 0.0)


def hold_to_fix(ac):
    H = ac.holdp
    if H is None:
        return 0.0
    crs, R = H["crs"], turn_radius(ac.gs)
    ux, uy = W.unit(crs)
    along = (H["fix"][0] - ac.x) * ux + (H["fix"][1] - ac.y) * uy
    if H["state"] == "inbound":
        return max(0.0, along)
    togo = ((crs - ac.hdg) * H["dir"]) % 360
    return R * math.radians(togo) + max(0.0, along) + (H.get("t", 0) / 3600 * ac.gs if H["state"] == "outbound" else 0)


# ------------------------------------------------------------------ LNAV
def lead_for(ac, c, c1):
    if c is None or c1 is None:
        return 0.0
    d = abs(W.ang_diff(c, c1))
    if d <= 1:
        return 0.0
    v, R = ac.gs / 3600, turn_radius(ac.gs)
    return min(R * math.tan(math.radians(min(d, 120.0) / 2)), 2 * R) + v * max_turn_rate(ac.gs) / ROLL_ACC / 2


def track_line(ac, A, c):
    """Cross-track steering onto the line through A with course c: wanted heading."""
    ux, uy = W.unit(c)
    xtk = (ac.x - A[0]) * uy - (ac.y - A[1]) * ux       # + right of course
    v = ac.gs / 3600
    return (c - max(-45.0, min(45.0, math.degrees(math.atan2(xtk, max(0.3, v * 20)))))) % 360, xtk


def steer(ac):
    """One LNAV step on ac.legs[0]: returns (want heading | None, turn cmd | None, passed)."""
    lg = ac.legs[0]
    pt = lg["pt"]
    pos = lg.get("pos")
    me = (ac.x, ac.y)
    v, R = ac.gs / 3600, turn_radius(ac.gs)
    nxt = ac.legs[1] if len(ac.legs) > 1 else None
    if pt in ("CA", "VA", "FA"):
        want = lg["crs"]
        done = lg.get("alt_term") is None and ac.leg_from is not None and W.dist(me, ac.leg_from) > 1.5
        return want, None, (lg.get("alt_term") is not None and ac.alt >= lg["alt_term"] - 20) or done
    if pt in ("VI", "CI"):
        want = lg["crs"]
        if nxt is not None and nxt.get("pos") is not None:
            c1 = leg_course_at(me, nxt)
            if c1 is not None:
                ux, uy = W.unit(c1)
                xtk = (ac.x - nxt["pos"][0]) * uy - (ac.y - nxt["pos"][1]) * ux
                closing = W.ang_diff(c1, ac.hdg)
                if xtk * closing < 0 or abs(xtk) < 0.05:
                    lead = R * (1 - math.cos(math.radians(abs(closing)))) + v * 2
                    if abs(xtk) <= lead:
                        return want, None, True
        return want, None, False
    if pt == "VM":
        return lg["crs"], None, False
    if pt == "FM":
        want, _ = track_line(ac, pos, lg["crs"])
        ux, uy = W.unit(lg["crs"])
        if (pos[0] - ac.x) * ux + (pos[1] - ac.y) * uy > 0.5:   # still before the fix: direct to it first
            want = W.bearing(me, pos)
        return want, None, False
    if pt == "RF" and lg.get("center"):
        c, r = lg["center"], lg["radius"]
        s = 1 if lg["turn"] == "R" else -1
        d = W.dist(me, c)
        tangent = (W.bearing(c, me) + 90 * s) % 360
        e = (d - r) * s                                   # + outside the arc for a right turn
        want = (tangent + s * max(-30.0, min(30.0, math.degrees(math.atan2(d - r, max(0.3, v * 20)))))) % 360
        omega = math.degrees(v / r) * s
        cmd = omega + 0.8 * W.ang_diff(ac.hdg, want)
        a_now = W.bearing(c, me)
        a_end = W.bearing(c, pos)
        togo = ((a_end - a_now) * s) % 360
        if togo > 300:
            togo = 0.0
        lead = 0.0
        if nxt is not None:
            lead = lead_for(ac, (W.bearing(c, pos) + 90 * s) % 360, leg_course_at(pos, nxt))
        passed = r * math.radians(togo) <= max(lead, v * 0.5)
        return None, cmd, passed
    if pt in HOLD_LEGS:
        return None, None, True
    # fix-terminated legs: IF, DF, TF, CF (+ unknown fix legs)
    if pos is None:
        return ac.hdg, None, True
    if pt == "CF" or (pt == "TF" and ac.leg_from is not None and W.dist(ac.leg_from, pos) > 1e-6):
        c = lg["crs"] if pt == "CF" else W.bearing(ac.leg_from, pos)
        A = (pos[0] - W.unit(c)[0] * 30, pos[1] - W.unit(c)[1] * 30) if pt == "CF" else ac.leg_from
        want, xtk = track_line(ac, A, c)
        ux, uy = W.unit(c)
        along = (pos[0] - ac.x) * ux + (pos[1] - ac.y) * uy
        if pt == "CF" and abs(xtk) > 2.0 and along > 0:
            # far off the course (e.g. a CF after a vector): intercept at 45 degrees
            want = (c - math.copysign(45.0, xtk)) % 360
        direct = False
    else:
        c = W.bearing(me, pos)
        want = c
        along = W.dist(me, pos)
        direct = True
        if lg.get("turn") and abs(W.ang_diff(ac.hdg, c)) > 20:
            s = 1 if lg["turn"] == "R" else -1
            e = W.ang_diff(ac.hdg, c)
            if e * s < 0:                                     # go the long way round as published
                return None, s * max_turn_rate(ac.gs), False
    lead = 0.0
    if nxt is not None and not lg["flyover"] and nxt["pt"] not in HOLD_LEGS:
        lead = lead_for(ac, c, leg_course_at(pos, nxt))
    if lg["flyover"] or (nxt is not None and nxt["pt"] in HOLD_LEGS):
        h = math.radians(ac.hdg)
        ahead = (pos[0] - ac.x) * math.sin(h) + (pos[1] - ac.y) * math.cos(h)
        passed = along <= v * 0.6 or (ahead <= 0 and along < 2 * R)
    elif direct:
        h = math.radians(ac.hdg)
        ahead = (pos[0] - ac.x) * math.sin(h) + (pos[1] - ac.y) * math.cos(h)
        passed = along <= max(lead, v * 0.5) or (ahead <= 0 and along < 2 * R)
    else:
        passed = along <= lead
    return want, None, passed


# ------------------------------------------------------------------ holds
def start_hold(ac, fix, inbound, turns="R", leg_min=None, leg_nm=None, kind="hold"):
    """Racetrack at `fix`, inbound course `inbound` (true). Entry per AIM 5-3-8: direct,
    parallel or teardrop, from the heading on arrival at the fix."""
    s = 1 if turns == "R" else -1
    rel = W.ang_diff(inbound, ac.hdg) * s               # heading relative to inbound, + toward the holding side
    if -70 <= rel <= 110:
        entry = "direct"
    elif rel > 110:
        entry = "teardrop"
    else:
        entry = "parallel"
    if entry == "direct":
        state, d, crs0 = "turn_out", s, (inbound + 180) % 360
    elif entry == "teardrop":
        state, d, crs0 = "teardrop", s, (inbound + 180 - 30 * s) % 360
    else:
        state, d, crs0 = "parallel", -s, (inbound + 180) % 360
    ac.holdp = {"fix": tuple(fix), "crs": inbound % 360, "s": s, "dir": d, "state": state, "entry": entry,
                "t": 0, "leg_min": leg_min, "leg_nm": leg_nm, "crs0": crs0, "kind": kind, "laps": 0, "out_from": None}


def hold_leg_time(ac, H):
    if H["leg_min"]:
        return 60 * H["leg_min"]
    return 60 if ac.alt <= 14000 else 90


def fly_hold(ac):
    H = ac.holdp
    s, wmax = H["s"], max_turn_rate(ac.gs)
    crs = H["crs"]
    want = cmd = None
    st = H["state"]
    if st in ("parallel", "teardrop"):
        # entry leg: fly the entry heading ~1 minute (teardrop 30 deg off, parallel outbound on the
        # non-holding side), then turn back toward the fix and join inbound
        want = H["crs0"]
        if abs(W.ang_diff(ac.hdg, want)) < 3:
            H["t"] += 1
        if H["t"] >= 60:
            H["state"], H["dir"] = "turn_in", (s if st == "teardrop" else -s)
            H["entry_turn"] = True
            H["t"] = 0
        st = None
    if st in ("turn_out", "turn_in"):
        tgt = (crs + 180) % 360 if st == "turn_out" else crs
        d = H["dir"]
        togo = ((tgt - ac.hdg) * d) % 360
        if 30 <= togo <= 330 or (H.get("entry_turn") and togo > 30):
            cmd = d * wmax
        elif st == "turn_out":
            want = tgt
            if abs(W.ang_diff(ac.hdg, tgt)) < 1.0:
                H["state"], H["t"] = "outbound", hold_leg_time(ac, H)
                H["out_from"] = (ac.x, ac.y)
        else:
            H["state"], H["dir"], H["entry_turn"] = "inbound", s, False
    if H["state"] == "outbound":
        want = (crs + 180) % 360
        H["t"] -= 1
        if H["leg_nm"] and H.get("out_from"):
            # distance legs: outbound until the inbound leg will be leg_nm long
            ux, uy = W.unit(crs)
            back = (H["fix"][0] - ac.x) * ux + (H["fix"][1] - ac.y) * uy
            if back >= H["leg_nm"]:
                H["state"] = "turn_in"
        elif H["t"] <= 0:
            H["state"] = "turn_in"
    elif H["state"] == "inbound":
        fx, fy = H["fix"]
        ux, uy = W.unit(crs)
        along = (fx - ac.x) * ux + (fy - ac.y) * uy
        A = (fx - 30 * ux, fy - 30 * uy)
        want, _ = track_line(ac, A, crs)
        if along <= 0:
            H["laps"] += 1
            if ac.exit_hold:
                ac.holdp = None
                ac.exit_hold = False
                ac.hold_fix = None
                ac.hold_alt = None
                ac.leg_from = (fx, fy)
                return "exit"
            H["state"] = "turn_out"
            H["dir"] = s
    advance(ac, turn(ac, want=want, cmd=cmd))
    return None
