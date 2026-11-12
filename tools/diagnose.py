"""Group violations by rule, owning position and the flight legs involved, with an example each.

    python tools/diagnose.py [--split train] [--controller controller] [--top 25]
"""
import argparse
import collections
import importlib
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from atc import scenarios  # noqa: E402
from atc.sim import Sim    # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--split", default="train")
ap.add_argument("--controller", default="controller")
ap.add_argument("--top", type=int, default=25)
args = ap.parse_args()
mod = importlib.import_module(args.controller)

count, example = collections.Counter(), {}
for seed in scenarios.SPLITS[args.split]:
    sim = Sim(scenarios.make(seed), mod.Controller(), record=True).run()
    frames = {t: {f[0]: f for f in acs} for t, acs in sim.frames}
    for v in sim.monitor.violations:
        key = (v["severity"], v["rule"], v["pos"])
        F = frames.get(v["t"] - v["t"] % 2, {})
        m = re.search(r"with (\S+?)[: ]", v["msg"])
        who = [F[c] for c in [v["cs"], m.group(1) if m else None] if c in F]
        if who:
            key += (" vs ".join(sorted(f"{f[6]}/{f[8]}:{f[5]}" for f in who)),)
        count[key] += 1
        example.setdefault(key, f"seed {seed} t={v['t']}: {v['cs']} {v['msg']}")
print("legend: intent d=departure a=arrival / freq g,t,a,d / leg-or-phase\n")
for k, n in count.most_common(args.top):
    print(f"{n:4}  {' | '.join(k)}\n      e.g. {example[k]}")
