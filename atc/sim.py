"""Fast-time simulation of KSFO (West Plan) with Ground, Tower, Approach and Departure.

One tick is one simulated second. Each tick: spawn traffic, deliver controller instructions
whose pilot reaction delay has elapsed, move every aircraft, let the rules monitor look at the
world, then ask the controller for commands.

Airborne flight model and procedure flying: see sim/flight.py. Ground model:
  taxi      along the OSM taxiway graph (edge geometry, not straight node-to-node lines), with
            smooth heading changes, ~8-10 kt in corners and up to 20 kt on straights, stopping
            for traffic ahead. Routes follow the taxiway names of the clearance (drag & taxi).
  runways   a pilot never enters a runway strip without a clearance: taxi routes stop at a
            hold-short point ~260 ft from the centerline of every runway they cross until
            "cross runway XX" is received (7110.65 3-7-2), and the departure hold point
            until line-up / takeoff clearance.
  pushback  tail first along the stand lead-in line, then the tug swings the nose to the taxi
            direction.
  landing   threshold at TCH on the runway's glidepath, flare, touchdown ~1,000-1,500 ft in,
            braking to the turn-off speed of the chosen exit (30 kt high-speed, 12 kt
            right-angle), then along the exit taxiway until clear of the runway edge. Exits off
            28R to the south end between the runways, holding short of 28L.
  takeoff   line up from the holding point, roll to Vr, 3 s rotation, liftoff, runway heading
            to 400 ft, then the SID leg by leg.

Pilots are imperfect on purpose: reaction delays, slow rolls, sloppy speed and spacing,
occasional rejected takeoffs, slow exits, late frequency changes, localizer overshoots.
"""
import copy
import heapq
import math

from . import flight as F
from . import procs as P
from . import world as W
from .commands import ANSWERS, INFORMATIONAL, R, normalize, positions, render, render_pilot
from .flight import Aircraft
from .rules import Monitor

PATTERN_ALT = 1500        # jets, 1,500 ft above the aerodrome (AIM 4-3-3); KSFO is at 13 ft
GROUND_PHASES = {"pushing", "pushed", "taxiing", "holding_short", "clear", "taxi_in", "exiting", "lining_up"}
RUNWAY_PHASES = {"lining_up", "lined_up", "rolling", "landing_roll", "exiting", "rto"}
TAXI_ACC = 1.0
TAXI_BRAKE = 1.5
HARD_BRAKE = 3.0
CORNER_KT = 8.0
LOOKAHEAD_NM = 0.012
GROUND_R_NM = 0.01
TAXI_SPEED = 20.0
PUSH_SPEED = 4.0
RWY_EXIT_LEAD = 0.01
FT = W.FT_NM
INF = float("inf")
AAR = 36                  # KSFO arrival acceptance rate per hour used by the ground delay program
GDP_EXEMPT_S = 300        # arrivals in the first 5 minutes are already airborne nearby: no EDCT
MAYDAY_MARGIN_S = 300     # Mayday fuel must land within its direct time to land plus this, or divert
OAKLAND = (7.36, 6.15)    # KOAK (nm east, north of KSFO): the diversion airport
DIVERT_LEAVE_S = 120      # a diverting aircraft leaves the frequency (and the session) after this


class Sim:
    def __init__(self, scenario, controller, record=False):
        self.sc = scenario
        self.seed = scenario["seed"]
        W.set_config(scenario.get("config", "west"))      # the airport configuration of this session
        self.controller = controller
        self.record = record
        self.t = 0
        self.duration = scenario["duration"]
        self.t_end = self.duration + 45 * 60
        self.atis = "Alpha"
        self.aircraft = {}
        self.spawns = sorted(self.meter_arrivals(scenario["traffic"]), key=lambda s: (s["t"], s["cs"]))
        self.pending = []
        self.requests = []
        self.req_id = 0
        self.queue = {r: [] for r in W.DEP_RUNWAYS}
        self.departed = []                 # (t_roll, runway, wake, cs)
        self.transcript = []
        self.frames = []
        self.fixlog = []                   # (t, cs, fix, tag, x, y, alt, ias, amin, amax, spd, sptype)
        self.controller_errors = 0
        self.monitor = Monitor(self)
        self.reserved = {s.get("stand") for s in self.spawns if s.get("stand")}

    # ------------------------------------------------------------------ traffic management
    def meter_arrivals(self, traffic):
        """Ground delay program (FAA TFMS): when arrival demand is above the airport acceptance rate,
        flights still at their origin get an EDCT so they reach the runway in a free slot instead of
        holding. Slots are given in order of runway ETA (unimpeded); flights in the first five
        minutes are already airborne close in and are exempt, but use their slots. The ground delay is
        not the controller's and is not scored (reported as gdp_delay_min)."""
        out, eta = [], []
        for s in traffic:
            s = dict(s)
            out.append(s)
            if s["intent"] == "arrival":
                probe = Aircraft(s, self.seed)              # own RNG: nothing shared is consumed
                self.spawn_arrival(probe, s)
                eta.append((s["t"] + probe.m["nominal"], s))
        gap = 3600.0 / W.CONFIGS[W.CONFIG_KEY].get("aar", AAR)     # single-runway configurations accept fewer
        last = -INF
        for e, s in sorted(eta, key=lambda p: (p[0], p[1]["cs"])):
            slot = max(e, last + gap)
            last = slot
            s["edct_s"] = 0
            if s["t"] >= GDP_EXEMPT_S and slot - e > 60:
                s["edct_s"] = int(slot - e)
                s["t"] += s["edct_s"]
        return out

    # ------------------------------------------------------------------ taxi routes
    def plan_route(self, start, goal, via=None):
        """Node path from start to goal. With `via`, named taxiways are used in that order;
        unnamed connectors, stand lines and taxilanes may be used anywhere. Runway centerline
        edges are never taxied on; entering a runway strip costs extra so routes cross only
        where they must."""
        if start is None or goal is None:
            return None
        if start == goal:
            return [start]
        via = list(via or [])
        last = len(via) - 1
        lead = W.lead_in_nodes(start, goal)
        pq = [(0.0, start, 0)]
        best = {(start, 0): 0.0}
        prev = {}
        while pq:
            d, n, i = heapq.heappop(pq)
            if n == goal and i >= last:
                path = [n]
                key = (n, i)
                while key in prev:
                    key = prev[key]
                    path.append(key[0])
                return list(reversed(path))
            if best.get((n, i), 1e18) < d:
                continue
            for m, k in W.ADJ[n]:
                a, b, name, kind, L, _ = W.EDGES[k]
                if kind in W.RUNWAY_EDGE_KINDS:
                    continue
                if kind == "parking_position" and not (m in lead and n in lead) \
                        and not (m == goal or n == goal or n == start or m == start):
                    continue
                free = W.edge_free(k)
                if not via or free:
                    j = i
                elif name == via[i]:
                    j = i
                elif i < last and name == via[i + 1]:
                    j = i + 1
                else:
                    continue
                cost = d + L + (0.4 if W.NODE_ZONE[m] and not W.NODE_ZONE[n] else 0.0)
                if cost < best.get((m, j), 1e18):
                    best[(m, j)] = cost
                    prev[(m, j)] = (n, i)
                    heapq.heappush(pq, (cost, m, j))
        return None

    @staticmethod
    def route_points(nodes):
        pts = [(*W.NODES[nodes[0]], nodes[0])]
        for a, b in zip(nodes, nodes[1:]):
            ks = [k for m, k in W.ADJ[a] if m == b]
            k = min(ks, key=lambda kk: W.EDGES[kk][4])
            g = W.edge_geom(k, a)
            for p in g[1:-1]:
                pts.append((p[0], p[1], None))
            pts.append((*W.NODES[b], b))
        return pts

    @staticmethod
    def add_stops(pts, ignore=()):
        """Insert a hold-short point where the route enters a runway strip. Returns (pts, stops)."""
        out = [pts[0]]
        stops = []
        inside = set(W.zone_runways(pts[0][0], pts[0][1])) | set(ignore)
        for p in pts[1:]:
            q = out[-1]
            zq = set(W.zone_runways(q[0], q[1]))
            zp = set(W.zone_runways(p[0], p[1]))
            inside &= zq | zp
            for r in sorted(zp - zq - inside):
                R = W.RUNWAYS[r]
                vq = abs(R.uv(q[0], q[1])[1]) * FT
                vp = abs(R.uv(p[0], p[1])[1]) * FT
                if vq > W.HOLD_FT + 5 and vq > vp:
                    f = (vq - W.HOLD_FT) / (vq - vp)
                    h = (q[0] + f * (p[0] - q[0]), q[1] + f * (p[1] - q[1]), None)
                    out.append(h)
                stops.append({"k": len(out) - 1, "rwy": r, "cleared": False, "done": False, "entered": False})
                inside.add(r)
            out.append(p)
        return out, stops

    def set_taxi(self, ac, nodes, ignore=()):
        pts = self.route_points(nodes)
        pts, stops = self.add_stops(pts, ignore)
        ac.gpath, ac.gk, ac.gstops = pts, 1 if len(pts) > 1 else 0, stops
        ac.taxi_total = max(0.01, self.path_len(ac))
        ac._cross_names = [s["rwy"] for s in stops]

    def path_len(self, ac):
        pts = [(ac.x, ac.y)] + [p[:2] for p in ac.gpath[ac.gk:]]
        return sum(W.dist(pts[i], pts[i + 1]) for i in range(len(pts) - 1))

    def stop_k(self, ac):
        end = len(ac.gpath) - 1
        for s in ac.gstops:
            if not s["done"] and not s["cleared"]:
                end = s["k"]
                break
        return end

    # ------------------------------------------------------------------ follow traffic (3-7-2)
    @staticmethod
    def edge_name(a, b):
        ks = [k for m, k in W.ADJ[a] if m == b]
        return W.EDGES[min(ks, key=lambda kk: W.EDGES[kk][4])][2] if ks else None

    @staticmethod
    def ahead_pts(ac, look):
        """The aircraft's position and its route ahead, `look` nm of it."""
        pts = [(ac.x, ac.y)]
        if not ac.gpath:
            return pts
        d = 0.0
        for p in ac.gpath[ac.gk:]:
            d += W.dist(pts[-1], p[:2])
            pts.append(p[:2])
            if d >= look:
                break
        return pts

    @staticmethod
    def poly_dist(p, q):
        """Smallest distance between two polylines (nm)."""
        def ps(pt, a, b):
            dx, dy = b[0] - a[0], b[1] - a[1]
            L = dx * dx + dy * dy
            t = 0.0 if L < 1e-12 else max(0.0, min(1.0, ((pt[0] - a[0]) * dx + (pt[1] - a[1]) * dy) / L))
            return math.hypot(pt[0] - a[0] - t * dx, pt[1] - a[1] - t * dy)
        segs_p = list(zip(p, p[1:])) or [(p[0], p[0])]
        segs_q = list(zip(q, q[1:])) or [(q[0], q[0])]
        best = 1e9
        for a, b in segs_p:
            for c, d in segs_q:
                best = min(best, ps(a, c, d), ps(b, c, d), ps(c, a, b), ps(d, a, b))
        return best

    def follow_target(self, ac, cs):
        """The traffic named in a 'follow' (3-7-2): on the ground (or rolling out), near enough to matter."""
        o = self.aircraft.get(cs)
        if o is None or o is ac or o.m["done"] or (o.airborne and o.phase != "landing_roll"):
            return None
        if o.phase not in GROUND_PHASES and o.phase not in ("landing_roll", "exiting", "clear"):
            return None
        return o if math.hypot(o.x - ac.x, o.y - ac.y) <= 0.6 else None

    def update_give_way(self, ac):
        """Pilot told to follow traffic (3-7-2): keeps taxiing, stops short of the traffic's path while
        it conflicts, and keeps the instruction until the traffic has passed (the paths met and are
        clear again), is gone, or is standing still itself. One instruction is enough."""
        gw = ac.give_way
        o = self.aircraft.get(gw["cs"])
        gone = o is None or o.m["done"] or (o.airborne and o.phase != "landing_roll") \
            or o.phase in ("lined_up", "rolling", "airborne") or self.t - gw["t0"] > 240
        conf = False
        if not gone:
            mine = self.ahead_pts(ac, 0.12)
            theirs = self.ahead_pts(o, 0.25) if o.gpath and o.gk < len(o.gpath) else [(o.x, o.y)]
            conf = self.poly_dist(mine, theirs) < 0.035
            gw["still_s"] = gw["still_s"] + 1 if o.gspeed < 0.5 else 0
            if (gw["still_s"] > 20 and ac.gspeed < 0.5) or (o.blocked_s > 5 and ac.gspeed < 0.5):
                gone = True                 # traffic is waiting itself (holding short, or for me): carry on
        if conf:
            gw["met"] = True
        gw["clear_s"] = 0 if conf else gw["clear_s"] + 1
        if gone or (gw.get("met") and gw["clear_s"] >= 3):
            ac.give_way, ac.gw_stop = None, False
            return
        ac.gw_stop = conf

    def ground_traffic(self):
        return [o for o in self.aircraft.values() if not o.m["done"] and o.phase in GROUND_PHASES]

    def traffic_ahead(self, ac, ux, uy, rng=0.05):
        squeeze = getattr(ac, "squeeze", None)
        for o in self._ground:
            if o is ac or (squeeze and squeeze[0] == o.cs and self.t < squeeze[1]):
                continue
            ox, oy = o.x - ac.x, o.y - ac.y
            if abs(ox) > rng or abs(oy) > rng:
                continue
            od = math.hypot(ox, oy)
            if 0 < od < rng and (ox * ux + oy * uy) / od > 0.8:
                ac.blocked_s += 1
                ac.blocked_by = o.cs
                head_on = o.blocked_s > 0 and (abs(W.ang_diff(ac.hdg, o.hdg)) > 120
                                               or getattr(o, "blocked_by", None) == ac.cs)   # blocking each other
                if head_on and ac.blocked_s == 90:
                    self.monitor.on_event("ground_blocked", ac, other=o)
                if not head_on or ac.blocked_s < 120:
                    return True
                ac.squeeze = (o.cs, self.t + 90)
                break
        ac.blocked_s = 0
        return False

    @staticmethod
    def ground_speed_to(ac, v_t, brake):
        ac.gspeed += max(-brake, min(TAXI_ACC, v_t - ac.gspeed))
        ac.gspeed = max(0.0, ac.gspeed)
        ac.gs = ac.gspeed

    @staticmethod
    def route_point(ac, look, end):
        px, py = ac.x, ac.y
        for i in range(ac.gk, end + 1):
            nx, ny = ac.gpath[i][0], ac.gpath[i][1]
            d = math.hypot(nx - px, ny - py)
            if d >= look:
                return px + (nx - px) / d * look, py + (ny - py) / d * look
            look -= d
            px, py = nx, ny
        return ac.gpath[end][0], ac.gpath[end][1]

    def taxi_move(self, ac, speed, corner=CORNER_KT, check_traffic=True):
        """Advance along ac.gpath up to the next stop (hold short) or the end. True when the end
        of the route is reached; holding short of an uncleared runway returns False."""
        if not ac.gpath or ac.gk >= len(ac.gpath):
            ac.gspeed = ac.gs = 0.0
            return True
        end = self.stop_k(ac)
        at_stop = end < len(ac.gpath) - 1
        tx, ty = self.route_point(ac, LOOKAHEAD_NM, end)
        lx, ly = self.route_point(ac, 0.02, end)
        dl = math.hypot(lx - ac.x, ly - ac.y)
        h = math.radians(ac.hdg)
        ux, uy = ((lx - ac.x) / dl, (ly - ac.y) / dl) if dl > 1e-6 else (math.sin(h), math.cos(h))
        if (check_traffic and self.traffic_ahead(ac, ux, uy)) or ac.hold or ac.gw_stop:
            v_t, brake = 0.0, HARD_BRAKE
        else:
            v_t, brake = speed, HARD_BRAKE
            pts = [(ac.x, ac.y)] + [p[:2] for p in ac.gpath[ac.gk:end + 1]]
            d_acc, prev = 0.0, ac.hdg
            for i in range(len(pts) - 1):
                seg = W.dist(pts[i], pts[i + 1])
                if seg < 1e-6:
                    continue
                c = W.bearing(pts[i], pts[i + 1])
                th = abs(W.ang_diff(prev, c))
                vc = speed if th < 20 else (min(speed, corner + 2) if th < 60 else min(speed, corner))
                if i == 0:
                    if th > 30:
                        v_t = min(v_t, vc)
                else:
                    v_t = min(v_t, math.sqrt(vc * vc + 2 * TAXI_BRAKE * d_acc * 3600))
                d_acc += seg
                prev = c
                if d_acc > 0.2:
                    break
            else:
                v_t = min(v_t, math.sqrt(2.0 ** 2 + 2 * TAXI_BRAKE * d_acc * 3600))
        self.ground_speed_to(ac, v_t, brake)
        step = ac.gspeed / 3600
        ex, ey = ac.gpath[end][0], ac.gpath[end][1]
        if ac.gk >= end and math.hypot(ex - ac.x, ey - ac.y) <= max(step, 0.0005):
            ac.x, ac.y = ex, ey
            ac.gspeed = ac.gs = 0.0
            if ac.gpath[end][2]:
                ac.gnode = ac.gpath[end][2]
            if at_stop:
                return False
            ac.gk = len(ac.gpath)
            return True
        if step > 0:
            want = W.bearing((ac.x, ac.y), (tx, ty)) if math.hypot(tx - ac.x, ty - ac.y) > 1e-7 else ac.hdg
            wmax = min(20.0, math.degrees(step / GROUND_R_NM))
            old = ac.hdg
            ac.hdg = (ac.hdg + max(-wmax, min(wmax, W.ang_diff(ac.hdg, want)))) % 360
            hm = math.radians(old + W.ang_diff(old, ac.hdg) / 2)
            ac.x += math.sin(hm) * step
            ac.y += math.cos(hm) * step
        while ac.gk < end:
            nx, ny, node = ac.gpath[ac.gk]
            d = math.hypot(nx - ac.x, ny - ac.y)
            fx, fy = ac.gpath[ac.gk - 1][:2] if ac.gk > 0 else (ac.x, ac.y)
            behind = (nx - ac.x) * (nx - fx) + (ny - ac.y) * (ny - fy) <= 0
            if d < LOOKAHEAD_NM or behind:
                if node:
                    ac.gnode = node
                for s in ac.gstops:
                    if s["k"] == ac.gk and s["cleared"]:
                        s["done"] = True
                ac.gk += 1
            else:
                break
        return False

    def push_move(self, ac):
        """Pushback: tail first along the stand line to the taxiway, then the tug swings the nose
        toward the taxi direction (in place)."""
        P_ = ac.push
        if P_["stage"] == "back":
            h = math.radians((ac.hdg + 180) % 360)
            if ac.hold or self.behind_tail(ac, math.sin(h), math.cos(h)):
                self.ground_speed_to(ac, 0.0, 1.0)
            else:
                self.ground_speed_to(ac, min(PUSH_SPEED, ac.gspeed + 0.3), 1.0)
            step = ac.gspeed / 3600
            pts = P_["pts"]
            while step > 0 and P_["i"] < len(pts):
                tx, ty = pts[P_["i"]]
                d = math.hypot(tx - ac.x, ty - ac.y)
                if d <= step:
                    ac.x, ac.y = tx, ty
                    step -= d
                    P_["i"] += 1
                else:
                    mv = W.bearing((ac.x, ac.y), (tx, ty))
                    ac.x += math.sin(math.radians(mv)) * step
                    ac.y += math.cos(math.radians(mv)) * step
                    want = (mv + 180) % 360
                    ac.hdg = (ac.hdg + max(-3.0, min(3.0, W.ang_diff(ac.hdg, want)))) % 360
                    step = 0
            if P_["i"] >= len(pts):
                P_["stage"] = "swing"
                ac.gspeed = ac.gs = 0.0
            return False
        # swing: rotate to the taxi direction, ~3 deg/s
        e = W.ang_diff(ac.hdg, P_["nose"])
        if ac.hold:
            return False
        ac.hdg = (ac.hdg + max(-3.0, min(3.0, e))) % 360
        ac.gs = 1.0
        if abs(e) <= 3.0:
            ac.hdg = P_["nose"]
            ac.gs = ac.gspeed = 0.0
            ac.gnode = P_["end"]
            ac.push = None
            return True
        return False

    def behind_tail(self, ac, ux, uy):
        for o in self._ground:
            if o is ac:
                continue
            ox, oy = o.x - ac.x, o.y - ac.y
            od = math.hypot(ox, oy)
            near = 0.035 if (o.gspeed > 0.5 or o.phase == "pushing") else 0.02
            if 0 < od < near and (ox * ux + oy * uy) / od > 0.5:
                return True
        return False

    def stand_line(self, stand):
        """Nodes from the stand along its lead-in line to the taxiway (stand ... taxiway node)."""
        return W.STAND_LINES[W.STANDS[stand]["node"]]

    def free_stand(self, ac):
        pool = W.CARGO_STANDS if ac.cargo else W.PAX_STANDS
        if ac.wake in ("H", "J"):
            pool = [s for s in pool if W.STANDS[s]["heavy_ok"]] or pool
        occupied = {o.stand for o in self.aircraft.values() if o is not ac and not o.m["done"] and o.stand}
        due = {}
        for sp in self.spawns:
            if sp.get("stand"):
                due[sp["stand"]] = min(due.get(sp["stand"], 1e9), sp["t"])
        free = [s for s in pool if s not in occupied and due.get(s, 1e9) > self.t + 1500]
        if free:
            return ac.rng.choice(free)
        cands = [s for s in pool if s not in occupied] or pool
        return max(cands, key=lambda s: due.get(s, 1e9))

    # ------------------------------------------------------------------ radio
    def log(self, who, text, kind="radio", cs=None):
        self.transcript.append({"t": self.t, "who": who, "text": text, "kind": kind, "cs": cs})

    def pilot_message(self, ac, kind, data):
        self.log(ac.cs, render_pilot(kind, ac, data, self.atis), kind="pilot", cs=ac.cs)
        if kind in INFORMATIONAL:
            return
        self.req_id += 1
        self.requests.append({"id": self.req_id, "t": self.t, "cs": ac.cs, "kind": kind, "freq": ac.freq,
                              "data": data, "answered": None})

    def resolve(self, ac, *kinds):
        for r in self.requests:
            if r["cs"] == ac.cs and r["answered"] is None and r["kind"] in kinds:
                r["answered"] = self.t
        if ac.msg_due and ac.msg_due[1] in kinds:
            ac.msg_due = None

    def issue(self, raw):
        cmd, err = normalize(raw)
        if not isinstance(cmd, dict):
            self.monitor.on_command(None, {"cs": None, "cmd": None}, err)
            self.log("SYS", f"[rejected: {err}] {raw!r}", kind="invalid")
            return
        ac = self.aircraft.get(cmd.get("cs"))
        if err is None and (ac is None or ac.m["done"]):
            err = f"no such aircraft {cmd.get('cs')}"
        if err is None:
            err = self.state_error(ac, cmd)
        self.monitor.on_command(ac, cmd, err)
        if err:
            self.log("SYS", f"[rejected: {err}] {raw}", kind="invalid", cs=cmd.get("cs"))
            return
        if cmd["cmd"] in ("taxi_to_runway", "taxi_to_parking"):
            self.preview_taxi(ac, cmd)
        who = W.POS.get(ac.freq, "ATC")
        self.log(who, render(cmd, ac), cs=ac.cs)
        for r in self.requests:
            if r["cs"] == ac.cs and r["answered"] is None and cmd["cmd"] in ANSWERS.get(r["kind"], ()):
                r["answered"] = self.t
        urgent = cmd["cmd"] in ("go_around", "cancel_takeoff", "hold_position")
        delay = max(1, ac.reaction // 2) if urgent else ac.reaction
        self.pending.append((self.t + delay, ac.cs, cmd))

    def preview_taxi(self, ac, cmd):
        """Work out the route (for the read-back of hold-short instructions)."""
        nodes = self.taxi_nodes_for(ac, cmd)
        ac._cross_names = []
        if nodes:
            pts, stops = self.add_stops(self.route_points(nodes), ignore=self.ignore_zones(ac))
            ac._cross_names = [s["rwy"] for s in stops]

    def ignore_zones(self, ac):
        return tuple(W.zone_runways(ac.x, ac.y))

    def taxi_nodes_for(self, ac, cmd):
        if cmd["cmd"] == "taxi_to_runway":
            goal = W.DEP_HOLD.get(cmd["runway"])
        else:
            st = ac.stand or self.free_stand(ac)
            ac.stand = st
            goal = W.STANDS[st]["node"]
        return self.plan_route(ac.gnode, goal, cmd.get("via"))

    def front(self, ac):
        q = self.queue.get(ac.runway) or []
        busy = any(o.phase in ("lining_up", "lined_up") and o.runway == ac.runway
                   for o in self.aircraft.values() if o is not ac and not o.m["done"])
        return bool(q) and q[0] == ac.cs and not busy

    def state_error(self, ac, cmd):
        n = cmd["cmd"]
        pos = positions(n)
        on = "radar" if ac.freq in W.RADAR else ac.freq
        if on not in pos:
            return f"{n} is not a {ac.freq} command (positions: {', '.join(pos)})"
        ph = ac.phase
        radar_air = ac.airborne and ac.freq in W.RADAR
        if n in ("contact_frequency", "freq_change_approved"):
            target = cmd.get("facility") or self.next_facility(ac)
            ok_moves = {
                ("ground", "tower"): ac.intent == "departure" and ph in ("taxiing", "holding_short"),
                ("tower", "ground"): ph in ("landing_roll", "exiting", "clear", "rto"),
                ("tower", "departure"): ac.airborne and ac.intent == "departure",
                ("tower", "approach"): ac.airborne and ac.intent == "arrival",
                ("approach", "tower"): ac.airborne and ac.intent == "arrival",
                ("departure", "center"): ac.airborne,
                ("approach", "center"): ac.airborne,
            }
            if not ok_moves.get((ac.freq, target), False):
                return f"cannot hand {ac.cs} from {ac.freq} to {target} in phase {ph}"
            return None
        rw = cmd.get("runway")
        ok = {
            "hold_position": ph in ("gate_req", "pushing", "pushed", "taxiing", "clear", "taxi_in", "lining_up"),
            "hold_short": ph in ("holding_short", "taxiing", "taxi_in", "clear", "exiting"),
            "cross_runway": ph in ("taxiing", "taxi_in", "clear", "exiting", "pushed"),
            "pushback_approved": ph in ("gate_req", "push_hold") or (ph == "pushing" and ac.hold),
            "taxi_to_runway": ph in ("pushed", "taxiing") and rw in W.DEP_RUNWAYS,
            "taxi_to_parking": ph in ("clear", "taxi_in"),
            "continue_taxi": ph in ("taxiing", "taxi_in", "pushing", "clear") and bool(ac.hold),
            "follow": ph in ("taxiing", "taxi_in", "clear", "pushed")
                      and self.follow_target(ac, cmd.get("traffic")) is not None,
            "line_up_and_wait": ph == "holding_short" and self.front(ac) and rw == ac.runway,
            "cleared_takeoff": ((ph == "holding_short" and self.front(ac)) or ph in ("lining_up", "lined_up")) and rw == ac.runway,
            "cancel_takeoff": ph in ("lining_up", "lined_up", "rolling"),
            "departure_sequence": ph in ("holding_short", "taxiing"),
            "exit_runway": ph in ("landing_roll",),
            "cleared_to_land": ac.airborne and ac.intent == "arrival" and rw in W.ARR_RUNWAYS
                               and (not cmd.get("follow") or self.traffic_to_follow(ac, cmd["follow"], rw) is not None),
            "sequence": ac.airborne and ac.intent == "arrival" and self.traffic_to_follow(ac, cmd.get("follow")) is not None,
            "cancel_landing_clearance": ac.airborne and ac.clearance is not None,
            "go_around": ac.airborne and ac.intent == "arrival" and (rw is None or rw in W.ARR_RUNWAYS),
            "approach_speed": ac.airborne, "resume_normal_speed": ac.airborne, "maintain_present_speed": ac.airborne,
            "slowest_practical_speed": ac.airborne, "maximum_forward_speed": ac.airborne,
            "make_360": ac.airborne and not ac.established,
            "radar_contact": radar_air, "expect_approach": radar_air and ac.intent == "arrival" and rw in P.APPROACH_IDS,
            "vector": radar_air, "continue_as_filed": radar_air,
            "resume_own_navigation": radar_air, "descend_via": radar_air, "expedite_altitude": radar_air,
            "cleared_to_airport": radar_air and ac.intent == "arrival" and ac.m["t_diverted"] is None,
            "maintain_speed": radar_air, "hold_present_position": radar_air,
            "hold_at_fix": radar_air and cmd.get("fix") in [lg.get("fix") for lg in ac.legs] and ac.holdp is None,
            "exit_hold": radar_air and (ac.holdp is not None or ac.hold_fix is not None),
            "direct_to": radar_air and cmd.get("fix") in [lg.get("fix") for lg in ac.legs + ac.exp_legs],
            "cleared_approach": radar_air and ac.intent == "arrival" and rw in P.APPROACH_IDS
                                and cmd.get("approach") in P.APPROACH_IDS.get(rw, {}),
        }[n]
        if not ok:
            return f"{n} not valid for {ac.cs} ({ac.freq}, {ph}{'/' + str(ac.leg()) if ac.airborne else ''})"
        if n in ("taxi_to_runway", "taxi_to_parking") and ph in ("pushed", "clear"):
            if self.taxi_nodes_for(ac, cmd) is None:
                return f"no taxi route from {ac.gnode} via {cmd.get('via')}"
        if n == "cross_runway":
            nxt = self.next_crossing(ac)
            if nxt is None:
                return f"{ac.cs} has no crossing of runway {rw} ahead"
        return None

    def next_crossing(self, ac):
        """The runway (in-use designator) of the next crossing on the route, or the one held short of."""
        if ac.hs_rwy and not ac.gpath[ac.gk:] if ac.gpath else ac.hs_rwy:
            return ac.hs_rwy
        for s in ac.gstops:
            if not s["done"]:
                return s["rwy"]
        return ac.hs_rwy

    def next_facility(self, ac):
        if ac.freq == "ground":
            return "tower"
        if ac.freq == "tower":
            if ac.airborne and ac.intent == "departure":
                return "departure"
            if ac.airborne:
                return "approach"
            return "ground"
        if ac.freq in ("departure",):
            return "center"
        if ac.freq == "approach":
            return "tower"
        return None

    # ------------------------------------------------------------------ loop
    def run(self):
        if hasattr(self.controller, "reset"):
            self.controller.reset(self.info())
        self.log("ATIS", W.ATIS.format(info=self.atis), kind="atis")
        while self.t < self.t_end:
            self.spawn()
            self.deliver()
            self._ground = self.ground_traffic()
            for ac in list(self.aircraft.values()):
                if not ac.m["done"]:
                    self.step_ac(ac)
            if self.t % 10 == 0:
                for ac in self.aircraft.values():
                    if ac.intent == "arrival" and ac.airborne and not ac.m["done"]:
                        self.fuel_check(ac)
            self.monitor.on_tick()
            try:
                cmds = self.controller.step(self.observe()) or []
            except Exception as e:  # noqa: BLE001 - a crashing controller loses the tick, not the run
                self.controller_errors += 1
                if self.controller_errors <= 3:
                    import traceback
                    self.log("SYS", f"controller error: {e!r} {traceback.format_exc()[-600:]}", kind="error")
                cmds = []
            for c in cmds:
                self.issue(c)
            if self.record and self.t % 2 == 0:
                self.frames.append([self.t, [self.frame_row(a) for a in self.aircraft.values() if not a.m["done"]]])
            if not self.spawns and all(a.m["done"] for a in self.aircraft.values()):
                break
            self.t += 1
        self.monitor.finish()
        return self

    def frame_row(self, a):
        hdg_a = int(round(W.true2mag(a.hdg_target))) if a.airborne and a.nav == "heading" and a.hdg_target is not None else -1
        return [a.cs, round(a.x, 4), round(a.y, 4), int(a.alt), int(a.gs), a.leg() or a.phase, a.intent[0],
                a.wake, (a.freq or "-")[0], int(a.hdg), hdg_a,
                int(a.alt_assigned) if a.airborne and a.alt_assigned is not None else -1,
                a.runway or "", a.type, (a.next_fix() or "") if a.airborne else (a.hs_rwy or ""),
                1 if a.established else 0]

    def info(self):
        from . import commands as Cm
        return {"field": W.FIELD, "icao": W.ICAO, "elev": W.ELEV, "magvar": W.MAGVAR, "config": W.CONFIG,
                "arr_runways": W.ARR_RUNWAYS, "dep_runways": W.DEP_RUNWAYS, "types": copy.deepcopy(W.TYPES),
                "values": {"altitudes": Cm.ALTITUDES, "headings": Cm.HEADINGS, "speeds": Cm.SPEEDS},
                "sid_top": W.SID_TOP, "approach_nm": W.APPROACH_NM}

    def observe(self):
        acs = []
        for a in self.aircraft.values():
            if a.m["done"]:
                continue
            rem = F.remaining(a) if a.airborne and a.intent == "arrival" else None
            ground = a.phase in GROUND_PHASES or a.phase in ("gate_req", "push_hold")
            acs.append({
                "cs": a.cs, "type": a.type, "cat": a.cat, "wake": a.wake, "intent": a.intent, "phase": a.phase,
                "leg": a.leg(), "freq": a.freq, "x": a.x, "y": a.y, "alt": a.alt, "ias": a.ias, "gs": a.gs, "vs": a.vs,
                "hdg": a.hdg, "hdg_mag": W.true2mag(a.hdg), "airborne": a.airborne, "runway": a.runway,
                "zone": list(a.zone), "stand": a.stand, "cargo": a.cargo,
                "queue_pos": (self.queue[a.runway].index(a.cs) + 1) if a.runway in self.queue and a.cs in self.queue[a.runway] else None,
                "gnode": a.gnode if ground else None,
                "groute": [p[2] for p in a.gpath[a.gk:] if p[2]] if ground and a.gpath else None,
                "gpts": [(round(p[0], 5), round(p[1], 5)) for p in a.gpath[a.gk:a.gk + 40]] if ground and a.gpath else None,
                "crossings": [dict(s) for s in a.gstops if not s["done"]] if ground else None,
                "hs_rwy": a.hs_rwy, "cross_clear": sorted(a.cross_clear), "gblocked": a.blocked_s > 0,
                "taxi_left_nm": self.path_len(a) if ground and a.gpath else None,
                "rem_nm": rem, "eta_s": self.eta(a, rem) if rem is not None else None,
                "nav": a.nav, "hdg_target_mag": W.true2mag(a.hdg_target) if a.hdg_target is not None else None,
                "alt_assigned": a.alt_assigned, "alt_until": a.alt_until, "speed": copy.copy(a.spd),
                "next_fix": a.next_fix() if a.airborne else None,
                "route": [lg.get("fix") for lg in a.legs if lg.get("fix")] if a.airborne else None,
                "star": a.star, "star_trans": a.star_trans, "sid": a.sid, "sid_trans": a.sid_trans,
                "approach_request": a.appr_req, "approach_expected": a.appr_exp, "approach_cleared": a.appr_cleared,
                "loc_armed": a.loc_armed, "established": a.established, "radar_contact": a.radar_contact,
                "missed": a.missed, "circuit": a.circuit, "holding": a.holdp is not None, "hold_fix": a.hold_fix,
                "hold_info": {"fix": a.holdp["fix"], "crs": a.holdp["crs"], "laps": a.holdp["laps"]} if a.holdp else None,
                "vectors_expected": a.vectors_expected or (bool(a.legs) and a.legs[0]["pt"] in ("FM", "VM")),
                "clearance": a.clearance, "takeoff_cleared": a.takeoff_clear, "sequence": a.sequence,
                "follow": a.leader, "hold": a.hold,
                "give_way": a.give_way["cs"] if a.give_way else None, "giving_way_now": a.gw_stop,
                "exit_called": a.exit_cmd is not None,
                "t_landed": a.m["t_landed"], "t_airborne": a.m["t_airborne"], "t_roll": a.m["t_roll"],
                "filed_alt": a.filed_alt, "fuel": a.m["fuel_state"],
                "mayday_by": a.m["mayday_limit"], "diverted": a.m["t_diverted"] is not None,
            })
        reqs = [dict(r) for r in self.requests if r["answered"] is None]
        return {"t": self.t, "atis": self.atis, "aircraft": acs, "requests": reqs}

    def eta(self, ac, rem):
        if rem is None:
            return None
        p = ac.perf
        # simple speed profile: 280 GS > 30 nm, 230 to 12 nm, 180 to 5 nm, then approach speed
        segs = [(max(0.0, rem - 30), 290), (max(0.0, min(rem, 30) - 12), 235), (max(0.0, min(rem, 12) - 5), 180),
                (min(rem, 5), p["vapp"] + 10)]
        return sum(d / v * 3600 for d, v in segs)

    # ------------------------------------------------------------------ traffic
    def spawn(self):
        while self.spawns and self.spawns[0]["t"] <= self.t:
            s = self.spawns.pop(0)
            ac = Aircraft(s, self.seed)
            self.aircraft[ac.cs] = ac
            ac.m["t_spawn"] = self.t
            if ac.intent == "arrival":
                self.spawn_arrival(ac, s)
            else:
                self.spawn_departure(ac, s)

    def spawn_arrival(self, ac, s):
        legs = P.star_legs(ac.star, ac.star_trans, ac.runway)
        alts, ends, total, pts = P.descent_profile(legs)
        # start where the STAR is first inside the approach airspace (50 nm)
        i0, prev = 0, None
        start = None
        for i, lg in enumerate(legs):
            g = P.leg_geometry(prev, lg, legs[i + 1] if i + 1 < len(legs) else None)
            if prev is not None and lg.get("pos") is not None and math.hypot(*lg["pos"]) <= s["dist"]:
                a, b = prev, lg["pos"]
                lo, hi = 0.0, 1.0
                for _ in range(40):
                    mid = (lo + hi) / 2
                    p = (a[0] + (b[0] - a[0]) * mid, a[1] + (b[1] - a[1]) * mid)
                    if math.hypot(*p) > s["dist"]:
                        lo = mid
                    else:
                        hi = mid
                start = (a[0] + (b[0] - a[0]) * lo, a[1] + (b[1] - a[1]) * lo)
                i0 = i
                frac = lo
                break
            if g:
                prev = g[-1]
        if start is None:
            start = legs[1]["pos"] if len(legs) > 1 else legs[0]["pos"]
            i0, frac = 1, 0.0
        alt_next = alts[i0] if alts[i0] is not None else 12000
        seg = W.dist(start, legs[i0]["pos"])
        alt = alt_next + seg * 300
        for j in range(i0, len(legs)):                    # inside every window ahead that it must meet
            if legs[j]["amin"] is not None and j == i0:
                alt = max(alt, legs[j]["amin"])
        if legs[i0]["amax"] is not None:
            alt = min(alt, legs[i0]["amax"] + seg * 300)
        alt = min(alt, 24000)
        alt = int(round(alt / 1000.0)) * 1000
        ac.x, ac.y = start
        ac.alt = float(alt)
        ac.set_legs(legs[i0:], leg_from=legs[i0 - 1]["pos"] if i0 > 0 else None)
        ac.ias = 280.0 if alt > 10500 else 250.0
        ac.ias = min(ac.ias, W.TYPES[ac.type]["cruise"])
        ac.gs = W.gs_from_ias(ac.ias, ac.alt)
        ac.hdg = W.bearing(start, legs[i0]["pos"])
        ac.phase, ac.freq = "airborne", "approach"
        ac.t_freq = self.t
        self.expect(ac, ac.appr_req, ac.runway)
        ac.appr_exp_filed = ac.appr_exp
        rem = F.remaining(ac)
        ac.m["nominal"] = self.eta(ac, rem) + 30
        ac.m["rem0"] = rem
        ac.msg_due = (self.t + ac.rng.randint(3, 10), "check_in", {"alt": int(round(ac.alt, -2))})

    def fuel_check(self, ac):
        """An arrival carries enough fuel for its planned time to land (nominal) plus fuel_extra_s.
        Spare = fuel left minus the time still needed to land; the final reserve is not counted.
        Minimum fuel (FAA InFO 08004, ICAO Annex 6) when under 5 min spare: an advisory, no priority.
        Mayday fuel when spare runs out: it now expects to land on its final reserve."""
        if ac.m["nominal"] is None or ac.m["t_diverted"] is not None:
            return
        if ac.m["fuel_state"] == "emergency":
            if self.t > ac.m["mayday_limit"] and "mayday_late" not in ac.flags:
                ac.flags.add("mayday_late")
                self.monitor.add("hard", "2-1-4", ac, "Mayday fuel neither worked in nor diverted: still airborne "
                                 f"{int((self.t - ac.m['t_mayday']) / 60)} min after the emergency")
            return
        left = ac.m["nominal"] + ac.fuel_extra_s - (self.t - ac.m["t_spawn"])
        spare = left - (self.eta(ac, F.remaining(ac)) or 0)
        if spare < 0 and ac.m["fuel_state"] != "emergency":
            ac.m["fuel_state"] = "emergency"
            ac.m["t_mayday"] = self.t
            ac.m["mayday_limit"] = self.t + (self.eta(ac, F.remaining(ac)) or 0) + MAYDAY_MARGIN_S
            ac.say(self, "fuel_emergency", {"min": max(0, int(left / 60))})
            self.monitor.add("hard", "fuel", ac, "Mayday fuel: delayed until it would land below final reserve")
        elif spare < 300 and ac.m["fuel_state"] is None:
            ac.m["fuel_state"] = "minimum"
            ac.say(self, "minimum_fuel")
            self.monitor.add("rule", "2-1-8", ac, "minimum fuel: delayed to under 5 min of spare fuel")

    def spawn_departure(self, ac, s):
        st = W.STANDS[ac.stand]
        line = self.stand_line(ac.stand)
        ac.x, ac.y = st["x"], st["y"]
        ac.gnode = line[0]
        nxt = W.NODES[line[1]] if len(line) > 1 else (st["x"] + 0.01, st["y"])
        ac.hdg = W.bearing(nxt, (ac.x, ac.y))           # nose in
        ac.freq = "ground"
        ac.phase = "gate_req"
        ac.m["t_push_req"] = self.t
        ac.say(self, "request_pushback")

    def expect(self, ac, kind, runway):
        pid = P.APPROACH_IDS.get(runway, {}).get(kind) or P.APPROACH_IDS.get(runway, {}).get(P.default_approach(runway))
        ac.appr_exp, ac.runway = pid, runway
        end_fix, vec = P.star_end(P.star_legs(ac.star, ac.star_trans, runway))
        trans = P.transition_for(pid, end_fix) if end_fix else None
        ac.exp_legs = P.approach_legs(pid, trans) if trans else [lg for lg in P.approach_legs(pid) if lg["tag"] != "app"]
        if not trans and ac.exp_legs:
            ac.exp_legs[0] = dict(ac.exp_legs[0], pt="DF") if ac.exp_legs[0]["pt"] == "IF" else ac.exp_legs[0]
        ac._cum = None

    def deliver(self):
        if not self.pending:
            return
        due = [p for p in self.pending if p[0] <= self.t]
        if not due:
            return
        self.pending = [p for p in self.pending if p[0] > self.t]
        for _, cs, cmd in due:
            ac = self.aircraft.get(cs)
            if ac and not ac.m["done"]:
                self.apply(ac, cmd)

    # ------------------------------------------------------------------ pilots
    def switch(self, ac, facility):
        old = ac.freq
        if facility == old:
            return
        ac.freq = facility
        ac.t_freq = self.t
        self.resolve(ac, "request_freq_change")
        d = self.t + ac.rng.randint(3, 9)
        if facility == "tower" and old == "ground":
            if ac.phase == "holding_short":
                ac.msg_due = (d, "ready_for_departure", {})
            else:
                ac.contact_tower_when_ready = True
        elif facility == "ground":
            ex = getattr(ac, "exit_used", None)
            ac.msg_due = (d, "ground_checkin", {"rwy": ac.runway, "at": (ex or {}).get("name") or None, "hs": ac.hs_rwy})
        elif facility == "departure":
            ac.msg_due = (d, "check_in", {"alt": int(round(ac.alt, -2)), "target": W.SID_TOP})
        elif facility == "approach":
            ac.msg_due = (d, "check_in", {"alt": int(round(ac.alt, -2)), "missed": ac.missed, "runway": ac.runway})
        elif facility == "tower" and old == "approach":
            self.monitor.on_event("handoff_tower", ac)
            ac.msg_due = (d, "tower_checkin", {})
        elif facility == "center":
            ac.m["handed_center"] = True

    def apply(self, ac, cmd):
        n = cmd["cmd"]
        ph = ac.phase
        if n in ("contact_frequency", "freq_change_approved"):
            target = cmd.get("facility") or self.next_facility(ac)
            if ph in ("landing_roll", "exiting") and target == "ground":
                ac.switch_to_ground_when_clear = True
            elif ph == "clear" and target == "ground" and ac.hs_rwy:
                ac.switch_to_ground_when_clear = True
                self.switch(ac, target)
            else:
                self.switch(ac, target)
        elif n == "pushback_approved" and ph in ("gate_req", "push_hold", "pushing"):
            if ph != "pushing":
                ac.phase = "pushing"
                line = self.stand_line(ac.stand)
                ac.push = {"stage": "back", "pts": [W.NODES[k] for k in line[1:]], "i": 0, "end": line[-1]}
                # nose toward the shortest way to the expected departure runway
                rw = cmd.get("expect_runway") or ac.runway or W.DEP_RUNWAYS[0]
                way = self.plan_route(line[-1], W.DEP_HOLD[rw]) or [line[-1]]
                if len(way) > 1:
                    pts = self.route_points(way[:2])
                    ac.push["nose"] = W.bearing(W.NODES[line[-1]], pts[1][:2])
                else:
                    ac.push["nose"] = ac.hdg
                ac.m["t_push_ok"] = self.t
                self.free_stand_slot(ac)
            ac.hold = False
        elif n == "hold_position":
            if ph == "gate_req":
                ac.phase = "push_hold"
            elif ph != "lining_up":
                ac.hold = True
        elif n == "continue_taxi":
            ac.hold = False                # a follow stays in force until the traffic has passed
        elif n == "follow":
            o = self.follow_target(ac, cmd["traffic"])
            if o is not None:
                ac.give_way = {"cs": o.cs, "t0": self.t, "clear_s": 0, "still_s": 0}
        elif n == "hold_short":
            ac.cross_clear.discard(cmd["runway"])
            for s in ac.gstops:
                if s["rwy"] == cmd["runway"] and not s["done"] and not s["entered"]:
                    s["cleared"] = False
        elif n == "cross_runway":
            self.cross(ac, cmd["runway"])
        elif n == "taxi_to_runway" and ph in ("pushed", "taxiing"):
            if ph == "pushed" or cmd["runway"] != ac.runway:
                ac.runway = cmd["runway"]
                nodes = self.plan_route(ac.gnode, W.DEP_HOLD[ac.runway], cmd["via"])
                if nodes:
                    ac.phase = "taxiing"
                    self.set_taxi(ac, nodes, ignore=self.ignore_zones(ac))
            ac.contact_tower_when_ready = cmd["contact_tower"]
            ac.hold = False
        elif n == "taxi_to_parking" and ph in ("clear", "taxi_in"):
            if ph == "clear":
                ac.stand = ac.stand or self.free_stand(ac)
                nodes = self.plan_route(ac.gnode, W.STANDS[ac.stand]["node"], cmd["via"])
                if nodes:
                    ac.phase = "taxi_in"
                    self.set_taxi(ac, nodes, ignore=self.ignore_zones(ac))
                    ac.hs_rwy = None
            ac.hold = False
        elif n == "line_up_and_wait" and ph == "holding_short":
            self.start_lineup(ac, takeoff=False)
        elif n == "cleared_takeoff":
            ac.immediate = cmd["immediate"]
            if ph == "holding_short":
                self.start_lineup(ac, takeoff=True)
            elif ph in ("lining_up", "lined_up"):
                ac.takeoff_clear = True
                ac.roll_timer = self.roll_delay(ac)
        elif n == "cancel_takeoff":
            if ph in ("lining_up", "lined_up"):
                ac.takeoff_clear, ac.roll_timer = False, None
            elif ph == "rolling" and ac.gs < 100:
                self.begin_rto(ac)
        elif n == "exit_runway" and ph == "landing_roll":
            ac.exit_cmd = cmd
            ac.expedite_exit = bool(cmd["expedite"])
            ac.switch_to_ground_when_clear = True
            if cmd["then"] == "cross":
                ac.cross_clear.add(W.CSPR.get(ac.runway))
        elif ac.airborne:
            self.apply_air(ac, cmd)

    def free_stand_slot(self, ac):
        self.reserved.discard(ac.stand)

    def cross(self, ac, rwy):
        ac.cross_clear.add(rwy)
        for s in ac.gstops:
            if not s["done"] and s["rwy"] == rwy:
                s["cleared"] = True
                break
        if ac.phase == "clear" and ac.hs_rwy == rwy and (not ac.gpath or ac.gk >= len(ac.gpath)):
            path = self.cross_path(ac, rwy)
            if path:
                ac.gpath, ac.gk = path, 1
                ac.gstops = [{"k": 0, "rwy": rwy, "cleared": True, "done": False, "entered": False}]
        if ac.hold:
            ac.hold = False

    def cross_path(self, ac, rwy):
        """Straight across runway `rwy` along the taxiway from the hold-short node to the first
        node beyond the far hold line."""
        R = W.RUNWAYS[rwy]
        n = ac.gnode
        h0 = ac.hdg
        pts = [(ac.x, ac.y, None)]
        k_in = None
        crossed = False
        for _ in range(10):
            cands = [(m, k) for m, k in W.ADJ[n] if k != k_in and W.EDGES[k][3] not in W.RUNWAY_EDGE_KINDS
                     and W.EDGES[k][3] != "parking_position"]
            if not cands:
                return None
            m, k = min(cands, key=lambda c: abs(W.ang_diff(h0, W.bearing(W.NODES[n], W.edge_geom(c[1], n)[1]))))
            g = W.edge_geom(k, n)
            for p in g[1:-1]:
                pts.append((p[0], p[1], None))
            pts.append((*W.NODES[m], m))
            h0 = W.bearing(g[-2], g[-1])
            n, k_in = m, k
            v = R.uv(*W.NODES[m])[1] * FT
            vs = R.uv(ac.x, ac.y)[1] * FT
            if v * vs < 0:
                crossed = True
            if crossed and abs(v) >= W.HOLD_FT - 20 and not W.NODE_ZONE[m]:
                return pts
        return None

    def apply_air(self, ac, cmd):
        n = cmd["cmd"]
        p = ac.perf
        if n == "cleared_to_land":
            ac.clearance = cmd["runway"]
            if cmd["number"] and cmd["number"] > 1:
                ld = self.traffic_to_follow(ac, cmd["follow"], cmd["runway"])
                ac.sequence, ac.leader = cmd["number"], ld.cs if ld else None
            else:
                ac.sequence, ac.leader = 1, None
        elif n == "sequence":
            ld = self.traffic_to_follow(ac, cmd["follow"])
            if ld is not None:
                ac.sequence, ac.leader = cmd["number"], ld.cs
        elif n == "cancel_landing_clearance":
            ac.clearance = None
        elif n == "go_around":
            self.go_around(ac, cmd["then"], pilot=False, runway=cmd.get("runway"))
        elif n == "approach_speed":
            ac.spd = {"kind": "kts", "kts": cmd["kts"], "until": cmd["until_nm"]}
            if cmd["kts"] < p["vapp"]:
                ac.say(self, "unable", {"why": f"minimum speed {p['vapp']} kts"})
        elif n == "resume_normal_speed":
            ac.spd = None
        elif n == "maintain_present_speed":
            ac.spd = {"kind": "kts", "kts": round(ac.ias), "until": None}
        elif n == "slowest_practical_speed":
            ac.spd = {"kind": "slowest"}
        elif n == "maximum_forward_speed":
            ac.spd = {"kind": "best"}
        elif n == "maintain_speed":
            ac.spd = {"kind": "kts", "kts": cmd["kts"], "until": None, "mode": cmd["mode"]}
            if cmd["kts"] < p["vapp"]:
                ac.say(self, "unable", {"why": f"minimum speed {p['vapp']} kts"})
        elif n == "make_360":
            ac.turn360 = [cmd["direction"], 360.0]
        elif n == "radar_contact":
            ac.radar_contact = True
        elif n == "expect_approach":
            ac.radar_contact = True
            if not ac.appr_cleared:
                old_rwy = ac.runway
                self.expect(ac, cmd["approach"], cmd["runway"])
                if cmd["runway"] != old_rwy and ac.nav == "lnav" and ac.holdp is None:
                    self.reload_star(ac)
            if cmd["then"] == "expect_vectors":
                ac.vectors_expected = True
        elif n == "vector":
            ac.radar_contact = True
            if cmd["heading"] is not None:
                if ac.holdp is not None:
                    ac.holdp = None
                ac.nav, ac.hdg_target, ac.turn360 = "heading", W.mag2true(cmd["heading"]), None
                ac.turn_dir = None                         # the shorter way round
                ac.circuit = None
                ac.hold_alt = None
                ac.vectors_expected = True
                if ac.established:
                    ac.established = False
            if cmd["altitude"] is not None:
                ac.alt_assigned = cmd["altitude"]
                ac.expedite_alt = False
                if ac.hold_alt is not None and (ac.holdp is not None or ac.hold_fix is not None):
                    ac.hold_alt = cmd["altitude"]          # a new altitude in the hold (stack step-down)
                    if ac.hold_fix is not None:
                        ac.hold_fix["alt"] = cmd["altitude"]
        elif n == "cleared_to_airport":
            self.divert(ac, cmd["altitude"])
        elif n in ("continue_as_filed", "resume_own_navigation"):
            ac.radar_contact = True
            self.continue_as_filed(ac)
        elif n == "direct_to":
            self.direct_to(ac, cmd["fix"])
        elif n == "descend_via":
            ac.alt_assigned = None
            ac.via = True
        elif n == "expedite_altitude":
            ac.expedite_alt = True
        elif n == "hold_present_position":
            ac.alt_assigned = cmd["altitude"]
            ac.hold_alt = cmd["altitude"]
            ac.turn360 = None
            if ac.nav == "lnav" and ac.legs:
                ac.saved_legs = ac.legs
            ac.nav = "lnav"
            F.start_hold(ac, (ac.x, ac.y), ac.hdg, "R" if cmd["turns"] == "right" else "L", kind="present")
        elif n == "hold_at_fix":
            ac.hold_fix = {"fix": cmd["fix"], "turns": "R" if cmd["turns"] == "right" else "L", "alt": cmd["altitude"]}
            ac.alt_assigned = cmd["altitude"]
            ac.exit_hold = False
        elif n == "exit_hold":
            if ac.holdp is not None:
                ac.exit_hold = True
            ac.hold_fix = None
        elif n == "cleared_approach":
            self.clear_approach(ac, cmd)

    def reload_star(self, ac):
        """A new landing runway: the STAR's runway transition for it (if the STAR has one)."""
        if not ac.legs or ac.legs[0]["tag"] != "star":
            return
        legs = P.star_legs(ac.star, ac.star_trans, ac.runway)
        fixes = [lg.get("fix") for lg in legs]
        cur = ac.legs[0].get("fix")
        if cur in fixes:
            i = fixes.index(cur)
            ac.set_legs(legs[i:], ac.leg_from)

    def divert(self, ac, altitude):
        """Cleared to Oakland via direct (10-2-5): the pilot turns for Oakland and leaves the frequency."""
        ac.m["t_diverted"] = self.t
        ac.holdp, ac.hold_fix, ac.hold_alt, ac.turn360 = None, None, None, None
        ac.appr_cleared = None
        ac.nav, ac.hdg_target, ac.turn_dir = "heading", W.bearing((ac.x, ac.y), OAKLAND), None
        ac.alt_assigned = altitude
        self.monitor.add("rule", "10-2-5", ac, "diverted to Oakland" +
                         (" with Mayday fuel" if ac.m["fuel_state"] == "emergency" else ""))

    def continue_as_filed(self, ac):
        ac.turn360, ac.hold_alt = None, None
        ac.vectors_expected = False
        if ac.holdp is not None and ac.holdp.get("kind") == "present":
            ac.holdp = None
            if ac.saved_legs:
                ac.legs, ac.saved_legs = ac.saved_legs, None
        if ac.intent == "departure":
            legs = [lg for lg in ac.legs if lg["pt"] not in ("FM", "VM", "VA", "CA")]
            if not legs:
                legs = P.sid_legs(ac.sid, ac.runway, ac.sid_trans)
                legs = [lg for lg in legs if lg.get("pos") is not None and lg["pt"] not in ("FM", "VM")][-1:]
            if legs:
                legs[0] = dict(legs[0], pt="DF")
            ac.nav = "lnav"
            ac.set_legs(legs, None)
            return
        legs = [lg for lg in ac.legs if lg["pt"] not in ("FM", "VM")]
        if not legs:
            legs = list(ac.appr_cleared and P.approach_legs(ac.appr_cleared) or ac.exp_legs)
            legs = [lg for lg in legs if lg["tag"] in ("app", "final")]
        if legs:
            # proceed direct to the next fix on the flight plan
            first = next((i for i, lg in enumerate(legs) if lg.get("pos") is not None), 0)
            legs = legs[first:]
            legs[0] = dict(legs[0], pt="DF")
            ac.nav = "lnav"
            ac.set_legs(legs, None)

    def direct_to(self, ac, fix):
        legs = ac.legs if any(lg.get("fix") == fix for lg in ac.legs) else ac.legs + ac.exp_legs
        i = next(i for i, lg in enumerate(legs) if lg.get("fix") == fix)
        new = [dict(legs[i], pt="DF")] + legs[i + 1:]
        if any(lg.get("fix") == fix for lg in ac.exp_legs) and not any(lg.get("fix") == fix for lg in ac.legs):
            ac.appr_direct = True
        ac.nav = "lnav"
        ac.turn360 = None
        ac.holdp = None
        ac.vectors_expected = False
        ac.set_legs(new, None)

    def clear_approach(self, ac, cmd):
        rwy = cmd["runway"]
        pid = P.APPROACH_IDS[rwy][cmd["approach"]]
        ac.radar_contact = True
        if cmd["heading"] is None and pid[0] == "H" and ac.nav == "heading":
            ac.say(self, "unable", {"why": "RNP approach must be joined at the IF"})
            return
        ac.appr_cleared = pid
        ac.appr_exp = pid
        ac.circuit = None
        ac.runway = rwy
        ac.final_rwy = W.RUNWAYS[rwy]
        ac.glide_angle = P.glide_angle(pid)
        ac.alt_until = cmd["altitude"]
        ac.alt_assigned = None
        ac.via = True
        ac.missed = False
        ac.spd = None if ac.spd and ac.spd.get("kind") != "kts" else ac.spd       # 5-7-2
        ac.hold_alt = None
        ac.hold_fix = None
        ac.loc_armed = True
        ac.established = False
        ac._cum = None
        app = P.approach_legs(pid)
        if cmd["heading"] is not None:
            ac.nav, ac.hdg_target, ac.turn360 = "heading", W.mag2true(cmd["heading"]), None
            ac.holdp = None
            ac.vectors_expected = True
            return
        if ac.holdp is not None:
            fixname = next((n for n, p in W.K.FIXES.items() if W.dist(p, ac.holdp["fix"]) < 1e-4), None)
            trans = P.transition_for(pid, fixname) if fixname else None
            if trans:
                ac.legs = P.approach_legs(pid, trans)[1:]
                ac._cum = None
                ac.legs_ver += 1
                ac.exit_hold = True
                return
            on_app = [i for i, lg in enumerate(app) if lg.get("fix") == fixname]
            if on_app:
                ac.legs = app[on_app[0] + 1:]
                ac._cum = None
                ac.exit_hold = True
                return
            ac.exit_hold = True
            ac.legs = [dict(lg) for lg in app if lg["tag"] in ("final", "missed")]
            ac.legs[0] = dict(ac.legs[0], pt="DF")
            ac._cum = None
            return
        if ac.nav == "lnav" and ac.legs and ac.legs[0]["pt"] not in ("FM", "VM"):
            fixes = [lg.get("fix") for lg in ac.legs]
            route = [lg for lg in ac.legs if lg["pt"] not in ("FM", "VM")]
            end_fix = route[-1].get("fix") if route else None
            trans = P.transition_for(pid, end_fix) if end_fix else None
            if trans:
                ac.legs = P.join(route, P.approach_legs(pid, trans))
                ac._cum = None
                ac.legs_ver += 1
                return
            app_fixes = [lg.get("fix") for lg in app]
            common = [f for f in fixes if f in app_fixes]
            if common:
                j = app_fixes.index(common[0])
                i = fixes.index(common[0])
                ac.legs = ac.legs[:i] + app[j:]
                ac._cum = None
                ac.legs_ver += 1
                return
        # on vectors (or on a vectors leg): keep the heading with the final course armed;
        # GPS/RNAV: proceed to the final approach course fix and fly the final segment (4-8-1)
        if ac.nav == "lnav" and ac.legs and ac.legs[0]["pt"] in ("FM", "VM"):
            ac.nav, ac.hdg_target = "heading", ac.hdg
        if pid[0] in ("R",) and ac.nav == "heading":
            legs = [dict(lg) for lg in app if lg["tag"] in ("final", "missed")]
            legs[0] = dict(legs[0], pt="DF")
            ac.nav = "lnav"
            ac.set_legs(legs, None)
            ac.loc_armed = False

    def roll_delay(self, ac):
        if ac.immediate:
            return ac.rng.randint(1, 3)
        return ac.rng.randint(8, 20) if ac.sloppy else ac.rng.randint(2, 7)

    def start_lineup(self, ac, takeoff):
        self.resolve(ac, "ready_for_departure")
        q = self.queue.get(ac.runway, [])
        if ac.cs in q:
            q.remove(ac.cs)
        ac.phase = "lining_up"
        L = W.LINEUP[ac.runway]
        rwy = W.RUNWAYS[ac.runway]
        pts = [(ac.x, ac.y, ac.gnode)] + [(p[0], p[1], None) for p in L["pts"][1:]]
        pts.append((*rwy.xy(L["u"] + 0.03), None))
        ac.gpath, ac.gk, ac.gstops = pts, 1, []
        ac.takeoff_clear = takeoff
        ac.roll_timer = self.roll_delay(ac) if takeoff else None
        ac.lineup_slow = 0.75 if ac.sloppy else 1.0

    def begin_rto(self, ac):
        ac.phase = "rto"
        ac.rto_done = True
        ac.m["rto"] = True
        ac.say(self, "rejecting_takeoff")
        self.monitor.on_event("rto", ac)

    # ------------------------------------------------------------------ approach / landing
    def go_around(self, ac, then, pilot, runway=None):
        ac.m["go_arounds"] += 1
        if pilot:
            ac.say(self, "going_around")
        self.monitor.on_event("go_around", ac, pilot=pilot)
        pid = ac.appr_cleared or ac.appr_exp
        missed = [dict(lg) for lg in P.approach_legs(pid) if lg["tag"] == "missed"] if pid else []
        m_alt = next((lg["amin"] for lg in missed if lg.get("amin")), 3000)
        ac.turn360 = None
        ac.holdp = None
        ac.clearance, ac.leader, ac.sequence = None, None, None
        ac.spd = None
        ac.flags.discard("final_checked")
        ac.climbout = ac.alt < F.CLIMBOUT_FT
        ac.gs_cap = False
        ac.appr_cleared, ac.loc_armed, ac.established, ac.alt_until = None, False, False, None
        ac.circuit = None
        if then in ("left_traffic", "right_traffic"):
            self.fly_circuit(ac, then.split("_")[0], runway or ac.runway)
            return
        ac.missed = True
        ac.t_missed = self.t
        rwy = W.RUNWAYS[ac.runway]
        if then == "runway_heading" or not missed:
            ac.nav, ac.hdg_target = "heading", rwy.hdg
            ac.alt_assigned = m_alt
            ac.vectors_expected = True
            ac.set_legs([], None)
        else:
            ac.nav = "lnav"
            ac.alt_assigned = None
            ac.set_legs(missed, (ac.x, ac.y))
        ac.missed_alt = m_alt
        self.expect(ac, ac.appr_req if ac.appr_req in P.APPROACH_IDS.get(ac.runway, {}) else P.default_approach(ac.runway),
                    ac.runway)

    def fly_circuit(self, ac, side, runway):
        """Go around, make left/right traffic: a visual circuit at 1,500 ft (AIM 4-3-3, jets),
        upwind -> crosswind -> downwind 2.5 nm abeam -> base -> the instrument final course at 5 nm.
        Radar (approach/departure) is always open here, so Tower still hands the aircraft to Approach
        (3-8-1); it stays in the pattern only until then, and needs a new landing clearance to land (3-10-5)."""
        ac.missed = True
        ac.t_missed = self.t
        ac.circuit = side
        ac.runway = runway
        rwy = W.RUNWAYS[runway]
        pid = P.APPROACH_IDS[runway][P.default_approach(runway)]
        s = 1 if side == "right" else -1
        pa = PATTERN_ALT
        up = max(rwy.len_nm + 1.0, rwy.uv(ac.x, ac.y)[0] + 2.0)
        u_base = rwy.u_thr - 5.0
        leg = lambda tag, u, v, alt: {"pt": "TF", "fix": None, "pos": rwy.xy(u, v), "crs": None,  # noqa: E731
                                      "amin": alt, "amax": alt, "spd": None, "tag": tag, "flyover": False}
        legs = [leg("upwind", up, 0.0, pa), leg("crosswind", up, s * 2.5, pa),
                leg("downwind", u_base, s * 2.5, pa), leg("base", u_base, 0.0, pa)]
        thr = rwy.thr
        final = [dict(lg) for lg in P.approach_legs(pid) if lg["tag"] in ("final", "missed")
                 and (lg["tag"] == "missed" or lg.get("pos") is None or W.dist(lg["pos"], thr) < 5.0)]
        final[0] = dict(final[0], pt="CF", crs=rwy.loc_course)
        ac.nav = "lnav"
        ac.alt_assigned = None
        ac.vectors_expected = False
        ac.set_legs(legs + final, (ac.x, ac.y))
        # flown visually, joined at 5 nm: the glidepath from there on
        ac.appr_cleared = ac.appr_exp = pid
        ac.final_rwy = rwy
        ac.glide_angle = P.glide_angle(pid)
        ac.via = True

    def final_checks(self, ac, rem):
        if "final_checked" in ac.flags or rem is None or not ac.legs or ac.legs[0]["tag"] != "final":
            return
        then = "missed_approach"
        rwy = W.RUNWAYS[ac.runway]
        for o in self.aircraft.values():
            if (o is not ac and o.airborne and not o.m["done"] and o.runway == ac.runway and o.intent == "arrival"
                    and o.legs and o.legs[0]["tag"] == "final" and abs(o.x - ac.x) < 0.6 and abs(o.y - ac.y) < 0.6
                    and math.hypot(o.x - ac.x, o.y - ac.y) < 0.6 and abs(o.alt - ac.alt) < 400
                    and (F.remaining(o) or 99) < rem and o.gs < ac.gs - 10):
                ac.flags.add("final_checked")
                self.monitor.on_event("pilot_ga_traffic", ac)
                self.go_around(ac, then, pilot=True)
                return
        if 0.35 <= rem < 2.0 and ac.appr_cleared and ac.final_rwy is not None:
            high = ac.alt - ac.final_rwy.glide_alt(rem, ac.glide_angle)
            if high > 400 or (not ac.established and rem < 1.5):
                ac.flags.add("final_checked")
                self.monitor.on_event("unstable", ac, high=high)
                self.go_around(ac, then, pilot=True)
                return
        if rem < 0.35:
            ac.flags.add("final_checked")
            if ac.freq != "tower" or ac.clearance != ac.runway:
                self.monitor.on_event("no_clearance", ac)
                self.go_around(ac, then, pilot=True)
                return
            if not ac.established:
                self.monitor.on_event("not_established", ac)
                self.go_around(ac, then, pilot=True)
                return
            blocking = self.runway_blocked(ac, rwy)
            if blocking is not None and ac.rng.random() < (0.3 if ac.sloppy else 0.75):
                self.monitor.on_event("pilot_ga_occupied", ac, other=blocking)
                self.go_around(ac, then, pilot=True)

    def runway_blocked(self, ac, rwy):
        """Something the pilot on short final can see on the runway or about to cross it."""
        for o in self.aircraft.values():
            if o is ac or o.m["done"] or o.airborne:
                continue
            if rwy.ident in o.zone and rwy.u(( o.x, o.y)) > rwy.u_thr - 0.05:
                return o
            if o.phase == "rolling" and o.runway != rwy.ident and (rwy.ident, o.runway) in W.INTERSECTIONS:
                u_int = W.INTERSECTIONS[(o.runway, rwy.ident)]
                if W.RUNWAYS[o.runway].u((o.x, o.y)) < u_int + W.BEYOND_NM:
                    return o
        return None

    def threshold(self, ac):
        ac.m["t_landed"] = self.t
        self.resolve(ac, "fuel_emergency")
        if ac.stand is None:                 # the gate is known (company) by the time the aircraft lands
            ac.stand = self.free_stand(ac)
        self.monitor.on_event("threshold", ac)
        ac.phase = "landing_roll"
        ac.circuit = None
        ac.flare = True
        ac.touchdown_u = None
        ac.rate = 0.0
        ac.clearance, ac.leader, ac.sequence, ac.spd = None, None, None, None
        ac.target_exit = None
        ac.legs = []

    # ------------------------------------------------------------------ per tick
    def step_ac(self, ac):
        if ac.msg_due and self.t >= ac.msg_due[0]:
            _, kind, data = ac.msg_due
            ac.msg_due = None
            ac.say(self, kind, data)
        if ac.hold_until and self.t >= ac.hold_until:
            ac.hold, ac.hold_until = False, None
        if ac.give_way:
            self.update_give_way(ac)
        ph, p = ac.phase, ac.perf
        if ph != "airborne":
            ac.zone = tuple(W.zone_runways(ac.x, ac.y)) if ph not in ("gate_req", "push_hold") else ()
        if ph == "pushing":
            if self.push_move(ac):
                ac.phase = "pushed"
                ac.msg_due = (self.t + ac.rng.randint(4, 15), "request_taxi", {})
        elif ph in ("taxiing", "taxi_in", "clear") and ac.gpath and ac.gk < len(ac.gpath):
            self.step_taxi(ac)
        elif ph == "taxiing":
            pass
        elif ph == "lining_up":
            if self.taxi_move(ac, 10.0 * ac.lineup_slow, corner=6.0, check_traffic=False):
                rwy = W.RUNWAYS[ac.runway]
                u = rwy.u((ac.x, ac.y))
                ac.x, ac.y = rwy.xy(u)
                ac.hdg = rwy.hdg
                ac.phase = "lined_up"
                ac.gs = ac.gspeed = 0.0
        elif ph == "lined_up":
            if ac.takeoff_clear and ac.roll_timer is not None:
                ac.roll_timer -= 1
                if ac.roll_timer <= 0:
                    self.start_roll(ac)
        elif ph == "rolling":
            rwy = W.RUNWAYS[ac.runway]
            v0 = ac.gs
            ac.gs += p["accel"] * (0.92 if ac.sloppy else 1.0)
            ac.ias = ac.gs
            u = rwy.u((ac.x, ac.y)) + (v0 + ac.gs) / 2 / 3600
            ac.x, ac.y = rwy.xy(u)
            ac.hdg = rwy.hdg
            if ac.will_rto and not ac.rto_done and ac.gs >= 60 and ac.rot is None:
                self.begin_rto(ac)
            elif ac.gs >= p["vr"]:
                ac.rot = 3 if ac.rot is None else ac.rot
                ac.rot -= 1
                if ac.rot <= 0:
                    ac.rot = None
                    self.liftoff(ac)
        elif ph == "rto":
            self.rollout_to_exit(ac, decel=5.0)
        elif ph == "landing_roll":
            if ac.flare:
                self.flare_step(ac)
                return
            self.rollout_to_exit(ac, decel=p["decel"])
        elif ph == "exiting":
            done = self.taxi_move(ac, 20.0 if ac.exit_cmd else 14.0, corner=self.turnoff_kt(ac, None), check_traffic=False)
            if not getattr(ac, "vacated", False) and ac.runway not in W.zone_runways(ac.x, ac.y):
                ac.vacated = True
                self.monitor.on_event("vacated", ac)
            if done:
                ac.phase = "clear"
                ac.gs = ac.gspeed = 0
                ac.clear_t = self.t
                ex = getattr(ac, "exit_used", None) or {}
                ac.hs_rwy = ex.get("hold_short")
                ac.gnode = ex.get("end", ac.gnode)
                ac.gpath, ac.gk, ac.gstops = [], 0, []
                if not getattr(ac, "vacated", False):
                    ac.vacated = True
                    self.monitor.on_event("vacated", ac)
                if ac.intent == "departure":
                    ac.m["done"] = True
                    return
                if ac.hs_rwy:
                    ac.say(self, "holding_short", {"rwy": ac.hs_rwy})
                    if ac.hs_rwy in ac.cross_clear:
                        self.cross(ac, ac.hs_rwy)
                elif ac.switch_to_ground_when_clear:
                    self.switch(ac, "ground")
        elif ph == "clear":
            if ac.freq == "tower" and not ac.hs_rwy and self.t - getattr(ac, "clear_t", self.t) >= 20:
                self.switch(ac, "ground")
            if ac.hs_rwy:
                ac.m["cross_wait_s"] += 1
        elif ph == "airborne":
            self.step_air(ac)

    def step_taxi(self, ac):
        ph = ac.phase
        speed = TAXI_SPEED * (0.85 if ac.sloppy else 1.0)
        prev_zone = set(ac.zone)
        done = self.taxi_move(ac, speed)
        zone = set(W.zone_runways(ac.x, ac.y))
        for s in ac.gstops:
            if s["done"] and not s.get("out") and s.get("entered"):
                if s["rwy"] not in zone:
                    s["out"] = True
                    ac.cross_clear.discard(s["rwy"])
                    self.monitor.on_event("crossed", ac, rwy=s["rwy"])
            elif not s["done"] and s["cleared"] and s["rwy"] in zone and not s["entered"]:
                s["entered"] = True
                s["done"] = True
                self.monitor.on_event("cross_enter", ac, rwy=s["rwy"])
        for s in ac.gstops:
            if s["done"] and not s.get("entered") and s["rwy"] in zone:
                s["entered"] = True
                self.monitor.on_event("cross_enter", ac, rwy=s["rwy"])
        # holding short at a stop
        end = self.stop_k(ac)
        st = next((s for s in ac.gstops if s["k"] == end and not s["done"] and not s["cleared"]), None)
        if st is not None and end < len(ac.gpath) - 1 and ac.gk >= end and ac.gspeed == 0:
            if ac.hs_rwy != st["rwy"]:
                ac.hs_rwy = st["rwy"]
                ac.m.setdefault("t_hs", self.t)
            ac.m["cross_wait_s"] += 1
        elif ac.hs_rwy and ph != "clear":
            ac.hs_rwy = None
        if ph == "clear" and done:
            # finished a crossing out of the between-runways hold
            ac.gpath, ac.gk, ac.gstops = [], 0, []
            ac.hs_rwy = None
            ac.clear_t = self.t
            if ac.switch_to_ground_when_clear and ac.freq != "ground":
                self.switch(ac, "ground")
            return
        if not done:
            return
        if ph == "taxiing":
            ac.phase = "holding_short"
            ac.m["t_hold_short"] = self.t
            self.queue[ac.runway].append(ac.cs)
            if ac.contact_tower_when_ready or ac.freq == "tower":
                if ac.freq != "tower":
                    self.switch(ac, "tower")
                else:
                    ac.msg_due = (self.t + ac.rng.randint(3, 12), "ready_for_departure", {})
        elif ph == "taxi_in":
            ac.m["done"] = True

    def step_air(self, ac):
        self.fly(ac)
        if ac.m["done"] or not ac.airborne:
            return
        if ac.m["t_diverted"] is not None:
            if self.t - ac.m["t_diverted"] >= DIVERT_LEAVE_S:
                ac.m["done"] = True
            return
        d_field = math.hypot(ac.x, ac.y)
        if ac.intent == "departure":
            rwy = W.RUNWAYS[ac.runway]
            past_end = rwy.u((ac.x, ac.y)) - rwy.len_nm
            if ac.freq == "tower" and past_end > getattr(ac, "freq_req_off", 1.0) and "freq_req" not in ac.flags:
                ac.flags.add("freq_req")
                ac.say(self, "request_freq_change")
            if ac.freq == "tower" and (d_field > W.TOWER_NM or ac.alt > W.TOWER_CEILING):
                self.monitor.on_event("left_tower_unhanded", ac)
                self.switch(ac, "departure")
            if ac.freq == "departure" and (d_field > W.APPROACH_NM - 8 or ac.alt > W.APPROACH_CEILING - 1500) \
                    and "freq_req2" not in ac.flags:
                ac.flags.add("freq_req2")
                ac.say(self, "request_freq_change")
            if d_field > W.APPROACH_NM or ac.alt >= W.APPROACH_CEILING:
                if ac.freq != "center":
                    self.monitor.on_event("left_airspace_unhanded", ac)
                ac.m["done"] = True
            return
        if ac.missed and ac.freq == "tower" and self.t - getattr(ac, "t_missed", self.t) > 150:
            self.monitor.on_event("missed_not_handed", ac)
            self.switch(ac, "approach")
        if d_field > W.APPROACH_NM + 15:
            self.monitor.on_event("lost", ac)
            ac.m["done"] = True

    # ------------------------------------------------------------------ flight
    def target_ias(self, ac, rem):
        p = ac.perf
        vt = p["cruise"] if ac.alt >= 10500 else min(p["vterm"], 250)
        s = ac.spd
        sloppy = 5 if ac.sloppy else 0
        if s:
            if s["kind"] == "kts":
                if s.get("until") is not None and rem is not None and rem <= s["until"]:
                    ac.spd = None
                else:
                    v = max(p["vapp"], min(s["kts"], p["cruise"] + 20)) + sloppy
                    if rem is not None and rem < 4:
                        v = min(v, p["vapp"] + 10)
                    return v
            elif s["kind"] == "slowest":
                return p["vapp"] + (20 if rem is None or rem > 6 else 0)
            elif s["kind"] == "best":
                return vt if (rem is None or rem > 6) else p["vapp"]
            elif s["kind"] == "follow":
                ld = self.aircraft.get(ac.leader) if ac.leader else self.closest_ahead(ac, rem)
                if ld is not None and ld.airborne:
                    return max(p["vapp"], min(vt, ld.ias))
        if ac.intent == "departure":
            v = vt
            if ac.alt < 1000 + W.ELEV:
                v = min(v, p["vr"] + 20)
            if ac.proc_spd:
                v = min(v, ac.proc_spd)
            return v
        v = vt
        lg = ac.leg()
        # published speed limits: the last one passed holds; slow down in time for the next one
        if ac.proc_spd and not ac.established:
            v = min(v, ac.proc_spd)
        if ac.nav == "lnav" and ac.legs and lg not in ("missed",):
            d = F.dist_to_leg_end(ac, ac.legs[0])
            cum = F.cum_lengths(ac)[1]
            for j, leg in enumerate(ac.legs[:8]):
                if leg.get("spd") and leg.get("sptype") in ("at", "max"):
                    dj = d + ((cum[0] - cum[j]) if j < len(cum) else 0.0)
                    v = min(v, leg["spd"] + max(0.0, dj - 1.0) * F.DECEL_NM)
        if rem is not None:
            if rem < 20:
                v = min(v, 210)
            if (ac.appr_cleared or ac.established) and rem < 12:
                v = min(v, 180)
            if rem < 6.5:
                v = min(v, max(p["vapp"] + 15, 160))
            if rem < 4.5:
                v = p["vapp"]
        if lg == "missed":
            v = min(vt, 210)
        if lg in ("upwind", "crosswind", "downwind"):
            v = min(v, 210)
        elif lg == "base":
            v = min(v, 180)
        if lg == "hold":
            v = min(v, F.HOLD_SPD[0] if ac.alt <= 14000 else F.HOLD_SPD[1])
        le = self.leader_eta(ac)
        if le is not None and rem is not None and ac.freq == "tower":
            if self.eta(ac, rem) - le < 70 + (20 if ac.sloppy else 0):
                v = min(v, p["vapp"] + 5)
        return max(v + (sloppy if v > p["vapp"] + 20 else 0), p["vapp"])

    def closest_ahead(self, ac, rem):
        best, br = None, None
        for o in self.aircraft.values():
            if o is ac or o.m["done"] or not o.airborne or o.intent != "arrival":
                continue
            ro = F.remaining(o)
            if rem is not None and ro is not None and ro < rem and math.hypot(o.x - ac.x, o.y - ac.y) < 8:
                if br is None or ro > br:
                    best, br = o, ro
        return best

    def traffic_to_follow(self, ac, where, runway=None):
        """'Traffic to follow is on final': the arrival nearest ahead of ac to the same runway.
        On left/right base or downwind: the aircraft flying that leg of a circuit."""
        rw = runway or ac.runway
        if where is None:
            return None
        if where != "final":
            side, part = where.split("_")
            c = [o for o in self.aircraft.values() if o is not ac and not o.m["done"] and o.airborne
                 and o.circuit == side and o.leg() == part and o.runway == rw]
            return min(c, key=lambda o: math.hypot(o.x - ac.x, o.y - ac.y)) if c else None
        if rw not in W.RUNWAYS:
            return None
        thr = W.RUNWAYS[rw].xy(0)
        mine = W.dist((ac.x, ac.y), thr)
        best, bd = None, -1
        for o in self.aircraft.values():
            if o is ac or o.m["done"] or not o.airborne or o.intent != "arrival" or o.runway != rw:
                continue
            d = W.dist((o.x, o.y), thr)
            if d < mine and d > bd:
                best, bd = o, d
        return best

    def leader_eta(self, ac):
        ld = self.aircraft.get(ac.leader) if ac.leader else None
        if ld is None or ld.m["done"] or not ld.airborne:
            return None
        r = F.remaining(ld)
        return self.eta(ld, r) if r is not None else None

    def vnav_target(self, ac):
        """Altitude the pilot is flying to: the ATC altitude, or the procedure (descend/climb via)."""
        if ac.alt_assigned is not None:
            return ac.alt_assigned
        if ac.holdp is not None and ac.hold_alt:
            return ac.hold_alt
        if ac.intent == "departure":
            top = ac.filed_alt if ac.radar_contact else W.SID_TOP
            if ac.nav == "lnav":
                for lg in ac.legs[:6]:
                    if lg.get("amax") is not None and lg["tag"] == "sid":
                        top = min(top, max(lg["amax"], ac.alt if ac.alt > lg["amax"] else 0))
            return top
        lg = ac.leg()
        if lg == "missed" or (ac.missed and ac.nav == "lnav" and ac.legs and ac.legs[0]["tag"] == "missed"):
            return max([l2["amin"] for l2 in ac.legs if l2.get("amin")] or [getattr(ac, "missed_alt", 3000)])
        if ac.nav != "lnav" or not ac.legs or ac.legs[0]["pt"] in ("FM", "VM"):
            floor = ac.alt_until if (ac.alt_until and not ac.established) else None
            if ac.legs and ac.legs[0]["pt"] in ("FM", "VM"):
                last = [l2 for l2 in ac.legs if l2.get("amin") or l2.get("amax")]
            return ac.alt if floor is None else max(min(ac.alt, ac.alt), floor)
        # descend via: the FMS path is built backward through every constraint ahead (crossing
        # 'at or above' fixes at their value, 'at or below' / 'at' fixes at or under theirs) at
        # ~3 degrees, so the descent starts as late as the path allows. Never below an 'at or
        # above' still ahead, never below the clearance limit (the STAR's last altitude) without an
        # approach clearance, nor below the 'maintain until established' altitude.
        d0 = F.dist_to_leg_end(ac, ac.legs[0])
        cum = F.cum_lengths(ac)[0]
        floor, path, last_alt = -1e9, 1e9, None
        for j, leg in enumerate(ac.legs):
            if leg["tag"] == "missed":
                break
            dj = d0 + ((cum[0] - cum[j]) if j < len(cum) else 0.0)
            if leg.get("map") and ac.appr_cleared:
                rwy = W.RUNWAYS[ac.runway]
                path = min(path, rwy.glide_alt(0.0, ac.glide_angle) + dj * F.DESC_GRAD)
                continue
            lo, hi = leg.get("amin"), leg.get("amax")
            if lo is not None or hi is not None:
                last_alt = lo if lo is not None else hi
            anchor = hi if hi is not None else lo
            if anchor is not None:
                path = min(path, anchor + max(0.0, dj - 0.5) * F.DESC_GRAD)
        # 'at or above' constraints still ahead keep the aircraft at or above the nearest one
        nxt_lo = next((lg["amin"] for lg in ac.legs if lg["tag"] != "missed" and lg.get("amin") is not None
                       and not lg.get("map")), None)
        if nxt_lo is not None:
            floor = max(floor, nxt_lo)
        if not ac.appr_cleared and last_alt is not None:
            floor = max(floor, last_alt)
        if ac.alt_until and not ac.established:
            floor = max(floor, ac.alt_until)
        tgt = min(ac.alt, path)
        if floor > tgt:
            tgt = min(ac.alt, floor) if floor > ac.alt else floor
        return tgt

    def fly(self, ac):
        rem = F.remaining(ac) if ac.intent == "arrival" else None
        lg = ac.leg()
        on_final = ac.nav == "lnav" and ac.legs and ac.legs[0]["tag"] == "final" and ac.holdp is None
        if not on_final:
            ac.gs_cap = False
        tv = self.target_ias(ac, rem)
        acc = 1.0 if ac.vs > 300 else 1.5
        dec = 0.5 if ac.vs < -300 and not on_final else 1.0
        ac.ias += max(-dec, min(acc, tv - ac.ias))
        # vertical
        if on_final and rem is not None and ac.appr_cleared:
            rwy = ac.final_rwy
            glide = rwy.glide_alt(rem, ac.glide_angle)
            nominal = ac.gs * W.FT_NM * math.tan(math.radians(ac.glide_angle)) / 60
            lead = (nominal / 60) ** 2 / (2 * F.VS_ACC / 60)
            if ac.established and not ac.gs_cap and ac.alt >= glide - lead:
                ac.gs_cap = True
            if ac.gs_cap:
                vs = max(-1500.0, min(0.0, -nominal + 4.0 * (glide - ac.alt)))
                F.vertical(ac, vs_cmd=vs)
            else:
                F.vertical(ac, ta=min(ac.alt, self.vnav_target(ac)))
        else:
            F.vertical(ac, ta=self.vnav_target(ac))
        if ac.intent == "departure" and abs(ac.vs) < 50 and ac.alt < ac.filed_alt - 100 and math.hypot(ac.x, ac.y) < 40:
            ac.m["level_s"] += 1
        if lg in ("hold", "360"):
            ac.m["hold_s"] += 1
        ac.gs = W.gs_from_ias(ac.ias, ac.alt)
        if ac.climbout and ac.alt >= F.CLIMBOUT_FT + W.ELEV:
            ac.climbout = False
        # lateral
        if ac.turn360:
            sgn = 1 if ac.turn360[0] == "right" else -1
            cmd = sgn * min(F.max_turn_rate(ac.gs), math.sqrt(2 * F.ROLL_ACC * 0.8 * max(0.0, ac.turn360[1])))
            old = F.turn(ac, cmd=cmd)
            ac.turn360[1] -= sgn * W.ang_diff(old, ac.hdg)
            F.advance(ac, old)
            if ac.turn360[1] <= 0.5:
                ac.turn360 = None
            return
        if ac.holdp is not None:
            r = F.fly_hold(ac)
            if r == "exit":
                if not ac.legs:
                    self.clearance_limit(ac)
                else:
                    ac._cum = None
            return
        if ac.nav == "heading":
            want = W.RUNWAYS[ac.runway].hdg if ac.climbout else ac.hdg_target
            td = getattr(ac, "turn_dir", None)
            if td and abs(W.ang_diff(ac.hdg, want)) > 5 and not ac.climbout:
                s = 1 if td == "right" else -1
                if W.ang_diff(ac.hdg, want) * s < 0:
                    F.advance(ac, F.turn(ac, cmd=s * F.max_turn_rate(ac.gs)))
                    return
                ac.turn_dir = None
            F.advance(ac, F.turn(ac, want=want))
            if ac.loc_armed and ac.appr_cleared:
                self.check_capture(ac)
            return
        # lnav
        want = cmd = None
        for _ in range(4):
            if not ac.legs:
                break
            lg0 = ac.legs[0]
            if ac.hold_fix and lg0.get("fix") == ac.hold_fix["fix"] and lg0["pt"] not in F.HOLD_LEGS:
                want, cmd, passed = F.steer(ac)
                if passed or (lg0.get("pos") and W.dist((ac.x, ac.y), lg0["pos"]) < ac.gs / 3600 * 1.5):
                    self.enter_atc_hold(ac)
                    return
                break
            want, cmd, passed = F.steer(ac)
            if ac.climbout:
                want, cmd = W.RUNWAYS[ac.runway].hdg, None
                if lg0["pt"] not in ("VA", "CA"):
                    passed = False
            if lg0["pt"] == "VM":
                ac.nav, ac.hdg_target = "heading", lg0["crs"]
                ac.vectors_expected = True
                ac.legs = ac.legs[1:]
                ac._cum = None
                F.advance(ac, F.turn(ac, want=ac.hdg_target))
                return
            if lg0.get("map") and rem is not None and rem <= 0.0:
                self.threshold(ac)
                return
            if lg0.get("map"):
                passed = False
            if not passed:
                break
            self.sequence_leg(ac, lg0)
            if ac.holdp is not None or not ac.airborne or ac.m["done"]:
                if ac.holdp is not None:
                    F.fly_hold(ac)
                return
            want = cmd = None
        if not ac.legs:
            want = ac.hdg if want is None else want
        old = F.turn(ac, want=ac.hdg if (want is None and cmd is None) else want, cmd=cmd)
        F.advance(ac, old)
        if ac.intent == "arrival":
            self.check_established(ac)
            if ac.legs and ac.legs[0]["tag"] == "final":
                self.final_checks(ac, F.remaining(ac))

    def sequence_leg(self, ac, lg):
        ac.legs = ac.legs[1:]
        ac._cum = None
        end = lg.get("pos") or (ac.x, ac.y)
        ac.leg_from = end if lg["pt"] not in ("CA", "VA", "VI", "FA") else (ac.x, ac.y)
        if lg.get("fix") and lg["pt"] not in ("CA", "VA", "VI", "VM", "FM"):
            self.fixlog.append((self.t, ac.cs, lg["fix"], lg["tag"], round(ac.x, 3), round(ac.y, 3), int(ac.alt),
                                int(ac.ias), lg.get("amin"), lg.get("amax"), lg.get("spd"), lg.get("sptype"),
                                round(W.dist((ac.x, ac.y), lg["pos"]), 3) if lg.get("pos") else None,
                                lg["proc"]))
        if ac.intent == "arrival":
            if lg.get("spd") and lg.get("sptype") in ("at", "max"):
                ac.proc_spd = lg["spd"]
        else:
            nxt = ac.legs[0] if ac.legs else None
            ac.proc_spd = nxt["spd"] if nxt and nxt.get("spd") and nxt.get("sptype") in ("at", "max") else (
                ac.proc_spd if lg["pt"] in ("VA", "CA") else None)
        if ac.legs and ac.legs[0]["pt"] in F.HOLD_LEGS:
            h = ac.legs[0]["hold"]
            ac.legs = ac.legs[1:]
            F.start_hold(ac, ac.legs_hold_fix(lg) if hasattr(ac, "legs_hold_fix") else end, h["crs"], h["turn"],
                         leg_min=h["leg_min"], leg_nm=h["leg_nm"], kind="published")
            ac.hold_alt = None
            return
        if not ac.legs:
            if ac.intent == "arrival" and lg["tag"] in ("star", "app"):
                self.clearance_limit(ac, lg)
            elif ac.intent == "arrival" and lg["tag"] == "missed":
                ac.nav, ac.hdg_target = "heading", ac.hdg
            elif ac.intent == "departure":
                ac.nav, ac.hdg_target = "heading", ac.hdg
                ac.vectors_expected = True

    def clearance_limit(self, ac, lg=None):
        """End of the cleared route without an approach clearance: hold at the fix (standard right
        turns on the inbound course, or the published hold there)."""
        fix = (ac.x, ac.y) if lg is None or not lg.get("pos") else lg["pos"]
        crs = ac.hdg
        if lg is not None and ac.leg_from is not None and lg.get("pos") is not None:
            crs = W.bearing(ac.leg_from, lg["pos"]) if W.dist(ac.leg_from, lg["pos"]) > 0.5 else ac.hdg
        pub = W.K.published_hold(lg.get("fix"), ac.star) if lg is not None else None
        if pub:
            crs, turns = W.mag2true(pub["inbound_course_deg"]), "R" if pub["turn"] == "right" else "L"
        else:
            turns = "R"
        if lg is not None and lg.get("pos"):
            crs = W.bearing(ac.leg_from if ac.leg_from and W.dist(ac.leg_from, lg["pos"]) > 0.5 else (ac.x, ac.y), lg["pos"]) \
                if not pub else crs
        F.start_hold(ac, fix, crs, turns, kind="limit", leg_min=pub and pub["leg_min"], leg_nm=pub and pub["leg_nm"])
        ac.hold_alt = ac.hold_alt or int(round(ac.alt / 100.0) * 100)
        self.monitor.on_event("clearance_limit", ac)

    def enter_atc_hold(self, ac):
        hf = ac.hold_fix
        lg = ac.legs[0]
        pos = lg["pos"]
        pub = W.K.published_hold(hf["fix"], ac.star)
        turns = hf["turns"]
        if pub:     # "hold as published": the charted inbound course, turns and leg length
            crs, turns = W.mag2true(pub["inbound_course_deg"]), "R" if pub["turn"] == "right" else "L"
        else:
            crs = F.leg_course_at(ac.leg_from or (ac.x, ac.y), lg) or ac.hdg
            if lg["pt"] in ("TF", "DF", "IF") and ac.leg_from:
                crs = W.bearing(ac.leg_from, pos) if W.dist(ac.leg_from, pos) > 0.3 else ac.hdg
        ac.legs = ac.legs[1:]
        ac._cum = None
        F.start_hold(ac, pos, crs, turns, kind="atc", leg_min=pub and pub["leg_min"], leg_nm=pub and pub["leg_nm"])
        ac.hold_alt = hf["alt"]

    def check_capture(self, ac):
        """Final course capture from a heading (7110.65 5-9-2): the turn starts at the lead point
        R(1 - cos delta), but not before the needle comes alive, so steep or fast intercepts
        overshoot."""
        pid = ac.appr_cleared
        rwy = ac.final_rwy
        if pid[0] in ("I", "L"):
            A, crs = rwy.thr, rwy.loc_course
            legs = [dict(lg) for lg in P.approach_legs(pid) if lg["tag"] in ("final", "missed")]
        else:
            fl = [dict(lg) for lg in P.approach_legs(pid) if lg["tag"] in ("final", "missed")]
            if len(fl) < 2:
                return
            A = fl[1]["pos"]
            crs = fl[1]["crs"] if fl[1]["pt"] == "CF" else W.bearing(fl[0]["pos"], fl[1]["pos"])
            legs = fl
        ux, uy = W.unit(crs)
        along = (A[0] - ac.x) * ux + (A[1] - ac.y) * uy          # before the reference point
        xtk = (ac.x - A[0]) * uy - (ac.y - A[1]) * ux
        a_thr = rwy.to_thr(ac.x, ac.y)[0]
        if not (0.5 < a_thr < 25.0):
            if a_thr <= 0.5 and ac.loc_armed:
                ac.loc_armed = False
                self.monitor.on_event("missed_intercept", ac)
            return
        delta = W.ang_diff(crs, ac.hdg)
        if abs(delta) >= 90:
            return
        toward = xtk * delta < 0
        v, wmax = ac.gs / 3600, F.max_turn_rate(ac.gs)
        lead = (F.turn_radius(ac.gs) * (1 - math.cos(math.radians(delta)))
                + v * abs(math.sin(math.radians(delta))) * wmax / F.ROLL_ACC / 2)
        if ac.sloppy:
            lead *= 0.8
        beam = (a_thr + rwy.len_nm) * math.tan(math.radians(F.LOC_HALF_WIDTH))
        if (toward or abs(xtk) < 0.05) and abs(xtk) <= min(lead, beam):
            ac.icpt_angle = abs(delta)
            # fly the final segment from the first fix still ahead
            ahead = []
            for lg in legs:
                if lg["tag"] == "missed" or lg.get("pos") is None:
                    ahead.append(lg)
                    continue
                if lg.get("map") or (lg["pos"][0] - ac.x) * ux + (lg["pos"][1] - ac.y) * uy > 0.3:
                    ahead.append(lg)
            ahead = [lg for lg in ahead if lg["pt"] != "IF"]
            first = ahead[0]
            if first["pt"] != "CF":
                ahead[0] = dict(first, pt="CF", crs=crs)
            ac.nav = "lnav"
            ac.set_legs(ahead, (ac.x, ac.y))

    def check_established(self, ac):
        if ac.established or not ac.appr_cleared or not ac.legs or ac.legs[0]["tag"] != "final":
            return
        lg = ac.legs[0]
        if lg["pt"] == "RF" and lg.get("center"):
            err = abs(W.dist((ac.x, ac.y), lg["center"]) - lg["radius"])
            tang = (W.bearing(lg["center"], (ac.x, ac.y)) + (90 if lg["turn"] == "R" else -90)) % 360
            ok = err < 0.1 and abs(W.ang_diff(ac.hdg, tang)) < 10
        elif lg.get("pos") is not None:
            if lg["pt"] == "CF":
                c = lg["crs"]
            elif ac.leg_from is not None and W.dist(ac.leg_from, lg["pos"]) > 0.05:
                c = W.bearing(ac.leg_from, lg["pos"])
            else:
                return
            ux, uy = W.unit(c)
            A = lg["pos"]
            xtk = (ac.x - A[0]) * uy - (ac.y - A[1]) * ux
            ok = abs(xtk) < 0.08 and abs(W.ang_diff(ac.hdg, c)) < 5
        else:
            return
        if ok:
            ac.established = True
            ac.loc_armed = False
            ac.missed = False
            ac.vectors_expected = False
            if ac.icpt_angle is None and ac.leg_from is not None:
                ac.icpt_angle = 0.0
            self.monitor.on_event("established", ac, angle=round(ac.icpt_angle if ac.icpt_angle is not None else 30))

    def start_roll(self, ac):
        ac.phase = "rolling"
        ac.gs = ac.ias = 0.0
        ac.rot = None
        if ac.m["t_roll"] is None:
            ac.m["t_roll"] = self.t
        self.monitor.on_event("roll_start", ac)
        self.departed.append((self.t, ac.runway, ac.wake, ac.cs))

    def liftoff(self, ac):
        rwy = W.RUNWAYS[ac.runway]
        ac.phase = "airborne"
        ac.alt = float(rwy.elev)
        ac.vs = 0.0
        ac.rate = 0.0
        ac.climbout, ac.gs_cap = True, False
        ac.m["t_airborne"] = ac.m["t_airborne"] or self.t
        ac.zone = ()
        self.monitor.on_event("airborne", ac)
        ac.nav = "lnav"
        ac.set_legs(P.sid_legs(ac.sid, ac.runway, ac.sid_trans), (ac.x, ac.y))
        ac.proc_spd = ac.legs[0]["spd"] if ac.legs and ac.legs[0].get("spd") else None
        ac.freq_req_off = 0.5 + ac.rng.uniform(0.0, 1.0)

    def flare_step(self, ac):
        """After the threshold: down the glidepath to flare height, flare, touch down."""
        rwy = W.RUNWAYS[ac.runway]
        ac.ias = max(0.0, ac.ias - 0.7)
        ac.gs = W.gs_from_ias(ac.ias, ac.alt)
        vs = min(0.0, ac.vs / 60)
        u, v = rwy.uv(ac.x, ac.y)
        for _ in range(4):
            agl = ac.alt - rwy.elev
            if agl <= F.FLARE_FT:
                vs = max(vs, -max(agl / F.FLARE_TAU, 2.0))
            if agl + vs * 0.25 <= 0:
                f = agl / -vs if vs < 0 else 0.25
                u += ac.gs / 3600 * f
                ac.touchdown_u = u
                u += ac.gs / 3600 * (0.25 - f)
                ac.alt, vs, ac.flare = float(rwy.elev), 0.0, False
                continue
            if ac.flare:
                ac.alt += vs * 0.25
            u += ac.gs / 3600 * 0.25
        ac.vs = vs * 60
        v -= max(-0.002, min(0.002, v))
        ac.x, ac.y = rwy.xy(u, v)
        ac.hdg = (ac.hdg + max(-2.0, min(2.0, W.ang_diff(ac.hdg, rwy.hdg)))) % 360
        if not ac.flare:
            self.monitor.on_event("touchdown", ac, u=ac.touchdown_u)

    @staticmethod
    def turnoff_kt(ac, exit_):
        hs = exit_ is None or exit_["kind"] == "high_speed"
        base = 30 if hs else 12
        return base + (5 if ac.expedite_exit else 0) - (4 if ac.sloppy else 0)

    def choose_exit(self, ac, decel):
        rwy = W.RUNWAYS[ac.runway]
        side = "right" if ac.cargo else "left"
        u_now = rwy.u((ac.x, ac.y))
        cands = [e for e in W.EXITS[ac.runway] if e["side"] == side]
        pick = None
        skip = ac.sloppy and ac.rng.random() < 0.5
        for e in cands:
            tk = self.turnoff_kt(ac, e)
            need = max(0.0, ac.gs ** 2 - tk ** 2) / (2 * decel * 0.8) / 3600
            if e["u"] - RWY_EXIT_LEAD >= u_now + need:
                if skip:
                    skip = False
                    continue
                pick = e
                break
        if pick is None:
            pick = cands[-1] if cands else W.EXITS[ac.runway][-1]
        return pick

    def rollout_to_exit(self, ac, decel):
        rwy = W.RUNWAYS[ac.runway]
        if ac.target_exit is None:
            ac.target_exit = self.choose_exit(ac, decel)
            ac.exit_used = ac.target_exit
        e = ac.target_exit
        tk = self.turnoff_kt(ac, e)
        u, v = rwy.uv(ac.x, ac.y)
        d = max(0.0, e["u"] - RWY_EXIT_LEAD - u)
        ve = 40 if ac.expedite_exit else (30 if ac.exit_cmd else 25)
        v_lim = math.sqrt(tk ** 2 + 2 * 2.0 * d * 3600)
        v0 = ac.gs
        ac.gs = max(v0 - decel, min(max(min(ve, v0), v0 - decel), v_lim))
        if ac.phase == "rto" and v0 < min(ve, v_lim):
            # a takeoff rejected at (or near) brake release: taxi on to the exit instead of
            # stopping on the runway for good (it would otherwise keep v0, possibly 0 kt)
            ac.gs = min(v0 + TAXI_ACC, ve, v_lim)
        ac.ias = ac.gs
        u += (v0 + ac.gs) / 2 / 3600
        v -= max(-0.001, min(0.001, v))
        ac.x, ac.y = rwy.xy(u, v)
        ac.hdg = (ac.hdg + max(-2.0, min(2.0, W.ang_diff(ac.hdg, rwy.hdg)))) % 360
        if u >= e["u"] - RWY_EXIT_LEAD:
            ac.phase = "exiting"
            ac.gpath = [(ac.x, ac.y, None)] + [(p[0], p[1], None) for p in e["pts"][1:]]
            ac.gpath[-1] = (*ac.gpath[-1][:2], e["end"])
            ac.gk, ac.gstops = 1, []
            ac.gspeed = ac.gs
            ac.gnode = e["node"]
