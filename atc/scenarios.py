"""Seeded KSFO traffic (West Plan). Same seed -> same traffic and same pilot quirks.

Arrivals enter NorCal Approach airspace (~40-48 nm) on their filed STAR with an enroute
transition, already descending via it, metered by Center (at least 2 minutes in trail per
STAR). Streams and shares follow the real West Plan feeds:
  SERFR4  (south, the OCEANIC/LA flow)      -> EDDYY -> ILS/RNAV 28L or 28R    35 %
  DYAMD5  (east)                            -> ARCHI -> ILS/RNAV 28L or 28R    25 %
  YOSEM3  (southeast)                       -> FAITH -> ILS/RNAV 28L           12 %
  BDEGA4  (north, over the field at 11,000) -> BRIXX, then radar vectors 28R  28 %
Approach requests: ILS ~75 %, RNAV (GPS) ~10 %, RNAV (RNP) Y 28R ~15 % of 28R arrivals.
Departures push from the real OSM stands (terminal stands south of 28L; cargo stands north of
28R, which must cross both 28s), file a 01L/01R SID (SSTIK5, NIITE4, TRUKN2, SEGUL1) with an
enroute transition, and cruise at FL330-FL390.

Levels (movements per hour): light ~20 arr + ~18 dep, medium ~30 + ~26, peak ~42 + ~36.
Splits: train 0-11 (40 min sessions, so a full train evaluation stays under ~2 minutes),
holdout 1000-1011 (60 min; 1010 and 1011 are 3-hour peak sessions with 200+ aircraft),
smoke 900, 901 (20 min).
"""
import random

from . import world as W

SPLITS = {
    "train": list(range(0, 12)),
    "holdout": list(range(1000, 1012)),   # humans only
    "smoke": [900, 901],
}
LONG = {1010, 1011}                        # 3-hour peak sessions (200+ aircraft)
LEVELS = {  # (arrivals/h, departures/h)
    "light": ((18, 22), (16, 20)),
    "medium": ((28, 32), (24, 28)),
    "peak": ((40, 44), (34, 38)),
}

NARROW = [("A320", 14), ("A319", 6), ("A321", 6), ("B738", 8), ("B39M", 8), ("E175", 10), ("CRJ9", 2)]
WIDE = [("B77W", 4), ("B789", 5), ("A359", 3), ("B744", 1), ("A388", 1)]
DOMESTIC = [("UAL", 30), ("SKW", 12), ("ASA", 8), ("DAL", 6), ("AAL", 6), ("SWA", 4), ("JBU", 3), ("FFT", 1)]
INTL = [("UAL", 10), ("BAW", 2), ("CPA", 2), ("ANA", 2), ("SIA", 2), ("KAL", 2), ("DLH", 2), ("AFR", 1),
        ("EVA", 2), ("CAL", 1), ("QFA", 1), ("ACA", 2), ("UAE", 1)]
CARGO = [("FDX", 3), ("UPS", 2), ("CKS", 1)]
STARS = [  # (STAR, enroute transitions, weight, runways, entry distance nm)
    ("SERFR4", [None], 35, ("28L", "28R"), (44, 48)),
    ("DYAMD5", ["INYOE", "RUSME"], 25, ("28L", "28R"), (44, 48)),
    ("YOSEM3", [None], 12, ("28L",), (44, 48)),
    ("BDEGA4", ["AMAKR", "LEGGS", "MLBEC", "MRRLO"], 28, ("28R",), (38, 42)),
]
SIDS = [("SSTIK5", 30), ("NIITE4", 25), ("TRUKN2", 30), ("SEGUL1", 15)]


def _pick(rng, table):
    items, weights = zip(*table)
    return rng.choices(items, weights)[0]


def make(seed, duration=None):
    rng = random.Random(seed)
    if duration is None:
        duration = (3 * 3600 if seed in LONG else 20 * 60 if seed in SPLITS["smoke"]
                    else 40 * 60 if seed < 1000 else 60 * 60)
    level = "peak" if seed in LONG else rng.choice(list(LEVELS))
    (a0, a1), (d0, d1) = LEVELS[level]
    hours = duration / 3600
    n_arr = round(rng.randint(a0, a1) * hours)
    n_dep = round(rng.randint(d0, d1) * hours)
    used = set()

    def callsign(table):
        while True:
            cs = f"{_pick(rng, table)}{rng.randint(10, 2999)}"
            if cs not in used:
                used.add(cs)
                return cs

    def pilot(ac):
        sloppy = rng.random() < 0.15
        ac["sloppy"] = sloppy
        ac["reaction"] = rng.randint(5, 12) if sloppy else rng.randint(2, 6)
        return ac

    def aircraft():
        r = rng.random()
        if r < 0.07:
            return _pick(rng, [("B77W", 1), ("B744", 1), ("A321", 1)]), callsign(CARGO), True
        if r < 0.27:
            return _pick(rng, WIDE), callsign(INTL), False
        return _pick(rng, NARROW), callsign(DOMESTIC), False

    traffic = []
    # ---- arrivals: Center meters each STAR stream at least 120 s in trail
    last = {s[0]: -999 for s in STARS}
    first_wave = min(n_arr, 3)
    times = sorted([rng.randint(0, 120) for _ in range(first_wave)] +
                   [rng.randint(0, max(60, duration - 22 * 60)) for _ in range(n_arr - first_wave)])
    rwy_count = {"28L": 0, "28R": 0}
    for t in times:
        star, trans_list, _, rwys, (dmin, dmax) = _pick(rng, [(s, s[2]) for s in STARS])
        t = max(t, last[star] + 120)
        last[star] = t
        typ, cs, cargo = aircraft()
        rwy = rwys[0] if len(rwys) == 1 else min(rwys, key=lambda r: (rwy_count[r], rng.random()))
        rwy_count[rwy] += 1
        x = rng.random()
        if rwy == "28R" and x < 0.15:
            appr = "rnav"
        elif x < 0.25:
            appr = "gps"
        else:
            appr = "ils"
        traffic.append(pilot({"t": t, "cs": cs, "type": typ, "intent": "arrival", "star": star,
                              "star_trans": rng.choice(trans_list), "runway": rwy, "approach": appr,
                              "dist": round(rng.uniform(dmin, dmax), 1), "cargo": cargo}))
    # ---- departures from real stands; a stand is not reused within 50 minutes
    stand_free = {}
    for t in sorted(rng.randint(0, max(60, duration - 12 * 60)) for _ in range(n_dep)):
        typ, cs, cargo = aircraft()
        heavy = W.TYPES[typ]["wake"] in ("H", "J")
        pool = W.CARGO_STANDS if cargo else W.PAX_STANDS
        ok = [s for s in pool if stand_free.get(s, -1) <= t and (W.STANDS[s]["heavy_ok"] or not heavy)]
        if not ok:
            ok = [s for s in pool if stand_free.get(s, -1) <= t] or list(pool)
        stand = rng.choice(ok)
        stand_free[stand] = t + 50 * 60
        sid = _pick(rng, SIDS)
        trans = rng.choice(W.K.PROCEDURES[sid]["enroute_transitions"])
        rwy = "01R" if heavy else rng.choice(W.DEP_RUNWAYS)
        traffic.append(pilot({"t": t, "cs": cs, "type": typ, "intent": "departure", "stand": stand, "cargo": cargo,
                              "sid": sid, "sid_trans": trans, "runway": rwy, "rto": rng.random() < 0.015,
                              "filed_alt": rng.choice([33000, 35000, 37000, 39000])}))
    return {"seed": seed, "level": level, "duration": duration, "traffic": sorted(traffic, key=lambda a: (a["t"], a["cs"]))}
