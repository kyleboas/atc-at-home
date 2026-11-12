"""Controller instructions and radio phraseology for KSFO (Ground, Tower, Approach, Departure),
after FAA Order JO 7110.65 (reference/faa-7110.65.txt; paragraph numbers below are from it).

Values follow the order, not a menu: headings to the degree (MAGNETIC, 001-360, 2-4-17),
altitudes in 100 ft steps from 1,000 ft (FL180 and up in flight levels), speeds as indicated
airspeed in 5 kt steps (5-7-1). Holds carry an expect-further-clearance time (4-6-1).

A command is a dict: {"cs": callsign, "cmd": name, **args}. In a controller:
    C.vector("UAL123", heading=255, altitude=4000)
    C.cleared_approach("UAL123", approach="ils", runway="28R", heading=250, altitude=3000)
"""
from . import world as W

G, T, R = "ground", "tower", "radar"
ANY = (G, T, R)

ALTITUDES = list(range(1000, 60001, 100))
HEADINGS = list(range(1, 361))
SPEEDS = list(range(100, 405, 5))
FACILITIES = ("ground", "tower", "approach", "departure", "center")
APPROACHES = ("ils", "gps", "rnav")
RUNWAYS = tuple(sorted(W.RUNWAYS))
GO_AROUND_THEN = ("runway_heading", "left_traffic", "right_traffic")
FOLLOW = ("final", "left_base", "right_base", "left_downwind", "right_downwind")

# name: (positions, required args, optional args with defaults, 7110.65 phraseology)
SPEC = {
    # ---- shared ----
    "contact_frequency": (ANY, ["facility"], {}, "CONTACT (facility) (frequency) (2-1-17)"),
    "freq_change_approved": (ANY, [], {}, "FREQUENCY CHANGE APPROVED (2-1-17)"),
    "hold_position": ((G, T), [], {}, "HOLD POSITION (3-7-2)"),
    "hold_short": ((G, T), ["runway"], {}, "HOLD SHORT OF RUNWAY (number) (3-7-2)"),
    "cross_runway": ((G, T), ["runway"], {"expedite": False},
                     "CROSS RUNWAY (number) [, EXPEDITE] (3-7-2)"),
    # ---- ground ----
    "pushback_approved": ((G,), [], {"expect_runway": None}, "PUSHBACK APPROVED [, EXPECT RUNWAY (number)]"),
    "taxi_to_runway": ((G,), ["runway"], {"contact_tower": False, "via": None},
                       "RUNWAY (number), TAXI VIA (route) [, HOLD SHORT OF RUNWAY (number)] (3-7-2)"),
    "taxi_to_parking": ((G,), [], {"via": None}, "TAXI TO (ramp/gate) VIA (route) (3-7-2)"),
    "continue_taxi": ((G,), [], {}, "CONTINUE TAXI (3-7-2)"),
    "follow": ((G,), ["traffic"], {},
               "FOLLOW (traffic) (3-7-2): the pilot stops short of that traffic's path while it conflicts and "
               "continues behind it once it has passed"),
    # ---- tower: departures ----
    "line_up_and_wait": ((T,), ["runway"], {}, "RUNWAY (number), LINE UP AND WAIT (3-9-4)"),
    "cleared_takeoff": ((T,), ["runway"], {"immediate": False}, "RUNWAY (number), CLEARED FOR [IMMEDIATE] TAKEOFF (3-9-10)"),
    "cancel_takeoff": ((T,), [], {}, "CANCEL TAKEOFF CLEARANCE (3-9-11)"),
    "departure_sequence": ((T,), ["number"], {}, "NUMBER (n) FOR DEPARTURE (3-9-1)"),
    # ---- tower: arrivals ----
    "exit_runway": ((T,), [], {"side": "any", "expedite": None, "then": None},
                    "TURN LEFT/RIGHT [, EXPEDITE] [, HOLD SHORT OF / CROSS RUNWAY (number)] (3-10-9)"),
    "cleared_to_land": ((T,), ["runway"], {"number": 1, "follow": None},
                        "[NUMBER (n), FOLLOW (traffic),] RUNWAY (number), CLEARED TO LAND (3-10-5)"),
    "sequence": ((T,), ["number", "follow"], {}, "NUMBER (n), FOLLOW (description and location of traffic) (3-8-1)"),
    "cancel_landing_clearance": ((T,), [], {}, "CANCEL LANDING CLEARANCE (3-10-5)"),
    "go_around": ((T,), [], {"then": "runway_heading", "runway": None},
                  "GO AROUND, FLY RUNWAY HEADING | MAKE LEFT/RIGHT TRAFFIC RUNWAY (number) (3-8-1, 3-10-9); "
                  "either way Tower hands the aircraft to Approach"),
    # ---- speed (tower + radar) ----
    "approach_speed": ((T, R), ["kts"], {"until_nm": 5},
                       "MAINTAIN (speed) KNOTS UNTIL (n) MILE FINAL (5-7-2); not closer than 5 mile final (5-7-1)"),
    "resume_normal_speed": ((T, R), [], {}, "RESUME NORMAL SPEED (5-7-4)"),
    "maintain_present_speed": ((T, R), [], {}, "MAINTAIN PRESENT SPEED (5-7-2)"),
    "slowest_practical_speed": ((T, R), [], {}, "MAINTAIN SLOWEST PRACTICAL SPEED (5-7-2)"),
    "maximum_forward_speed": ((T, R), [], {}, "MAINTAIN MAXIMUM FORWARD SPEED (5-7-2)"),
    "make_360": ((T, R), ["direction"], {}, "MAKE LEFT/RIGHT THREE-SIXTY (3-8-1, 5-6-2)"),
    # ---- radar: approach + departure ----
    "radar_contact": ((R,), [], {}, "RADAR CONTACT (5-3-2) in reply to a check-in"),
    "expect_approach": ((R,), ["approach", "runway"], {"then": "continue_as_filed"},
                        "EXPECT (approach) RUNWAY (number) [, EXPECT VECTORS] (4-7-10)"),
    "vector": ((R,), [], {"heading": None, "altitude": None},
               "TURN LEFT/RIGHT HEADING (degrees) | FLY HEADING; CLIMB/DESCEND AND MAINTAIN (altitude) (5-6-2, 4-5-7)"),
    "continue_as_filed": ((R,), [], {}, "RESUME (STAR) ARRIVAL / PROCEED ON COURSE (5-6-2)"),
    "resume_own_navigation": ((R,), [], {}, "RESUME OWN NAVIGATION (5-6-2)"),
    "direct_to": ((R,), ["fix"], {}, "PROCEED DIRECT (fix) (4-2-1, 5-6-2)"),
    "descend_via": ((R,), [], {}, "DESCEND VIA THE (STAR) ARRIVAL (4-5-7)"),
    "expedite_altitude": ((R,), [], {}, "EXPEDITE CLIMB/DESCENT (4-5-7)"),
    "maintain_speed": ((R,), ["kts"], {"mode": None},
                       "MAINTAIN (speed) KNOTS [OR GREATER] | DO NOT EXCEED (speed) KNOTS (5-7-2)"),
    "hold_present_position": ((R,), ["turns", "altitude", "efc"], {},
                              "HOLD (direction) OF PRESENT POSITION, (L/R) TURNS, MAINTAIN (altitude), "
                              "EXPECT FURTHER CLEARANCE (time) (4-6-1, 4-6-4); efc in session seconds"),
    "hold_at_fix": ((R,), ["fix", "altitude", "efc"], {"turns": "right"},
                    "HOLD (direction) OF (fix) AS PUBLISHED, MAINTAIN (altitude), EXPECT FURTHER CLEARANCE (time) "
                    "(4-6-1, 4-6-4); efc in session seconds"),
    "exit_hold": ((R,), [], {}, "LEAVE (fix), RESUME THE (STAR) ARRIVAL: clearance beyond the holding fix (4-6-2)"),
    "cleared_approach": ((R,), ["approach", "runway"], {"heading": None, "altitude": None},
                         "(n) MILES FROM (fix), [TURN LEFT/RIGHT HEADING (degrees),] MAINTAIN (altitude) UNTIL "
                         "ESTABLISHED ON THE LOCALIZER, CLEARED (approach) RUNWAY (number) APPROACH (5-9-4); "
                         "intercept per 5-9-1/5-9-2"),
    "cleared_to_airport": ((R,), ["airport", "altitude"], {},
                           "CLEARED TO (airport) AIRPORT VIA DIRECT, MAINTAIN (altitude) (4-2-1, 10-2-5): the "
                           "diversion an emergency aircraft asks for"),
}
AIRPORTS = ("KOAK",)

ANSWERS = {
    "request_pushback": {"pushback_approved", "hold_position"},
    "request_taxi": {"taxi_to_runway", "hold_position"},
    "ground_checkin": {"taxi_to_parking", "hold_position", "follow", "cross_runway", "hold_short"},
    "ready_for_departure": {"hold_short", "line_up_and_wait", "cleared_takeoff", "departure_sequence"},
    "tower_checkin": {"cleared_to_land", "sequence", "go_around"},
    "request_freq_change": {"freq_change_approved", "contact_frequency"},
    "check_in": {"radar_contact", "expect_approach", "vector", "continue_as_filed", "cleared_approach",
                 "hold_present_position", "hold_at_fix"},
    "holding_short": {"cross_runway", "hold_short", "hold_position"},
    # 10-2-5: Mayday fuel asks for a priority landing or a diversion to Oakland; a clearance to
    # Oakland grants the diversion, a clearance toward the runway works it in
    "fuel_emergency": {"cleared_to_airport", "vector", "direct_to", "exit_hold", "cleared_approach",
                       "cleared_to_land"},
}
INFORMATIONAL = {"going_around", "rejecting_takeoff", "unable", "clear_of_runway", "crossed", "minimum_fuel"}


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
    """(command with defaults, error or None). Checks the values are valid."""
    if not isinstance(cmd, dict) or "cs" not in cmd or "cmd" not in cmd:
        return cmd, "malformed command"
    name = cmd["cmd"]
    if name not in SPEC:
        return cmd, f"'{name}' is not a known instruction"
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
        ("heading", lambda v: v is None or v in HEADINGS, "heading must be a whole number of degrees 001-360"),
        ("altitude", lambda v: v is None or v in ALTITUDES, "altitude must be 1,000-60,000 ft in 100 ft steps"),
        ("efc", lambda v: isinstance(v, (int, float)) and v >= 0, "efc must be a session time in seconds"),
        ("traffic", lambda v: isinstance(v, str), "traffic must be the callsign to follow"),
        ("airport", lambda v: v in AIRPORTS, f"airport must be one of {AIRPORTS}"),
        ("direction", lambda v: v in ("left", "right"), "direction must be left|right"),
        ("turns", lambda v: v in ("left", "right"), "turns must be left|right"),
        ("number", lambda v: v is None or (isinstance(v, int) and 1 <= v <= 8), "number must be 1-8"),
        ("follow", lambda v: v is None or v in FOLLOW, f"follow (traffic to follow is on) must be one of {FOLLOW}"),
        ("side", lambda v: v in ("left", "right", "any"), "side must be left|right|any"),
        ("expedite", lambda v: v in (None, False, True, "final"), "expedite must be None|True|'final'"),
        ("mode", lambda v: v in (None, "or_greater", "or_below"), "mode must be None|or_greater|or_below"),
        ("runway", lambda v: v in RUNWAYS or (name == "go_around" and v is None), f"runway must be one of {RUNWAYS}"),
        ("expect_runway", lambda v: v is None or v in RUNWAYS, "expect_runway must be a runway"),
        ("approach", lambda v: v in APPROACHES, "approach must be ils|gps|rnav"),
        ("fix", lambda v: isinstance(v, str), "fix must be a fix name"),
    ]
    for k, ok, msg in checks:
        if k in out and not ok(out[k]):
            return cmd, msg
    if name == "go_around" and out["then"] not in GO_AROUND_THEN:
        return cmd, "go_around then must be runway_heading|left_traffic|right_traffic"
    if name == "go_around" and (out["then"] == "runway_heading") != (out["runway"] is None):
        return cmd, "go around: make left|right traffic needs the runway [XX]; fly runway heading takes none"
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
    if name in ("approach_speed", "maintain_speed") and out["kts"] not in SPEEDS:
        return cmd, "speed must be 100-400 kts in 5 kt steps"
    if name == "approach_speed" and not (isinstance(out["until_nm"], int) and 5 <= out["until_nm"] <= 15):
        return cmd, "approach speed until_nm must be a 5-15 mile final (no speed adjustment inside 5 miles, 5-7-1)"
    if name == "expect_approach" and out["then"] not in ("continue_as_filed", "expect_vectors"):
        return cmd, "then must be continue_as_filed|expect_vectors"
    if name == "vector" and out["heading"] is None and out["altitude"] is None:
        return cmd, "vector needs a heading, an altitude, or both"
    if name == "sequence" and out["number"] < 2:
        return cmd, "sequence number must be 2-8"
    return out, None


def alt_txt(a):
    return f"flight level {a // 100}" if a >= W.K.TRANSITION_ALT else f"{a:,} ft"


SESSION_START_Z = 15 * 3600      # sessions start at 1500Z (0800 local): times read on the clock


def efc_txt(t):
    s = int(SESSION_START_Z + t) % 86400
    return f"{s // 3600:02d}{(s % 3600) // 60:02d}Z"


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
    if n == "follow":
        return f"{cs}, follow {cmd['traffic']}."
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
    star = (ac.star if ac is not None and ac.star else "")
    simple = {
        "resume_normal_speed": "resume normal speed", "maintain_present_speed": "maintain present speed",
        "slowest_practical_speed": "maintain slowest practical speed",
        "maximum_forward_speed": "maintain maximum forward speed", "radar_contact": "radar contact",
        "continue_as_filed": f"resume the {star} arrival" if star else "proceed on course",
        "resume_own_navigation": "resume own navigation",
        "descend_via": f"descend via the {star} arrival" if star else "descend via the arrival",
        "expedite_altitude": "expedite " + ("climb" if ac is not None and ac.intent == "departure" else "descent"),
        "exit_hold": f"leave the hold, resume the {star} arrival" if star else "leave the hold, proceed on course",
    }
    if n == "cleared_to_airport":
        return f"{cs}, cleared to Oakland airport via direct, maintain {alt_txt(cmd['altitude'])}."
    if n in simple:
        return f"{cs}, {simple[n]}."
    if n == "go_around":
        if cmd["then"] == "runway_heading":
            return f"{cs}, go around, fly runway heading."
        return f"{cs}, go around, make {cmd['then'].split('_')[0]} traffic runway {rw}."
    if n == "approach_speed":
        return f"{cs}, maintain {cmd['kts']} knots until {cmd['until_nm']} mile final."
    if n == "make_360":
        return f"{cs}, make {cmd['direction']} three-sixty."
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
        if cmd["mode"] == "or_below":
            return f"{cs}, do not exceed {cmd['kts']} knots."
        return f"{cs}, maintain {cmd['kts']} knots" + (" or greater." if cmd["mode"] == "or_greater" else ".")
    if n == "hold_present_position":
        return (f"{cs}, hold present position, {cmd['turns']} turns, maintain {alt_txt(cmd['altitude'])}, "
                f"expect further clearance {efc_txt(cmd['efc'])}.")
    if n == "hold_at_fix":
        return (f"{cs}, hold at {cmd['fix']} as published, maintain {alt_txt(cmd['altitude'])}, "
                f"expect further clearance {efc_txt(cmd['efc'])}.")
    if n == "direct_to":
        return f"{cs}, proceed direct {cmd['fix']}."
    if n == "cleared_approach":
        txt = f"{cs}, "
        if cmd["heading"] is not None:
            txt += f"fly heading {hdg_txt(cmd['heading'])}, "
        if cmd["altitude"] is not None:
            txt += f"maintain {alt_txt(cmd['altitude'])} until established, "
        return txt + f"cleared {KIND_TXT[cmd['approach']]} runway {rw} approach."
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
    if kind == "minimum_fuel":
        return f"{cs}, minimum fuel."
    if kind == "fuel_emergency":
        return (f"Mayday, mayday, mayday, {cs}, minimum fuel emergency, {data.get('min', 0)} minutes of fuel "
                f"remaining, request priority landing runway {ac.runway} or diversion to Oakland.")
    if kind == "unable":
        return f"{cs}, unable, {data.get('why', '')}."
    return f"{cs}, {kind}."
