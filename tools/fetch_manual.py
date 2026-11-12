"""Download the Infinite Flight ATC manual (sections 1-6) as plain text.

    python3 tools/fetch_manual.py            # writes reference/atc-manual.txt

The manual is Infinite Flight's (https://infiniteflight.com/guide/atc-manual) and is not
redistributed in this repository; this script fetches your own copy for reference.
"""
import html
import json
import os
import re
import urllib.request

URL = "https://infiniteflight.com/guide/atc-manual/3.-tower/3.1-separation"
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "reference", "atc-manual.txt")


def main():
    req = urllib.request.Request(URL, headers={"User-Agent": "atc-autoresearch/1.0 (manual fetch)"})
    page = urllib.request.urlopen(req, timeout=30).read().decode("utf-8")
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', page, re.S)
    if not m:
        raise SystemExit("could not find the manual data on the page; the site layout may have changed")
    groups = json.loads(m.group(1))["props"]["pageProps"]["groupedItems"]
    out = []
    for key, items in groups.items():
        if not re.match(r"^[1-6]\.-", key):
            continue
        for it in items:
            b = re.sub(r"(?s)<svg.*?</svg>", "", it.get("bodyHtml", ""))
            b = re.sub(r"<(br|/p|/li|/h\d|/tr)[^>]*>", "\n", b)
            b = re.sub(r"<li[^>]*>", "- ", b)
            b = re.sub(r"</t[dh]>", " | ", b)
            b = html.unescape(re.sub(r"<[^>]+>", "", b))
            b = re.sub(r"\n\s*\n+", "\n", re.sub(r"[ \t]+", " ", b))
            out.append(f"\n\n######## {it['title']}\n{b}")
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("".join(out))
    print(f"wrote {OUT} ({sum(len(x) for x in out):,} chars, {len(out)} sections)")


if __name__ == "__main__":
    main()
