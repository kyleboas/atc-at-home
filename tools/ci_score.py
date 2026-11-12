"""Score controller.py on the training scenarios and compare with leaderboard/best.json.

    python3 tools/ci_score.py [--out result.json] [--summary FILE] [--update-leaderboard]

Exit status: 0 if it beats the current best (or with --update-leaderboard), 1 otherwise.
This is what the GitHub check runs on every pull request; the official numbers are CI's.
"""
import argparse
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(HERE, "tools"))
from verdict import REASONS, manual, verdict  # noqa: E402

BEST = os.path.join(HERE, "leaderboard", "best.json")
HISTORY = os.path.join(HERE, "leaderboard", "results.jsonl")
KEYS = ("cost", "hard", "rule", "service", "go_arounds", "delay_min_per_scenario", "movements",
        "controller_errors", "violations_by_position", "violations_by_rule")


def evaluate():
    out = subprocess.run([sys.executable, "evaluate.py", "--split", "train"], cwd=HERE, capture_output=True, text=True)
    for line in out.stdout.splitlines():
        if line.startswith("JSON "):
            return json.loads(line[5:])
    sys.stderr.write(out.stdout[-3000:] + out.stderr[-3000:])
    raise SystemExit("evaluate.py did not produce a result (frozen files changed, or the controller crashed)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out")
    ap.add_argument("--summary")
    ap.add_argument("--update-leaderboard", action="store_true")
    ap.add_argument("--commit", default="")
    ap.add_argument("--author", default="")
    ap.add_argument("--note", default="")
    args = ap.parse_args()

    best = json.load(open(BEST)) if os.path.exists(BEST) else None
    s = evaluate()
    res = {k: s.get(k) for k in KEYS}
    res["manual"] = manual(res)
    beats, fails = verdict(res, best)
    res["beats_best"], res["fail_reasons"] = beats, fails
    res["best"] = {k: best.get(k) for k in ("cost", "hard", "rule", "go_arounds", "commit")} | {"manual": manual(best)} if best else None
    if args.out:
        json.dump(res, open(args.out, "w"), indent=1)

    lines = ["## ATC autoresearch: training score", "",
             "| | this submission | current best |", "|---|---|---|"]
    for k in ("cost", "hard", "manual", "rule", "go_arounds"):
        lines.append(f"| {k} | {res[k]} | {best and (manual(best) if k == 'manual' else best.get(k))} |")
    lines += ["", ("**Beats the current best.**" if beats else "**Does not beat the current best:** "
                   + "; ".join(REASONS[f] for f in fails))]
    text = "\n".join(lines)
    print(text)
    if args.summary:
        with open(args.summary, "a") as f:
            f.write(text + "\n")

    if args.update_leaderboard:
        row = {**res, "commit": args.commit, "author": args.author, "note": args.note, "t": time.time(),
               "status": "baseline" if best is None else ("kept" if beats else "not better")}
        row.pop("best", None)
        with open(HISTORY, "a") as f:
            f.write(json.dumps(row) + "\n")
        if beats:
            json.dump({k: row[k] for k in row if k not in ("beats_best", "fail_reasons")}, open(BEST, "w"), indent=1)
        return 0
    return 0 if beats else 1


if __name__ == "__main__":
    sys.exit(main())
