"""Run one autoresearch experiment and record it. The keep/revert rule lives here.

    python3 tools/experiment.py start "short description of the idea"
    python3 tools/experiment.py finish            # evaluate, keep or revert, record

finish: runs the train evaluation on the edited controller.py. The change is
KEPT (committed) if hard violations did not increase, the approach and taxi
violations (FAA JO 7110.65 5-9 and 3-7) did not increase, and cost went
down versus the best kept result; otherwise controller.py is reverted. Either way a row is
appended to results.tsv / results.jsonl, which the dashboard plots live.
"""
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATUS = os.path.join(HERE, "status.json")
TSV = os.path.join(HERE, "results.tsv")
JSONL = os.path.join(HERE, "results.jsonl")


def sh(*args, check=True):
    return subprocess.run(args, cwd=HERE, check=check, capture_output=True, text=True).stdout.strip()


def evaluate():
    out = subprocess.run([sys.executable, "evaluate.py"], cwd=HERE, capture_output=True, text=True)
    for line in out.stdout.splitlines():
        if line.startswith("JSON "):
            return json.loads(line[5:])
    raise SystemExit("evaluate.py failed:\n" + out.stdout[-2000:] + out.stderr[-2000:])


sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from verdict import manual, verdict  # noqa: E402  (the same rule CI uses)


def rows():
    if not os.path.exists(JSONL):
        return []
    return [json.loads(line) for line in open(JSONL) if line.strip()]


def best(rs):
    kept = [r for r in rs if r["status"] in ("baseline", "kept")]
    return kept[-1] if kept else None


def record(row):
    if not os.path.exists(TSV):
        with open(TSV, "w") as f:
            f.write("commit\tcost\thard\trule\tgo_arounds\tstatus\tidea\n")
    with open(JSONL, "a") as f:
        f.write(json.dumps(row) + "\n")
    with open(TSV, "a") as f:
        f.write("\t".join(str(row[k]) for k in ("commit", "cost", "hard", "rule", "go_arounds", "status", "idea")) + "\n")


def driver(msg):
    with open(os.path.join(HERE, "autoresearch.log"), "a") as f:
        f.write(time.strftime("%H:%M:%S ") + msg + "\n")


def write_status(**kw):
    kw["updated"] = time.time()
    json.dump(kw, open(STATUS, "w"))


def main():
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    cmd = sys.argv[1]
    rs = rows()
    if cmd == "baseline":
        s = evaluate()
        record({**s, "pass": s["status"], "n": 0, "commit": sh("git", "rev-parse", "--short", "HEAD"),
                "status": "baseline", "idea": "baseline controller", "t": time.time()})
        write_status(state="idle", experiment=0)
        print(f"baseline cost {s['cost']} hard {s['hard']}")
    elif cmd == "start":
        idea = " ".join(sys.argv[2:]).strip() or "(no description)"
        n = len(rs)
        write_status(state="running", experiment=n, idea=idea, started=time.time())
        driver(f"experiment {n} started: {idea}")
        print(f"experiment {n} started: {idea}")
    elif cmd == "finish":
        st = json.load(open(STATUS)) if os.path.exists(STATUS) else {}
        idea = st.get("idea", "(no description)")
        n = st.get("experiment", len(rs))
        write_status(state="evaluating", experiment=n, idea=idea, started=st.get("started", time.time()))
        ref = best(rs)
        s = evaluate()
        improved = verdict(s, ref)[0]
        if improved:
            sh("git", "add", "controller.py")
            msg = f"{idea}: cost {ref['cost'] if ref else '-'} -> {s['cost']}, hard {ref['hard'] if ref else '-'} -> {s['hard']}"
            sh("git", "commit", "-q", "-m", msg + "\n\nCo-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>", check=False)
            status = "kept"
        else:
            sh("git", "checkout", "controller.py")
            status = "reverted"
        commit = sh("git", "rev-parse", "--short", "HEAD") if improved else "-"
        record({**s, "pass": s["status"], "n": n, "commit": commit, "status": status, "idea": idea,
                "t": time.time(), "seconds": round(time.time() - st.get("started", time.time()))})
        write_status(state="idle", experiment=n, last=status, idea=idea)
        driver(f"experiment {n} {status}: cost {s['cost']} (best {ref['cost'] if ref else '-'}), hard {s['hard']} "
               f"(best {ref['hard'] if ref else '-'})")
        print(f"{status.upper()}: cost {s['cost']} (best was {ref['cost'] if ref else '-'}), "
              f"hard {s['hard']} (best was {ref['hard'] if ref else '-'})")
        print("by position:", s["violations_by_position"])
        print(f"approach/taxi violations (FAA 5-9, 3-7): {manual(s)} (best {manual(ref) if ref else '-'})")
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main()
