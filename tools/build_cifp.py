"""Extract KSFO from an FAA CIFP file (ARINC 424-18 fixed-width records).

Usage (from anywhere; the CIFP file is untrusted data, read only as text):
    python3 -I tools/build_cifp.py raw/cifp/FAACIFP18 ksfo/data

Writes into the output directory:
    airport.json     ARP, elevation, mag var, MSA, runways, localizers/GS, FAS path points
    procedures.json  every SID / STAR / approach, per transition, as ARINC legs
    fixes.json       every waypoint / navaid / runway threshold the procedures reference,
                     plus all KSFO terminal waypoints
    holds.json       holding patterns (HM/HA/HF legs)
Column numbers in comments are 1-based ARINC 424 columns.
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ksfo.geo import LocalFrame, dms, inverse  # noqa: E402

AIRPORT = "KSFO"
ICAO = "K2"


def col(rec, a, b=None):
    """1-based inclusive column slice."""
    return rec[a - 1:(b if b else a)]


def num(text, scale=1):
    text = text.strip()
    if not text or not re.fullmatch(r"[+-]?\d+", text):
        return None
    v = int(text) * scale
    return round(v, 6) if isinstance(v, float) else v


def magvar(text):
    """E0140 -> +14.0 (east positive), W0050 -> -5.0, T... -> 0 (true)."""
    text = text.strip()
    if not text or text[0] not in "EW":
        return None
    v = int(text[1:]) / 10
    return v if text[0] == "E" else -v


def altitude(text):
    t = text.strip()
    if not t:
        return None
    if t.startswith("FL"):
        n = num(t[2:])
        return {"ft": n * 100, "fl": n} if n is not None else {"raw": t}
    n = num(t)
    return {"ft": n} if n is not None else {"raw": t}


# ------------------------------------------------------------- code tables
SID_ROUTE = {"0": "engine-out SID", "1": "runway transition", "2": "common route",
             "3": "enroute transition", "4": "RNAV runway transition", "5": "RNAV common route",
             "6": "RNAV enroute transition", "F": "FMS runway transition", "M": "FMS common route",
             "S": "FMS enroute transition", "T": "vector SID runway transition",
             "V": "vector SID enroute transition"}
STAR_ROUTE = {"1": "enroute transition", "2": "common route", "3": "runway transition",
              "4": "RNAV enroute transition", "5": "RNAV common route", "6": "RNAV runway transition",
              "7": "profile descent enroute transition", "8": "profile descent common route",
              "9": "profile descent runway transition", "F": "FMS enroute transition",
              "M": "FMS common route", "S": "FMS runway transition"}
APP_ROUTE = {"A": "approach transition", "B": "localizer/backcourse", "D": "VOR/DME", "F": "FMS",
             "G": "IGS", "H": "RNAV (RNP)", "I": "ILS", "J": "GLS", "L": "LOC", "M": "MLS",
             "N": "NDB", "P": "GPS", "Q": "NDB/DME", "R": "RNAV (GPS)", "S": "VOR (DME)",
             "T": "TACAN", "U": "SDF", "V": "VOR", "W": "MLS A", "X": "LDA", "Y": "MLS B/C",
             "Z": "missed approach"}
APP_NAME = {"H": "RNAV (RNP)", "I": "ILS", "J": "GLS", "L": "LOC", "R": "RNAV (GPS)",
            "X": "LDA", "V": "VOR", "S": "VOR/DME", "D": "VOR/DME", "N": "NDB", "P": "GPS",
            "B": "LOC/BC", "Q": "NDB/DME", "U": "SDF", "G": "IGS", "T": "TACAN"}
LEG_TYPES = {
    "IF": "initial fix", "TF": "track to fix", "CF": "course to fix", "DF": "direct to fix",
    "FA": "fix to altitude", "FC": "track from fix for distance", "FD": "track from fix to DME distance",
    "FM": "from fix to manual termination (vectors)", "CA": "course to altitude",
    "CD": "course to DME distance", "CI": "course to intercept", "CR": "course to radial",
    "RF": "constant-radius arc to fix", "AF": "arc to fix (DME arc)", "VA": "heading to altitude",
    "VD": "heading to DME distance", "VI": "heading to intercept", "VM": "heading to manual termination (vectors)",
    "VR": "heading to radial", "PI": "procedure turn", "HA": "hold to altitude",
    "HF": "hold, single circuit (to fix)", "HM": "hold to manual termination"}
ALT_DESC = {
    " ": "at alt1", "@": "at alt1", "+": "at or above alt1", "-": "at or below alt1",
    "B": "between: at or below alt1 and at or above alt2",
    "C": "at or above alt2",
    "G": "at alt1; glideslope altitude alt2",
    "H": "at or above alt1; glideslope altitude alt2",
    "I": "at alt1; glideslope intercept altitude alt2",
    "J": "at or above alt1; glideslope intercept altitude alt2",
    "V": "at or above alt1 (step-down fix); alt2 = altitude on the vertical path",
    "X": "at alt1; vertical-angle altitude alt2",
    "Y": "at or below alt1; vertical-angle altitude alt2",
}
SPEED_DESC = {" ": "at", "@": "at", "+": "at or above", "-": "at or below"}
WPT_DESC1 = {"A": "airport as waypoint", "E": "essential waypoint", "F": "off-airway waypoint",
             "G": "runway as waypoint", "H": "heliport as waypoint", "N": "NDB as waypoint",
             "P": "phantom waypoint", "R": "non-essential waypoint", "T": "transition essential waypoint",
             "V": "VHF navaid as waypoint"}
WPT_DESC2 = {"B": "fly-over, end of route", "E": "end of route/segment", "U": "uncharted airway intersection",
             "Y": "fly-over"}
WPT_DESC4 = {"A": "IAF", "B": "IF", "C": "IAF with hold", "D": "IAF with final approach course fix",
             "E": "final end point", "F": "FAF", "H": "holding fix", "I": "final approach course fix",
             "M": "missed approach point"}


def constraint(desc, a1, a2):
    """Normalise an ARINC altitude constraint to {'type', 'min_ft', 'max_ft', ...}."""
    if a1 is None and a2 is None:
        return None
    f1 = a1.get("ft") if a1 else None
    f2 = a2.get("ft") if a2 else None
    c = {"code": desc, "meaning": ALT_DESC.get(desc, "unknown code"), "alt1": a1, "alt2": a2}
    if desc in (" ", "@", "I", "G", "X"):
        c.update(type="at", min_ft=f1, max_ft=f1)
    elif desc in ("+", "J", "H", "V"):
        c.update(type="at_or_above", min_ft=f1, max_ft=None)
    elif desc in ("-", "Y"):
        c.update(type="at_or_below", min_ft=None, max_ft=f1)
    elif desc == "B":
        c.update(type="between", min_ft=f2, max_ft=f1)
    elif desc == "C":
        c.update(type="at_or_above", min_ft=f2, max_ft=None)
    else:
        c.update(type="unknown", min_ft=f1, max_ft=f1)
    if desc in "GHIJ" and f2 is not None:
        c["glideslope_ft"] = f2
    return c


def fmt_alt(ft):
    return f"FL{ft // 100:03d}" if ft >= 18000 else f"{ft}"


def constraint_text(c):
    if not c:
        return ""
    t = c["type"]
    if t == "at":
        return f"{fmt_alt(c['min_ft'])}" if c["min_ft"] is not None else ""
    if t == "at_or_above":
        return f"{fmt_alt(c['min_ft'])}+"
    if t == "at_or_below":
        return f"{fmt_alt(c['max_ft'])}-"
    if t == "between":
        return f"{fmt_alt(c['min_ft'])}..{fmt_alt(c['max_ft'])}"
    return "?"


def course(text):
    t = text.strip()
    if not t:
        return None, False
    if t.endswith("T"):
        return int(t[:-1]) / 10, True
    n = num(t)
    return (n / 10 if n is not None else None), False


def runways_for(trans):
    """RW28B -> ['28L','28R']; RW10L -> ['10L']; 'ALL'/blank/enroute -> None."""
    m = re.fullmatch(r"RW(\d\d)([LRCB]?)", trans.strip())
    if not m:
        return None
    n, s = m.groups()
    if s == "B":
        return [n + "L", n + "R"]
    return [n + s]


# ------------------------------------------------------------- parsing
def read(path):
    with open(path, "r", encoding="ascii", errors="replace") as fh:
        for line in fh:
            line = line.rstrip("\r\n")
            if len(line) >= 120 and line[0] == "S":
                yield line


def main(cifp_path, out_dir):
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    header = {}
    with open(cifp_path, "r", encoding="ascii", errors="replace") as fh:
        for _ in range(5):
            h = fh.readline().strip()
            m = re.search(r"VOLUME (\d{4})\s+EFFECTIVE (\d+ \w+ \d{4})", h)
            if m:
                header.update(cycle=m.group(1), effective=m.group(2))
            m = re.search(r"(\d\d-[A-Z]{3}-\d{4})", h)
            if m and "file_created" not in header:
                header["file_created"] = m.group(1)

    enroute, navaid_vhf, navaid_ndb, terminal_wpt = {}, {}, {}, {}
    apt = None
    runways, locs, pathpts, msa = {}, {}, [], []
    proc_recs = []
    other_airport_wpts = {}

    for r in read(cifp_path):
        sec = col(r, 5)
        if sec == "E" and col(r, 6) == "A":                       # enroute waypoint
            ident = col(r, 14, 18).strip()
            enroute[(ident, col(r, 20, 21))] = {
                "ident": ident, "icao": col(r, 20, 21), "kind": "enroute waypoint",
                "type_code": col(r, 27, 29).strip(), "lat": dms(col(r, 33, 41)), "lon": dms(col(r, 42, 51)),
                "magvar": magvar(col(r, 75, 79)), "name": col(r, 99, 123).strip()}
        elif sec == "D" and col(r, 22) in "01":
            ident = col(r, 14, 17).strip()
            sub = col(r, 6)
            if sub == " ":                                           # VHF navaid
                lat, lon = dms(col(r, 33, 41)), dms(col(r, 42, 51))
                dlat, dlon = dms(col(r, 56, 64)), dms(col(r, 65, 74))
                navaid_vhf[(ident, col(r, 20, 21))] = {
                    "ident": ident, "icao": col(r, 20, 21), "kind": "VHF navaid",
                    "class": col(r, 28, 32).strip(), "freq_mhz": (num(col(r, 23, 27)) or 0) / 100,
                    "lat": lat if lat is not None else dlat, "lon": lon if lon is not None else dlon,
                    "dme_lat": dlat, "dme_lon": dlon, "declination": magvar(col(r, 75, 79)),
                    "dme_elev_ft": num(col(r, 80, 84)), "name": col(r, 94, 123).strip()}
            elif sub == "B":                                         # enroute NDB
                navaid_ndb[(ident, col(r, 20, 21))] = {
                    "ident": ident, "icao": col(r, 20, 21), "kind": "NDB",
                    "freq_khz": (num(col(r, 23, 27)) or 0) / 10, "class": col(r, 28, 32).strip(),
                    "lat": dms(col(r, 33, 41)), "lon": dms(col(r, 42, 51)),
                    "magvar": magvar(col(r, 75, 79)), "name": col(r, 94, 123).strip()}
        elif sec == "P":
            arpt, sub = col(r, 7, 10).strip(), col(r, 13)
            if sub == "C":
                ident = col(r, 14, 18).strip()
                rec = {"ident": ident, "icao": col(r, 20, 21), "kind": "terminal waypoint",
                       "airport": arpt, "type_code": col(r, 27, 29).strip(),
                       "lat": dms(col(r, 33, 41)), "lon": dms(col(r, 42, 51)),
                       "magvar": magvar(col(r, 75, 79)), "name": col(r, 99, 123).strip()}
                if arpt == AIRPORT:
                    terminal_wpt[ident] = rec
                else:
                    other_airport_wpts[(arpt, ident)] = rec
                continue
            if sub == "N":                                           # terminal NDB
                ident = col(r, 14, 17).strip()
                navaid_ndb[(ident, col(r, 20, 21))] = {
                    "ident": ident, "icao": col(r, 20, 21), "kind": "terminal NDB", "airport": arpt,
                    "freq_khz": (num(col(r, 23, 27)) or 0) / 10, "lat": dms(col(r, 33, 41)),
                    "lon": dms(col(r, 42, 51)), "name": col(r, 94, 123).strip()}
                continue
            if arpt != AIRPORT:
                continue
            if sub == "A":
                apt = {"ident": AIRPORT, "iata": col(r, 14, 16).strip(), "name": col(r, 94, 123).strip(),
                       "lat": dms(col(r, 33, 41)), "lon": dms(col(r, 42, 51)),
                       "magvar": magvar(col(r, 52, 56)), "elevation_ft": num(col(r, 57, 61)),
                       "longest_runway_ft": (num(col(r, 28, 30)) or 0) * 100,
                       "speed_limit_kt": num(col(r, 62, 64)), "speed_limit_alt_ft": num(col(r, 23, 27)),
                       "transition_altitude_ft": num(col(r, 71, 75)), "transition_level_ft": num(col(r, 76, 80)),
                       "datum": col(r, 87, 89).strip()}
            elif sub == "G" and col(r, 22) in "01":
                ident = col(r, 14, 18).strip()
                runways[ident[2:]] = {
                    "ident": ident[2:], "length_ft": num(col(r, 23, 27)),
                    "magnetic_bearing": (num(col(r, 28, 31)) or 0) / 10,
                    "threshold_lat": dms(col(r, 33, 41)), "threshold_lon": dms(col(r, 42, 51)),
                    "gradient_pct": num(col(r, 52, 55), 0.001),
                    "ellipsoid_height_m": num(col(r, 61, 66), 0.1),
                    "threshold_elevation_ft": num(col(r, 67, 71)),
                    "displaced_threshold_ft": num(col(r, 72, 75)) or 0,
                    "tch_ft": num(col(r, 76, 77)), "width_ft": num(col(r, 78, 80)),
                    "tch_source": col(r, 81).strip(),
                    "localizer": col(r, 82, 85).strip() or None,
                    "ils_category": col(r, 86).strip() or None}
            elif sub == "I" and col(r, 22) in "01":
                ident = col(r, 14, 17).strip()
                locs[ident] = {
                    "ident": ident, "ils_category": col(r, 18).strip(),
                    "freq_mhz": (num(col(r, 23, 27)) or 0) / 100, "runway": col(r, 28, 32).strip()[2:],
                    "loc_lat": dms(col(r, 33, 41)), "loc_lon": dms(col(r, 42, 51)),
                    "loc_magnetic_bearing": (num(col(r, 52, 55)) or 0) / 10,
                    "gs_lat": dms(col(r, 56, 64)), "gs_lon": dms(col(r, 65, 74)),
                    "loc_distance_from_rwy_end_ft": num(col(r, 75, 78)),
                    "gs_distance_from_threshold_ft": num(col(r, 80, 83)),
                    "loc_width_deg": num(col(r, 84, 87), 0.01),
                    "gs_angle_deg": num(col(r, 88, 90), 0.01),
                    "station_declination": magvar(col(r, 91, 95)),
                    "gs_tch_ft": num(col(r, 96, 97)), "gs_elevation_ft": num(col(r, 98, 102))}
            elif sub == "P" and col(r, 27) == "1":
                pathpts.append({
                    "approach": col(r, 14, 19).strip(), "runway": col(r, 20, 24).strip()[2:],
                    "ref_path_id": col(r, 33, 36).strip(),
                    "ltp_lat": dms(col(r, 38, 48)), "ltp_lon": dms(col(r, 49, 60)),
                    "ltp_ellipsoid_height_m": num(col(r, 61, 66), 0.1),
                    "glide_path_angle_deg": num(col(r, 67, 70), 0.01),
                    "fpap_lat": dms(col(r, 71, 81)), "fpap_lon": dms(col(r, 82, 93)),
                    "course_width_m": num(col(r, 94, 98), 0.01),
                    "tch": num(col(r, 103, 108), 0.1), "tch_units": col(r, 109)})
            elif sub == "S" and col(r, 39) in "01":
                sectors = []
                for i in range(7):
                    s = col(r, 43 + 11 * i, 53 + 11 * i)
                    if not s.strip():
                        continue
                    sectors.append({"from_deg": num(s[0:3]), "to_deg": num(s[3:6]),
                                    "altitude_ft": (num(s[6:9]) or 0) * 100, "radius_nm": num(s[9:11])})
                msa.append({"center": col(r, 14, 18).strip(), "center_section": col(r, 21, 22),
                            "multiple_code": col(r, 23).strip(), "sectors": sectors,
                            "bearing_reference": "magnetic" if col(r, 120) == "M" else "true"})
            elif sub in "DEF" and col(r, 39) in "01":
                proc_recs.append(r)

    if apt is None:
        sys.exit("KSFO airport record not found")
    frame = LocalFrame(apt["lat"], apt["lon"])

    def xy(lat, lon):
        x, y = frame.to_xy(lat, lon)
        return round(x, 4), round(y, 4)

    # ----------------------------------------------------- runways
    for rid, rw in runways.items():
        rw["threshold_x"], rw["threshold_y"] = xy(rw["threshold_lat"], rw["threshold_lon"])
    pairs = {}
    for rid in runways:
        n = int(rid[:2])
        opp_n = (n + 18 - 1) % 36 + 1
        opp_s = {"L": "R", "R": "L", "C": "C", "": ""}[rid[2:]]
        opp = f"{opp_n:02d}{opp_s}"
        if opp in runways:
            pairs[rid] = opp
    for rid, rw in runways.items():
        opp = runways.get(pairs.get(rid))
        rw["opposite"] = pairs.get(rid)
        if opp:
            d, az, _ = inverse(rw["threshold_lat"], rw["threshold_lon"], opp["threshold_lat"], opp["threshold_lon"])
            rw["true_bearing"] = round(az, 2)
            rw["threshold_to_threshold_ft"] = round(d * 6076.11549)
            rw["implied_magvar"] = round(((az - rw["magnetic_bearing"]) + 180) % 360 - 180, 2)
            # physical runway end (start of pavement) = threshold moved back by displacement
            from ksfo.geo import direct
            back = (az + 180) % 360
            plat, plon = direct(rw["threshold_lat"], rw["threshold_lon"], back, rw["displaced_threshold_ft"] / 6076.11549)
            rw["runway_end_lat"], rw["runway_end_lon"] = plat, plon
            rw["runway_end_x"], rw["runway_end_y"] = xy(plat, plon)
    for loc in locs.values():
        loc["loc_x"], loc["loc_y"] = xy(loc["loc_lat"], loc["loc_lon"])
        if loc["gs_lat"] is not None:
            loc["gs_x"], loc["gs_y"] = xy(loc["gs_lat"], loc["gs_lon"])
        rw = runways.get(loc["runway"])
        if rw:
            loc["true_course"] = rw.get("true_bearing")
    for p in pathpts:
        p["ltp_x"], p["ltp_y"] = xy(p["ltp_lat"], p["ltp_lon"])

    # ----------------------------------------------------- fix resolution
    used = {}

    def resolve(ident, icao, section):
        ident = ident.strip()
        if not ident:
            return None
        key = f"{ident}/{section.strip() or '?'}"
        if key in used:
            return key
        rec = None
        if section == "PC":
            rec = terminal_wpt.get(ident)
        elif section == "EA":
            rec = enroute.get((ident, icao))
        elif section == "D ":
            rec = navaid_vhf.get((ident, icao))
        elif section in ("DB", "PN"):
            rec = navaid_ndb.get((ident, icao))
        elif section == "PG":
            rw = runways.get(ident[2:])
            if rw:
                rec = {"ident": ident, "icao": icao, "kind": "runway threshold",
                       "lat": rw["threshold_lat"], "lon": rw["threshold_lon"],
                       "elevation_ft": rw["threshold_elevation_ft"]}
        elif section == "PI":
            lc = locs.get(ident)
            if lc:
                rec = {"ident": ident, "icao": icao, "kind": "localizer", "lat": lc["loc_lat"],
                       "lon": lc["loc_lon"], "freq_mhz": lc["freq_mhz"], "runway": lc["runway"]}
        elif section == "PA":
            rec = {"ident": ident, "icao": icao, "kind": "airport", "lat": apt["lat"], "lon": apt["lon"]}
        if rec is None:
            used[key] = {"ident": ident, "icao": icao, "section": section, "kind": "UNRESOLVED",
                         "lat": None, "lon": None}
            return key
        rec = dict(rec)
        rec["section"] = section
        rec["x"], rec["y"] = xy(rec["lat"], rec["lon"])
        used[key] = rec
        return key

    for ident in terminal_wpt:                    # all KSFO terminal waypoints
        resolve(ident, ICAO, "PC")

    # ----------------------------------------------------- procedures
    procs = {}
    holds = []
    for r in proc_recs:
        kind = {"D": "SID", "E": "STAR", "F": "APPROACH"}[col(r, 13)]
        pid = col(r, 14, 19).strip()
        rtype = col(r, 20)
        trans = col(r, 21, 25).strip()
        fix_id = col(r, 30, 34).strip()
        fix_sec = col(r, 37, 38)
        fix_key = resolve(fix_id, col(r, 35, 36), fix_sec) if fix_id else None
        nav_id = col(r, 51, 54).strip()
        nav_key = resolve(nav_id, col(r, 55, 56), col(r, 79, 80)) if nav_id else None
        ctr_id = col(r, 107, 111).strip()
        pt = col(r, 48, 49)
        ctr_key = resolve(ctr_id, col(r, 113, 114), col(r, 115, 116)) if (ctr_id and pt in ("RF", "AF")) else None
        crs, crs_true = course(col(r, 71, 74))
        dist_raw = col(r, 75, 78).strip()
        dist_nm = hold_min = None
        if dist_raw.startswith("T"):
            hold_min = num(dist_raw[1:], 0.1)
        elif dist_raw:
            dist_nm = num(dist_raw, 0.1)
        a1, a2 = altitude(col(r, 85, 89)), altitude(col(r, 90, 94))
        desc = col(r, 83)
        cons = constraint(desc, a1, a2)
        spd = num(col(r, 100, 102))
        spd_desc = col(r, 118)
        wd = col(r, 40, 43)
        rnp_raw = col(r, 45, 47).strip()
        rnp = None
        if re.fullmatch(r"\d{3}", rnp_raw):
            rnp = int(rnp_raw[:2]) * 10 ** (-int(rnp_raw[2]))
        leg = {
            "seq": int(col(r, 27, 29)),
            "path_term": pt, "path_term_name": LEG_TYPES.get(pt, "?"),
            "fix": fix_id or None, "fix_ref": fix_key,
            "waypoint_desc": wd,
            "flyover": wd[1] in "BY",
            "role": WPT_DESC4.get(wd[3]) if wd[3].strip() else None,
            "missed_approach_start": wd[2] == "M",
            "turn": {"L": "left", "R": "right", "E": "either"}.get(col(r, 44)),
            "turn_valid": col(r, 50) == "Y",
            "rnp_nm": rnp,
            "recommended_navaid": nav_id or None, "recommended_navaid_ref": nav_key,
            "arc_radius_nm": num(col(r, 57, 62), 0.001),
            "theta_deg": num(col(r, 63, 66), 0.1) if pt != "RF" else None,
            "rho_nm": num(col(r, 67, 70), 0.1),
            "course_deg": crs, "course_is_true": crs_true,
            "distance_nm": dist_nm, "hold_time_min": hold_min,
            "altitude": cons, "altitude_text": constraint_text(cons),
            "transition_altitude_ft": num(col(r, 95, 99)),
            "speed_kt": spd, "speed_type": SPEED_DESC.get(spd_desc, spd_desc) if spd else None,
            "vertical_angle_deg": num(col(r, 103, 106), 0.01),
            "arc_center": ctr_id if ctr_key else None, "arc_center_ref": ctr_key,
            "col107_111": ctr_id if (ctr_id and not ctr_key) else None,
        }
        leg = {k: v for k, v in leg.items() if v not in (None, False, "")}
        p = procs.setdefault(pid, {"id": pid, "kind": kind, "transitions": {}})
        tkey = (rtype, trans)
        t = p["transitions"].setdefault(tkey, {"route_type": rtype, "transition": trans or None, "legs": []})
        t["legs"].append(leg)
        if pt in ("HM", "HA", "HF"):
            holds.append({"fix": fix_id, "fix_ref": fix_key, "procedure": pid, "kind": kind,
                          "transition": trans or None, "leg_type": pt,
                          "inbound_course_deg": crs, "course_is_true": crs_true,
                          "turn": leg.get("turn"), "leg_nm": dist_nm, "leg_min": hold_min,
                          "altitude": cons, "altitude_text": constraint_text(cons),
                          "speed_kt": spd})

    out_procs = []
    for pid, p in sorted(procs.items(), key=lambda kv: ({"SID": 0, "STAR": 1, "APPROACH": 2}[kv[1]["kind"]], kv[0])):
        trs = []
        in_missed = False
        for (rtype, trans), t in p["transitions"].items():
            t["legs"].sort(key=lambda l: l["seq"])
            if p["kind"] == "SID":
                t["route_type_name"] = SID_ROUTE.get(rtype, "?")
            elif p["kind"] == "STAR":
                t["route_type_name"] = STAR_ROUTE.get(rtype, "?")
            else:
                t["route_type_name"] = APP_ROUTE.get(rtype, "?")
                in_missed = False
                for leg in t["legs"]:
                    if leg.get("missed_approach_start"):
                        in_missed = True
                    if in_missed:
                        leg["missed"] = True
            name = t["route_type_name"]
            if p["kind"] == "APPROACH":
                t["segment"] = "transition" if rtype == "A" else "final"
            elif "runway" in name:
                t["segment"] = "runway_transition"
            elif "enroute" in name:
                t["segment"] = "enroute_transition"
            elif "common" in name:
                t["segment"] = "common"
            else:
                t["segment"] = "other"
            t["runways"] = runways_for(t["transition"] or "")
            trs.append(t)
        rec = {"id": pid, "kind": p["kind"], "transitions": trs}
        if p["kind"] == "APPROACH":
            m = re.fullmatch(r"([A-Z])(\d\d[LRC]?)([A-Z]?)", pid)
            if m:
                letter, rwy, suffix = m.groups()
                rec["approach_type"] = APP_NAME.get(letter, letter)
                rec["runway"] = rwy
                rec["variant"] = suffix or None
                rec["name"] = f"{APP_NAME.get(letter, letter)}{(' ' + suffix) if suffix else ''} RWY {rwy}"
                if letter == "I":
                    rec["name"] = f"ILS{(' ' + suffix) if suffix else ''} RWY {rwy}"
            rec["has_rf_legs"] = any(l["path_term"] == "RF" for t in trs for l in t["legs"])
        else:
            rw = set()
            for t in trs:
                if t["runways"]:
                    rw.update(t["runways"])
            rec["runways"] = sorted(rw)
            rec["enroute_transitions"] = [t["transition"] for t in trs if t["segment"] == "enroute_transition"]
            rec["runway_transitions"] = [t["transition"] for t in trs if t["segment"] == "runway_transition"]
        out_procs.append(rec)

    airport = dict(apt)
    airport["x"], airport["y"] = 0.0, 0.0
    airport["source"] = {"name": "FAA CIFP", **header}
    airport["runways"] = [runways[k] for k in sorted(runways)]
    airport["localizers"] = [locs[k] for k in sorted(locs)]
    airport["fas_path_points"] = pathpts
    airport["msa"] = msa
    airport["projection"] = {
        "type": "azimuthal equidistant on WGS-84 (Vincenty), centred on the ARP",
        "origin_lat": apt["lat"], "origin_lon": apt["lon"], "units": "nautical miles",
        "axes": "x east, y true north at the ARP"}

    fixes = {k: v for k, v in sorted(used.items())}
    meta = {"source": {"name": "FAA CIFP", **header}, "airport": AIRPORT}
    (out / "airport.json").write_text(json.dumps(airport, indent=1))
    (out / "procedures.json").write_text(json.dumps({**meta, "procedures": out_procs}, indent=1))
    (out / "fixes.json").write_text(json.dumps({**meta, "fixes": fixes}, indent=1))
    (out / "holds.json").write_text(json.dumps({**meta, "holds": holds}, indent=1))
    unresolved = [k for k, v in fixes.items() if v["kind"] == "UNRESOLVED"]
    print(f"cycle {header}  runways {len(runways)}  localizers {len(locs)}  procedures {len(out_procs)}  "
          f"fixes {len(fixes)}  holds {len(holds)}  unresolved {unresolved}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
