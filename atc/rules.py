"""Rules monitor for KSFO: checks Ground, Tower, Approach and Departure against the IFATC
manual (reference/atc-manual-1-to-6.txt), with the multi-runway rules a West Plan needs.

Severity:
  hard     loss of runway separation (3.1.1, incl. intersecting runways 01L/01R x 28L/28R),
           crossing a runway against traffic (2.3.2), collision alarm (6.2.4: < 1.5 nm and
           < 500 ft with a radar-controlled aircraft), a near miss anywhere (< 0.4 nm and
           < 300 ft), or terrain (6.2.1, NorCal TRACON MVA). Any one fails the run.
  rule     a procedural rule from the manual, cited by section. Added for KSFO:
             6.11.8  parallel approaches: lost 1000 ft / 3 nm between aircraft on approach
                     to 28L and 28R before both are established (6.11.7-6.11.8)
             wake    in-trail wake turbulence spacing on final (same runway or the closely
                     spaced parallel behind a heavy/super; FAA JO 7110.65 5-5-4) and
                     departures behind a heavy/super (5-8-3: 2 / 3 minutes)
             2.2.2   taxi clearance without a route; a crossing clearance for a runway that
                     is not the next one on the route (2.2.2 "must have crossed a previous
                     runway before another runway crossing clearance may be issued")
             2.3.1   crossing clearance not naming the runway in the direction in use
             2.2.1   taxi conflicts are resolved with "give way" (or hold short of a taxiway);
                     "hold position" for traffic only when a collision is imminent (< ~25 s)
  service  not a rule break, but worse service.

Each violation names the position that owns it (GND, TWR, APP, DEP). The monitor sees the
true world, not the controller's observation.
"""
import math

from . import flight as F
from . import procs as P
from . import world as W

NEAR_MISS_NM, NEAR_MISS_FT = 0.4, 300
ALARM_NM, ALARM_FT = 1.5, 500             # 6.2.4 red target + audible alarm
SEP_NM, SEP_FT = 3.0, 1000                # 6.2.2
LOC_RANGE = 25.0
GROUND = ("pushing", "pushed", "taxiing", "holding_short", "clear", "taxi_in")


def owner(ac):
    return W.POS.get(ac.freq if ac else None, "ATC")


def same_or_cspr(a, b):
    return a == b or W.CSPR.get(a) == b


class Monitor:
    def __init__(self, sim):
        self.sim = sim
        self.violations = []
        self.seen = set()
        self.alerts = 0
        self.invalid = 0
        self.req_wait_s = 0
        self.sep_loss_s = 0

    def add(self, severity, rule, ac_or_cs, msg, key=None, pos=None):
        if key is not None:
            if key in self.seen:
                return
            self.seen.add(key)
        cs = ac_or_cs.cs if hasattr(ac_or_cs, "cs") else ac_or_cs
        pos = pos or (owner(ac_or_cs) if hasattr(ac_or_cs, "cs") else "ATC")
        self.violations.append({"t": self.sim.t, "severity": severity, "rule": rule, "cs": cs, "pos": pos, "msg": msg})
        self.sim.log("MONITOR", f"[{severity} {rule} {pos}] {cs}: {msg}", kind=severity, cs=cs)

    def others(self, ac):
        return [o for o in self.sim.aircraft.values() if o is not ac and not o.m["done"]]

    @staticmethod
    def imminent(a, b):
        """Two aircraft on the ground about to collide: within ~400 ft and heading for < ~120 ft separation within 25 s."""
        px, py = b.x - a.x, b.y - a.y
        d = math.hypot(px, py)
        if d > 0.065:
            return False
        if abs(W.ang_diff(a.hdg, b.hdg)) < 60:
            return False                       # in trail, same direction: the pilots keep their spacing
        ha, hb = math.radians(a.hdg), math.radians(b.hdg)
        vx = (math.sin(hb) * b.gspeed - math.sin(ha) * a.gspeed) / 3600
        vy = (math.cos(hb) * b.gspeed - math.cos(ha) * a.gspeed) / 3600
        vv = vx * vx + vy * vy
        if vv < 1e-12:
            return False
        t_cpa = -(px * vx + py * vy) / vv                     # s to closest approach (straight lines)
        d_cpa = math.hypot(px + vx * t_cpa, py + vy * t_cpa)
        return 0 < t_cpa < 25 and d_cpa < 0.02                # < ~120 ft apart within 25 s

    # ------------------------------------------------------------------ commands
    def on_command(self, ac, cmd, err):
        sim = self.sim
        if err:
            self.invalid += 1
            self.add("rule", "menu", cmd.get("cs") or "?", f"invalid command: {err}", pos=owner(ac))
            return
        n = cmd["cmd"]
        others = self.others(ac)
        rem = F.remaining(ac) if ac.airborne else None
        if n == "pushback_approved":
            for o in others:
                if o.phase == "pushing" and not o.hold and math.hypot(o.x - ac.x, o.y - ac.y) < 0.045:
                    self.add("rule", "2.1.3", ac, f"pushback approved while {o.cs} is pushing {math.hypot(o.x - ac.x, o.y - ac.y) * W.FT_NM:.0f} ft away")
                    break
        if n == "hold_position" and ac.freq == "ground" and ac.phase in ("taxiing", "taxi_in") and ac.gspeed > 2:
            near = [o for o in others if (o.phase in GROUND or o.phase in ("exiting", "landing_roll")) and not o.airborne
                    and math.hypot(o.x - ac.x, o.y - ac.y) < 0.3]
            if near and not any(self.imminent(ac, o) for o in near):
                self.add("rule", "2.2.1", ac, f"stopped with 'hold position' for {near[0].cs}: use 'give way' "
                         "(or hold short of a taxiway); hold position only when a collision is imminent", pos="GND")
        if n in ("taxi_to_runway", "taxi_to_parking") and not cmd.get("via"):
            self.add("rule", "2.2.2", ac, "taxi clearance without a route (drag & taxi is available at KSFO)")
        if n == "taxi_to_runway" and ac.cat == "prop" and not cmd["contact_tower"]:
            self.add("rule", "2.2.2", ac, "props get 'taxi to runway, contact tower when ready'")
        if n == "cross_runway":
            rw = cmd["runway"]
            if rw not in W.IN_USE.values():
                self.add("rule", "2.3.1", ac, f"cross runway {rw}: refer to the runway in the direction in use "
                         f"({W.IN_USE.get(W.RUNWAYS[rw].pavement, '?')})")
            nxt = sim.next_crossing(ac)
            if nxt is not None and nxt != rw:
                self.add("rule", "2.2.2", ac, f"crossing clearance for runway {rw} before runway {nxt} was crossed")
        if n == "slowest_practical_speed" and rem is not None and rem <= 4.0:
            self.add("rule", "3.1.4", ac, f"slowest practical speed {rem:.1f} nm from touchdown (not inside 4 nm)")
        if n == "cleared_takeoff" and cmd["immediate"]:
            close = [o for o in others if o.airborne and o.intent == "arrival" and o.runway in W.ARR_RUNWAYS
                     and (F.remaining(o) or 99) <= 3.0]
            if not close:
                self.add("rule", "3.2.7", ac, "immediate takeoff with no arrival inside ~3 nm final")
        if n in ("freq_change_approved", "contact_frequency") and ac.intent == "departure" and ac.airborne \
                and ac.freq == "tower":
            rwy = W.RUNWAYS[ac.runway]
            if rwy.u((ac.x, ac.y)) < rwy.len_nm + 0.5:
                self.add("rule", "3.2.11", ac, "frequency change during a critical phase (before 1/2 nm off the departure end)")
        if n == "exit_runway" and ac.gs > 70:
            self.add("rule", "3.5.1", ac, f"exit runway issued at {ac.gs:.0f} kts (below ~70)")
        # ---- radar ----
        if n == "vector" and cmd["altitude"] is not None:
            for (x, y) in self.projected(ac, cmd):
                if cmd["altitude"] < W.mva(x, y):
                    self.add("rule", "6.3.2", ac, f"assigned {cmd['altitude']} ft where the MVA is {W.mva(x, y)} ft")
                    break
        if n in ("hold_present_position", "hold_at_fix"):
            fx = W.K.FIXES.get(cmd.get("fix")) if n == "hold_at_fix" else (ac.x, ac.y)
            if fx and cmd["altitude"] < W.mva(*fx):
                self.add("rule", "6.3.2", ac, f"hold at {cmd['altitude']} ft where the MVA is {W.mva(*fx)} ft")
        if n == "cleared_approach":
            self.check_intercept(ac, cmd)
        if n == "maintain_speed":
            if cmd["kts"] < ac.perf["vapp"]:
                self.add("rule", "6.15", ac, f"{cmd['kts']} kts is below what a {ac.type} can fly")
            if cmd["kts"] > 250 and ac.alt < 10000:
                self.add("rule", "6.15", ac, "more than 250 kts below 10,000 ft")
        if n in ("contact_frequency", "freq_change_approved") and ac.freq == "approach" and ac.airborne \
                and (cmd.get("facility") or sim.next_facility(ac)) == "tower":
            self.check_handoff(ac)

    def projected(self, ac, cmd, minutes=3):
        h = math.radians(W.mag2true(cmd["heading"]) if cmd["heading"] is not None else ac.hdg)
        d = ac.gs / 60 * minutes
        return [(ac.x + math.sin(h) * d * k / 4, ac.y + math.cos(h) * d * k / 4) for k in range(5)]

    def check_intercept(self, ac, cmd):
        """6.11.3-6.11.5 (ILS) and 6.10.2/6.10.4 (GPS / RNP): an intercept heading ~30 degrees
        that converges between 2 and 25 nm, or a published join on the flight plan; for the ILS
        always an altitude until established, below the glidepath where it joins."""
        rwy = W.RUNWAYS[cmd["runway"]]
        pid = P.APPROACH_IDS[cmd["runway"]][cmd["approach"]]
        ils = cmd["approach"] == "ils"
        if ils and cmd["altitude"] is None:
            self.add("rule", "6.11.5", ac, "ILS clearance without an altitude to maintain until established")
        hdg = cmd["heading"]
        if hdg is None:
            if not self.on_published_join(ac, pid):
                sec = "6.11.5" if ils else "6.10.4"
                self.add("rule", sec, ac, f"{cmd['approach'].upper()} clearance without an intercept heading while not "
                         "on a STAR / transition / hold that joins the approach")
                return
            fix, pos, _ = P.intermediate_fix(pid)
            join = rwy.to_thr(*pos)[0]
        else:
            h = W.mag2true(hdg)
            crs = rwy.loc_course
            angle = abs(W.ang_diff(crs, h))
            if angle > 45 or angle <= 10:
                self.add("rule", "6.11.3", ac, f"intercept heading {hdg:03d} is {angle:.0f} deg off the final course "
                         "(as close to 30 as possible, never 10 or less)")
            p = P.intersect_ray((ac.x, ac.y), h, rwy.thr, crs)
            if p is None:
                self.add("rule", "6.11.3", ac, f"heading {hdg:03d} does not converge on the final course")
                return
            join = rwy.to_thr(*p)[0]
            if not (2.0 < join < LOC_RANGE):
                self.add("rule", "6.11.3", ac, f"heading {hdg:03d} joins the final course {join:.1f} nm out, "
                         "not between 2 and 25 nm")
                return
        if cmd["altitude"] is not None:
            gs_alt = rwy.glide_alt(join, P.glide_angle(pid))
            if cmd["altitude"] > gs_alt + 50:
                self.add("rule", "6.11.4", ac, f"{cmd['altitude']} ft is above the glidepath ({gs_alt:.0f} ft) where "
                         f"it joins {join:.1f} nm out; intercept from below")

    def on_published_join(self, ac, pid):
        """On a STAR, approach transition or hold whose route leads onto this approach."""
        if ac.nav != "lnav" or ac.turn360:
            return False
        if ac.holdp is not None:
            fix = next((n for n, p in W.K.FIXES.items() if W.dist(p, ac.holdp["fix"]) < 1e-4), None)
            app = [lg.get("fix") for lg in P.approach_legs(pid)]
            route = [lg.get("fix") for lg in ac.legs if lg["pt"] not in ("FM", "VM")]
            return bool(fix and (P.transition_for(pid, fix) or fix in app or
                                 (route and (P.transition_for(pid, route[-1]) or route[-1] in app))))
        if not ac.legs or ac.legs[0]["pt"] in ("FM", "VM"):
            return False
        route = [lg for lg in ac.legs if lg["pt"] not in ("FM", "VM") and lg["tag"] != "missed"]
        if not route:
            return False
        app = [lg.get("fix") for lg in P.approach_legs(pid)]
        if any(lg.get("fix") in app for lg in route):
            return True
        return bool(P.transition_for(pid, route[-1].get("fix")))

    def check_handoff(self, ac):
        """6.14: established on the intended path, with acceptable closure."""
        if ac.intent == "arrival" and ac.appr_cleared is None:
            self.add("rule", "6.14.1", ac, "IFR arrival handed to tower without an approach clearance")
        elif ac.intent == "arrival" and not ac.established:
            self.add("rule", "6.14.1", ac, "handed to tower before established on the final approach course")
        rem = F.remaining(ac)
        if rem is None:
            return
        for o in self.others(ac):
            if not o.airborne or o.runway != ac.runway or o.intent != "arrival":
                continue
            ro = F.remaining(o)
            if ro is not None and ro < rem and rem - ro < 4 and ac.gs > o.gs + 40:
                self.add("rule", "6.14.3", ac, f"closing on {o.cs} ({ac.gs:.0f} vs {o.gs:.0f} kts GS, {rem - ro:.1f} nm) at handoff")
                return

    # ------------------------------------------------------------------ runway geometry
    @staticmethod
    def before_point(o, rwy_ident, u_point):
        """o is on runway rwy_ident (rolling / landing / lined up) and not yet beyond u_point."""
        R = W.RUNWAYS[rwy_ident]
        return R.u((o.x, o.y)) < u_point + W.BEYOND_NM

    def runway_users(self, ac):
        out = []
        for o in self.others(ac):
            if o.phase in ("rolling", "landing_roll", "rto"):
                out.append((o, o.runway))
            elif o.phase == "airborne" and o.intent == "departure" and o.alt < W.ELEV + 600 and o.runway in W.RUNWAYS:
                R = W.RUNWAYS[o.runway]
                if R.u((o.x, o.y)) < R.len_nm + 0.3:
                    out.append((o, o.runway))
            elif o.phase == "exiting" and o.runway in o.zone:
                out.append((o, o.runway))
        return out

    # ------------------------------------------------------------------ events
    def on_event(self, ev, ac, **kw):
        sim = self.sim
        if ev == "roll_start":
            rw = ac.runway
            for o, orw in self.runway_users(ac):
                if same_or_cspr(orw, rw):
                    if o.phase == "rolling":
                        self.add("hard", "3.1.1", ac, f"dep/dep: began takeoff roll on {rw} before {o.cs} ({orw}) was airborne", pos="TWR")
                    elif orw == rw and o.phase != "airborne":
                        self.add("hard", "3.1.1", ac, f"arr/dep: began takeoff roll on {rw} with {o.cs} still on the runway ({o.phase})", pos="TWR")
                elif (rw, orw) in W.INTERSECTIONS and self.before_point(o, orw, W.INTERSECTIONS[(orw, rw)]):
                    self.add("hard", "3.1.1", ac, f"intersecting: began takeoff roll on {rw} before {o.cs} on {orw} "
                             f"({o.phase}) was beyond the intersection", pos="TWR")
            for o in self.others(ac):
                if rw in o.zone and o.phase in GROUND + ("lining_up", "exiting"):
                    self.add("hard", "3.1.1", ac, f"began takeoff roll on {rw} with {o.cs} in the runway strip ({o.phase})", pos="TWR")
            for t, drw, wake, cs in reversed(sim.departed):
                need = W.DEP_WAKE_S.get(wake)
                if need and cs != ac.cs and same_or_cspr(drw, rw) and sim.t - t < need and ac.wake not in ("J",) \
                        and not (wake == "H" and ac.wake == "H"):
                    self.add("rule", "wake", ac, f"departure {sim.t - t} s behind {W.WAKE_NAMES[wake]} {cs} from {drw} "
                             f"(needs {need // 60} min, 7110.65 5-8-3)", pos="TWR")
                    break
        elif ev == "threshold":
            rw = ac.runway
            for o in self.others(ac):
                if rw in o.zone and o.phase not in ("airborne", "pending"):
                    if not (o.phase == "exiting" and getattr(o, "vacated", False)):
                        kind = {"landing_roll": "arr/arr", "exiting": "arr/arr", "rolling": "dep/arr"}.get(o.phase, "occupied")
                        self.add("hard", "3.1.1", ac, f"{kind}: crossed the {rw} threshold with {o.cs} on the runway ({o.phase})", pos="TWR")
            for o, orw in self.runway_users(ac):
                if orw != rw and (rw, orw) in W.INTERSECTIONS and self.before_point(o, orw, W.INTERSECTIONS[(orw, rw)]):
                    self.add("hard", "3.1.1", ac, f"intersecting: crossed the {rw} threshold before {o.cs} on {orw} "
                             f"({o.phase}) was beyond the intersection", pos="TWR")
        elif ev == "cross_enter":
            rw = kw.get("rwy")
            R = W.RUNWAYS[rw]
            u_x = R.u((ac.x, ac.y))
            for o in self.others(ac):
                if o.runway != rw:
                    continue
                if o.phase == "rolling" and R.u((o.x, o.y)) < u_x + W.BEYOND_NM:
                    self.add("hard", "2.3.2", ac, f"entered runway {rw} to cross while {o.cs} was rolling toward the crossing point")
                elif o.phase in ("landing_roll", "rto") and R.u((o.x, o.y)) < u_x + W.BEYOND_NM \
                        and not (o.target_exit and o.target_exit["u"] < u_x - 0.05):
                    self.add("hard", "2.3.2", ac, f"entered runway {rw} to cross while {o.cs} was landing toward the crossing point")
                elif o.phase == "airborne" and o.intent == "departure" and o.alt < W.ELEV + 300 \
                        and R.u((o.x, o.y)) < u_x + W.BEYOND_NM:
                    self.add("hard", "2.3.2", ac, f"entered runway {rw} to cross under {o.cs} lifting off toward it")
        elif ev == "established":
            angle = kw.get("angle", 30)
            rwy = ac.final_rwy or W.RUNWAYS[ac.runway]
            a_thr = rwy.to_thr(ac.x, ac.y)[0]
            gs_alt = rwy.glide_alt(a_thr, ac.glide_angle)
            if ac.alt > gs_alt + 300:
                self.add("rule", "6.11.4", ac, f"established {ac.alt - gs_alt:.0f} ft above the glidepath", pos="APP")
            if angle > 45:
                self.add("rule", "6.11.3", ac, f"joined the final approach course at {angle:.0f} degrees", pos="APP")
        elif ev == "ground_blocked":
            o = kw.get("other")
            self.add("rule", "2.2.1", ac, f"stuck nose to nose with {o.cs if o else '?'} on the taxiways for 90 s "
                     "(use give way / routes that don't conflict)", pos="GND")
        elif ev == "missed_intercept":
            self.add("rule", "6.11.3", ac, "flew through the final approach course without capturing it", pos="APP")
        elif ev == "no_clearance":
            self.add("rule", "3.6.3", ac, "short final with no landing clearance (pilot went around)", pos="TWR")
        elif ev == "not_established":
            self.add("rule", "6.14.1", ac, "reached short final not established on the approach (pilot went around)", pos="APP")
        elif ev == "unstable":
            self.add("rule", "6.11.4", ac, f"unstable on short final ({kw.get('high', 0):.0f} ft above the glidepath; "
                     "pilot went around)", pos="APP")
        elif ev == "pilot_ga_traffic":
            self.add("rule", "3.1.3", ac, "pilot went around for traffic ahead on final (spacing)",
                     pos="TWR" if ac.freq == "tower" else "APP")
        elif ev == "pilot_ga_occupied":
            self.add("rule", "3.1.3", ac, "pilot went around for an occupied runway", pos="TWR")
        elif ev == "left_tower_unhanded":
            self.add("service", "3.2.11", ac, "departure left tower airspace without a frequency change", pos="TWR")
        elif ev == "left_airspace_unhanded":
            self.add("service", "6.4", ac, "departure left the airspace without a handoff to center", pos="DEP")
        elif ev == "missed_not_handed":
            self.add("service", "3.5.4", ac, "missed approach not handed back to approach", pos="TWR")
        elif ev == "clearance_limit":
            self.add("service", "6.9.4", ac, "reached the end of its cleared route without an approach clearance (holding)",
                     pos="APP")
        elif ev == "lost":
            self.add("service", "lost", ac, "left the airspace while still inbound", pos="APP")

    # ------------------------------------------------------------------ ticks
    def on_tick(self):
        sim = self.sim
        air = [a for a in sim.aircraft.values() if a.airborne and not a.m["done"] and a.alt > W.ELEV + 150]
        for a in air:
            vectored = a.nav == "heading" or a.turn360 or (a.holdp is not None and a.holdp.get("kind") == "present")
            climbing_out = a.intent == "departure" and a.vs > 300 and math.hypot(a.x, a.y) < 12
            if a.freq in W.RADAR and vectored and not a.established and not climbing_out and not a.missed:
                need = W.mva(a.x, a.y)
                if a.alt < need - 100 and math.hypot(a.x, a.y) > 5:
                    self.add("hard", "6.2.1", a, f"vectored at {a.alt:.0f} ft where the MVA is {need} ft",
                             key=("terrain", a.cs, sim.t // 120))
        for i in range(len(air)):
            a = air[i]
            for j in range(i + 1, len(air)):
                b = air[j]
                dx, dy = a.x - b.x, a.y - b.y
                if abs(dx) >= SEP_NM or abs(dy) >= SEP_NM:
                    continue
                vert = abs(a.alt - b.alt)
                if vert >= SEP_FT:
                    continue
                lat = math.hypot(dx, dy)
                if lat >= SEP_NM:
                    continue
                pair = tuple(sorted((a.cs, b.cs)))
                radar = a.freq in W.RADAR or b.freq in W.RADAR
                resp = a if a.freq in W.RADAR else b
                both_est = a.established and b.established and a.runway in W.ARR_RUNWAYS and b.runway in W.ARR_RUNWAYS
                if lat < NEAR_MISS_NM and vert < NEAR_MISS_FT and not (both_est and a.runway != b.runway):
                    self.add("hard", "AIR", a, f"near miss with {b.cs}: {lat:.2f} nm, {vert:.0f} ft",
                             key=("near", pair, sim.t // 120), pos=owner(resp))
                elif both_est:
                    continue                        # 6.11.7: established on parallel / same final (wake checked below)
                elif radar and lat < ALARM_NM and vert < ALARM_FT:
                    self.add("hard", "6.2.4", resp, f"collision alarm with {(b if resp is a else a).cs}: {lat:.2f} nm, {vert:.0f} ft",
                             key=("alarm", pair, sim.t // 120))
                elif radar and a.ifr and b.ifr:
                    self.sep_loss_s += 1
                    par = (a.intent == b.intent == "arrival" and a.appr_cleared and b.appr_cleared
                           and W.CSPR.get(a.runway) == b.runway)
                    rule = "6.11.8" if par else "6.2.2"
                    what = "parallel approaches not separated until both established" if par else "lost IFR separation"
                    self.add("rule", rule, resp, f"{what} with {(b if resp is a else a).cs}: {lat:.1f} nm, {vert:.0f} ft",
                             key=("sep", pair, sim.t // 300))
                elif not radar and lat < ALARM_NM and vert < ALARM_FT:
                    k = ("alert", pair, sim.t // 120)
                    if k not in self.seen:
                        self.seen.add(k)
                        self.alerts += 1
        self.wake_on_final()
        ground = [a for a in sim.aircraft.values() if not a.m["done"] and a.phase in GROUND]
        for i in range(len(ground)):
            a = ground[i]
            for j in range(i + 1, len(ground)):
                b = ground[j]
                if abs(a.x - b.x) < 0.025 and abs(a.y - b.y) < 0.025 and math.hypot(a.x - b.x, a.y - b.y) < 0.025 \
                        and (a.gspeed > 0 or b.gspeed > 0):
                    pair = tuple(sorted((a.cs, b.cs)))
                    self.add("rule", "2.2.1", a, f"taxi conflict with {b.cs} (< 150 ft)", key=("gnd", pair, sim.t // 120),
                             pos="GND")
        for r in sim.requests:
            if r["answered"] is None:
                self.req_wait_s += 1
                if r["kind"] == "ready_for_departure" and sim.t - r["t"] > 30:
                    self.add("rule", "3.2.1", r["cs"], "ready for departure left unanswered >30 s (use hold short)",
                             key=("3.2.1", r["id"]), pos="TWR")
                if r["kind"] == "check_in" and sim.t - r["t"] > 30:
                    pos = "DEP" if r["freq"] == "departure" else "APP"
                    self.add("rule", "6.4.4", r["cs"], "check-in not answered within 30 s", key=("6.4.4", r["id"]), pos=pos)

    def wake_on_final(self):
        """Wake turbulence / in-trail spacing between aircraft established on final (7110.65 5-5-4):
        same runway at least max(2.5 nm, wake minimum); behind a heavy or super on the closely spaced
        parallel (750 ft apart, one runway for wake purposes) the wake minimum."""
        sim = self.sim
        fin = [a for a in sim.aircraft.values() if a.airborne and not a.m["done"] and a.established
               and a.runway in W.ARR_RUNWAYS and a.legs and a.legs[0]["tag"] == "final"]
        if len(fin) < 2:
            return
        ref = W.RUNWAYS["28R"]
        d = {a.cs: ref.to_thr(a.x, a.y)[0] for a in fin}
        for a in fin:
            for b in fin:
                if a is b or not same_or_cspr(a.runway, b.runway):
                    continue
                gap = d[b.cs] - d[a.cs]                 # b behind a
                if gap <= 0 or gap > 10 or d[b.cs] < 0.3:
                    continue
                need = W.wake_nm(a.wake, b.wake)
                if a.runway == b.runway:
                    need = max(need, 2.5)
                if need and gap < need - 0.05:
                    self.add("rule", "wake", b, f"{gap:.1f} nm behind {W.WAKE_NAMES[a.wake]} {a.cs} on final "
                             f"({a.runway}/{b.runway}, needs {need:.1f} nm, 7110.65 5-5-4)",
                             key=("wake", a.cs, b.cs), pos="APP" if b.freq == "approach" else "TWR")

    def finish(self):
        for a in self.sim.aircraft.values():
            if not a.m["done"]:
                self.add("service", "stuck", a, f"still {a.phase}{'/' + str(a.leg()) if a.airborne else ''} when the session ended")
