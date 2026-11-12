"""Spot-check the KSFO data package against known published values and against
itself (CIFP vs OSM, projection error). Prints a report; exit code 1 on a failed check.

Usage:  python3 -I tools/validate.py

"Expected" values are from the FAA airport diagram / chart supplement / IAP
charts as commonly published for KSFO; they are hand-entered here and should be
re-checked against the current charts when the cycle changes.
"""
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ksfo import data as K  # noqa: E402
from ksfo.geo import inverse  # noqa: E402

fails = []


def check(label, ok, detail=""):
    print(f"  [{'ok' if ok else 'FAIL'}] {label}{(' — ' + detail) if detail else ''}")
    if not ok:
        fails.append(label)


def near(a, b, tol):
    return a is not None and abs(a - b) <= tol


print(f"KSFO data, CIFP cycle {K.CYCLE} effective {K.EFFECTIVE}")
print("\nAirport")
check("ARP 37-37-07.7N 122-22-31.5W", near(K.ARP[0], 37.618806, 2e-5) and near(K.ARP[1], -122.375417, 2e-5),
      f"{K.ARP[0]:.6f}, {K.ARP[1]:.6f}")
check("field elevation 13 ft", K.ELEVATION_FT == 13, str(K.ELEVATION_FT))
check("airport magnetic variation 14 E (CIFP/airport record)", K.MAGVAR == 14.0, f"{K.MAGVAR}")

print("\nRunways (expected: length ft, magnetic heading, true heading ~)")
EXP = {"10L": (11870, 104, 117.8), "28R": (11870, 284, 297.8), "10R": (11381, 104, 117.8), "28L": (11381, 284, 297.8),
       "01R": (8650, 14, 27.8), "19L": (8650, 194, 207.8), "01L": (7650, 14, 27.8), "19R": (7650, 194, 207.8)}
for rid, (ln, mh, th) in EXP.items():
    r = K.RUNWAYS.get(rid)
    check(f"RWY {rid}", r is not None and r["length_ft"] == ln and near(r["magnetic_heading"], mh, 0.5)
          and near(r["true_heading"], th, 0.3) and r["width_ft"] == 200,
          f"{r['length_ft']} ft x {r['width_ft']}, {r['magnetic_heading']}M / {r['true_heading']}T, "
          f"displaced {r['displaced_threshold_ft']} ft, thr elev {r['elevation_ft']} ft")

print("\nILS")
for rwy, ident, freq, gs in (("28L", "ISFO", 109.55, 2.85), ("28R", "IGWQ", 111.70, 3.00), ("19L", "ISIA", 108.90, 3.00)):
    i = K.ILS.get(rwy)
    check(f"ILS {rwy} I-{ident[1:]} {freq} GS {gs}", i and i["ident"] == ident and near(i["freq"], freq, 0.001)
          and near(i["gs_angle"], gs, 0.001),
          f"{i['ident']} {i['freq']} cat {i['category']} GS {i['gs_angle']} TCH {i['tch_ft']} crs {i['course_mag']}M" if i else "missing")

print("\nNavaids / fixes (lat, lon)")
FX = {"SFO/D": (37.619486, -122.373894, "SFO VOR/DME 115.8"), "OSI/D": (37.392500, -122.281303, "OSI 113.9"),
      "SAU/D": (37.855333, -122.522753, "SAU 116.2")}
for key, (la, lo, lab) in FX.items():
    f = K.FIX_RECORDS.get(key)
    if f:
        d = inverse(f["lat"], f["lon"], la, lo)[0]
        check(lab, d < 0.05 and (f.get("freq_mhz") is None or near(f["freq_mhz"], float(lab.split()[-1]), 0.01)),
              f"{f['lat']:.6f}, {f['lon']:.6f}  x {f['x']:.2f} y {f['y']:.2f} nm  freq {f.get('freq_mhz')}")
    else:
        check(lab, False, "missing")
for ident in ("SERFR", "BDEGA", "DYAMD", "ARCHI", "DUYET", "AXMUL", "GUTTS", "PORTE", "BERKS"):
    p = K.FIXES.get(ident)
    rec = next((v for v in K.FIX_RECORDS.values() if v["ident"] == ident), None)
    if p:
        print(f"  [info] {ident:6s} {rec['lat']:.5f} {rec['lon']:.5f}  x {p[0]:7.2f} y {p[1]:7.2f}  "
              f"{math.hypot(*p):5.1f} nm @ {math.degrees(math.atan2(p[0], p[1])) % 360:5.1f}T from ARP")
    else:
        check(ident, False, "missing")

print("\nProcedures")
SIDS = {"CIITY3", "GAPP7", "GNNRR3", "MOLEN9", "NIITE4", "SAHEY4", "SEGUL1", "SNTNA2", "SSTIK5", "TRUKN2", "WESLA5"}
STARS = {"ALWYS3", "BDEGA4", "BSR3", "DYAMD5", "MOD9", "PIRAT3", "PYE3", "RISTI1", "SERFR4", "STINS4", "STLER4",
         "WWAVS2", "YOSEM3"}
check("SIDs present (CIFP 2611 index)", set(K.SID_IDS) == SIDS, " ".join(K.SID_IDS))
check("STARs present (CIFP 2611 index)", set(K.STAR_IDS) == STARS, " ".join(K.STAR_IDS))
print("  approaches:", ", ".join(f"{a} ({K.PROCEDURES[a]['name']})" for a in K.APPROACH_IDS))
for pid in K.SID_IDS + K.STAR_IDS:
    p = K.PROCEDURES[pid]
    print(f"  [info] {pid:7s} rwy {','.join(p['runways']) or '-':20s} enroute: {' '.join(p['enroute_transitions']) or '-'}")
rf = {a: sum(1 for t in K.PROCEDURES[a]["transitions"] for l in t["legs"] if l["path_term"] == "RF") for a in K.APPROACH_IDS}
check("RF legs present on an RNAV (RNP) approach", any(v for a, v in rf.items() if K.PROCEDURES[a]["approach_type"] == "RNAV (RNP)"),
      ", ".join(f"{a}: {v}" for a, v in rf.items() if v) + "; RNP approaches without RF: "
      + ", ".join(a for a, v in rf.items() if not v and K.PROCEDURES[a]["approach_type"] == "RNAV (RNP)"))
for a in K.APPROACH_IDS:
    for t in K.PROCEDURES[a]["transitions"]:
        for l in t["legs"]:
            if l["path_term"] == "RF":
                c = K.FIX_RECORDS[l["arc_center_ref"]]
                end = K.FIX_RECORDS[l["fix_ref"]]
                r = math.hypot(end["x"] - c["x"], end["y"] - c["y"])
                check(f"{a} RF to {l['fix']}: coded radius matches centre-to-fix distance",
                      abs(r - l["arc_radius_nm"]) < 0.02, f"coded {l['arc_radius_nm']} nm, geometric {r:.3f} nm, turn {l['turn']}, centre {l['arc_center']}")
ils28l = K.approach_fixes("I28L")
check("ILS 28L FAF DUYET, IF HEMAN, MAP RW28L", ils28l["FAF"] == "DUYET" and "HEMAN" in ils28l["IF"] and ils28l["MAP"] == "RW28L", str(ils28l))
ils28r = K.approach_fixes("I28R")
check("ILS 28R FAF AXMUL, IF CEPIN", ils28r["FAF"] == "AXMUL" and "CEPIN" in ils28r["IF"], str(ils28r))
n_missed = sum(1 for a in K.APPROACH_IDS if any(l.get("missed") for t in K.PROCEDURES[a]["transitions"] for l in t["legs"]))
check("every approach has missed-approach legs", n_missed == len(K.APPROACH_IDS), f"{n_missed}/{len(K.APPROACH_IDS)}")
unres = [k for k, v in K.FIX_RECORDS.items() if v["kind"] == "UNRESOLVED"]
check("all procedure fixes resolved to a position", not unres, " ".join(unres))

print("\nSurface (OSM) vs CIFP")
# CIFP physical runway ends vs nearest OSM runway way end point
osm_ends = []
for r in K.SURFACE["runways_osm"]:
    if r["line"]:
        osm_ends += [r["line"][0], r["line"][-1]]
for rid, r in sorted(K.RUNWAYS.items()):
    p = r["runway_end"]
    d = min(math.hypot(p[0] - q[0], p[1] - q[1]) for q in osm_ends) * 6076.1
    check(f"RWY {rid} pavement end: CIFP vs OSM runway way end", d < 60, f"{d:.0f} ft apart")
st = K.SURFACE["stats"]
check("taxiway graph mostly connected", st["main_component_nodes"] >= 0.98 * st["nodes"],
      f"{st['main_component_nodes']}/{st['nodes']} nodes in main component, {st['components']} components")
for tw in ("A", "B", "C", "F", "M", "Z"):
    check(f"taxiway {tw} present in OSM", tw in K.TAXIWAYS)

print("\nProjection")
for a, b, tol in (("ARCHI/PC", "BDEGA/PC", 0.01), ("DYAMD/PC", "SERFR/PC", 0.1), ("ARCHI/PC", "PORTE/EA", 0.01)):
    f1, f2 = K.FIX_RECORDS[a], K.FIX_RECORDS[b]
    g, az, _ = inverse(f1["lat"], f1["lon"], f2["lat"], f2["lon"])
    x = math.hypot(f1["x"] - f2["x"], f1["y"] - f2["y"])
    gb = math.degrees(math.atan2(f2["x"] - f1["x"], f2["y"] - f1["y"])) % 360
    check(f"{a[:5]}-{b[:5]} distance: x/y vs geodesic", abs(x - g) < tol,
          f"geodesic {g:.3f} nm / {az:.2f}T, x/y {x:.3f} nm / grid {gb:.2f}, diff {abs(x - g) * 6076:.0f} ft, {((gb - az + 540) % 360 - 180):+.2f} deg")
far = max((v for v in K.FIX_RECORDS.values() if v.get("x") is not None), key=lambda v: math.hypot(v["x"], v["y"]))
print(f"  [info] farthest referenced fix {far['ident']} at {math.hypot(far['x'], far['y']):.0f} nm")

print("\nMVA")
for name, pt in (("over the field", (0, 0)), ("San Bruno Mtn area (-5,-3)", (-5, -3)), ("ARCHI", K.FIXES["ARCHI"])):
    s = K.mva_sector(*pt)
    print(f"  [info] {name}: MVA {K.mva(*pt)} ft (sector {s['name'] if s else '-'})")

print(f"\n{len(fails)} failed check(s)" + (": " + "; ".join(fails) if fails else ""))
sys.exit(1 if fails else 0)
