"""Controller command menus and radio phraseology for KSFO (Ground, Tower, Approach, Departure).

Only commands Infinite Flight's controller UI can send: the ATC menus (reference/if-atc-menus.md,
IF 21.1) plus what the current IFATC manual documents in the app beyond those menus: "radar contact"
reply to a check-in (6.4.4), drag & taxi routes (2.2.2), drag & vector approach clearances with an
intercept heading and "maintain until established" (6.11), holds at a fix and exit hold (6.7.3),
direct-to (6.16) and "fly runway heading" on a go-around (3.5.4). No free-text instructions: give way
names a side, not a callsign; sequencing names where the traffic to follow is, not who it is.

Values are restricted to the menus: headings 010-360 in 10 degree steps (MAGNETIC), altitudes
1,000-9,500 ft in 500 ft steps, 10,000-17,000 ft, FL180-FL600 in 1,000 ft steps, speeds
100-400 kt in 10 kt steps.

A command is a dict: {"cs": callsign, "cmd": name, **args}. In a controller:
    C.vector("UAL123", heading=250, altitude=4000)
    C.cleared_approach("UAL123", approach="ils", runway="28R", altitude=3000)
"""
from . import world as W

G, T, R = "ground", "tower", "radar"
ANY = (G, T, R)

ALTITUDES = sorted(set(list(range(1000, 10000, 500)) + list(range(10000, 18000, 1000))
                       + list(range(18000, 60001, 1000))))
HEADINGS = list(range(10, 361, 10))
SPEEDS = list(range(100, 410, 10))
APPROACH_SPEEDS = {160: 4, 170: 5, 180: 6}
FACILITIES = ("ground", "tower", "approach", "departure", "center")
APPROACHES = ("ils", "gps", "rnav")
RUNWAYS = tuple(sorted(W.RUNWAYS))
GO_AROUND_THEN = ("runway_heading",)
FOLLOW = ("final", "left_base", "right_base", "left_downwind", "right_downwind")

SPEC = {
    # ---- shared ----
    "contact_frequency": (ANY, ["facility"], {}, "Contact other frequency > Facility [XX]"),
    "freq_change_approved": (ANY, [], {}, "Contact other frequency > Frequency change approved"),
    "hold_position": ((G, T), [], {}, "Hold position"),
    "hold_short": ((G, T), ["runway"], {}, "Hold short > Runway [XX]"),
    "cross_runway": ((G, T), ["runway"], {"expedite": False},
                     "Cross runway > Runway [XX] > Send | Expedite (2.3)"),
    # ---- ground ----
    "pushback_approved": ((G,), [], {"expect_runway": None}, "Pushback approved > Send | Runway [XX]"),
    "taxi_to_runway": ((G,), ["runway"], {"contact_tower": False, "via": None},
                       "Taxi to > Runway [XX] (drag & taxi route via taxiways, manual 2.2.2)"),
    "taxi_to_parking": ((G,), [], {"via": None}, "Taxi to > Parking (drag & taxi route, manual 2.2.4)"),
    "continue_taxi": ((G,), [], {}, "Continue taxi"),
    "give_way": ((G,), ["to"], {"runway": None},
                 "Give way to > Aircraft exiting runway > Runway [XX] | Aircraft on your left | on your right | ahead "
                 "(2.2.1); the pilot stops short of that traffic's path and continues on their own once it has passed"),
    # ---- tower: departures ----
    "line_up_and_wait": ((T,), ["runway"], {}, "Line up and wait > Runway [XX]"),
    "cleared_takeoff": ((T,), ["runway"], {"immediate": False}, "Cleared for takeoff > Runway [XX] > Send | Request immediate takeoff"),
    "cancel_takeoff": ((T,), [], {}, "Cancel takeoff clearance"),
    "departure_sequence": ((T,), ["number"], {}, "Departure sequencing > Number N"),
    # ---- tower: arrivals ----
    "exit_runway": ((T,), [], {"side": "any", "expedite": None, "then": None},
                    "Exit runway > Left|Right|Any > Send|Expedite... (then: hold_short | cross, 3.5.3)"),
    "cleared_to_land": ((T,), ["runway"], {"number": 1, "follow": None},
                        "Cleared to land > Number 1 > Runway [XX] | Number N > Traffic to follow is on final|..."),
    "sequence": ((T,), ["number", "follow"], {}, "Sequencing > Number N > Traffic to follow is on final|base|downwind"),
    "cancel_landing_clearance": ((T,), [], {}, "Pattern instructions > Cancel landing clearance"),
    "go_around": ((T,), [], {"then": "runway_heading"},
                  "Go around > fly runway heading (3.5.4; the menu's make left/right traffic is pattern work, not modelled)"),
    # ---- speed (tower + radar) ----
    "approach_speed": ((T, R), ["kts"], {}, "Request speed > Approach speed > 160/170/180 kts until 4/5/6 mile final"),
    "speed_at_discretion": ((T, R), [], {}, "Request speed > Speed at your discretion"),
    "maintain_present_speed": ((T, R), [], {}, "Request speed > Maintain present speed"),
    "slowest_practical_speed": ((T, R), [], {}, "Request speed > Maintain slowest practical speed"),
    "best_forward_speed": ((T, R), [], {}, "Request speed > Maintain best forward speed"),
    "adjust_speed_to_follow": ((T, R), [], {}, "Request speed > Adjust speed to follow"),
    "make_360": ((T, R), ["direction"], {}, "Request 360 > Make left|right 360"),
    # ---- radar: approach + departure ----
    "radar_contact": ((R,), [], {}, "Check-in reply > Radar contact (manual 6.4.4)"),
    "expect_approach": ((R,), ["approach", "runway"], {"then": "continue_as_filed"},
                        "Expect approach > ILS|GPS|RNAV > Runway [XX] > Continue as filed | Expect vectors"),
    "vector": ((R,), [], {"heading": None, "altitude": None},
               "Vectoring instructions > Altitude and heading | Altitude | Heading"),
    "continue_as_filed": ((R,), [], {}, "Vectoring instructions > Continue as filed"),
    "resume_own_navigation": ((R,), [], {}, "Vectoring instructions > Resume own navigation"),
    "direct_to": ((R,), ["fix"], {}, "Drag & vector over a fix on the flight plan > Direct to (manual 6.16)"),
    "altitude_at_discretion": ((R,), [], {}, "Misc > Altitude at discretion"),
    "expedite_altitude": ((R,), [], {}, "Misc > Expedite altitude change"),
    "maintain_speed": ((R,), ["kts"], {"mode": None}, "Request speed > Maintain speed > N kts > Send | Or greater | Or below"),
    "hold_present_position": ((R,), ["turns", "altitude"], {}, "Request hold > Present position > Left|Right turns > altitude"),
    "hold_at_fix": ((R,), ["fix", "altitude"], {"turns": "right"}, "Drag & vector over a fix on the flight plan > Hold > turns > altitude (manual 6.7.3)"),
    "exit_hold": ((R,), [], {}, "Exit hold (manual 6.7.3)"),
    "cleared_approach": ((R,), ["approach", "runway"], {"heading": None, "altitude": None},
                         "(manual 6.10 / 6.11, drag & vector) [fly heading X,] [maintain Y until established,] cleared ILS|GPS|RNAV approach runway [XX]"),
}

ANSWERS = {
    "request_pushback": {"pushback_approved", "hold_position"},
    "request_taxi": {"taxi_to_runway", "hold_position"},
    "ground_checkin": {"taxi_to_parking", "hold_position", "give_way", "cross_runway", "hold_short"},
    "ready_for_departure": {"hold_short", "line_up_and_wait", "cleared_takeoff", "departure_sequence"},
    "tower_checkin": {"cleared_to_land", "sequence", "go_around"},
    "request_freq_change": {"freq_change_approved", "contact_frequency"},
    "check_in": {"radar_contact", "expect_approach", "vector", "continue_as_filed", "cleared_approach",
                 "hold_present_position", "hold_at_fix"},
    "holding_short": {"cross_runway", "hold_short", "hold_position"},
}
INFORMATIONAL = {"going_around", "rejecting_takeoff", "unable", "clear_of_runway", "crossed"}


class _C:
    def __getattr__(self, name):
        if name not in SPEC:
            raise AttributeError(name)

        def make(cs, **args):
            return {"cs": cs, "cmd": name, **args}
        return make


C = _C()


def positions(name):
    return SPEC[name][0]


def normalize(cmd):
    """(command with defaults, error or None). Checks the menu allows it."""
    if not isinstance(cmd, dict) or "cs" not in cmd or "cmd" not in cmd:
        return cmd, "malformed command"
    name = cmd["cmd"]
    if name not in SPEC:
        return cmd, f"'{name}' is not in the menu"
    _, req, opt, _ = SPEC[name]
    out = {"cs": cmd["cs"], "cmd": name}
    for r in req:
        if cmd.get(r) is None:
            return cmd, f"{name} requires '{r}'"
        out[r] = cmd[r]
    for k, d in opt.items():
        out[k] = cmd.get(k, d)
    extra = set(cmd) - {"cs", "cmd"} - set(req) - set(opt)
    if extra:
        return cmd, f"{name} has unknown args {sorted(extra)}"
    checks = [
        ("facility", lambda v: v in FACILITIES, f"facility must be one of {FACILITIES}"),
        ("heading", lambda v: v is None or v in HEADINGS, "heading must be 010-360 in 10 degree steps"),
        ("altitude", lambda v: v is None or v in ALTITUDES, "altitude must be a menu value"),
        ("direction", lambda v: v in ("left", "right"), "direction must be left|right"),
        ("turns", lambda v: v in ("left", "right"), "turns must be left|right"),
        ("number", lambda v: v is None or (isinstance(v, int) and 1 <= v <= 8), "number must be 1-8"),
        ("to", lambda v: v in ("exiting", "left", "right", "ahead"), "give way to exiting|left|right|ahead"),
        ("follow", lambda v: v is None or v in FOLLOW, f"follow (traffic to follow is on) must be one of {FOLLOW}"),
        ("side", lambda v: v in ("left", "right", "any"), "side must be left|right|any"),
        ("expedite", lambda v: v in (None, False, True, "final"), "expedite must be None|True|'final'"),
        ("mode", lambda v: v in (None, "or_greater", "or_below"), "mode must be None|or_greater|or_below"),
        ("runway", lambda v: v in RUNWAYS or (name == "give_way" and v is None), f"runway must be one of {RUNWAYS}"),
        ("expect_runway", lambda v: v is None or v in RUNWAYS, "expect_runway must be a runway"),
        ("approach", lambda v: v in APPROACHES, "approach must be ils|gps|rnav"),
        ("fix", lambda v: isinstance(v, str), "fix must be a fix name"),
    ]
    for k, ok, msg in checks:
        if k in out and not ok(out[k]):
            return cmd, msg
    if name == "go_around" and out["then"] not in GO_AROUND_THEN:
        return cmd, "go_around then must be runway_heading"
    if name == "give_way" and (out["to"] == "exiting") != (out["runway"] is not None):
        return cmd, "give way: 'exiting' needs the runway [XX]; left|right|ahead take no runway"
    if name == "cleared_to_land" and out["number"] > 1 and out["follow"] is None:
        return cmd, "cleared to land number 2-8 needs follow (where the traffic to follow is)"
    if name == "cleared_to_land" and out["number"] == 1 and out["follow"] is not None:
        return cmd, "cleared to land number 1 has no traffic to follow"
    if name == "exit_runway" and out["then"] not in (None, "hold_short", "cross"):
        return cmd, "exit_runway then must be None|hold_short|cross"
    if "via" in out and out["via"] is not None:
        if not isinstance(out["via"], (list, tuple)) or not out["via"] or any(v not in W.TAXIWAYS for v in out["via"]):
            return cmd, "via must be a list of taxiway names"
        out["via"] = list(out["via"])
    if name == "approach_speed" and out["kts"] not in APPROACH_SPEEDS:
        return cmd, "approach speed must be 160, 170 or 180"
    if name == "maintain_speed" and out["kts"] not in SPEEDS:
        return cmd, "speed must be 100-400 kts in 10 kt steps"
    if name == "expect_approach" and out["then"] not in ("continue_as_filed", "expect_vectors"):
        return cmd, "then must be continue_as_filed|expect_vectors"
    if name == "vector" and out["heading"] is None and out["altitude"] is None:
        return cmd, "vector needs a heading, an altitude, or both"
    if name == "sequence" and out["number"] < 2:
        return cmd, "sequence number must be 2-8"
    return out, None


def alt_txt(a):
    return f"flight level {a // 100}" if a >= W.K.TRANSITION_ALT else f"{a:,} ft"


def hdg_txt(h):
    return f"{int(h):03d}"


FOLLOW_TXT = {"final": "final", "left_base": "left base", "right_base": "right base",
              "left_downwind": "left downwind", "right_downwind": "right downwind"}
KIND_TXT = {"ils": "ILS", "gps": "RNAV (GPS)", "rnav": "RNAV (RNP)"}


def render(cmd, ac=None, leader=None):
    cs, n = cmd["cs"], cmd["cmd"]
    rw = cmd.get("runway")
    fac = lambda f: f"{'NorCal ' if f in ('approach', 'departure') else ('Oakland ' if f == 'center' else 'San Francisco ')}{f} {W.FREQS[f]}"  # noqa: E731
    if n == "contact_frequency":
        return f"{cs}, contact {fac(cmd['facility'])}."
    if n == "freq_change_approved":
        return f"{cs}, frequency change approved, good day."
    if n == "hold_position":
        return f"{cs}, hold position."
    if n == "hold_short":
        return f"{cs}, hold short runway {rw}."
    if n == "cross_runway":
        return f"{cs}, cross runway {rw}" + (", expedite." if cmd["expedite"] else ".")
    if n == "pushback_approved":
        return f"{cs}, pushback approved" + (f", expect runway {cmd['expect_runway']}." if cmd["expect_runway"] else ".")
    if n == "taxi_to_runway":
        via = f" via {', '.join(cmd['via'])}" if cmd["via"] else ""
        hs = ""
        if ac is not None and getattr(ac, "_cross_names", None):
            hs = "".join(f", hold short runway {r}" for r in ac._cross_names)
        return (f"{cs}, runway {rw}, taxi{via}{hs}" + (", contact tower when ready." if cmd["contact_tower"] else "."))
    if n == "taxi_to_parking":
        via = f" via {', '.join(cmd['via'])}" if cmd["via"] else ""
        hs = ""
        if ac is not None and getattr(ac, "_cross_names", None):
            hs = "".join(f", hold short runway {r}" for r in ac._cross_names)
        return f"{cs}, taxi to parking{via}{hs}."
    if n == "continue_taxi":
        return f"{cs}, continue taxi."
    if n == "give_way":
        return f"{cs}, give way to aircraft " + {"exiting": f"exiting runway {rw}", "left": "on your left",
                                                 "right": "on your right", "ahead": "ahead"}[cmd["to"]] + "."
    if n == "line_up_and_wait":
        return f"{cs}, runway {rw}, line up and wait."
    if n == "cleared_takeoff":
        return f"{cs}, runway {rw}, cleared for {'immediate ' if cmd['immediate'] else ''}takeoff."
    if n == "cancel_takeoff":
        return f"{cs}, hold position, cancel takeoff clearance."
    if n == "departure_sequence":
        return f"{cs}, number {cmd['number']} for departure."
    if n == "exit_runway":
        side = {"any": "", "left": " left", "right": " right"}[cmd["side"]]
        ex = {None: "exit" + side + " when able", False: "exit" + side + " when able", True: "expedite exit" + side,
              "final": "expedite exit" + side + ", traffic on final"}[cmd["expedite"]]
        then = ""
        if cmd["then"] == "hold_short" and ac is not None and ac.runway:
            then = f", hold short runway {W.CSPR.get(ac.runway, '')}"
        elif cmd["then"] == "cross" and ac is not None and ac.runway:
            then = f", cross runway {W.CSPR.get(ac.runway, '')}"
        return f"{cs}, {ex}{then}, contact ground {W.FREQS['ground']} when clear."
    if n == "cleared_to_land":
        num = cmd["number"] or 1
        txt = f"{cs}, " + (f"number {num}, traffic to follow is on {FOLLOW_TXT[cmd['follow']]}, " if num > 1 else "")
        return txt + f"runway {rw}, cleared to land."
    if n == "sequence":
        return f"{cs}, number {cmd['number']}, traffic to follow is on {FOLLOW_TXT[cmd['follow']]}."
    if n == "cancel_landing_clearance":
        return f"{cs}, cancel landing clearance."
    simple = {
        "speed_at_discretion": "speed at your discretion", "maintain_present_speed": "maintain present speed",
        "slowest_practical_speed": "maintain slowest practical speed", "best_forward_speed": "maintain best forward speed",
        "adjust_speed_to_follow": "adjust speed to follow traffic", "radar_contact": "radar contact",
        "continue_as_filed": "continue as filed", "resume_own_navigation": "resume own navigation",
        "altitude_at_discretion": "altitude at your discretion", "expedite_altitude": "expedite altitude change",
        "exit_hold": "exit the hold, continue as filed",
    }
    if n in simple:
        return f"{cs}, {simple[n]}."
    if n == "go_around":
        return f"{cs}, go around, fly runway heading."
    if n == "approach_speed":
        return f"{cs}, maintain {cmd['kts']} kts until {APPROACH_SPEEDS[cmd['kts']]} mile final."
    if n == "make_360":
        return f"{cs}, make a {cmd['direction']} 360."
    if n == "expect_approach":
        lead = "continue as filed, expect the" if cmd["then"] == "continue_as_filed" else "expect vectors for the"
        return f"{cs}, {lead} {KIND_TXT[cmd['approach']]} approach runway {rw}."
    if n == "vector":
        parts = []
        if cmd["heading"] is not None:
            turn = ""
            if ac is not None:
                d = W.ang_diff(ac.hdg, W.mag2true(cmd["heading"]))
                turn = "turn right " if d > 5 else ("turn left " if d < -5 else "")
            parts.append(f"{turn or 'fly '}heading {hdg_txt(cmd['heading'])}")
        if cmd["altitude"] is not None:
            verb = "climb" if ac is not None and cmd["altitude"] > ac.alt + 50 else "descend"
            if ac is not None and abs(cmd["altitude"] - ac.alt) <= 50:
                verb = "maintain"
                parts.append(f"maintain {alt_txt(cmd['altitude'])}")
            else:
                parts.append(f"{verb} and maintain {alt_txt(cmd['altitude'])}")
        return f"{cs}, " + ", ".join(parts) + "."
    if n == "maintain_speed":
        tail = {None: "", "or_greater": " or greater", "or_below": " or less"}[cmd["mode"]]
        return f"{cs}, maintain {cmd['kts']} kts{tail}."
    if n == "hold_present_position":
        return f"{cs}, hold present position, {cmd['turns']} turns, maintain {alt_txt(cmd['altitude'])}."
    if n == "hold_at_fix":
        return f"{cs}, hold at {cmd['fix']} as published, {cmd['turns']} turns, maintain {alt_txt(cmd['altitude'])}."
    if n == "direct_to":
        return f"{cs}, proceed direct {cmd['fix']}."
    if n == "cleared_approach":
        txt = f"{cs}, "
        if cmd["heading"] is not None:
            txt += f"fly heading {hdg_txt(cmd['heading'])}, "
        if cmd["altitude"] is not None:
            txt += f"maintain {alt_txt(cmd['altitude'])} until established, "
        return txt + f"cleared {KIND_TXT[cmd['approach']]} approach runway {rw}."
    return f"{cs}, {n}."


def render_pilot(kind, ac, data, atis):
    cs = ac.cs
    if kind == "request_pushback":
        return f"San Francisco Ground, {cs}, at stand {ac.stand}, request pushback with information {atis}."
    if kind == "request_taxi":
        return f"San Francisco Ground, {cs}, ready to taxi with information {atis}."
    if kind == "ready_for_departure":
        return f"San Francisco Tower, {cs}, holding short runway {ac.runway}, ready for departure."
    if kind == "tower_checkin":
        how = {"I": "ILS", "R": "RNAV", "H": "RNAV", "L": "localizer"}.get((ac.appr_cleared or "I")[0], "ILS")
        return f"San Francisco Tower, {cs}, {how} runway {ac.runway}."
    if kind == "check_in":
        if data.get("missed"):
            return f"NorCal Approach, {cs}, missed approach runway {data.get('runway')}, climbing {data['alt']:,} ft."
        if ac.intent == "departure":
            return f"NorCal Departure, {cs}, passing {data['alt']:,} ft for {data['target']:,} ft, {ac.sid}."
        req = KIND_TXT.get(ac.appr_req, "ILS")
        return (f"NorCal Approach, {cs}, {alt_txt(data['alt'])} descending via the {ac.star} arrival, "
                f"information {atis}, request {req} runway {ac.runway}.")
    if kind == "request_freq_change":
        return f"{cs}, request frequency change."
    if kind == "ground_checkin":
        where = f" at {data.get('at')}" if data.get("at") else ""
        hs = f", holding short runway {data['hs']}" if data.get("hs") else ""
        return f"San Francisco Ground, {cs}, clear of runway {data.get('rwy', ac.runway)}{where}{hs}, request taxi to parking."
    if kind == "holding_short":
        return f"{cs}, holding short runway {data.get('rwy')}."
    if kind == "clear_of_runway":
        return f"{cs}, clear of runway {data.get('rwy')}."
    if kind == "crossed":
        return f"{cs}, crossed runway {data.get('rwy')}."
    if kind == "going_around":
        return f"{cs}, going around."
    if kind == "rejecting_takeoff":
        return f"{cs}, rejecting takeoff."
    if kind == "unable":
        return f"{cs}, unable, {data.get('why', '')}."
    return f"{cs}, {kind}."
