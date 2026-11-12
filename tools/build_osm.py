"""Build the KSFO surface (taxiway graph, stands, holding points, aprons) from an
Overpass JSON dump of OpenStreetMap aeroway features.

Usage:
    python3 -I tools/build_osm.py raw/osm/ksfo_aeroway.json ksfo/data/airport.json ksfo/data

The OSM file is untrusted data: it is only read with json.load. Output
(ksfo/data/surface.json) is derived from OpenStreetMap and is (c) OpenStreetMap
contributors, available under the Open Database License (ODbL) 1.0.

Graph construction
  * ways used as edges: aeroway=taxiway, taxilane, parking_position (stand lead-in
    lines) and runway (centre lines, so runway exits connect);
  * graph nodes: way end points, OSM nodes shared by two or more of those ways,
    holding-position nodes, and synthetic nodes where an aeroway=holding_position
    *line* crosses a taxiway segment (the segment is split there);
  * an edge is the polyline between consecutive graph nodes along one way; its
    name is the way's ref (or name), its length is the summed segment length in nm.
"""
import json
import math
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ksfo.geo import LocalFrame  # noqa: E402

EDGE_WAYS = {"taxiway", "taxilane", "parking_position", "runway"}


def seg_intersect(p1, p2, p3, p4):
    """Intersection of segments p1p2 and p3p4 -> (t along p1p2, point) or None."""
    d = (p2[0] - p1[0]) * (p4[1] - p3[1]) - (p2[1] - p1[1]) * (p4[0] - p3[0])
    if abs(d) < 1e-15:
        return None
    t = ((p3[0] - p1[0]) * (p4[1] - p3[1]) - (p3[1] - p1[1]) * (p4[0] - p3[0])) / d
    u = ((p3[0] - p1[0]) * (p2[1] - p1[1]) - (p3[1] - p1[1]) * (p2[0] - p1[0])) / d
    if 0 <= t <= 1 and 0 <= u <= 1:
        return t, (p1[0] + t * (p2[0] - p1[0]), p1[1] + t * (p2[1] - p1[1]))
    return None


def main(osm_path, airport_path, out_dir):
    raw = json.load(open(osm_path))
    apt = json.load(open(airport_path))
    frame = LocalFrame(apt["lat"], apt["lon"])
    els = raw["elements"]
    nodes = {}
    for e in els:                      # "out skel" repeats nodes without tags: keep the tagged copy
        if e["type"] == "node" and (e["id"] not in nodes or e.get("tags")):
            nodes[e["id"]] = e
    ways = [e for e in els if e["type"] == "way"]
    pos = {}

    def xy(nid):
        if nid not in pos:
            n = nodes[nid]
            pos[nid] = frame.to_xy(n["lat"], n["lon"])
        return pos[nid]

    def latlon(nid):
        n = nodes[nid]
        return n["lat"], n["lon"]

    edge_ways = [w for w in ways if w.get("tags", {}).get("aeroway") in EDGE_WAYS
                 and w.get("tags", {}).get("runway") != "displaced_threshold"
                 and all(n in nodes for n in w["nodes"])]
    # runway ways tagged displaced_threshold are the displaced part of the pavement:
    # keep them as edges too (the runway is continuous), but mark them.
    edge_ways += [w for w in ways if w.get("tags", {}).get("aeroway") == "runway"
                  and w.get("tags", {}).get("runway") == "displaced_threshold"
                  and all(n in nodes for n in w["nodes"])]

    # --- split taxiway segments where holding-position lines cross them
    synth = {}            # synthetic id -> (x, y, tags)
    next_id = -1
    hold_lines = [w for w in ways if w.get("tags", {}).get("aeroway") == "holding_position"
                  and all(n in nodes for n in w["nodes"])]
    seqs = {}
    for w in edge_ways:
        seqs[w["id"]] = list(w["nodes"])
    hold_line_hits = Counter()
    taxi_node_ids = {n for w in edge_ways if w["tags"].get("aeroway") in ("taxiway", "taxilane") for n in w["nodes"]}
    hold_vertex = {}      # existing taxiway vertex that a hold line passes through -> type
    for hl in hold_lines:
        for n in hl["nodes"]:
            if n in taxi_node_ids:
                hold_vertex[n] = hl["tags"].get("holding_position:type", "runway")
                hold_line_hits[hl["id"]] += 1
    for hl in hold_lines:
        if hold_line_hits[hl["id"]]:
            continue
        hpts = [xy(n) for n in hl["nodes"]]
        for w in edge_ways:
            if w["tags"].get("aeroway") not in ("taxiway", "taxilane"):
                continue
            seq = seqs[w["id"]]
            out = [seq[0]]
            for a, b in zip(seq, seq[1:]):
                pa = synth[a][:2] if a < 0 else xy(a)
                pb = synth[b][:2] if b < 0 else xy(b)
                hits = []
                for h1, h2 in zip(hpts, hpts[1:]):
                    r = seg_intersect(pa, pb, h1, h2)
                    if r and 1e-6 < r[0] < 1 - 1e-6:
                        hits.append(r)
                for t, p in sorted(hits):
                    synth[next_id] = (p[0], p[1], {"aeroway": "holding_position",
                                                   "holding_position:type": hl["tags"].get("holding_position:type", "runway"),
                                                   "osm_way": hl["id"]})
                    out.append(next_id)
                    next_id -= 1
                    hold_line_hits[hl["id"]] += 1
                out.append(b)
            seqs[w["id"]] = out

    def P(nid):
        return synth[nid][:2] if nid < 0 else xy(nid)

    use = Counter()
    for w in edge_ways:
        for n in set(seqs[w["id"]]):
            use[n] += 1
    hold_nodes = {e["id"] for e in els if e["type"] == "node" and e.get("tags", {}).get("aeroway") == "holding_position"}
    gate_nodes = {e["id"] for e in els if e["type"] == "node" and e.get("tags", {}).get("aeroway") in ("gate", "parking_position")}
    is_graph = set()
    for w in edge_ways:
        s = seqs[w["id"]]
        is_graph.update((s[0], s[-1]))
        for n in s:
            if use[n] > 1 or n < 0 or n in hold_nodes or n in gate_nodes or n in hold_vertex:
                is_graph.add(n)

    def nname(nid):
        return f"s{-nid}" if nid < 0 else f"n{nid}"

    g_nodes, g_edges = {}, []
    for w in edge_ways:
        tags = w["tags"]
        kind = tags["aeroway"]
        if kind == "runway":
            name = "RWY " + tags.get("ref", "?")
            if tags.get("runway") == "displaced_threshold":
                kind = "runway_displaced"
        elif kind == "parking_position":
            name = "stand " + tags.get("ref", "?")
        else:
            name = tags.get("ref") or tags.get("name") or None
        s = seqs[w["id"]]
        start = 0
        for i in range(1, len(s)):
            if s[i] in is_graph or i == len(s) - 1:
                pts = [P(n) for n in s[start:i + 1]]
                length = sum(math.dist(a, b) for a, b in zip(pts, pts[1:]))
                g_edges.append({"a": nname(s[start]), "b": nname(s[i]), "name": name, "kind": kind,
                                "osm_way": w["id"], "length_nm": round(length, 5),
                                "length_ft": round(length * 6076.11549, 1),
                                "geometry": [[round(x, 5), round(y, 5)] for x, y in pts]})
                for n in (s[start], s[i]):
                    if nname(n) not in g_nodes:
                        x, y = P(n)
                        rec = {"x": round(x, 5), "y": round(y, 5)}
                        if n > 0:
                            rec["lat"], rec["lon"] = latlon(n)
                            rec["osm_node"] = n
                            t = nodes[n].get("tags", {})
                            if t.get("aeroway") == "holding_position":
                                rec["holding_position"] = t.get("holding_position:type", "unspecified")
                            elif n in hold_vertex:
                                rec["holding_position"] = hold_vertex[n]
                            if t.get("aeroway") in ("gate", "parking_position") and t.get("ref"):
                                rec["gate_ref"] = t["ref"]
                        else:
                            rec["lat"], rec["lon"] = frame.to_latlon(x, y)
                            rec["holding_position"] = synth[n][2]["holding_position:type"]
                            rec["from_osm_hold_line"] = synth[n][2]["osm_way"]
                        g_nodes[nname(n)] = rec
                start = i

    # incident edge names per node, runway membership
    inc = defaultdict(set)
    deg = Counter()
    for e in g_edges:
        for k in ("a", "b"):
            deg[e[k]] += 1
            inc[e[k]].add(e["name"] or "(unnamed)")
    for nid, rec in g_nodes.items():
        rec["edges"] = sorted(inc[nid])
        rec["degree"] = deg[nid]

    # --- helpers to attach isolated stand lead-in lines to the nearest taxiway/taxilane
    SNAP_NM = 0.05                      # ~300 ft
    synth_counter = [0]

    def nearest_taxi(p):
        best = None
        for ei, e in enumerate(g_edges):
            if e["kind"] not in ("taxiway", "taxilane"):
                continue
            g = e["geometry"]
            for si, (q1, q2) in enumerate(zip(g, g[1:])):
                dx, dy = q2[0] - q1[0], q2[1] - q1[1]
                L2 = dx * dx + dy * dy
                t = 0.0 if L2 == 0 else max(0.0, min(1.0, ((p[0] - q1[0]) * dx + (p[1] - q1[1]) * dy) / L2))
                q = (q1[0] + t * dx, q1[1] + t * dy)
                d = math.dist(p, q)
                if best is None or d < best[0]:
                    best = (d, ei, si, t, q)
        return best

    def snap(entry, near):
        d, ei, si, t, q = near
        e = g_edges[ei]
        g = e["geometry"]
        synth_counter[0] += 1
        nid = f"j{synth_counter[0]}"
        lat, lon = frame.to_latlon(*q)
        g_nodes[nid] = {"x": round(q[0], 5), "y": round(q[1], 5), "lat": lat, "lon": lon, "synthetic_junction": True}
        g1 = g[:si + 1] + [[round(q[0], 5), round(q[1], 5)]]
        g2 = [[round(q[0], 5), round(q[1], 5)]] + g[si + 1:]
        e2 = dict(e)
        for part, geom, a, b in ((e, g1, e["a"], nid), (e2, g2, nid, e["b"])):
            ln = sum(math.dist(u, v) for u, v in zip(geom, geom[1:]))
            part.update(a=a, b=b, geometry=geom, length_nm=round(ln, 5), length_ft=round(ln * 6076.11549, 1))
        g_edges.append(e2)
        en = g_nodes[entry]
        link = math.dist((en["x"], en["y"]), q)
        g_edges.append({"a": entry, "b": nid, "name": "stand link", "kind": "stand_link", "synthetic": True,
                        "length_nm": round(link, 5), "length_ft": round(link * 6076.11549, 1),
                        "geometry": [[en["x"], en["y"]], [round(q[0], 5), round(q[1], 5)]]})
        for k in (entry, nid, nid, nid):
            deg[k] += 1
        inc[nid].update({e["name"] or "(unnamed)", "stand link"})
        inc[entry].add("stand link")
        return round(link * 6076.11549, 1)

    # --- stands: parking_position lead-in lines; stand point = the dead-end end
    stands = []
    for w in edge_ways:
        if w["tags"]["aeroway"] != "parking_position":
            continue
        s = seqs[w["id"]]
        a, b = nname(s[0]), nname(s[-1])
        snapped = None
        if deg[a] == 1 and deg[b] != 1:
            end, entry = a, b
        elif deg[b] == 1 and deg[a] != 1:
            end, entry = b, a
        elif deg[a] > 1 and deg[b] > 1:
            end, entry = b, a           # line joins two taxiways; treat the last node as the stand
        else:
            # isolated lead-in line: the end nearer a taxiway is the entry; snap it on
            da = nearest_taxi((g_nodes[a]["x"], g_nodes[a]["y"]))
            db = nearest_taxi((g_nodes[b]["x"], g_nodes[b]["y"]))
            end, entry, near = (b, a, da) if (da and (not db or da[0] <= db[0])) else (a, b, db)
            if near and near[0] <= SNAP_NM:
                snapped = snap(entry, near)
        g_nodes[end]["stand"] = w["tags"].get("ref")
        stands.append({"ref": w["tags"].get("ref"), "node": end, "entry_node": entry,
                       "x": g_nodes[end]["x"], "y": g_nodes[end]["y"],
                       "lat": g_nodes[end]["lat"], "lon": g_nodes[end]["lon"],
                       "connected": deg[entry] > 1 or snapped is not None,
                       "snapped_link_ft": snapped, "osm_way": w["id"]})
    for e in els:
        t = e.get("tags", {})
        if e["type"] == "node" and t.get("aeroway") == "parking_position":
            x, y = xy(e["id"])
            stands.append({"ref": t.get("ref"), "node": nname(e["id"]) if nname(e["id"]) in g_nodes else None,
                           "x": round(x, 5), "y": round(y, 5), "lat": e["lat"], "lon": e["lon"],
                           "connected": nname(e["id"]) in g_nodes, "osm_node": e["id"]})
    gates = []
    for e in els:
        t = e.get("tags", {})
        if e["type"] == "node" and t.get("aeroway") == "gate":
            x, y = xy(e["id"])
            gates.append({"ref": t.get("ref"), "x": round(x, 5), "y": round(y, 5), "lat": e["lat"],
                          "lon": e["lon"], "osm_node": e["id"]})

    for nid, rec in g_nodes.items():
        rec["edges"] = sorted(inc[nid])
        rec["degree"] = deg[nid]

    # --- connected components
    adj = defaultdict(set)
    for e in g_edges:
        adj[e["a"]].add(e["b"])
        adj[e["b"]].add(e["a"])
    comp, comps = {}, []
    for n in g_nodes:
        if n in comp:
            continue
        stack, members = [n], []
        comp[n] = len(comps)
        while stack:
            c = stack.pop()
            members.append(c)
            for m in adj[c]:
                if m not in comp:
                    comp[m] = len(comps)
                    stack.append(m)
        comps.append(members)
    comps.sort(key=len, reverse=True)
    main_comp = set(comps[0]) if comps else set()
    for n, rec in g_nodes.items():
        rec["main_component"] = n in main_comp

    def poly(w):
        return [[round(v, 5) for v in xy(n)] for n in w["nodes"] if n in nodes]

    aprons = [{"name": w["tags"].get("name"), "ref": w["tags"].get("ref"), "osm_way": w["id"], "polygon": poly(w)}
              for w in ways if w.get("tags", {}).get("aeroway") == "apron"]
    terminals = [{"name": w["tags"].get("name"), "osm_way": w["id"], "polygon": poly(w)}
                 for w in ways if w.get("tags", {}).get("aeroway") == "terminal"]
    runways_osm = [{"ref": w["tags"].get("ref"), "displaced": w["tags"].get("runway") == "displaced_threshold",
                    "width_m": w["tags"].get("width"), "length_m": w["tags"].get("length"),
                    "osm_way": w["id"], "line": poly(w)}
                   for w in ways if w.get("tags", {}).get("aeroway") == "runway"]
    hold_lines_out = [{"type": w["tags"].get("holding_position:type"), "osm_way": w["id"], "line": poly(w),
                       "taxiway_crossings": hold_line_hits[w["id"]]} for w in hold_lines]
    tower = [w for w in ways if w.get("tags", {}).get("aeroway") == "tower"]
    tower_xy = None
    if tower:
        pts = poly(tower[0])
        tower_xy = [round(sum(p[0] for p in pts) / len(pts), 5), round(sum(p[1] for p in pts) / len(pts), 5)]

    names = Counter(e["name"] for e in g_edges if e["kind"] in ("taxiway", "taxilane"))
    surface = {
        "source": {"name": "OpenStreetMap via Overpass API", "osm_base": raw.get("osm3s", {}).get("timestamp_osm_base"),
                   "license": "ODbL 1.0", "attribution": "(c) OpenStreetMap contributors",
                   "license_url": "https://www.openstreetmap.org/copyright",
                   "bbox": [37.595, -122.405, 37.645, -122.350]},
        "frame": "same local frame as airport.json (nm, x east, y north from the KSFO ARP)",
        "nodes": g_nodes, "edges": g_edges, "stands": stands, "gates": gates,
        "hold_lines": hold_lines_out, "aprons": aprons, "terminals": terminals, "runways_osm": runways_osm,
        "tower": tower_xy,
        "taxiway_names": sorted(n for n in names if n),
        "stats": {"nodes": len(g_nodes), "edges": len(g_edges), "components": len(comps),
                  "main_component_nodes": len(main_comp),
                  "unnamed_taxiway_edges": names.get(None, 0),
                  "taxiway_edges": sum(names.values()),
                  "stands": len(stands), "stands_snapped": sum(1 for x in stands if x.get("snapped_link_ft") is not None),
                  "stands_connected": sum(1 for x in stands if x.get("node") in main_comp), "stands_with_ref": sum(1 for s in stands if s["ref"]),
                  "gates": len(gates),
                  "holding_nodes": sum(1 for r in g_nodes.values() if "holding_position" in r),
                  "hold_lines": len(hold_lines), "hold_lines_matched": sum(1 for h in hold_lines if hold_line_hits[h["id"]])},
    }
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    (Path(out_dir) / "surface.json").write_text(json.dumps(surface, separators=(",", ":")))
    print(json.dumps(surface["stats"]))
    print("taxiway names:", " ".join(surface["taxiway_names"]))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], sys.argv[3])
