"""Convert FAA MVA chart AIXM 5.1 files (NorCal TRACON, NCT) to ksfo/data/mva.json.

Usage:
    python3 -I tools/build_mva.py ksfo/data/airport.json ksfo/data raw/mva/NCT_MVA_FUS3.xml raw/mva/NCT_MVA_FUS5.xml

Each AIXM Airspace becomes one sector: name, minimum vectoring altitude (ft MSL),
exterior ring and interior rings (holes), as lon/lat (CRS84, as published) and
local x/y nm. The XML is untrusted: parsed with xml.etree (expat; no external
entity resolution), only text values are read.
"""
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ksfo.geo import LocalFrame  # noqa: E402

AIXM = "{http://www.aixm.aero/schema/5.1}"
GML = "{http://www.opengis.net/gml/3.2}"


def ring(el, frame):
    pl = el.find(f".//{GML}posList")
    vals = [float(v) for v in pl.text.split()]
    lonlat = list(zip(vals[0::2], vals[1::2]))
    xy = [[round(c, 4) for c in frame.to_xy(lat, lon)] for lon, lat in lonlat]
    return [[round(lon, 5), round(lat, 5)] for lon, lat in lonlat], xy


def main(airport_path, out_dir, *files):
    apt = json.load(open(airport_path))
    frame = LocalFrame(apt["lat"], apt["lon"])
    charts = []
    for f in files:
        root = ET.parse(f).getroot()
        desc = root.find(f"{GML}description")
        chart = {"file": Path(f).name, "chart": desc.text if desc is not None else Path(f).stem, "sectors": []}
        for asp in root.iter(f"{AIXM}Airspace"):
            name = asp.find(f".//{AIXM}name")
            lim = asp.find(f".//{AIXM}minimumLimit")
            for patch in asp.iter(f"{GML}PolygonPatch"):
                ext = patch.find(f"{GML}exterior")
                ll, xy = ring(ext, frame)
                holes = [ring(h, frame) for h in patch.findall(f"{GML}interior")]
                chart["sectors"].append({
                    "name": name.text if name is not None else None,
                    "mva_ft": int(float(lim.text)) if lim is not None and lim.text else None,
                    "uom": lim.get("uom") if lim is not None else None,
                    "exterior_lonlat": ll, "exterior_xy": xy,
                    "holes_lonlat": [h[0] for h in holes], "holes_xy": [h[1] for h in holes]})
        charts.append(chart)
        print(chart["chart"], len(chart["sectors"]), "sectors")
    out = {"source": {"name": "FAA Aeronautical Information Services, MVA charts (AIXM 5.1)",
                      "url": "https://www.faa.gov/air_traffic/flight_info/aeronav/digital_products/mva_mia/mva/",
                      "facility": "NCT (Northern California TRACON)"},
           "note": "FUS3 = sectors used with 3 nm radar separation (terminal), FUS5 = 5 nm. "
                   "Point-in-polygon on the xy rings; holes are excluded areas covered by another sector.",
           "charts": charts}
    (Path(out_dir) / "mva.json").write_text(json.dumps(out, separators=(",", ":")))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], *sys.argv[3:])
