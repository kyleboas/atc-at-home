"""MAINTAINERS ONLY: score a pull request's controller on the private held-out scenarios.

    ATC_HOLDOUT_SEEDS=... python3 tools/judge_holdout.py

The held-out seeds are never published, so nobody can tune to them. Run this before merging a
pull request that beat the best in CI, and merge only if it also holds up here.

WARNING: this runs the submitted controller.py on YOUR machine. Read the diff first: it is
arbitrary Python. Never run unreviewed code from a pull request.
"""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if not os.environ.get("ATC_HOLDOUT_SEEDS"):
    raise SystemExit("set ATC_HOLDOUT_SEEDS to the private comma-separated seed list")
out = subprocess.run([sys.executable, "evaluate.py", "--split", "holdout"], cwd=HERE, capture_output=True, text=True)
res = next((json.loads(l[5:]) for l in out.stdout.splitlines() if l.startswith("JSON ")), None)
if res is None:
    raise SystemExit(out.stdout[-2000:] + out.stderr[-2000:])
print(json.dumps({k: res[k] for k in ("cost", "hard", "rule", "go_arounds", "violations_by_rule")}, indent=1))
