"""Sim replays on kyleboas/scope: the dashboard's /replay page is scope's own (web/scope.html), the same scope as
livescope, infinitescope and vatscope, with a session played through its timelapse bar and the radio transcript
in its radio log.

A replay is still made by run_demo.py (an HTML file with the session in it, cached by the dashboard; the mirror
on coding gets them from the Mac). This reads one a frame at a time (the long peak sessions are ~40 MB), turns
the simulator's x/y (nm from the ARP, ksfo/geo.py's azimuthal equidistant frame) into lat/lon and keeps it in
memory zlib-compressed, then serves scope's endpoints:

    /api/live/timelapse?a=list                      the cached replays, newest first
    /api/live/timelapse?a=frames&id=&from=&every=   frames from t >= from, one block at a time ("more" until the end)
    /api/live/radio?id=                             the transcript, for the radio log

A replay's id is <commit>-<seed>. Times are the session's seconds plus ZERO (scope reads 0 as "no snapshot yet").
"""
import bisect
import json
import math
import os
import pickle
import re
import threading
import zlib
from collections import OrderedDict

ZERO = 1                     # frame times are sim seconds + ZERO; the page shows them as elapsed time from ZERO
BLOCK = 300                  # frames per compressed block (and per frames request)
FORMAT = 2                # bump when convert()'s output changes, so stored conversions are redone
STORE_MAX = 300           # converted replays kept on disk (0.1-3.5 MB each)
KEEP = 4                     # replays kept converted in memory
SPEEDS = [4, 16, 32, 64, 128]
GROUND = {"gate_req", "push_hold", "pushing", "pushed", "taxiing", "holding_short", "clear", "taxi_in", "pending"}
RUNWAY = {"exiting", "lining_up", "lined_up", "rolling", "landing_roll", "rto"}
CLEARED = re.compile(r"cleared (ILS|RNAV \(GPS\)|RNAV \(RNP\)) runway (\d\d[LRC]?) approach")
WITH = re.compile(r"\bwith ([A-Z]{2,4}\d{1,5}[A-Z]{0,2})\b")
FREQ = {"g": "#8b949e", "t": "#3fb950", "a": "#f0883e", "d": "#58a6ff", "c": "#bc8cff"}
FREQ_NAME = {"g": "GND", "t": "TWR", "a": "APP", "d": "DEP", "c": "CTR"}
POS = {"GND": "#8b949e", "TWR": "#3fb950", "APP": "#f0883e", "DEP": "#58a6ff", "CTR": "#bc8cff", "ATIS": "#3fb950"}
GROUPS = [["GND", "GND", True], ["TWR", "TWR", True], ["APP", "APP", True], ["DEP", "DEP", True],
          ["pilot", "Pilots", False], ["mon", "Monitor", True]]
ROW = ["", 0.0, 0.0, 0, 0, "", "", "", "-", 0, -1, -1, "", "", "", 0]   # cs x y alt gs leg intent wake freq hdg hdgA altA rwy type nfix est
WAKE = {"J": "super", "H": "heavy", "M": "medium", "L": "large", "S": "small"}
KIND = {"hard": "alert", "invalid": "alert", "error": "alert", "rule": "warn", "service": "note"}
NAME = re.compile(r"^replay-([0-9a-zA-Z]+)-(\d+)\.html$")
_dec = json.JSONDecoder()
_ws = re.compile(r"[ \t\n\r]*")


def _walk(s, i):
    """(key, value) for each member of the JSON object at s[i], and ("frame", f) for each element of "frames",
    so a big replay is never all in memory as Python objects at once."""
    i = _ws.match(s, i).end() + 1
    while True:
        i = _ws.match(s, i).end()
        if s[i] == "}":
            return
        key, i = _dec.raw_decode(s, i)
        i = _ws.match(s, i).end() + 1                     # the ':'
        i = _ws.match(s, i).end()
        if key == "frames":
            i += 1
            while True:
                i = _ws.match(s, i).end()
                if s[i] == "]":
                    i += 1
                    break
                f, i = _dec.raw_decode(s, i)
                yield "frame", f
                i = _ws.match(s, i).end()
                if s[i] == ",":
                    i += 1
        else:
            v, i = _dec.raw_decode(s, i)
            yield key, v
        i = _ws.match(s, i).end()
        if s[i] == ",":
            i += 1


def _a3(v):
    return f"{max(0, round(v / 100)):03d}"


class Replays:
    def __init__(self, cache_dir, root, store=None):
        self.dir, self.root, self.store = cache_dir, root, store   # store: converted replays kept on disk (one each)
        self.mem, self.lock, self.busy, self.heads = OrderedDict(), threading.Lock(), {}, {}
        self.one = threading.Lock()          # one conversion at a time (a long session peaks at ~200 MB while read)
        self._geo = None

    # ------------------------------------------------------------------ x/y -> lat/lon
    def geo(self):
        if self._geo is None:
            import ksfo.data as K
            import ksfo.geo as G
            self._geo = (K.ARP, G.direct, K.FIELD if hasattr(K, "FIELD") else "")
        return self._geo

    def latlon(self, x, y):
        (la, lo), direct, _ = self.geo()
        d = math.hypot(x, y)
        if d < 1e-7:
            return la, lo
        return direct(la, lo, math.degrees(math.atan2(x, y)) % 360, d)

    # ------------------------------------------------------------------ the cached replays
    def path(self, rid):
        commit, _, seed = rid.partition("-")
        if not commit.isalnum() or not seed.isdigit():
            return None
        p = os.path.join(self.dir, f"replay-{commit}-{seed}.html")
        return p if os.path.isfile(p) else None

    def listing(self):
        out = []
        try:
            names = os.listdir(self.dir)
        except OSError:
            names = []
        for n in names:
            m = NAME.match(n)
            if m:
                try:
                    out.append((os.path.getmtime(os.path.join(self.dir, n)), m.group(1), int(m.group(2))))
                except OSError:
                    pass
        out.sort(reverse=True)
        return [self.rec(c, sd, mt) for mt, c, sd in out[:120]]

    def rec(self, commit, seed, mtime=None, meta=None):
        rid = f"{commit}-{seed}"
        meta = meta or (self.mem.get(rid) or {}).get("meta")
        la, lo = self.geo()[0]
        dur = meta["last"] if meta else _duration(seed)
        sub = f"controller {commit} · " + (f"{meta['level']} · {_mins(dur)} · cost {meta['summary'].get('cost')}, "
                                           f"hard {meta['summary'].get('hard')}" if meta else f"{_mins(dur)}")
        return {"id": rid, "name": f"seed {seed} · {commit}", "sub": sub, "kind": "area", "lat": la, "lon": lo, "nm": 65,
                "first": ZERO, "last": ZERO + dur, "start": ZERO, "until": ZERO + dur, "every_s": 2, "rec": False,
                "clock": "elapsed", "zero": ZERO, "speeds": SPEEDS, "speed": 16,
                "back": [f"/sim?commit={commit}", "ALL REPLAYS", "Pick another controller or session"]}

    # ------------------------------------------------------------------ convert one
    def get(self, rid):
        with self.lock:
            if rid in self.mem:
                self.mem.move_to_end(rid)
                return self.mem[rid]
            ev = self.busy.get(rid)
            if ev is None:
                ev = self.busy[rid] = threading.Event()
                mine = True
            else:
                mine = False
        if not mine:
            ev.wait(120)
            return self.mem.get(rid)
        try:
            p = self.path(rid)
            r = self.load(p) if p else None
            if p and r is None:
                with self.one:
                    r = self.convert(p)
                self.save(p, r)
            with self.lock:
                if r:
                    self.mem[rid] = r
                    while len(self.mem) > KEEP:
                        self.mem.popitem(last=False)
            return r
        finally:
            with self.lock:
                self.busy.pop(rid, None)
            ev.set()

    # converting takes ~40 s for a long session on the small server, so each is done once and kept on disk,
    # named by the replay file's size and time (a regenerated replay converts again) and FORMAT
    def stored(self, path):
        st = os.stat(path)
        return os.path.join(self.store, f"{os.path.basename(path)[:-5]}-{st.st_size}-{int(st.st_mtime)}-v{FORMAT}.pickle")

    def load(self, path):
        if not self.store:
            return None
        try:
            with open(self.stored(path), "rb") as f:
                return pickle.load(f)                # written by save() below, never from outside
        except (OSError, EOFError, pickle.UnpicklingError, AttributeError, ValueError):
            return None

    def save(self, path, r):
        if not self.store or not r:
            return
        try:
            os.makedirs(self.store, exist_ok=True)
            dest = self.stored(path)
            tmp = dest + ".tmp"
            with open(tmp, "wb") as f:
                pickle.dump(r, f, protocol=pickle.HIGHEST_PROTOCOL)
            os.replace(tmp, dest)
            keep = os.path.basename(dest)
            pre = keep.split("-")[:3]                # replay-<commit>-<seed>: older copies of this one go
            for n in os.listdir(self.store):
                if n != keep and n.split("-")[:3] == pre:
                    os.remove(os.path.join(self.store, n))
            names = sorted((os.path.getmtime(os.path.join(self.store, n)), n) for n in os.listdir(self.store))
            for _, n in names[:-STORE_MAX]:          # and the least recently converted past STORE_MAX
                os.remove(os.path.join(self.store, n))
        except OSError:
            pass

    def convert(self, path):
        with open(path, encoding="utf-8") as f:
            s = f.read()
        i = s.index("const DATA = ") + len("const DATA = ")
        meta, times, blocks, cur, prev = {}, [], [], [], {}
        for key, v in _walk(s, i):
            if key != "frame":
                if key == "world":
                    v = {k: v.get(k) for k in ("field", "config", "arr", "dep", "magvar")}
                meta[key] = v
                continue
            t, rows = v[0], v[1]
            out = []
            for r in rows:
                out.append(self.row(r, t, prev, meta))
            times.append(ZERO + t)
            cur.append([ZERO + t, out])
            if len(cur) == BLOCK:
                blocks.append(zlib.compress(json.dumps(cur, separators=(",", ":")).encode(), 6))
                cur = []
        if cur:
            blocks.append(zlib.compress(json.dumps(cur, separators=(",", ":")).encode(), 6))
        del s
        meta["last"] = (times[-1] - ZERO) if times else 0
        meta["radio"] = self.radio_json(meta)
        meta.pop("transcript", None)
        return {"meta": meta, "times": times, "blocks": blocks}

    def row(self, r, t, prev, meta):
        """A simulator frame row -> scope's timelapse row [id, cs, lat, lon, alt, gs, hdg, vs, gnd, type, reg, sq, emer, x],
        x the scope's extras: ps (position symbol), l3 (third data block line), gt (ground tag), st (card line)."""
        r = r + ROW[len(r):]                 # an older simulator's rows may be shorter
        cs, x, y, alt, gs, leg, intent, wake, freq, hdg, hdg_a, alt_a, rwy, typ, nfix = r[:15]
        leg, rwy, typ, nfix = leg or "", rwy or "", typ or "", nfix or ""
        la, lo = self.latlon(x, y)
        gnd = leg in GROUND or leg in RUNWAY
        p = prev.get(cs)
        vs = 0 if gnd or not p or t <= p[0] else int(round((alt - p[1]) / (t - p[0]) * 60 / 50) * 50)
        prev[cs] = (t, alt)
        fq = (freq or "-")[0]
        ext = {"ps": [fq.upper() if fq in FREQ else "·", FREQ.get(fq, "#9a9a9a")]}
        if gnd:
            ext["gt"] = leg.replace("_", " ").upper() + (" " + nfix if nfix else "")
        else:
            asg = (f"H{hdg_a:03d} " if hdg_a >= 0 else "") + (
                ("↑" if alt_a > alt + 100 else "↓" if alt_a < alt - 100 else "=") + _a3(alt_a) if alt_a >= 0 else "")
            l3 = (asg + " " + rwy).strip()
            if l3:
                ext["l3"] = l3
        kind = {"d": "departure", "a": "arrival", "p": "pattern"}.get(intent, "")
        ext["st"] = " · ".join(v for v in (f"{kind} {rwy}".strip(), WAKE.get(wake, wake or ""), leg + (" → " + nfix if nfix else ""),
                                           "on " + FREQ_NAME[fq] if fq in FREQ_NAME else "",
                                           (f"assigned H{hdg_a:03d}" if hdg_a >= 0 else "") +
                                           (f" {alt_a:,} ft" if alt_a >= 0 else "")) if v.strip())
        return [cs, cs, round(la, 6), round(lo, 6), alt, gs, hdg, vs, 1 if gnd else 0, typ, "", "", "", ext]

    def radio_json(self, meta):
        lines = []
        for l in meta.get("transcript") or []:
            who, kind = l.get("who") or "", l.get("kind") or ""
            g = ("DEP" if who == "CTR" else who) if who in ("GND", "TWR", "APP", "DEP", "CTR") else (
                "mon" if kind in ("hard", "rule", "service", "invalid", "error") else "TWR" if kind == "atis" else "pilot")
            o = {"t": ZERO + l.get("t", 0), "who": who, "cs": l.get("cs") or "", "g": g, "text": l.get("text") or ""}
            if KIND.get(kind):
                o["k"] = KIND[kind]
            if kind == "hard" and o["cs"]:
                o["a"] = "CA"
                m = WITH.search(o["text"])          # "... conflict alert with JBU2045": the other aircraft flashes too
                if m and m.group(1) != o["cs"]:
                    o["ao"] = m.group(1)
            lines.append(o)
        w, sm = meta.get("world") or {}, meta.get("summary") or {}
        head = (f"{w.get('field', '')} · {w.get('config', '')}: land {'/'.join(w.get('arr') or [])}, depart "
                f"{'/'.join(w.get('dep') or [])}\nseed {meta.get('seed')} ({meta.get('level')})"
                + (f" · controller {meta['commit']}" if meta.get("commit") else "")
                + f"\ncost {sm.get('cost')} · hard {sm.get('hard')} · rule {sm.get('rule')} · go-arounds "
                  f"{sm.get('go_arounds')} · movements {sm.get('movements')}")
        # the session's ATIS, so scope draws only the procedures for its runways (it reads them from an ATIS)
        first = next((l for l in meta.get("transcript") or [] if l.get("kind") == "atis"), None)
        code = re.search(r"information (\w+)", first["text"]) if first else None
        atis = {"KSFO": [{"type": "combined", "code": code.group(1)[0] if code else "", "text": first["text"], "time": ""}]} if first else {}
        return json.dumps({"ok": True, "head": head, "lines": lines, "colors": POS, "groups": GROUPS, "talk_s": 6,
                           "alert_s": 20, "atis": atis,
                           "procs": {"KSFO": self.procs_in_use(meta)}}, separators=(",", ":")).encode()

    # ------------------------------------------------------------------ scope's endpoints
    def procs_in_use(self, meta):
        """The procedures this session flew, by scope's (CIFP) names: each arrival's STAR and approach and each departure's
        SID from the scenario, and any approach the controller cleared instead (the transcript)."""
        sid, star, app, arr, dep = set(), set(), set(), set(meta["world"].get("arr") or []), set()
        try:
            from atc import procs as P
            from atc import scenarios
            ids = P.APPROACH_IDS
            names = lambda rw, kind: P.APPROACH_NAMES.get((ids.get(rw) or {}).get(kind) or (ids.get(rw) or {}).get(P.default_approach(rw)) or "")   # noqa: E731
            for f in scenarios.make(int(meta["seed"]))["traffic"]:
                rw = f.get("runway") or ""
                if f.get("star"):
                    star.add(f["star"]); arr.add(rw)
                    if names(rw, f.get("approach") or "ils"):
                        app.add(names(rw, f.get("approach") or "ils"))
                if f.get("sid"):
                    sid.add(f["sid"]); dep.add(rw)
            for l in meta.get("transcript") or []:
                for kind, rw in CLEARED.findall(l.get("text") or ""):
                    n = names(rw, {"ILS": "ils", "RNAV (GPS)": "gps", "RNAV (RNP)": "rnav"}[kind])
                    if n:
                        app.add(n)
        except Exception:  # noqa: BLE001  (an older checkout's simulator: the page falls back to reading the ATIS)
            return None
        arr.discard(""); dep.discard("")
        return {"arr": sorted(arr), "dep": sorted(dep or meta["world"].get("dep") or []), "sid": sorted(sid),
                "star": sorted(star), "approach": sorted(app)}

    def timelapse(self, s):
        a = s("a", 10)
        if a == "list":
            return {"ok": True, "recs": self.listing(), "no_rec": True,
                    "note": "Sim replays: each a whole session at KSFO worked by one controller version. "
                            "New ones are made from the Sim replays page."}
        if a == "frames":
            rid = s("id", 40)
            r = self.get(rid)
            if not r:
                return {"ok": False, "error": "no such replay", "frames": [], "more": False}
            try:
                t0 = float(s("from", 20) or 0)
                every = max(1, min(1000, int(float(s("every", 6) or 1))))
            except ValueError:
                t0, every = 0.0, 1
            k = bisect.bisect_left(r["times"], t0 - 1e-6)
            out, b = [], k // BLOCK
            while not out and b < len(r["blocks"]):     # one block's frames from k on (the next block if thinning left none)
                fr = json.loads(zlib.decompress(r["blocks"][b]))
                out = [f for j, f in enumerate(fr, b * BLOCK) if j >= k and j % every == 0]
                b += 1
            return {"ok": True, "frames": out, "more": b < len(r["blocks"])}
        return {"ok": False, "error": "bad request"}

    def radio(self, s):
        """The radio log, ATIS and procedures in use: from the conversion if it's done, else straight from the replay
        file's head and transcript (well under a second), so the airport is set up while the frames still convert."""
        rid = s("id", 40)
        with self.lock:
            r = self.mem.get(rid)
            if r:
                return r["meta"]["radio"]
            if rid in self.heads:
                return self.heads[rid]
        p = self.path(rid)
        if not p:
            return {"ok": False, "error": "no such replay", "lines": []}
        r = self.load(p)                         # converted on an earlier run
        out = r["meta"]["radio"] if r else self.head(p)
        with self.lock:
            self.heads[rid] = out
            while len(self.heads) > 32:
                self.heads.pop(next(iter(self.heads)))
        return out

    def head(self, path):
        with open(path, encoding="utf-8") as f:
            s = f.read()
        i = s.index("const DATA = ") + len("const DATA = ")
        meta = {}
        for key, v in _walk(s, i):               # everything before the frames
            if key == "frame":
                break
            meta[key] = {k: v.get(k) for k in ("field", "config", "arr", "dep", "magvar")} if key == "world" else v
        j = s.rfind('"transcript":')             # and the transcript, after them
        if j > s.find('"frames":', i):
            meta["transcript"] = _dec.raw_decode(s, _ws.match(s, j + len('"transcript":')).end())[0]
        return self.radio_json(meta)


_dur = {}


def _duration(seed):
    if seed not in _dur:
        try:
            from atc import scenarios
            _dur[seed] = int(scenarios.make(seed).get("duration", 3600))
        except Exception:  # noqa: BLE001
            _dur[seed] = 3600
    return _dur[seed]


def _mins(s):
    return f"{round(s / 60)} min"
