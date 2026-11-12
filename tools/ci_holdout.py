"""CI held-out check: score a pull request's controller and main's on unseen scenarios.

    ATC_CI_SALT=... python3 tools/ci_holdout.py seeds --out FILE
    python3 tools/ci_holdout.py judge --controller PR_controller.py --seeds FILE --out verdict.json

seeds: this week's held-out seeds, derived from a secret salt and the ISO week, so they change
every Monday and nobody can tune to them. They are not the maintainers' private holdout set.

judge: scores the pull request's controller on the training scenarios and on this week's seeds,
and main's controller on the same seeds, then applies tools/verdict.py to both. Every run goes
through $ATC_SANDBOX (in CI: no network, an unprivileged user) with a clean environment; only
the summary numbers are read back and nothing the controller prints is shown.
"""
import argparse
import datetime
import hashlib
import hmac
import json
import math
import os
import shlex
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(HERE, "tools"))
from ci_guard import check  # noqa: E402
from verdict import REASONS, manual, verdict  # noqa: E402

N_SEEDS = 12
INTS = ("hard", "rule", "service", "go_arounds", "movements", "controller_errors", "scenarios")
FLOATS = ("cost", "delay_min_per_scenario")


def week_seeds(salt, n=N_SEEDS, today=None):
    y, w, _ = (today or datetime.datetime.now(datetime.UTC).date()).isocalendar()
    out = []
    for i in range(n):
        h = hmac.new(salt.encode(), f"{y}-W{w:02d}:{i}".encode(), hashlib.sha256).digest()
        out.append(10_000 + int.from_bytes(h[:4]) % 1_000_000_000)
    return out, f"{y}-W{w:02d}"


def clean(res):
    """Keep only well-formed numbers from a child's summary (it ran untrusted code)."""
    out = {}
    for k in INTS:
        v = res.get(k)
        if not (isinstance(v, int) and not isinstance(v, bool) and 0 <= v < 10**7):
            raise ValueError(f"bad {k}")
        out[k] = v
    for k in FLOATS:
        v = res.get(k)
        if not (isinstance(v, (int, float)) and math.isfinite(v) and 0 <= v < 1e7):
            raise ValueError(f"bad {k}")
        out[k] = float(v)
    vbr = res.get("violations_by_rule") or {}
    if not (isinstance(vbr, dict) and len(vbr) < 200 and all(
            isinstance(k, str) and len(k) < 20 and k.replace(".", "").isalnum()
            and isinstance(v, int) and 0 <= v < 10**7 for k, v in vbr.items())):
        raise ValueError("bad violations_by_rule")
    out["violations_by_rule"] = dict(vbr)
    out["manual"] = manual(out)
    return out


def score(controller_src, split, seeds=None):
    box = tempfile.mkdtemp(prefix="atcbox-")
    os.chmod(box, 0o755)
    try:
        repo = os.path.join(box, "repo")
        shutil.copytree(HERE, repo, ignore=shutil.ignore_patterns(".git", "__pycache__", "out", "raw", "*.json.tmp"))
        with open(os.path.join(repo, "controller.py"), "w") as f:
            f.write(controller_src)
        for root, dirs, files in os.walk(box):
            for d in dirs:
                os.chmod(os.path.join(root, d), 0o755)
            for fn in files:
                os.chmod(os.path.join(root, fn), 0o644)
        env = {"PATH": os.path.dirname(sys.executable) + ":/usr/bin:/bin", "HOME": box,
               "PYTHONDONTWRITEBYTECODE": "1", "LANG": "C.UTF-8"}
        if seeds:
            env["ATC_HOLDOUT_SEEDS"] = ",".join(map(str, seeds))
        cmd = shlex.split(os.environ.get("ATC_SANDBOX", "")) + [
            "/usr/bin/env", "-i", *[f"{k}={v}" for k, v in env.items()],
            sys.executable, "evaluate.py", "--split", split]
        try:
            out = subprocess.run(cmd, cwd=repo, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                 stdin=subprocess.DEVNULL, timeout=40 * 60).stdout
        except subprocess.TimeoutExpired:
            return None
        lines = [line for line in out.decode("utf-8", "replace").splitlines() if line.startswith("JSON ")]
        try:
            return clean(json.loads(lines[-1][5:])) if lines else None
        except (ValueError, AttributeError):
            return None
    finally:
        shutil.rmtree(box, ignore_errors=True)


def cmd_seeds(a):
    salt = os.environ.get("ATC_CI_SALT", "")
    if len(salt) < 16:
        raise SystemExit("ATC_CI_SALT is not set (repository secret); the held-out check is off")
    seeds, week = week_seeds(salt)
    fd = os.open(a.out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump({"week": week, "seeds": seeds}, f)
    print(f"held-out week {week}: {len(seeds)} scenarios")


def cmd_judge(a):
    pr_src = open(a.controller).read()
    main_src = open(os.path.join(HERE, "controller.py")).read()
    s = json.load(open(a.seeds))
    res = {"week": s["week"], "scenarios": len(s["seeds"])}
    errors = check(pr_src)
    if errors:
        res |= {"ok": False, "reason": "guard", "guard": errors[:20]}
    else:
        best = json.load(open(os.path.join(HERE, "leaderboard", "best.json")))
        train = score(pr_src, "train")
        pr_h = score(pr_src, "holdout", s["seeds"]) if train else None
        main_h = score(main_src, "holdout", s["seeds"]) if pr_h else None
        if train is None or pr_h is None:
            res |= {"ok": False, "reason": "crash"}
        elif main_h is None:
            res |= {"ok": False, "reason": "main_crash"}
        else:
            t_ok, t_fail = verdict(train, best)
            h_ok, h_fail = verdict(pr_h, main_h)
            res |= {"ok": t_ok and h_ok, "train": train, "train_best": {k: best.get(k) for k in ("cost", "hard")}
                    | {"manual": manual(best)}, "train_fail": t_fail, "holdout": pr_h, "holdout_main": main_h,
                    "holdout_fail": h_fail}
    json.dump(res, open(a.out, "w"), indent=1)
    print(summary(res))
    if a.summary:
        with open(a.summary, "a") as f:
            f.write(summary(res) + "\n")


def summary(r):
    if r.get("reason") == "guard":
        return ("### ATC autoresearch: held-out check\n\nNot run: `controller.py` uses something a controller "
                "may not (see `tools/ci_guard.py`):\n\n" + "\n".join(f"- `{e}`" for e in r["guard"]))
    if r.get("reason") in ("crash", "main_crash"):
        who = "this submission" if r["reason"] == "crash" else "main"
        return f"### ATC autoresearch: held-out check\n\nNo score: {who} crashed or timed out (output is not shown)."
    row = lambda k, a, b: f"| {k} | {a} | {b} |"  # noqa: E731
    t, tb, h, hm = r["train"], r["train_best"], r["holdout"], r["holdout_main"]
    lines = ["### ATC autoresearch: held-out check", "",
             f"Scored again in a sandbox, and on {r['scenarios']} unseen scenarios that change every week ({r['week']}).", "",
             "| | training | training best | held-out | held-out, main |", "|---|---|---|---|---|"]
    for k in ("cost", "hard", "manual"):
        lines.append(f"| {k} | {t[k]} | {tb.get(k)} | {h[k]} | {hm[k]} |")
    lines.append("")
    if r["ok"]:
        lines.append("✅ **Better on both.**")
    else:
        why = [f"training: {REASONS[x]}" for x in r["train_fail"]] + \
              [f"held-out: {REASONS[x].replace('the current best', 'main')}" for x in r["holdout_fail"]]
        lines.append("❌ **Not better:** " + "; ".join(why))
    return "\n".join(lines)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("seeds")
    p.add_argument("--out", required=True)
    p = sub.add_parser("judge")
    p.add_argument("--controller", required=True)
    p.add_argument("--seeds", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--summary")
    a = ap.parse_args()
    {"seeds": cmd_seeds, "judge": cmd_judge}[a.cmd](a)
