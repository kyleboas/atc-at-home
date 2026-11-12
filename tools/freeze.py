"""Record hashes of the frozen files. Run this only when a human changes the sim or evaluator."""
import hashlib
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
from evaluate import FROZEN  # noqa: E402

with open(os.path.join(HERE, "FROZEN.sha256"), "w") as f:
    for p in FROZEN:
        f.write(f"{hashlib.sha256(open(os.path.join(HERE, p), 'rb').read()).hexdigest()}  {p}\n")
print("froze", len(FROZEN), "files")
