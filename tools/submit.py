"""Check your controller.py against the current best before opening a pull request.

    git fetch upstream && git checkout upstream/main -- leaderboard/   # latest best
    python3 tools/submit.py

Runs the same scoring and rule as the GitHub check. Your local numbers can differ slightly
from CI's (floating point differs between machines); the official score is the one CI posts
on your pull request.
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
rc = subprocess.run([sys.executable, os.path.join(HERE, "tools", "ci_score.py")], cwd=HERE).returncode
if rc == 0:
    print("\nLooks like an improvement. Open a pull request that changes only controller.py, e.g.:\n"
          "  git checkout -b my-idea && git commit -am \"<what you changed, cite the FAA 7110.65 paragraph>\"\n"
          "  git push -u origin my-idea && gh pr create --fill\n")
else:
    print("\nNot better than the current best yet (see above). Keep experimenting with tools/experiment.py.")
sys.exit(rc)
