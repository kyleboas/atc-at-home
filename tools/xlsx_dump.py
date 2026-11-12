"""Dump rows of an .xlsx (stdlib only) as tab-separated text. Usage: xlsx_dump.py FILE [FILTER]"""
import sys, zipfile, re
import xml.etree.ElementTree as ET

NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


def rows(path):
    z = zipfile.ZipFile(path)
    shared = []
    if "xl/sharedStrings.xml" in z.namelist():
        for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall("m:si", NS):
            shared.append("".join(t.text or "" for t in si.iter("{%s}t" % NS["m"])))
    for name in sorted(n for n in z.namelist() if re.match(r"xl/worksheets/sheet\d+\.xml$", n)):
        for r in ET.fromstring(z.read(name)).iter("{%s}row" % NS["m"]):
            out = []
            for c in r.findall("m:c", NS):
                v = c.find("m:v", NS)
                val = v.text if v is not None else ""
                if c.get("t") == "s" and val:
                    val = shared[int(val)]
                elif c.get("t") == "inlineStr":
                    val = "".join(t.text or "" for t in c.iter("{%s}t" % NS["m"]))
                out.append(val)
            yield out


if __name__ == "__main__":
    flt = sys.argv[2] if len(sys.argv) > 2 else None
    for i, r in enumerate(rows(sys.argv[1])):
        line = "\t".join(r)
        if i < 2 or not flt or flt in line:
            print(line)
