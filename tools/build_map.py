"""Build ksfo/map.html: a self-contained map (data embedded) for checking the
KSFO data against the published charts.

Usage:
    python3 -I tools/build_map.py ksfo/data ksfo/map.html

Procedure geometry is approximated for display only: TF/CF/DF/IF are straight
lines between fixes, RF legs are true arcs about the coded centre fix, VA/CA/VI/
VM/FM legs (no terminating fix) are dashed stubs along the coded course (magnetic
course + the airport's 14.0 E variation), holds are racetracks with a 1.06 nm turn
radius (standard rate at ~200 kt) and the coded leg length/time.
"""
import json
import math
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def load(d, n):
    return json.load(open(Path(d) / n))


def r4(p):
    return [round(p[0], 4), round(p[1], 4)]


def fwd(p, brg, d):
    a = math.radians(brg)
    return (p[0] + d * math.sin(a), p[1] + d * math.cos(a))


def brg(a, b):
    return math.degrees(math.atan2(b[0] - a[0], b[1] - a[1])) % 360


def racetrack(fix, inbound, turn, leg_nm):
    """Points of a holding pattern: inbound leg ends at fix."""
    r = 1.06
    side = 1 if turn != "left" else -1
    out_brg = (inbound + 180) % 360
    pts = []
    # turn at the fix from inbound to outbound (centre abeam the fix on the holding side)
    c1 = fwd(fix, (inbound + 90 * side) % 360, r)
    for i in range(0, 19):
        ang = (inbound - 90 * side) % 360 + side * i * 10
        pts.append(fwd(c1, ang % 360, r))
    end_out = fwd(pts[-1], out_brg, leg_nm)
    c2 = fwd(end_out, (out_brg + 90 * side) % 360, r)
    for i in range(0, 19):
        ang = (out_brg - 90 * side) % 360 + side * i * 10
        pts.append(fwd(c2, ang % 360, r))
    pts.append(fix)
    return [r4(p) for p in pts]


def arc(start, end, centre, turn):
    a0 = brg(centre, start)
    a1 = brg(centre, end)
    rad = (math.dist(centre, start) + math.dist(centre, end)) / 2
    if turn == "right":
        sweep = (a1 - a0) % 360
    else:
        sweep = -((a0 - a1) % 360)
    n = max(8, int(abs(sweep) / 3))
    return [r4(fwd(centre, a0 + sweep * i / n, rad)) for i in range(n + 1)]


def build_geometry(proc, trans, fixes, runways, magvar, elev):
    """List of drawable segments for one transition."""
    segs = []
    pos, course = None, None
    if proc["kind"] == "SID" and trans.get("runways"):
        rw = runways.get(trans["runways"][0])
        if rw and rw.get("opposite") in runways:
            o = runways[rw["opposite"]]
            pos = (o["threshold_x"], o["threshold_y"])
            course = rw.get("true_bearing")
    if proc["kind"] == "SID" and pos is None and trans["segment"] == "common":
        pos = None

    def fxy(ref):
        f = fixes.get(ref) if ref else None
        return (f["x"], f["y"]) if f and f.get("x") is not None else None

    for i, leg in enumerate(trans["legs"]):
        pt = leg["path_term"]
        tgt = fxy(leg.get("fix_ref"))
        crs = leg.get("course_deg")
        tcrs = None
        if crs is not None:
            tcrs = crs if leg.get("course_is_true") else (crs + magvar) % 360
        style = "missed" if leg.get("missed") else "solid"
        if pt == "IF":
            pos = tgt
            course = None
            continue
        if pt in ("TF", "CF", "DF") and tgt:
            if pos is not None:
                segs.append({"leg": i, "style": style, "pts": [r4(pos), r4(tgt)]})
            elif tcrs is not None:          # CF with no known start: draw 3 nm of course into the fix
                segs.append({"leg": i, "style": style, "pts": [r4(fwd(tgt, tcrs + 180, 3)), r4(tgt)]})
            course = brg(pos, tgt) if pos and pos != tgt else tcrs
            pos = tgt
        elif pt == "RF" and tgt:
            ctr = fxy(leg.get("arc_center_ref"))
            if pos is not None and ctr is not None:
                segs.append({"leg": i, "style": style + " arc", "pts": arc(pos, tgt, ctr, leg.get("turn"))})
            pos = tgt
        elif pt in ("VA", "CA", "VI", "VM", "FM", "FA", "CI", "VD", "CD", "FC", "FD"):
            start = tgt if pt.startswith("F") and tgt else pos
            if start is None:
                continue
            if tcrs is None:
                tcrs = course or 0
            if pt in ("VM", "FM"):
                d = 6.0
            elif leg.get("altitude") and leg["altitude"].get("min_ft"):
                d = max(1.0, min(6.0, (leg["altitude"]["min_ft"] - elev) / 400))
            elif leg.get("distance_nm"):
                d = leg["distance_nm"]
            else:
                d = 2.0
            end = fwd(start, tcrs, d)
            segs.append({"leg": i, "style": style + " stub", "pts": [r4(start), r4(end)],
                         "label": f"{pt} {crs:.0f}°{'T' if leg.get('course_is_true') else ''}" if crs is not None else pt})
            pos, course = end, tcrs
        elif pt in ("HM", "HA", "HF") and tgt:
            leg_nm = leg.get("distance_nm") or (leg.get("hold_time_min") or 1.0) * 3.5
            segs.append({"leg": i, "style": style + " hold",
                         "pts": racetrack(tgt, tcrs if tcrs is not None else 0, leg.get("turn"), leg_nm)})
            pos = tgt
        elif tgt:
            if pos is not None:
                segs.append({"leg": i, "style": style, "pts": [r4(pos), r4(tgt)]})
            pos = tgt
    return segs


def main(data_dir, out_html):
    apt = load(data_dir, "airport.json")
    procs = load(data_dir, "procedures.json")
    fixes = load(data_dir, "fixes.json")["fixes"]
    surface = load(data_dir, "surface.json")
    try:
        mva = load(data_dir, "mva.json")
    except FileNotFoundError:
        mva = {"charts": []}
    runways = {r["ident"]: r for r in apt["runways"]}
    magvar, elev = apt["magvar"], apt["elevation_ft"]

    keep_leg = ("seq", "path_term", "fix", "fix_ref", "role", "turn", "course_deg", "course_is_true",
                "distance_nm", "hold_time_min", "altitude_text", "speed_kt", "speed_type", "arc_radius_nm",
                "arc_center", "rnp_nm", "vertical_angle_deg", "missed", "flyover", "recommended_navaid",
                "theta_deg", "rho_nm")
    out_procs = []
    for p in procs["procedures"]:
        q = {k: p.get(k) for k in ("id", "kind", "name", "runways", "has_rf_legs")}
        q["transitions"] = []
        for t in p["transitions"]:
            q["transitions"].append({
                "transition": t["transition"], "segment": t["segment"], "route_type_name": t["route_type_name"],
                "legs": [{k: l[k] for k in keep_leg if k in l} for l in t["legs"]],
                "geom": build_geometry(p, t, fixes, runways, magvar, elev)})
        out_procs.append(q)

    fx = {k: {kk: v.get(kk) for kk in ("ident", "kind", "section", "x", "y", "lat", "lon", "name", "freq_mhz", "class")}
          for k, v in fixes.items()}
    surf = {
        "nodes": {k: [v["x"], v["y"], v.get("holding_position"), v.get("stand")] for k, v in surface["nodes"].items()},
        "edges": [[e["a"], e["b"], e["name"], e["kind"], e["length_ft"], e["geometry"]] for e in surface["edges"]],
        "stands": [[s["ref"], s["x"], s["y"]] for s in surface["stands"]],
        "gates": [[g["ref"], g["x"], g["y"]] for g in surface["gates"]],
        "hold_lines": [h["line"] for h in surface["hold_lines"]],
        "aprons": [[a["name"], a["polygon"]] for a in surface["aprons"]],
        "terminals": [[a["name"], a["polygon"]] for a in surface["terminals"]],
        "tower": surface.get("tower"),
        "source": surface["source"],
    }
    mv = [{"chart": "FUS3" if "FUS3" in c["chart"] else "FUS5", "sectors": [
        {"name": s["name"], "mva": s["mva_ft"], "ring": s["exterior_xy"], "holes": s["holes_xy"]} for s in c["sectors"]]}
        for c in mva["charts"]]
    apt_small = {k: v for k, v in apt.items() if k not in ("fas_path_points",)}
    blob = {"airport": apt_small, "procedures": out_procs, "fixes": fx, "surface": surf, "mva": mv,
            "holds": load(data_dir, "holds.json")["holds"]}
    js = json.dumps(blob, separators=(",", ":")).replace("</", "<\\/")
    html = (HERE / "map_template.html").read_text().replace("/*__DATA__*/null", js)
    Path(out_html).write_text(html)
    print(f"wrote {out_html} ({len(html) // 1024} KiB)")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
