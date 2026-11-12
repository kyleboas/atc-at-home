"""Baseline AI controller for KSFO (West Plan): Ground, Tower, Approach, Departure.

THIS IS THE FILE AUTORESEARCH EDITS. Everything under atc/ and evaluate.py is frozen. Each
position only talks to aircraft on its own frequency, using the menus in atc/commands.py
(reference/if-atc-menus.md). The controller reads the airport diagram and the charts
(atc.world, atc.procs: runways, taxiways, CIFP procedures, MVA) like a controller reads the
published plates; it only sees traffic through the observation.

Baseline behaviour, by manual section:
  Ground     pushback unless an adjacent stand is pushing (2.1.3); taxi routes by taxiway name
             (2.2.2), shortest route on the OSM graph, departures told to contact tower when
             ready; runway crossings on the way out only when the runway is clear of landing
             traffic per 2.3.2, in the direction in use (2.3.1), one runway at a time (2.2.2)
  Tower      runway separation per 3.1.1 incl. the intersecting 01s x 28s: a departure rolls
             only if it will be beyond the intersection before the next arrival reaches the
             threshold and every landing aircraft is past the intersection or off the runway;
             hold short when we can't (3.2.1), LUAW (3.2.3); departure behind a heavy waits
             2/3 min (7110.65 5-8-3); contact departure 1/2 nm past the end (3.2.11); landing
             clearance on check-in, go-around with "fly runway heading" when the runway will
             not be clear (3.5.4); exit runway below 70 kts, then cross the parallel per 2.3.2
             and hand to ground (3.5.3)
  Approach   procedures first: "continue as filed, expect the ILS/RNAV" on check-in (6.9.3);
             arrivals fly their STAR and get the approach clearance on the flight plan with
             "maintain 3,000 until established" off the published transition (6.9.4, 6.10.2,
             6.11.5). One landing sequence for 28L+28R (the IFs are 0.1 nm apart, so parallel
             arrivals keep 3 nm until both are established, 6.11.7-6.11.8), spaced for wake
             (7110.65 5-5-4), runway occupancy and a departure gap when departures wait
             (3.6.1). Delay is absorbed with speed (6.15) and holds at a STAR fix with 1,000 ft
             stacks (6.7); headings only where the STAR ends in vectors (BDEGA4 -> BRIXX, 6.10.4)
             and for missed approaches: downwind, base, then a ~30 degree intercept with an
             altitude below the glideslope (6.11.3-6.11.4). Hand to tower once established
             (6.14).
  Departure  radar contact on check-in (6.4.4); vertical separation first: cap the climb
             1,000 ft under arrivals ahead (6.2.2), never below the MVA (6.2.1) or the present
             altitude; altitude at discretion once clear (6.4.4); hand to center at the
             boundary.
"""
import heapq
import math

from atc import procs as P
from atc import world as W
from atc.commands import ALTITUDES, C

INF = float("inf")
FT = W.FT_NM


def mag(h_true):
    """Menu heading (magnetic, 10 degree steps) nearest a true heading."""
    h = int(round(W.true2mag(h_true) / 10.0)) * 10 % 360
    return 360 if h == 0 else h


def menu_alt_below(a):
    """Highest menu altitude <= a."""
    best = ALTITUDES[0]
    for v in ALTITUDES:
        if v <= a:
            best = v
    return best


def menu_alt_above(a):
    for v in ALTITUDES:
        if v >= a:
            return v
    return ALTITUDES[-1]


def dist(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


def mva_ahead(x, y, hdg_true, gs, minutes=3.0):
    """Highest MVA along the next `minutes` on this heading (what 6.3.2 is checked against)."""
    ux, uy = W.unit(hdg_true)
    d = gs / 60 * minutes
    return max(W.mva(x + ux * d * k / 6, y + uy * d * k / 6) for k in range(7))


def mva_clear_360(a, r=4.5):
    """A 360 here stays above the MVA everywhere within its ~2 nm radius circle (6.2.1, 6.3.2)."""
    pts = [(a["x"] + r * math.sin(math.radians(b)), a["y"] + r * math.cos(math.radians(b))) for b in range(0, 360, 45)]
    alt = min(a["alt"], a.get("alt_assigned") or a["alt"])         # it keeps descending in the turn
    return alt >= max(W.mva(x, y) for x, y in pts + [(a["x"], a["y"])]) + 300


# ---------------------------------------------------------------------------- arrival model
def ias_profile(perf, rem, alt, cleared=True):
    """The speed a pilot flies at `rem` track miles to go with no ATC speed (sim pilot model)."""
    v = perf["cruise"] if alt >= 10500 else 250
    if rem < 20:
        v = min(v, 210)
    if cleared and rem < 12:
        v = min(v, 180)
    if rem < 6.5:
        v = min(v, max(perf["vapp"] + 15, 160))
    if rem < 4.5:
        v = perf["vapp"]
    return max(v, perf["vapp"])


def time_to_go(perf, rem, alt, gs_now=None, ias_cap=None, cap_until=0.0, caps=None):
    """Seconds to fly `rem` track miles to the threshold with the pilots' speed profile, under an
    ATC speed `ias_cap` (kts, or 'slowest') that holds until `cap_until` nm to go; or a list
    `caps` of (until_nm, cap) applied in order (the first whose until is below r)."""
    if rem is None:
        return None
    if caps is None:
        caps = [(cap_until, ias_cap)] if ias_cap else []
    t, r, step = 0.0, rem, 0.5
    a0 = alt
    while r > 0:
        d = min(step, r)
        rm = r - d / 2
        a = min(a0, max(200.0, rm * 320.0))
        v = ias_profile(perf, rm, a)
        cap = next((cp for until, cp in caps if rm > until), None)
        if cap:
            v = perf["vapp"] + 20 if cap == "slowest" else max(perf["vapp"], cap)
            if rm < 4:
                v = min(v, perf["vapp"] + 10)
        gs = W.gs_from_ias(v, a)
        if gs_now is not None and rem - r < 3.0:          # the next few miles at the present speed
            gs = 0.5 * gs + 0.5 * max(gs_now, 100.0)
        t += d / gs * 3600
        r -= d
    return t


def absorbable(perf, rem, alt):
    """Seconds of delay speed control alone can absorb over `rem` track miles."""
    if not rem:
        return 0.0
    return slowest_time(perf, rem, alt) - time_to_go(perf, rem, alt)


def slowest_time(perf, rem, alt, gs_now=None):
    """The latest an arrival can land by speed control alone: 210 kts to 16 nm, then 160 kts to 4 nm."""
    caps = []
    if rem > 18:
        caps.append((16.0, 210 if alt < 10500 else 250))
    caps.append((4.0, 160))
    return time_to_go(perf, rem, alt, gs_now, caps=caps)


RWY28R = W.RUNWAYS["28R"]
LOC_T = RWY28R.loc_course                   # true
DOWNWIND_T, BASE_T = W.mag2true(90), W.mag2true(180)
INTERCEPT_MAG = 250                         # 34 degrees to the 28R localizer (284 mag), from the north
INTERCEPT_T = W.mag2true(INTERCEPT_MAG)
DOWNWIND_ALT = 7000
INTERCEPT_OFFSET = 2.6                      # nm right of the final course when turning to intercept
X_BASE_MIN, X_BASE_MAX = 15.5, 19.0         # base turn between ~10 and ~16 nm finals (trombone)
TROMBONE_S = (X_BASE_MAX - X_BASE_MIN) * 2 / 210 * 3600 * 0.8     # delay the downwind extension absorbs

# where each STAR stream holds when it must absorb delay (a fix on the STAR, base altitude)
HOLD_FIX = {"SERFR4": ("FOLET", 8000), "DYAMD5": ("FRELY", 8000), "YOSEM3": ("SOOIE", 11000),
            "BDEGA4": ("BDEGA", 12000)}
# turn direction: holding side away from the neighbouring stream (SOOIE left: DYAMD5 passes north)
HOLD_TURNS = {"FOLET": "right", "FRELY": "right", "SOOIE": "left", "BDEGA": "right"}
HOLD_LAP_S = 240


def final_xy(d):
    """Point on the 28R extended final d nm before the threshold."""
    return RWY28R.xy(RWY28R.u_thr - d)


def offset_right(x, y):
    """nm right (north) of the 28R final course, and distance to the threshold along it."""
    a, v = RWY28R.to_thr(x, y)
    return v, a                              # v > 0: right of the inbound course (north)


def vector_path(x, y, hdg, gs, phase, x_base=None):
    """Track miles of the downwind/base/intercept pattern from here to the 28R threshold.
    phase 'downwind' with x_base (turn base when x reaches it), 'base', 'intercept'."""
    R = max(1.0, (gs / 3600) / math.radians(min(3.0, math.degrees(9.80665 / 1852 * math.tan(math.radians(25)) / max(gs / 3600, 0.02)))))
    p = (x, y)
    L = 0.0
    if phase == "downwind":
        ux, uy = W.unit(DOWNWIND_T)
        # project to the base turn
        if x_base is not None and x < x_base:
            k = (x_base - x) / max(0.2, ux)
            p = (x + ux * k, y + uy * k)
            L += k
        L += R * math.pi / 2
        bx, by = W.unit(BASE_T)
        p = (p[0] + ux * R + bx * R, p[1] + uy * R + by * R)
        phase = "base"
    if phase == "base":
        v, a = offset_right(*p)
        bx, by = W.unit(BASE_T)
        # base runs nearly perpendicular to the final course: distance to the intercept turn
        closing = math.sin(math.radians(abs(W.ang_diff(LOC_T, BASE_T))))
        k = max(0.0, (v - INTERCEPT_OFFSET) / max(0.3, closing))
        p = (p[0] + bx * k, p[1] + by * k)
        L += k + R * math.radians(abs(W.ang_diff(BASE_T, INTERCEPT_T)))
        phase = "intercept"
    v, a = offset_right(*p)
    ang = math.radians(abs(W.ang_diff(LOC_T, INTERCEPT_T)))
    leg = max(0.0, v) / max(0.2, math.sin(ang))
    a_join = a - max(0.0, v) / math.tan(ang)
    L += leg
    return L, a_join


# ---------------------------------------------------------------------------- controller
class Controller:
    def reset(self, info):
        self.info = info
        self.types = info["types"]
        self.done = {}
        self.last_cmd = {}
        self.ground = Ground(self)
        self.tower = Tower(self)
        self.approach = Approach(self)
        self.departure = Departure(self)
        self.t = 0

    def once(self, cs, key):
        s = self.done.setdefault(cs, set())
        if key in s:
            return False
        s.add(key)
        return True

    def forget(self, cs, key):
        self.done.get(cs, set()).discard(key)

    def perf(self, a):
        return self.types[a["type"]]

    def quiet(self, cs, s=8):
        """True if we have not instructed this aircraft in the last s seconds (pilot reaction)."""
        return self.t - self.last_cmd.get(cs, -999) >= s

    def send(self, out, cmd):
        self.last_cmd[cmd["cs"]] = self.t
        out.append(cmd)

    def step(self, obs):
        self.t = obs["t"]
        acs = {a["cs"]: a for a in obs["aircraft"]}
        for a in acs.values():
            if a["missed"] and a["airborne"] and not self.done.get(a["cs"], set()) & {"missed_seen"}:
                self.done.setdefault(a["cs"], set()).add("missed_seen")
                for k in ("ctl", "exit", "to_ground", "to_tower", "exit_hold"):
                    self.forget(a["cs"], k)
                self.approach.cleared.discard(a["cs"])
                self.approach.slow.discard(a["cs"])
            if not a["missed"]:
                self.forget(a["cs"], "missed_seen")
        reqs = obs["requests"]
        out = []
        self.ground.step(obs, acs, reqs, out)
        self.tower.step(obs, acs, reqs, out)
        self.approach.step(obs, acs, reqs, out)
        self.departure.step(obs, acs, reqs, out)
        return out


# ---------------------------------------------------------------------------- runway picture
def landing_eta(ctl, a):
    """Seconds until this airborne arrival reaches its threshold (None if unknown)."""
    if not a["airborne"] or a["intent"] != "arrival":
        return None
    sched = ctl.approach.vec.get(a["cs"])
    if a["missed"] and not (sched and sched["phase"] in ("downwind", "base", "intercept")):
        return None
    rem = a.get("rem_nm")
    if a["nav"] == "heading" and a["leg"] == "vectors":
        if sched and sched["phase"] in ("downwind", "base", "intercept"):
            xb = max(X_BASE_MIN, a["x"]) if sched["phase"] == "downwind" else None
            L, a_join = vector_path(a["x"], a["y"], a["hdg"], a["gs"], sched["phase"], xb)
            return L / max(a["gs"], 150) * 3600 + time_to_go(ctl.perf(a), a_join, min(a["alt"], a_join * 320))
    if a["star"] == "BDEGA4" and not sched and a["nav"] == "lnav" and not a["approach_cleared"]:
        return bdega_eta(ctl, a)
    if rem is None:
        return None
    cap, until = speed_cap(a)
    return time_to_go(ctl.perf(a), rem, a["alt"], a["gs"], cap, until)


def route_dist(a, upto):
    """Track miles along the route fixes from the aircraft to fix `upto`."""
    d, p = 0.0, (a["x"], a["y"])
    for f in a["route"] or []:
        q = W.K.FIXES.get(f)
        if q is None:
            continue
        d += dist(p, q)
        p = q
        if f == upto:
            break
    return d, p


def bdega_eta(ctl, a):
    """BDEGA4 ends in vectors at BRIXX: the STAR to CORKK, then our downwind/base/intercept pattern
    with the earliest base turn, then the final."""
    route = a["route"] or []
    if "CORKK" in route:
        d, p = route_dist(a, "CORKK")
    else:
        d, p = 0.0, (a["x"], a["y"])
    L, a_join = vector_path(p[0], p[1], DOWNWIND_T, 230, "downwind", X_BASE_MIN)
    v1 = max(a["gs"], 200)
    t1 = d / v1 * 3600
    if a["holding"]:
        t1 += 60
    return t1 + L / 230 * 3600 + time_to_go(ctl.perf(a), a_join, a_join * 320)


def speed_cap(a):
    """(cap, until nm) of the ATC speed the aircraft is flying (observation 'speed')."""
    s = a.get("speed")
    if not s:
        return None, 0.0
    if s["kind"] == "kts":
        return s["kts"], (s.get("until") or 0.0)
    if s["kind"] == "slowest":
        return "slowest", 6.0
    return None, 0.0


def crossing_near(o, rwy):
    """Cleared to cross runway rwy and past its hold line (within 230 ft of the centerline, 2.3.2)."""
    if o["airborne"] or rwy not in (o.get("cross_clear") or []):
        return False
    R = W.RUNWAYS[rwy]
    u, v = R.uv(o["x"], o["y"])
    return abs(v) * FT < 230 and -0.1 < u < R.len_nm + 0.1


def on_runway(a, rwy):
    return rwy in (a.get("zone") or []) and a["phase"] in ("landing_roll", "rolling", "rto", "exiting", "lined_up",
                                                         "lining_up", "taxiing", "taxi_in", "clear", "holding_short")


# ---------------------------------------------------------------------------- ground
class Ground:
    def __init__(self, ctl):
        self.c = ctl
        self.route_cache = {}
        self.wait = {}          # cs -> (cs it waits for, shared nodes)
        self.stopped = {}       # (cs, cs) -> t: both told to hold position, collision imminent

    def route(self, start, goal, acs=None, me=None):
        """Shortest taxi route on the OSM graph (as the sim's pilots taxi it), never along a runway,
        stand lead-in lines only at the ends; extra cost to enter a runway strip, to use a taxiway
        another aircraft will taxi the opposite way (2.2.1), or to pass an aircraft standing still.
        Returns (nodes, via names)."""
        pen_edge, pen_node = {}, set()
        if acs:
            for o in acs.values():
                if o is me or o["airborne"]:
                    continue
                r = o.get("groute") or []
                if o["phase"] in ("taxiing", "taxi_in") and r:
                    for u, v in zip(r, r[1:]):
                        pen_edge[(v, u)] = 0.6               # we would meet it head-on
                if o["phase"] in ("holding_short", "clear", "pushed", "push_hold") or \
                        (o["phase"] in ("taxiing", "taxi_in") and o["gs"] < 1):
                    if o.get("gnode"):
                        pen_node.add(o["gnode"])
        key = (start, goal)
        if not pen_edge and not pen_node and key in self.route_cache:
            return self.route_cache[key]
        lead = W.lead_in_nodes(start, goal)
        own = next((r for r, h in W.DEP_HOLD.items() if h == goal), None)
        pq = [(0.0, start)]
        best = {start: 0.0}
        prev = {}
        found = None
        while pq:
            d, n = heapq.heappop(pq)
            if n == goal:
                found = n
                break
            if best.get(n, INF) < d:
                continue
            for m, k in W.ADJ[n]:
                _, _, name, kind, L, _ = W.EDGES[k]
                if kind in W.RUNWAY_EDGE_KINDS:
                    continue
                if kind == "parking_position" and not (m in lead and n in lead) \
                        and not (m == goal or n == goal or n == start or m == start):
                    continue
                cost = d + L + (0.4 if W.NODE_ZONE[m] and not W.NODE_ZONE[n] else 0.0)
                if own and own in W.NODE_ZONE[m]:
                    cost += 10.0                     # never across its own departure runway
                if pen_edge or pen_node:
                    cost += pen_edge.get((n, m), 0.0) + (0.5 if m in pen_node and m != goal else 0.0)
                if cost < best.get(m, INF):
                    best[m] = cost
                    prev[m] = (n, k)
                    heapq.heappush(pq, (cost, m))
        if found is None:
            if not pen_edge and not pen_node:
                self.route_cache[key] = (None, None)
            return None, None
        nodes, ks = [goal], []
        while nodes[-1] != start:
            n, k = prev[nodes[-1]]
            ks.append(k)
            nodes.append(n)
        nodes.reverse()
        ks.reverse()
        via = []
        for k in ks:
            if W.edge_free(k):
                continue
            name = W.EDGES[k][2]
            if not via or via[-1] != name:
                via.append(name)
        if not pen_edge and not pen_node:
            self.route_cache[key] = (nodes, via or None)
        return nodes, via or None

    def step(self, obs, acs, reqs, out):
        c = self.c
        t = obs["t"]
        for r in reqs:
            a = acs.get(r["cs"])
            if a is None or a["freq"] != "ground":
                continue
            k = r["kind"]
            if k == "request_pushback" and c.quiet(a["cs"], 3):
                if self.push_conflict(a, acs):
                    if a["phase"] == "gate_req" and c.once(a["cs"], "push_wait"):
                        c.send(out, C.hold_position(a["cs"]))
                    continue
                c.send(out, C.pushback_approved(a["cs"], expect_runway=a["runway"]))
            elif k == "request_taxi" and a["phase"] == "pushed" and c.quiet(a["cs"], 3):
                nodes, via = self.route(a["gnode"], W.DEP_HOLD[a["runway"]], acs, a)
                if nodes:
                    self.give_way_first(a, nodes, acs, out)
                    c.send(out, C.taxi_to_runway(a["cs"], runway=a["runway"], via=via, contact_tower=True))
            elif k == "ground_checkin" and a["phase"] in ("clear", "taxi_in") and c.quiet(a["cs"], 3):
                if a["hs_rwy"]:
                    continue
                goal = W.STANDS[a["stand"]]["node"] if a.get("stand") in W.STANDS else None
                nodes, via = self.route(a["gnode"], goal, acs, a) if goal else (None, None)
                if nodes:
                    self.give_way_first(a, nodes, acs, out)
                    c.send(out, C.taxi_to_parking(a["cs"], via=via))
        # pushbacks held for traffic: release when clear
        for a in acs.values():
            if a["freq"] == "ground" and a["phase"] == "push_hold" and c.quiet(a["cs"], 10) and not self.push_conflict(a, acs):
                c.send(out, C.pushback_approved(a["cs"], expect_runway=a["runway"]))
        self.deconflict(acs, out)
        # runway crossings on the taxi route (2.3.2, anticipated 2.3.3)
        for a in acs.values():
            if a["freq"] != "ground" or not a.get("crossings") or a["phase"] not in ("taxiing", "taxi_in"):
                continue
            nxt = next((s for s in a["crossings"] if not s["cleared"]), None)
            if nxt is None or not c.quiet(a["cs"], 6) or nxt["rwy"] in a["cross_clear"]:
                continue
            # only when close to the hold-short point (within ~600 ft of taxiing) or stopped there
            if a["hs_rwy"] != nxt["rwy"] and _dist_along(a, a.get("gpts") or [], nxt) > 0.1:
                continue
            if c.tower.crossing_ok(nxt["rwy"], a, acs):
                c.send(out, C.cross_runway(a["cs"], runway=nxt["rwy"]))

    def deconflict(self, acs, out):
        """Opposite-direction traffic on the same taxiway (2.2.1): the aircraft that reaches the
        shared stretch later holds position short of it until the other has passed."""
        c = self.c
        movers = [a for a in acs.values() if a["phase"] in ("taxiing", "taxi_in") and a.get("groute")]
        prof = {}
        for a in movers:
            nodes = ([a["gnode"]] if a.get("gnode") and a["gnode"] != a["groute"][0] else []) + a["groute"][:40]
            cum, d, p = {}, 0.0, (a["x"], a["y"])
            for n in nodes:
                q = W.NODES[n]
                d += dist(p, q)
                p = q
                cum.setdefault(n, d)
            if nodes and nodes[0] == a.get("gnode") and len(nodes) > 1:
                cum[nodes[0]] = -dist((a["x"], a["y"]), W.NODES[nodes[0]])     # just passed it
            prof[a["cs"]] = (nodes, cum)
        self.imminent(acs, out)
        # release waiters whose conflict has passed
        for cs, (ocs, shared, t0) in list(self.wait.items()):
            a, o = acs.get(cs), acs.get(ocs)
            gone = o is None or not set(o.get("groute") or []) & shared or o["phase"] not in ("taxiing", "taxi_in", "pushing") \
                or c.t - t0 > 150 or ((o["hold"] or o.get("give_way") or o.get("hold_short_taxiway")) and o["cs"] in self.wait)
            if a is None or a["phase"] not in ("taxiing", "taxi_in"):
                self.wait.pop(cs)
            elif c.t - t0 > 15 and not (a["hold"] or a.get("hold_short_taxiway") or a.get("give_way")):
                self.wait.pop(cs)                      # gave way and has continued on their own
            elif gone and c.quiet(cs, 4) and (a["hold"] or a.get("hold_short_taxiway")):
                self.wait.pop(cs)
                c.send(out, C.continue_taxi(cs))
        for i, a in enumerate(movers):
            na, ca = prof[a["cs"]]
            ea = {(u, v) for u, v in zip(na, na[1:])}
            for b in movers[i + 1:]:
                if a["cs"] in self.wait or b["cs"] in self.wait:
                    continue
                nb, cb = prof[b["cs"]]
                shared = {u for u, v in zip(nb, nb[1:]) if (v, u) in ea} | {v for u, v in zip(nb, nb[1:]) if (v, u) in ea}
                if not shared:
                    # paths that cross at a node: the later one holds if they would meet there
                    common = [n for n in nb if n in ca and ca[n] > 0.004 and cb[n] > 0.004]
                    if not common:
                        continue
                    n = min(common, key=lambda k: ca[k] + cb[k])
                    ta = ca[n] / max(a["gs"], 8) * 3600
                    tb = cb[n] / max(b["gs"], 8) * 3600
                    if ca[n] > 0.15 or cb[n] > 0.15 or abs(ta - tb) > 14:
                        continue
                    shared = {n}
                da, db = min(ca[n] for n in shared), min(cb[n] for n in shared)
                if da > 0.5 or db > 0.5:
                    continue
                waiter, other, dw = (a, b, da) if da > db else (b, a, db)
                if _in_path(waiter, other):
                    waiter, other, dw = other, waiter, min(da, db)   # the one in the way cannot give way
                if dw < 0.008 or not c.quiet(waiter["cs"], 3):
                    continue                           # already on it (cannot stop short)
                nw = prof[waiter["cs"]][0]
                entry = min((n for n in shared if n in prof[waiter["cs"]][1]), key=lambda k: prof[waiter["cs"]][1][k])
                twy = _entering_taxiway(waiter, nw, entry)
                if dw > 0.06:
                    # early: hold short of the taxiway where the conflict starts, if it is a different one
                    if twy and dw < 0.25:
                        c.send(out, C.hold_short_taxiway(waiter["cs"], taxiway=twy))
                        self.wait[waiter["cs"]] = (other["cs"], shared, c.t)
                    continue
                side = _side(waiter, other)
                if not side and twy is None and _side(other, waiter) and c.quiet(other["cs"], 3):
                    waiter, other, side = other, waiter, _side(other, waiter)   # traffic behind: it gives way
                if side:
                    c.send(out, C.give_way(waiter["cs"], to=side, traffic=other["cs"]))     # 2.2.1
                elif twy:
                    c.send(out, C.hold_short_taxiway(waiter["cs"], taxiway=twy))
                else:
                    continue
                self.wait[waiter["cs"]] = (other["cs"], shared, c.t)

    def imminent(self, acs, out):
        """Hold position is for an imminent collision only (2.2.1): stop both, then one gives way."""
        c = self.c
        movers = [a for a in acs.values() if a["freq"] == "ground" and a["phase"] in ("taxiing", "taxi_in")]
        for pair, t0 in list(self.stopped.items()):
            a, b = acs.get(pair[0]), acs.get(pair[1])
            if a is None or b is None or a["phase"] not in ("taxiing", "taxi_in") or b["phase"] not in ("taxiing", "taxi_in"):
                for x in (a, b):
                    if x is not None and x["hold"] and c.quiet(x["cs"], 3):
                        c.send(out, C.continue_taxi(x["cs"]))
                self.stopped.pop(pair)
                continue
            if c.t - t0 < 8 or (a["gs"] > 1 or b["gs"] > 1) and c.t - t0 < 20:
                continue
            # the one with the other more nearly ahead gives way; the other goes first
            ra = abs(W.ang_diff(a["hdg"], W.bearing((a["x"], a["y"]), (b["x"], b["y"]))))
            rb = abs(W.ang_diff(b["hdg"], W.bearing((b["x"], b["y"]), (a["x"], a["y"]))))
            loser, winner = (a, b) if ra < rb else (b, a)
            side = _side(loser, winner) or "ahead"
            if winner["hold"]:
                c.send(out, C.continue_taxi(winner["cs"]))
            c.send(out, C.give_way(loser["cs"], to=side, traffic=winner["cs"]))
            if loser["hold"]:
                c.send(out, C.continue_taxi(loser["cs"]))
            self.wait.pop(loser["cs"], None)
            self.wait.pop(winner["cs"], None)
            self.stopped.pop(pair)
        busy = {cs for p in self.stopped for cs in p}
        for i, a in enumerate(movers):
            for b in movers[i + 1:]:
                if a["cs"] in busy or b["cs"] in busy or (a["gs"] < 3 and b["gs"] < 3):
                    continue
                if not _imminent(a, b):
                    continue
                for x in (a, b):
                    if not x["hold"]:
                        c.send(out, C.hold_position(x["cs"]))
                self.stopped[(a["cs"], b["cs"])] = c.t
                busy |= {a["cs"], b["cs"]}

    def give_way_first(self, a, nodes, acs, out):
        """2.2.1: if the new taxi route meets moving traffic right away, give way first, then taxi."""
        mine = [(a["x"], a["y"])] + [W.NODES[n] for n in nodes[:4]]
        for o in acs.values():
            if o is a or o["airborne"] or o["phase"] not in ("taxiing", "taxi_in", "exiting", "landing_roll") or o["gs"] < 3:
                continue
            if dist((a["x"], a["y"]), (o["x"], o["y"])) > 0.3:
                continue
            theirs = [(o["x"], o["y"])] + list(o.get("gpts") or [])[:12]
            if _poly_dist(mine, theirs) < 0.04:
                side = _side(a, o)
                if side:
                    self.c.send(out, C.give_way(a["cs"], to=side, traffic=o["cs"]))
                    return

    @staticmethod
    def push_conflict(a, acs):
        st = W.STANDS.get(a.get("stand"))
        p = (st["x"], st["y"]) if st else (a["x"], a["y"])
        for o in acs.values():
            if o is a or o["airborne"]:
                continue
            if o["phase"] in ("pushing",) and dist(p, (o["x"], o["y"])) < 0.06:
                return True
            if o["phase"] in ("taxi_in", "taxiing") and dist(p, (o["x"], o["y"])) < 0.035 and o["gs"] > 2:
                return True
        return False


def _imminent(a, b):
    """Within ~400 ft and heading for < ~120 ft separation within 25 s (same test as the rules monitor)."""
    px, py = b["x"] - a["x"], b["y"] - a["y"]
    d = math.hypot(px, py)
    if d > 0.065:
        return False
    if abs(W.ang_diff(a["hdg"], b["hdg"])) < 60:
        return False                       # in trail, same direction: the pilots keep their spacing
    ha, hb = math.radians(a["hdg"]), math.radians(b["hdg"])
    vx = (math.sin(hb) * b["gs"] - math.sin(ha) * a["gs"]) / 3600
    vy = (math.cos(hb) * b["gs"] - math.cos(ha) * a["gs"]) / 3600
    vv = vx * vx + vy * vy
    if vv < 1e-12:
        return False
    t_cpa = -(px * vx + py * vy) / vv                     # s to closest approach (straight lines)
    d_cpa = math.hypot(px + vx * t_cpa, py + vy * t_cpa)
    return 0 < t_cpa < 25 and d_cpa < 0.02                # < ~120 ft apart within 25 s


def _in_path(a, o):
    """a stands on o's route ahead (o would have to pass through a)."""
    pts = [(o["x"], o["y"])] + list(o.get("gpts") or [])[:15]
    return _poly_dist([(a["x"], a["y"])], pts) < 0.02


def _side(a, o):
    """Give way menu side for traffic o as seen from a: exiting / ahead / left / right (None if behind)."""
    if o["phase"] in ("exiting", "landing_roll") or (o["phase"] == "clear" and o["intent"] == "arrival" and o.get("zone")):
        return "exiting"
    rb = W.ang_diff(a["hdg"], W.bearing((a["x"], a["y"]), (o["x"], o["y"])))
    if abs(rb) <= 45:
        return "ahead"
    if -160 <= rb <= -20:
        return "left"
    if 20 <= rb <= 160:
        return "right"
    return None


def _entering_taxiway(a, nodes, entry):
    """Name of the taxiway the route turns onto at or just before node `entry`, if not the current one."""
    if entry not in nodes:
        return None
    i = nodes.index(entry)
    names = []
    for u, v in zip(nodes, nodes[1:]):
        ks = [k for m, k in W.ADJ[u] if m == v]
        names.append(W.EDGES[min(ks, key=lambda kk: W.EDGES[kk][4])][2] if ks else None)
    cur = names[0] if names else None
    if i >= len(names) or i == 0:
        return None
    nxt = names[i]
    return nxt if nxt and nxt != cur and nxt in W.TAXIWAYS and names[i - 1] != nxt else None


def _poly_dist(p, q):
    def ps(pt, a, b):
        dx, dy = b[0] - a[0], b[1] - a[1]
        L = dx * dx + dy * dy
        t = 0.0 if L < 1e-12 else max(0.0, min(1.0, ((pt[0] - a[0]) * dx + (pt[1] - a[1]) * dy) / L))
        return math.hypot(pt[0] - a[0] - t * dx, pt[1] - a[1] - t * dy)
    sp = list(zip(p, p[1:])) or [(p[0], p[0])]
    sq = list(zip(q, q[1:])) or [(q[0], q[0])]
    return min(min(ps(a, c, d), ps(b, c, d), ps(c, a, b), ps(d, a, b)) for a, b in sp for c, d in sq)


def _dist_along(a, pts, s):
    """Distance from the aircraft along its upcoming route points to the k-th crossing stop."""
    # the observation gives the next 40 route points and the stop indices of the full route
    # path; the route from gnode: approximate with the straight distance to the first point
    # that lies in a runway strip of the crossing runway.
    R = W.RUNWAYS[s["rwy"]]
    d, prev = 0.0, (a["x"], a["y"])
    for p in pts:
        d += dist(prev, p)
        prev = p
        if abs(R.uv(*p)[1]) * FT <= W.HOLD_FT + 30:
            return d
        if d > 0.3:
            break
    return INF


# ---------------------------------------------------------------------------- tower
class Tower:
    LINEUP_S = 35          # holding point -> lined up (measured ~25-45 s)
    ROLL_BEYOND_S = 42     # brake release -> beyond the intersection with the 28s
    REACT_S = 12           # worst pilot reaction + roll delay allowance beyond the typical

    def __init__(self, ctl):
        self.c = ctl
        self.lineup_t = {}
        self.to_cleared = {}        # cs -> time we cleared it for takeoff (until airborne)

    # ---- runway picture
    def arrivals(self, acs):
        out = []
        for a in acs.values():
            if a["intent"] == "arrival" and a["airborne"] and a["runway"] in W.ARR_RUNWAYS:
                e = landing_eta(self.c, a)
                if e is not None:
                    out.append((e, a))
        out.sort(key=lambda p: p[0])
        return out

    def landing_users(self, acs):
        """Aircraft on a 28 that have not yet vacated it."""
        return [a for a in acs.values() if a["phase"] in ("landing_roll", "rto") or
                (a["phase"] == "exiting" and a["runway"] in (a.get("zone") or []))]

    @staticmethod
    def beyond(a, rwy_of_a, other):
        """a (on rwy_of_a) is beyond its intersection with runway `other`."""
        u = W.INTERSECTIONS.get((rwy_of_a, other))
        if u is None:
            return True
        return W.RUNWAYS[rwy_of_a].u((a["x"], a["y"])) > u + W.BEYOND_NM + 0.02

    def missed_near(self, acs):
        return any(a["airborne"] and a["missed"] and math.hypot(a["x"], a["y"]) < 6 and a["alt"] < 5000
                   for a in acs.values())

    def departure_ok(self, a, acs, extra_s=0.0):
        """Can departure `a` start its roll in ~extra_s seconds without breaking 3.1.1 or wake?"""
        rw = a["runway"]
        t = self.c.t
        for o in acs.values():
            if o is a:
                continue
            if o["intent"] == "departure" and o["runway"] in W.DEP_RUNWAYS:
                if o["phase"] == "rolling" or (o["phase"] in ("lined_up", "lining_up") and o["takeoff_cleared"]):
                    return False
                if o["cs"] in self.to_cleared and not o["airborne"] and o["phase"] != "rto":
                    return False
                if o["airborne"] and o["t_airborne"] is not None:
                    # wake 5-8-3 (2 min heavy, 3 min super) from the same / closely spaced runway
                    need = W.DEP_WAKE_S.get(o["wake"])
                    if need and not (o["wake"] == "H" and a["wake"] == "H") and a["wake"] != "J":
                        if t - (o["t_roll"] or o["t_airborne"]) < need + 5 - extra_s:
                            return False
                    # radar separation once both are with departure: let the first get ~2.5 nm ahead
                    if dist((o["x"], o["y"]), W.RUNWAYS[rw].start) < 2.2 and o["alt"] < 3000:
                        return False
            if on_runway(o, rw) and o["phase"] not in ("lined_up",) and o is not a:
                if o["phase"] in ("taxiing", "taxi_in", "clear", "holding_short", "lining_up"):
                    return False
            if crossing_near(o, rw):
                return False                         # cleared to cross this runway, at it (2.3.2)
        for o in self.landing_users(acs):
            if (rw, o["runway"]) in W.INTERSECTIONS and not self.beyond(o, o["runway"], rw):
                return False
        if self.missed_near(acs):
            return False
        # the next arrival on either 28 must not reach the threshold before we are beyond the intersection
        need = extra_s + self.ROLL_BEYOND_S + self.REACT_S
        for e, o in self.arrivals(acs):
            if (rw, o["runway"]) in W.INTERSECTIONS and e < need + 8:
                return False
        return True

    def crossing_ok(self, rwy, a, acs):
        """2.3.2: cross a landing runway only if nothing is landing or rolling toward the
        crossing point and the next arrival is far enough out to be clear of the runway edge."""
        R = W.RUNWAYS[rwy]
        u_x = R.u((a["x"], a["y"]))
        for o in acs.values():
            if o is a:
                continue
            if o["runway"] == rwy and o["phase"] in ("landing_roll", "rto", "rolling"):
                return False
            if o["runway"] == rwy and o["phase"] == "exiting" and rwy in (o.get("zone") or []):
                return False
            if o["airborne"] and o["runway"] == rwy and o["intent"] == "departure" and o["alt"] < 400:
                return False
            if o["runway"] == rwy and o["intent"] == "departure" and not o["airborne"] and \
                    (o["takeoff_cleared"] or o["cs"] in self.to_cleared):
                return False                         # a departure cleared to roll on it (2.3.2)
            # someone else already crossing this runway close by is fine; nobody lined up for it
        for e, o in self.arrivals(acs):
            if o["runway"] == rwy and e < 75:
                return False
        return True

    # ---- per tick
    def step(self, obs, acs, reqs, out):
        c = self.c
        t = obs["t"]
        for r in reqs:
            a = acs.get(r["cs"])
            if a is None or a["freq"] != "tower":
                continue
            if r["kind"] == "tower_checkin" and c.quiet(a["cs"], 2) and c.once(a["cs"], "ctl"):
                c.send(out, C.cleared_to_land(a["cs"], runway=a["runway"]))
            elif r["kind"] == "request_freq_change" and a["airborne"] and a["intent"] == "departure":
                R_ = W.RUNWAYS[a["runway"]]
                past = R_.u((a["x"], a["y"])) - R_.len_nm
                if (past > 1.5 or a["alt"] > 1500) and past > 0.6 and c.quiet(a["cs"], 3) and c.once(a["cs"], "to_dep"):
                    c.send(out, C.contact_frequency(a["cs"], facility="departure"))
            elif r["kind"] == "holding_short" and a["phase"] == "clear" and a["hs_rwy"]:
                pass                               # crossing below
        self.arrivals_step(acs, out)
        self.departures_step(acs, reqs, out)

    def arrivals_step(self, acs, out):
        c = self.c
        for a in acs.values():
            if a["freq"] != "tower" or a["intent"] != "arrival":
                continue
            cs = a["cs"]
            if a["airborne"] and not a["missed"]:
                if a["clearance"] is None and c.quiet(cs, 6) and (a["rem_nm"] or 99) < 4 and c.once(cs, "ctl"):
                    c.send(out, C.cleared_to_land(cs, runway=a["runway"]))
                    continue
                rem = a["rem_nm"]
                if rem is not None and rem < 2.5 and c.quiet(cs, 3) and self.must_go_around(a, acs):
                    c.send(out, C.go_around(cs, then="runway_heading"))
                    for k in ("missed_handoff", "ctl", "exit", "to_ground"):
                        c.forget(cs, k)
            elif a["airborne"] and a["missed"]:
                if a["alt"] > 1200 and c.quiet(cs, 5) and c.once(cs, "missed_handoff"):
                    c.send(out, C.contact_frequency(cs, facility="approach"))
            elif a["phase"] == "landing_roll" and not a["exit_called"] and a["gs"] < 70 and c.quiet(cs, 2) \
                    and c.once(cs, "exit"):
                c.send(out, C.exit_runway(cs, side="any"))
            elif a["phase"] == "clear" and a["hs_rwy"] and c.quiet(cs, 6) and a["hs_rwy"] not in a["cross_clear"]:
                if self.crossing_ok(a["hs_rwy"], a, acs):
                    c.send(out, C.cross_runway(cs, runway=a["hs_rwy"], then_contact="ground"))
            elif a["phase"] == "clear" and not a["hs_rwy"] and c.quiet(cs, 4) and c.once(cs, "to_ground"):
                c.send(out, C.contact_frequency(cs, facility="ground"))

    def must_go_around(self, a, acs):
        """The runway will not be clear when this aircraft crosses the threshold (3.1.1)."""
        rw = a["runway"]
        e = landing_eta(self.c, a) or 0
        for o in acs.values():
            if o is a:
                continue
            if o["phase"] in ("landing_roll", "rto") and o["runway"] == rw:
                # it must vacate before we arrive: a landing roll + exit takes ~45-60 s
                since = self.c.t - (o["t_landed"] or self.c.t)
                if e < max(8, 55 - since):
                    return True
            if o["phase"] == "exiting" and rw in (o.get("zone") or []) and e < 8:
                return True
            if rw in (o.get("zone") or []) and o["phase"] in ("taxiing", "taxi_in", "clear", "holding_short") and e < 15:
                return True
            if o["intent"] == "departure" and o["runway"] in W.DEP_RUNWAYS and (rw, o["runway"]) in W.INTERSECTIONS:
                if o["phase"] == "rolling" and not self.beyond(o, o["runway"], rw):
                    # will it be beyond by the time we cross the threshold?
                    u = W.RUNWAYS[o["runway"]].u((o["x"], o["y"]))
                    need = W.INTERSECTIONS[(o["runway"], rw)] + W.BEYOND_NM - u
                    v = max(o["gs"], 5) / 3600
                    acc = c_acc(self.c, o)
                    tt = (-v + math.sqrt(v * v + 2 * acc * max(0.0, need))) / acc if acc > 0 else INF
                    if tt > e - 3:
                        return True
                if o["phase"] in ("lined_up",) and o["takeoff_cleared"] and e < 50:
                    return True
                if rw in (o.get("zone") or []) and o["phase"] not in ("airborne",):
                    if e < 15:
                        return True
        return False

    def departures_step(self, acs, reqs, out):
        c = self.c
        t = c.t
        ready = {r["cs"] for r in reqs if r["kind"] == "ready_for_departure"}
        # cancel a takeoff clearance that no longer works (3.1.2/3.1.3)
        for a in acs.values():
            if a["freq"] == "tower" and a["intent"] == "departure" and a["phase"] in ("lined_up", "lining_up") \
                    and a["takeoff_cleared"] and c.quiet(a["cs"], 4):
                if not self.departure_ok(a, acs, 0.0) and self.unsafe_now(a, acs):
                    c.send(out, C.cancel_takeoff(a["cs"]))
                    self.to_cleared.pop(a["cs"], None)
        for cs in list(self.to_cleared):
            if cs not in acs or acs[cs]["airborne"] or acs[cs]["phase"] == "rto":
                self.to_cleared.pop(cs)
        for rw in W.DEP_RUNWAYS:
            q = [a for a in acs.values() if a["runway"] == rw and a["intent"] == "departure" and a["freq"] == "tower"
                 and a["phase"] in ("holding_short", "lining_up", "lined_up")]
            lined = [a for a in q if a["phase"] in ("lining_up", "lined_up")]
            holding = sorted([a for a in q if a["phase"] == "holding_short"], key=lambda a: a["queue_pos"] or 99)
            for a in lined:
                if not a["takeoff_cleared"] and a["cs"] not in self.to_cleared and c.quiet(a["cs"], 4) \
                        and self.departure_ok(a, acs, 4.0):
                    c.send(out, C.cleared_takeoff(a["cs"], runway=rw, immediate=self.immediate(acs)))
                    self.to_cleared[a["cs"]] = t
            if holding and not lined:
                a = holding[0]
                if a["queue_pos"] == 1 and c.quiet(a["cs"], 4):
                    if self.departure_ok(a, acs, self.LINEUP_S + 4):
                        c.send(out, C.cleared_takeoff(a["cs"], runway=rw, immediate=self.immediate(acs)))
                        self.to_cleared[a["cs"]] = t
                        continue
                    other_lined = any(o["phase"] in ("lining_up", "lined_up") for o in acs.values()
                                      if o["runway"] == W.CSPR[rw] and o["intent"] == "departure")
                    if not other_lined:
                        c.send(out, C.line_up_and_wait(a["cs"], runway=rw))
                        continue
            for a in holding:
                if a["cs"] in ready and c.quiet(a["cs"], 3) and c.once(a["cs"], "hs"):
                    c.send(out, C.hold_short(a["cs"], runway=rw))
        # airborne departures: contact departure 1/2 nm past the end (3.2.11)
        for a in acs.values():
            if a["freq"] == "tower" and a["intent"] == "departure" and a["airborne"]:
                R = W.RUNWAYS[a["runway"]]
                past = R.u((a["x"], a["y"])) - R.len_nm
                if (past > 2.5 or (a["alt"] > 2500 and past > 0.6)) and c.quiet(a["cs"], 3) and c.once(a["cs"], "to_dep"):
                    c.send(out, C.contact_frequency(a["cs"], facility="departure"))
            if a["freq"] == "tower" and a["intent"] == "departure" and a["phase"] == "clear" and c.quiet(a["cs"], 5):
                pass

    def immediate(self, acs):
        """3.2.7: 'immediate' only with an arrival inside ~3 nm."""
        return any(o["intent"] == "arrival" and o["airborne"] and (o["rem_nm"] or 99) <= 3.0 and not o["missed"]
                   for o in acs.values())

    def unsafe_now(self, a, acs):
        """Lined up with a takeoff clearance and the roll would now be unsafe (arrival close)."""
        rw = a["runway"]
        for o in acs.values():
            if o is not a and (crossing_near(o, rw) or (on_runway(o, rw) and o["phase"] in ("taxiing", "taxi_in", "clear"))):
                return True                          # crossing traffic on the runway (3.1.1)
        for e, o in self.arrivals(acs):
            if (rw, o["runway"]) in W.INTERSECTIONS and e < self.ROLL_BEYOND_S + 6:
                return True
        for o in self.landing_users(acs):
            if (rw, o["runway"]) in W.INTERSECTIONS and not self.beyond(o, o["runway"], rw):
                return True
        return False


def c_acc(ctl, o):
    """Take-off acceleration in nm/s^2."""
    return ctl.perf(o)["accel"] / 3600


# ---------------------------------------------------------------------------- approach
class Approach:
    def __init__(self, ctl):
        self.c = ctl
        self.vec = {}           # cs -> vector plan: {phase, x_base}
        self.hold = {}          # cs -> {fix, alt}   (holds we assigned)
        self.sta = {}           # cs -> scheduled landing time
        self.committed = {}     # cs -> committed STA (order frozen)
        self.speed = {}         # cs -> assigned speed (kts)
        self.cleared = set()
        self.slow = set()
        self.kind = {}
        self.key = {}           # cs -> landing order key (ETA when first sequenced)
        self.new = []
        self.depgap = {}
        self.speed_t = {}
        self.t360 = {}
        self.level = {}         # cs -> (altitude, other cs): conflict probe level-offs
        self.reseq = set()      # vectored arrivals moved to the first uncommitted slot
        self.acs = {}

    # ---- spacing
    def gap(self, lead, fol, dep_gap):
        """Seconds between consecutive threshold times on 28L/28R (one merged sequence)."""
        pf = self.c.perf(fol)
        vf = pf["vapp"]
        same = lead["runway"] == fol["runway"]
        d = W.wake_nm(lead["wake"], fol["wake"])
        if same:
            d = max(d, 2.5)
        g = max(d / vf * 3600 + 15, 85.0)            # 3 nm (+margin) at ~180 kt at the IF, until both established
        if lead["cs"] in self.vec or fol["cs"] in self.vec:
            g = max(g, 100.0)                        # a radar intercept crosses the published transitions
        if same:
            g = max(g, 92.0)                         # runway occupancy (landing roll + exit), 2.5 nm
        if dep_gap:
            g = max(g, 110.0)                        # 3.6.1: room for a departure between arrivals
        return g

    def schedule(self, acs, t):
        arr = []
        for a in acs.values():
            if a["intent"] != "arrival" or not a["airborne"] or a["runway"] not in W.ARR_RUNWAYS:
                continue
            e = landing_eta(self.c, a)
            if e is None:
                continue
            if a["cs"] not in self.key:
                self.key[a["cs"]] = t + e
                self.new.append(a["cs"])
            arr.append((self.key[a["cs"]], t + e, a))
        # a holding stack leaves from the bottom: holders of one fix take their places in the
        # sequence lowest first (swap their keys)
        byfix = {}
        for i, (k, e, a) in enumerate(arr):
            h = self.hold.get(a["cs"])
            if h and not h.get("out") and (a["holding"] or a["hold_fix"]):
                byfix.setdefault(h["fix"], []).append(i)
        for fix, idx in byfix.items():
            keys = sorted(arr[i][0] for i in idx)
            order = sorted(idx, key=lambda i: self.hold[arr[i][2]["cs"]]["alt"])
            for k, i in zip(keys, order):
                self.key[arr[i][2]["cs"]] = k
                arr[i] = (k, arr[i][1], arr[i][2])
        arr.sort(key=lambda p: p[0])
        # 3.6.1: when departures are on their way to the runway, every other new arrival in the
        # sequence brings a departure gap with it (decided once, so slots do not jump around)
        dep_wait = sum(1 for a in acs.values() if a["intent"] == "departure" and
                       a["phase"] in ("holding_short", "lining_up", "lined_up", "taxiing", "pushed"))
        order = [a["cs"] for _, _, a in arr]
        for cs in self.new:
            i = order.index(cs) if cs in order else -1
            prev_gap = self.depgap.get(order[i - 1], False) if i > 0 else False
            self.depgap[cs] = dep_wait >= 1 and not prev_gap
        self.new = []
        sta = self.slots(arr)
        # a vectored arrival the pattern cannot delay to its slot (downwind/base, missed approach)
        # takes the first slot not committed to another arrival; the uncommitted ones behind it
        # (holding, or far out) absorb the extra gap instead (6.11.8, 6.7.4)
        moved = False
        for i, (key, eta, a) in enumerate(arr):
            cs = a["cs"]
            if cs in self.reseq or not self.cannot_absorb(a, sta[cs] - eta):
                continue
            self.reseq.add(cs)
            rest = [(k, e, o) for k, e, o in arr if o["cs"] != cs]
            j = 0
            while j < len(rest) and sta[rest[j][2]["cs"]] < eta - 40:
                j += 1
            for m in range(len(rest)):
                if self.committed_to_slot(rest[m][2]) and sta[rest[m][2]["cs"]] < eta + 300:
                    j = max(j, m + 1)
            if j >= i:
                continue
            lo = rest[j - 1][0] if j > 0 else rest[0][0] - 2.0
            self.key[cs] = (lo + rest[j][0]) / 2.0
            moved = True
            break
        if moved:
            arr = sorted(((self.key[a["cs"]], e, a) for _, e, a in arr), key=lambda p: p[0])
            sta = self.slots(arr)
        self.sta = sta
        return arr

    def slots(self, arr):
        prev = None
        sta = {}
        for key, eta, a in arr:
            s = eta
            if prev is not None:
                s = max(s, sta[prev["cs"]] + self.gap(prev, a, self.depgap.get(a["cs"], False)))
            sta[a["cs"]] = s
            prev = a
        return sta

    def cannot_absorb(self, a, delay):
        """A vectored arrival whose delay even the longest downwind plus a 360 cannot take."""
        v = self.vec.get(a["cs"])
        if not v or a["freq"] != "approach" or a["leg"] != "vectors":
            return False
        if v["phase"] == "downwind":
            gs = max(a["gs"], 180)
            L, a_join = vector_path(a["x"], a["y"], a["hdg"], gs, "downwind", max(X_BASE_MAX + 6, a["x"]))
            T = L / gs * 3600 + time_to_go(self.c.perf(a), a_join, a_join * 320)
            L0, j0 = vector_path(a["x"], a["y"], a["hdg"], gs, "downwind", max(X_BASE_MIN, a["x"]))
            T0 = L0 / gs * 3600 + time_to_go(self.c.perf(a), j0, j0 * 320)
            return delay > (T - T0) + 200
        if v["phase"] == "base":
            return delay > 200
        return False

    def committed_to_slot(self, o):
        """An arrival whose landing time can no longer move by a whole gap: cleared for the
        approach, established, turned in by vectors, out of its hold, or inside ~30 nm."""
        cs = o["cs"]
        v = self.vec.get(cs)
        h = self.hold.get(cs)
        if cs in self.cleared or o["established"] or v:
            return True                      # vectored traffic stays in its order on the downwind
        if h and h.get("out"):
            return True
        if o["holding"] or o["hold_fix"] or (h and not h.get("out")):
            return False
        return (o["rem_nm"] or 99) < 30

    # ---- per tick
    def step(self, obs, acs, reqs, out):
        c = self.c
        t = obs["t"]
        self.acs = acs
        for d in (self.vec, self.hold, self.committed, self.speed, self.key, self.depgap):
            for cs in list(d):
                if cs not in acs:
                    d.pop(cs)
        for r in reqs:
            a = acs.get(r["cs"])
            if a is None or a["freq"] != "approach" or r["kind"] != "check_in" or not c.quiet(a["cs"], 2):
                continue
            if a["missed"]:
                c.send(out, C.expect_approach(a["cs"], approach="ils", runway="28R", then="expect_vectors"))
                self.vec[a["cs"]] = {"phase": "missed"}
                self.committed.pop(a["cs"], None)
                self.key.pop(a["cs"], None)
                self.cleared.discard(a["cs"])
                self.slow.discard(a["cs"])
                self.kind[a["cs"]] = "ils"
                continue
            kind = a["approach_request"] or "ils"
            then = "continue_as_filed"
            if kind not in P.APPROACH_IDS.get(a["runway"], {}):
                kind = "ils"
            if a["star"] == "BDEGA4":                 # the STAR ends in vectors: ILS off a radar intercept
                kind, then = "ils", "expect_vectors"
            c.send(out, C.expect_approach(a["cs"], approach=kind, runway=a["runway"], then=then))
            self.kind[a["cs"]] = kind
        arr = self.schedule(acs, t)
        for key, eta, a in arr:
            if a["freq"] != "approach" or not a["radar_contact"]:
                continue
            delay = self.sta[a["cs"]] - eta
            if a["cs"] in self.vec:
                self.fly_vectors(a, delay, acs, out)
            else:
                self.fly_star(a, delay, acs, out)
        for a in acs.values():
            v = self.vec.get(a["cs"])
            if a["freq"] == "approach" and a["airborne"] and v and v["phase"] in ("missed", "missed_north"):
                self.fly_vectors(a, 0.0, acs, out)
        self.probe(acs, out)
        self.handoffs(acs, out)

    def probe(self, acs, out):
        """Conflict probe (6.2.2): two minutes straight ahead; resolve vertically by levelling the
        aircraft that is moving toward the other's level, 1,000 ft away from it. Release when clear."""
        c = self.c
        mine = [a for a in acs.values() if a["freq"] == "approach" and a["airborne"] and not a["established"]
                and not (a["cs"] in self.cleared and a["leg"] == "final") and not a["holding"]
                and not a["hold_fix"] and not (a["cs"] in self.hold and not self.hold[a["cs"]].get("out"))]
        others = [o for o in acs.values() if o["airborne"] and o["alt"] > W.ELEV + 300]
        for a in mine:
            cs = a["cs"]
            if cs in self.level:
                alt, ocs = self.level[cs]
                o = acs.get(ocs)
                if o is None or not o["airborne"] or o["established"] or \
                        (closest_approach(a, o, 120) > 5.0 and dist((a["x"], a["y"]), (o["x"], o["y"])) > 5.0):
                    if c.quiet(cs, 10):
                        self.level.pop(cs)
                        v = self.vec.get(cs)
                        if v and v["phase"] in ("downwind", "missed_north"):
                            pass
                        elif v and v["phase"] == "base":
                            c.send(out, C.vector(cs, altitude=self.intercept_alt(a, "base")))
                        elif not v:
                            c.send(out, C.altitude_at_discretion(cs))
                continue
            if not c.quiet(cs, 8) or cs in self.cleared:
                continue
            for o in others:
                if o is a:
                    continue
                if o["established"] and a["runway"] != o["runway"] and o["runway"] in W.ARR_RUNWAYS:
                    pass
                md = closest_approach(a, o, 120)
                if md > 3.6:
                    continue
                dz = a["alt"] - o["alt"]
                if abs(dz) >= 1500 and (dz * a["vs"] >= 0 or abs(dz) > 3000):
                    continue
                if abs(dz) >= 1000 and (dz * a["vs"] >= 0):
                    continue
                # a is moving toward o's level (or already within 1,000 ft): level a 1,000 ft away
                if dz >= 0:
                    lvl = menu_alt_above(o["alt"] + 1000)
                    if lvl > a["alt"] + 600 and dz * a["vs"] >= 0:
                        continue
                else:
                    lvl = menu_alt_below(o["alt"] - 1000)
                    lvl = max(lvl, menu_alt_above(mva_ahead(a["x"], a["y"], a["hdg"], a["gs"])))
                    if lvl > o["alt"] - 900:
                        continue
                c.send(out, C.vector(cs, altitude=lvl))
                self.level[cs] = (lvl, o["cs"])
                break

    def kind_of(self, a):
        k = self.kind.get(a["cs"]) or a["approach_request"] or "ils"
        if k not in P.APPROACH_IDS.get(a["runway"], {}):
            k = "ils"
        return k

    def star_end(self, a):
        """The STAR's last fix, or None when the STAR ends in vectors (no published join)."""
        fix, vec = P.star_end(P.star_legs(a["star"], a["star_trans"], a["runway"]))
        return None if vec else fix

    def clear(self, a, out):
        """Approach clearance on the flight plan off the STAR's published transition (6.10.2,
        6.11.5), with 3,000 ft (below the glidepath at the IF) until established."""
        cs = a["cs"]
        self.c.send(out, C.cleared_approach(cs, approach=self.kind_of(a), runway=a["runway"], altitude=3000))
        self.cleared.add(cs)
        self.committed[cs] = self.sta.get(cs, self.c.t)

    def fly_star(self, a, delay, acs, out):
        c = self.c
        cs = a["cs"]
        route = a["route"] or []
        rem = a["rem_nm"] or 99
        if not c.quiet(cs, 6):
            return
        # BDEGA4 ends in vectors at BRIXX: downwind from CORKK (6.10.4)
        if a["star"] == "BDEGA4" and not a["holding"] and a["hold_fix"] is None \
                and (a["leg"] == "vectors" or (route and route[0] == "BRIXX")):
            alt = max(DOWNWIND_ALT, menu_alt_above(mva_ahead(a["x"], a["y"], DOWNWIND_T, a["gs"])))
            c.send(out, C.vector(cs, heading=90, altitude=alt))
            out.append(C.maintain_speed(cs, kts=210, mode=None))
            self.vec[cs] = {"phase": "downwind"}
            self.speed[cs] = (210, "kts")
            return
        if a["holding"]:
            if cs in self.hold:
                self.manage_hold(a, delay, acs, out)
            elif cs not in self.cleared and delay < 150 and self.star_end(a):
                self.clear(a, out)                     # holding at the clearance limit (6.9.4)
            return
        hf = HOLD_FIX.get(a["star"])
        if a["hold_fix"] is not None:
            return
        # 1) holds at the stream's fix when speed alone cannot absorb the delay (6.7), stacked 1,000 ft
        absorb = absorbable(c.perf(a), rem, a["alt"]) if a["star"] != "BDEGA4" else TROMBONE_S
        if delay > absorb + 15 and delay < absorb + 150 and cs not in self.cleared and rem > 12 \
                and c.quiet(cs, 30) and self.t360.get(cs, -999) < c.t - 200 and not self.followed(a, acs):
            c.send(out, C.make_360(cs, direction="right"))           # ~150 s of delay (6.7.2)
            self.t360[cs] = c.t
            return
        # an active stack on the route is joined on top, never descended through (6.7.2, 6.2.2)
        through = bool(hf and hf[0] in route[:2] and cs not in self.cleared and self.stack_active(hf[0], a, acs))
        if hf and (delay > absorb + 15 or through) and hf[0] in route[:2] and cs not in self.cleared:
            alt = self.stack_alt(hf, a, acs)
            if alt is not None:
                # levels in sequence order: a later arrival still descending to the fix from above
                # that was given a lower level takes this (higher) one, we take its level
                swap = None
                for o, h in self.hold.items():
                    ob = acs.get(o)
                    if o == cs or ob is None or h["fix"] != hf[0] or h.get("out") or ob["holding"] \
                            or not ob["hold_fix"] or h["alt"] >= alt:
                        continue
                    if self.key.get(o, 0) > self.key.get(cs, 0) and ob["alt"] > a["alt"] + 1000 \
                            and ob["alt"] >= alt - 300 and (swap is None or h["alt"] < self.hold[swap]["alt"]):
                        swap = o
                if swap is not None and c.quiet(swap, 4):
                    low = self.hold[swap]["alt"]
                    self.hold[swap]["alt"] = alt
                    c.send(out, C.vector(swap, altitude=alt))
                    alt = low
                c.send(out, C.hold_at_fix(cs, fix=hf[0], altitude=alt, turns=HOLD_TURNS.get(hf[0], "right")))
                self.hold[cs] = {"fix": hf[0], "alt": alt}
                self.speed.pop(cs, None)
                return
            through = False
        # 2) the approach clearance when approaching the STAR's last fix (the IAF)
        end = self.star_end(a)
        if end and cs not in self.cleared and a["leg"] == "star" and end in route and not through:
            p = W.K.FIXES.get(end)
            if p and dist((a["x"], a["y"]), p) < 14 and route.index(end) <= 1:
                self.clear(a, out)
                return
        # 3) too early even at the slowest speed and no hold ahead: a 360 (6.7.2) if nobody follows close
        if delay > absorb + 45 and not a["established"] and rem > 10 and c.quiet(cs, 40) \
                and self.t360.get(cs, -999) < c.t - 200 and not self.followed(a, acs):
            c.send(out, C.make_360(cs, direction="right"))
            self.t360[cs] = c.t
            return
        # 4) speed (6.15): the fastest speed that still meets the slot
        self.speed_control(a, acs, out)

    def stack_active(self, fix, a, acs):
        """Another aircraft is in the holding pattern at `fix`, or assigned to it and nearer the fix."""
        p = W.K.FIXES.get(fix)
        if p is None:
            return False
        mine = dist((a["x"], a["y"]), p)
        for o, h in self.hold.items():
            if o == a["cs"] or o not in acs or h["fix"] != fix:
                continue
            ob = acs[o]
            if ob["holding"]:
                return True
            if ob["hold_fix"] and not h.get("out") and dist((ob["x"], ob["y"]), p) < mine:
                return True
        return False

    def followed(self, a, acs, d=7.0):
        """Traffic that a 360 here would get in the way of: anyone within 6 nm / 1,500 ft, anyone
        following within 7 nm, or a holding fix in use within 8 nm."""
        for o in acs.values():
            if o is a or not o["airborne"]:
                continue
            r = dist((a["x"], a["y"]), (o["x"], o["y"]))
            if r < 6 and abs(o["alt"] - a["alt"]) < 1500:
                return True
            if o["intent"] == "arrival" and r < d and abs(o["alt"] - a["alt"]) < 2000 \
                    and (o["rem_nm"] or 0) > (a["rem_nm"] or 0):
                return True
        for cs, h in self.hold.items():
            if cs in acs and not h.get("out") and dist((a["x"], a["y"]), W.K.FIXES[h["fix"]]) < 8:
                return True
        return False

    SPEED_STEPS = [(None, None), (240, "kts"), (230, "kts"), (220, "kts"), (210, "kts"), (180, "app"),
                   (170, "app"), (160, "app"), ("slowest", "slow")]

    def speed_control(self, a, acs, out):
        """Pick the fastest of: own speed, 240-210 kts (while > 16 nm out), approach speed 180/170/160
        (to 6/5/4 nm) or slowest practical (> 6 nm, never inside 4 nm, 3.1.4), whose ETA is not
        early for the slot. Changes at most every 30 s, and only for a 15 s improvement."""
        c = self.c
        cs = a["cs"]
        rem = a["rem_nm"]
        if rem is None or a["established"] or rem < 7.0:
            return
        sta = self.sta.get(cs)
        if sta is None:
            return
        perf = c.perf(a)
        best = None
        for v, kind in self.SPEED_STEPS:
            if kind == "kts" and (rem < 18 or v > (250 if a["alt"] < 10500 else perf["cruise"]) or v < perf["vapp"] + 30):
                continue
            if kind == "app" and (rem > 20 or v < perf["vapp"] + 5 or a["alt"] > 8000):
                continue
            if kind == "slow" and (rem < 9 or rem > 25 or a["alt"] > 8000):
                continue
            until = {None: 0.0, "kts": 16.0, "app": {180: 6, 170: 5, 160: 4}.get(v, 0), "slow": 6.0}[kind]
            e = time_to_go(perf, rem, a["alt"], a["gs"], v, until)
            best = (v, kind, e)
            if c.t + e >= sta - 8:
                break
        if best is None:
            return
        cur = self.speed.get(cs)
        cur_key = cur[0] if cur else None
        if best[0] == cur_key:
            return
        forced = cur is not None and cur[1] == "kts" and rem < 17
        if not forced:
            if self.c.t - self.speed_t.get(cs, -999) < 30:
                return
            e_cur = landing_eta(c, a)
            if e_cur is not None and abs((c.t + e_cur) - sta) < 15:
                return
        v, kind, _ = best
        if kind is None:
            c.send(out, C.speed_at_discretion(cs))
            self.speed.pop(cs, None)
        elif kind == "kts":
            c.send(out, C.maintain_speed(cs, kts=v, mode=None))
            self.speed[cs] = (v, kind)
        elif kind == "app":
            c.send(out, C.approach_speed(cs, kts=v))
            self.speed[cs] = (v, kind)
        else:
            c.send(out, C.slowest_practical_speed(cs))
            self.speed[cs] = (v, kind)
        self.speed_t[cs] = c.t

    def stack_alt(self, hf, a, acs):
        fix, base = hf
        used = {h["alt"] for cs, h in self.hold.items() if cs in acs and acs[cs]["airborne"] and h["fix"] == fix
                and cs != a["cs"] and (not h.get("out") or acs[cs]["holding"] or acs[cs]["alt"] > h["alt"] - 900)}
        # on top of the stack, so the stack leaves in sequence order from the bottom
        alt = max(used) + 1000 if used else base
        return alt if alt <= 17000 else None

    def manage_hold(self, a, delay, acs, out):
        """Exit the hold when the slot comes (the pilot leaves at the next fix passage), lowest
        first, then descend via; step the stack down 1,000 ft at a time."""
        c = self.c
        cs = a["cs"]
        h = self.hold[cs]
        same = sorted((hh["alt"], o) for o, hh in self.hold.items()
                      if o in acs and hh["fix"] == h["fix"] and not hh.get("out"))
        lowest = same and same[0][1] == cs
        absorb = absorbable(c.perf(a), a["rem_nm"] or 0, a["alt"]) if a["star"] != "BDEGA4" else 30.0
        if lowest and delay < absorb * 0.9 + 10 and c.once(cs, "exit_hold"):
            h["out"] = True
            c.send(out, C.exit_hold(cs))
            out.append(C.altitude_at_discretion(cs))
            return
        below = h["alt"] - 1000
        if below >= HOLD_FIX_BASE.get(h["fix"], 0) and c.quiet(cs, 20):
            taken = any(hh["fix"] == h["fix"] and hh["alt"] == below and o in acs and o != cs
                        and not hh.get("out") for o, hh in self.hold.items())
            busy = any(o != cs and acs[o]["airborne"] and abs(acs[o]["alt"] - below) < 900 and
                       dist((a["x"], a["y"]), (acs[o]["x"], acs[o]["y"])) < 8 for o in acs)
            if not taken and not busy:
                c.send(out, C.vector(cs, altitude=below))
                h["alt"] = below

    def fly_vectors(self, a, delay, acs, out):
        """BDEGA4 ends in vectors at BRIXX, and missed approaches: downwind (090) at 7,000, base
        (180), then a 34 degree intercept (250) to the 28R localizer below the glideslope."""
        c = self.c
        cs = a["cs"]
        v = self.vec[cs]
        if not c.quiet(cs, 8):
            return
        ph = v["phase"]
        if ph == "missed":
            # runway heading to the missed approach altitude, then right turn north over the bay
            if a["alt"] > 1500 and a["x"] < -1.5:
                alt = max(5000, menu_alt_above(mva_ahead(a["x"], a["y"], W.mag2true(360), a["gs"])))
                c.send(out, C.vector(cs, heading=360, altitude=alt, turn="right"))
                v["phase"] = "missed_north"
            return
        if ph == "missed_north":
            if a["y"] > 4.0:
                alt = max(5000, menu_alt_above(mva_ahead(a["x"], a["y"], DOWNWIND_T, a["gs"])))
                c.send(out, C.vector(cs, heading=90, altitude=alt))
                out.append(C.maintain_speed(cs, kts=210, mode=None))
                self.speed[cs] = (210, "kts")
                v["phase"] = "downwind"
            return
        if ph == "downwind":
            xb = self.plan_base(a)
            v["x_base"] = xb
            if not a["leg"] == "360" and self.trombone_short(a, max(xb, a["x"])) > 60:
                # more delay than the trombone can take: a 360 on the downwind, away from the
                # final (6.7.2), or slow down if traffic follows, rather than turning base early
                # into the other streams' slots (6.11.8)
                if self.t360.get(cs, -999) < c.t - 200 and not self.followed(a, acs):
                    c.send(out, C.make_360(cs, direction="left"))
                    self.t360[cs] = c.t
                    return
                if self.speed.get(cs, (None,))[0] != 180:
                    c.send(out, C.maintain_speed(cs, kts=180, mode=None))
                    self.speed[cs] = (180, "kts")
                    return
            if a["x"] >= xb and a["x"] < X_BASE_MAX + 7 and self.join_conflict(a, "downwind", acs, a["x"]):
                return                               # extend the downwind: the final is busy there
            if a["x"] >= xb:
                v["x_base"] = a["x"]
                alt = self.intercept_alt(a, "base")
                alt = max(alt, menu_alt_above(mva_ahead(a["x"], a["y"], BASE_T, a["gs"])))
                if cs in self.level:
                    c.send(out, C.vector(cs, heading=180))
                else:
                    c.send(out, C.vector(cs, heading=180, altitude=alt))
                v["phase"] = "base"
                self.committed[cs] = self.sta.get(cs, c.t)
            return
        if ph == "base":
            off, along = offset_right(a["x"], a["y"])
            short = self.pattern_short(a, "base") if a["leg"] != "360" else 0.0
            if a["leg"] != "360" and off > INTERCEPT_OFFSET + 3.0 and (short > 60 or self.join_conflict(a, "base", acs)):
                # still well ahead of the slot on base: a left 360 (away from the field and the
                # final, 6.7.2) only for more than ~120 s (it takes ~150 s), else slow down
                # (180 kts, then 160 kts approach speed, 6.15), instead of joining early (6.11.8)
                if (short > 120 or self.join_conflict(a, "base", acs)) and self.t360.get(cs, -999) < c.t - 200 \
                        and not self.followed(a, acs) and mva_clear_360(a):
                    c.send(out, C.make_360(cs, direction="left"))
                    self.t360[cs] = c.t
                    return
                if self.speed.get(cs, (None,))[0] not in (180, 160):
                    c.send(out, C.maintain_speed(cs, kts=180, mode=None))
                    self.speed[cs] = (180, "kts")
                    return
                if self.speed.get(cs, (None,))[0] == 180 and short > 60 and c.perf(a)["vapp"] + 5 <= 160:
                    c.send(out, C.approach_speed(cs, kts=160))
                    self.speed[cs] = (160, "app")
                    return
            R = (a["gs"] / 3600) / math.radians(2.2)
            lead = R * (1 - math.cos(math.radians(abs(W.ang_diff(BASE_T, INTERCEPT_T)))))
            if off <= INTERCEPT_OFFSET + lead and abs(W.ang_diff(a["hdg"], BASE_T)) < 8:
                alt = self.intercept_alt(a, "intercept")
                c.send(out, C.cleared_approach(cs, approach="ils", runway="28R", heading=INTERCEPT_MAG, altitude=alt))
                if a["alt"] > alt + 200 and cs not in self.level:
                    # maintain-until-established holds a higher aircraft level: descend it to the
                    # intercept altitude so it joins from below the glidepath (6.11.4)
                    out.append(C.vector(cs, heading=INTERCEPT_MAG, altitude=alt))
                out.append(C.approach_speed(cs, kts=180))
                self.speed[cs] = (180, "app")
                v["phase"] = "intercept"
                self.level.pop(cs, None)
                self.cleared.add(cs)
            return

    def plan_base(self, a):
        """Turn base where the remaining pattern fits the slot (trombone), X_BASE_MIN..X_BASE_MAX east,
        and at least 4 nm east of where the vectored aircraft ahead in the sequence turned (or will)."""
        target = self.sta.get(a["cs"], self.c.t) - self.c.t
        perf = self.c.perf(a)
        xb = X_BASE_MIN
        mine = self.sta.get(a["cs"], 0)
        for o, v in self.vec.items():
            if o != a["cs"] and v.get("x_base") and v["phase"] in ("downwind", "base") and self.sta.get(o, 0) < mine:
                xb = max(xb, v["x_base"] + 4.0)
        while xb < X_BASE_MAX:
            L, a_join = vector_path(a["x"], a["y"], a["hdg"], max(a["gs"], 200), "downwind", max(xb, a["x"]))
            T = L / max(a["gs"], 200) * 3600 + time_to_go(perf, a_join, a_join * 320)
            if T >= target - 5:
                break
            xb += 0.5
        # vectored traffic behind us in the sequence but already further out on the downwind
        # turns base after us, 4 nm further out: leave it room before the downwind limit
        behind = sum(1 for o, v in self.vec.items() if o != a["cs"] and v["phase"] == "downwind"
                     and self.sta.get(o, 0) > mine and o in self.acs and self.acs[o]["x"] > a["x"])
        return min(max(xb, X_BASE_MIN), X_BASE_MAX + 6 - 4.0 * behind)

    def join_conflict(self, a, phase, acs, x_base=None):
        """Traffic for 28L/28R that would be within 3.5 track miles of where this vectored
        arrival joins the final, at the time it gets there (6.11.8: 3 nm until established)."""
        gs = max(a["gs"], 180)
        L, a_join = vector_path(a["x"], a["y"], a["hdg"], gs, phase, x_base)
        T = L / gs * 3600
        for o in acs.values():
            if o is a or not o["airborne"] or o["intent"] != "arrival" or o["missed"] or o["rem_nm"] is None \
                    or o["runway"] not in W.ARR_RUNWAYS:
                continue
            ov = self.vec.get(o["cs"])
            if not (o["established"] or o["leg"] in ("final", "app") or (ov and ov["phase"] == "intercept")):
                continue
            r = o["rem_nm"] - max(o["gs"], 120) * T / 3600
            if r > 0.5 and abs(r - a_join) < 3.5:
                return True
        return False

    def pattern_short(self, a, phase):
        """Seconds the rest of the vector pattern from here lands ahead of the slot."""
        target = self.sta.get(a["cs"], self.c.t) - self.c.t
        gs = max(a["gs"], 180)
        L, a_join = vector_path(a["x"], a["y"], a["hdg"], gs, phase)
        return target - (L / gs * 3600 + time_to_go(self.c.perf(a), a_join, a_join * 320))

    def trombone_short(self, a, xb):
        """Seconds the downwind/base/intercept pattern with the base turn at xb still lands
        ahead of the slot."""
        target = self.sta.get(a["cs"], self.c.t) - self.c.t
        gs = max(a["gs"], 180)
        L, a_join = vector_path(a["x"], a["y"], a["hdg"], gs, "downwind", max(xb, a["x"]))
        T = L / gs * 3600 + time_to_go(self.c.perf(a), a_join, a_join * 320)
        return target - T

    def intercept_alt(self, a, phase):
        L, a_join = vector_path(a["x"], a["y"], a["hdg"], a["gs"], phase)
        gs_alt = RWY28R.glide_alt(max(2.0, a_join), RWY28R.gs_angle)
        return max(2000, min(menu_alt_below(gs_alt - 200), 5000))

    def handoffs(self, acs, out):
        """6.14: established on final -> tower."""
        c = self.c
        for a in acs.values():
            if a["freq"] == "approach" and a["airborne"] and a["intent"] == "arrival" and a["established"] \
                    and a["approach_cleared"] and c.quiet(a["cs"], 3) and c.once(a["cs"], "to_tower"):
                c.send(out, C.contact_frequency(a["cs"], facility="tower"))
                self.vec.pop(a["cs"], None)
                c.forget(a["cs"], "exit_hold")


HOLD_FIX_BASE = {f: b for f, b in HOLD_FIX.values()}


# ---------------------------------------------------------------------------- departure
class Departure:
    def __init__(self, ctl):
        self.c = ctl
        self.cap = {}

    def step(self, obs, acs, reqs, out):
        c = self.c
        for r in reqs:
            a = acs.get(r["cs"])
            if a is None or a["freq"] != "departure":
                continue
            if r["kind"] == "check_in" and c.quiet(a["cs"], 2):
                c.send(out, C.radar_contact(a["cs"]))
            elif r["kind"] == "request_freq_change" and c.quiet(a["cs"], 2):
                c.send(out, C.contact_frequency(a["cs"], facility="center"))
        for a in acs.values():
            if a["freq"] != "departure" or not a["airborne"]:
                continue
            cs = a["cs"]
            d = math.hypot(a["x"], a["y"])
            if (d > 42 or a["alt"] > 16500) and c.quiet(cs, 3) and c.once(cs, "to_ctr"):
                c.send(out, C.contact_frequency(cs, facility="center"))
                continue
            if not a["radar_contact"] or not c.quiet(cs, 8):
                continue
            cap = self.needed_cap(a, acs)
            cur = self.cap.get(cs)
            if cap is not None and (cur is None or cap < cur):
                c.send(out, C.vector(cs, altitude=cap))
                self.cap[cs] = cap
            elif cap is None and cur is not None:
                c.send(out, C.altitude_at_discretion(cs))
                self.cap.pop(cs)

    def needed_cap(self, a, acs):
        """1,000 ft under any other aircraft above us that we would come within 5 nm of in the next
        3 minutes (straight-line projection)."""
        best = None
        for o in acs.values():
            if o is a or not o["airborne"] or o["alt"] < a["alt"] - 300:
                continue
            if o["alt"] - a["alt"] > 6000:
                continue
            md = closest_approach(a, o, 180)
            if md < 5.5:
                lim = menu_alt_below(o["alt"] - 1000)
                if o["vs"] < -300:
                    lim = menu_alt_below(min(o["alt"], o.get("alt_assigned") or o["alt"]) - 1000)
                if lim < a["alt"] - 50:
                    lim = menu_alt_above(a["alt"] - 50)       # never descend a departure
                lim = max(lim, menu_alt_above(W.mva(a["x"], a["y"])))
                if best is None or lim < best:
                    best = lim
        return best


def closest_approach(a, o, horizon):
    ax, ay = W.unit(a["hdg"])
    bx, by = W.unit(o["hdg"])
    va, vb = a["gs"] / 3600, o["gs"] / 3600
    dx, dy = o["x"] - a["x"], o["y"] - a["y"]
    dvx, dvy = bx * vb - ax * va, by * vb - ay * va
    vv = dvx * dvx + dvy * dvy
    tt = 0.0 if vv < 1e-12 else max(0.0, min(horizon, -(dx * dvx + dy * dvy) / vv))
    return math.hypot(dx + dvx * tt, dy + dvy * tt)
