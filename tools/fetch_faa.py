"""Download FAA Order JO 7110.65 (Air Traffic Control) as plain text.

    python3 tools/fetch_faa.py            # writes reference/faa-7110.65.txt

The simulator's rules and phraseology follow this order. It is a US government work (public
domain), published at https://www.faa.gov/air_traffic/publications/atpubs/atc_html/.
Chapters fetched: 2 (general), 3 (airport traffic control: tower), 4 (IFR), 5 (radar),
7 (visual), 10 (emergencies).
"""
import html
import os
import re
import time
import urllib.request

BASE = "https://www.faa.gov/air_traffic/publications/atpubs/atc_html/"
CHAPTERS = (2, 3, 4, 5, 7, 10)
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "reference", "faa-7110.65.txt")


def get(name):
    req = urllib.request.Request(BASE + name, headers={"User-Agent": "curl/8.7.1", "Accept": "*/*"})
    return urllib.request.urlopen(req, timeout=60).read().decode("utf-8", "replace")


def text(page):
    m = re.search(r'(?s)<div[^>]*id="content"[^>]*>(.*)', page)
    body = m.group(1) if m else page
    body = re.sub(r"(?s)<(script|style|nav|header|footer)\b.*?</\1>", "", body)
    body = re.sub(r"(?i)<br\s*/?>|</(p|div|li|h[1-6]|tr|table)>", "\n", body)
    t = html.unescape(re.sub(r"<[^>]+>", "", body))
    t = "\n".join(line.strip() for line in t.splitlines())
    return re.sub(r"\n{2,}", "\n", t).strip()


def main():
    index = get("index.html")
    names = sorted(set(re.findall(r'href="\./(chap(\d+)_section_(\d+)\.html)"', index)),
                   key=lambda p: (int(p[1]), int(p[2])))
    out = []
    for name, ch, sec in names:
        if int(ch) not in CHAPTERS:
            continue
        t = text(get(name))
        i = t.find(f"Section {sec}.")
        out.append(f"\n\n===== Chapter {ch}, Section {sec} ({BASE}{name}) =====\n" + (t[i:] if i >= 0 else t))
        time.sleep(0.5)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w") as f:
        f.write("FAA Order JO 7110.65, Air Traffic Control (public domain). Fetched by tools/fetch_faa.py.\n")
        f.write("".join(out))
    print(f"wrote {OUT} ({sum(len(o) for o in out)} chars, {len(out)} sections)")


if __name__ == "__main__":
    main()
