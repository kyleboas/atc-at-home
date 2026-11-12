"""Work with everyone else on lowering the score. GitHub issues are the shared notebook.

    python3 tools/collab.py status                 # current best, who is trying what, recent results
    python3 tools/collab.py pull-best              # start from the current best on upstream main
    python3 tools/collab.py start "idea (manual section)"   # claim the idea, then start the experiment
    python3 tools/collab.py finish                 # score it, keep or revert, publish the result
    python3 tools/collab.py submit                 # open a pull request if you beat the current best
    python3 tools/collab.py idea "idea"            # leave an idea for someone else to try

Each experiment is one issue on the upstream repo: "[claim] idea" while it runs, then retitled
"[kept 512.3] idea", "[reverted 540.1] idea" or "[crash] idea" and closed, with the numbers and
the controller.py diff in a comment. Unfinished claims expire after 45 minutes.

Needs the GitHub CLI (`gh auth login`). Set ATC_UPSTREAM to use another upstream repo.
"""
import calendar
import difflib
import json
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UPSTREAM = os.environ.get("ATC_UPSTREAM", "kyleboas/atc-autoresearch")
STATE = os.path.join(HERE, ".collab.json")
JSONL = os.path.join(HERE, "results.jsonl")
TITLE = re.compile(r"^\[(claim|kept|reverted|crash|idea|expired)(?: ([0-9.]+))?\]\s*(.*)$")
SIMILAR = 0.75

sys.path.insert(0, os.path.join(HERE, "tools"))
from verdict import manual, verdict  # noqa: E402


def run(*args, check=True, capture=True):
    r = subprocess.run(args, cwd=HERE, capture_output=capture, text=True)
    if check and r.returncode != 0:
        raise SystemExit(f"{' '.join(args[:3])} failed:\n{(r.stderr or r.stdout or '').strip()}")
    return (r.stdout or "").strip()


def gh_json(*args):
    return json.loads(run("gh", *args) or "null")


def issues(state="open", limit=100):
    out = gh_json("issue", "list", "--repo", UPSTREAM, "--state", state, "--limit", str(limit),
                  "--json", "number,title,author,createdAt,closedAt,url")
    rows = []
    for i in out or []:
        m = TITLE.match(i["title"])
        if m:
            rows.append({**i, "kind": m.group(1), "cost": m.group(2), "idea": m.group(3),
                         "who": (i.get("author") or {}).get("login", "?")})
    return rows


def upstream_best():
    try:
        raw = run("gh", "api", f"repos/{UPSTREAM}/contents/leaderboard/best.json",
                  "-H", "Accept: application/vnd.github.raw")
        return json.loads(raw)
    except (SystemExit, ValueError):
        return None


def ago(iso):
    t = calendar.timegm(time.strptime(iso, "%Y-%m-%dT%H:%M:%SZ"))
    m = int((time.time() - t) / 60)
    return f"{m} min ago" if m < 120 else f"{m // 60} h ago" if m < 2880 else f"{m // 1440} d ago"


def load_state():
    return json.load(open(STATE)) if os.path.exists(STATE) else {}


def save_state(**kw):
    json.dump(kw, open(STATE, "w"))


def similar(idea, rows):
    a = idea.lower()
    out = []
    for r in rows:
        score = difflib.SequenceMatcher(None, a, r["idea"].lower()).ratio()
        if score >= 0.5:
            out.append((score, r))
    return sorted(out, key=lambda x: -x[0])


def cmd_status(_):
    best = upstream_best()
    if best:
        print(f"Current best: cost {best['cost']}, hard {best['hard']}, manual {manual(best)} "
              f"(commit {best.get('commit')}, by {best.get('author') or '?'}: {best.get('note', '')[:80]})")
    open_ = issues("open")
    claims = [r for r in open_ if r["kind"] == "claim"]
    ideas = [r for r in open_ if r["kind"] == "idea"]
    print(f"\nIn progress ({len(claims)}):")
    for r in claims:
        print(f"  #{r['number']} {r['who']}, {ago(r['createdAt'])}: {r['idea']}")
    print(f"\nIdeas waiting for someone ({len(ideas)}):")
    for r in ideas[:20]:
        print(f"  #{r['number']} {r['idea']}")
    done = [r for r in issues("closed", 60) if r["kind"] in ("kept", "reverted", "crash")]
    print(f"\nRecent results ({len(done)}):")
    for r in done[:30]:
        print(f"  {r['kind']:8} {r['cost'] or '':>8}  {r['who']}: {r['idea']}")


def cmd_pull_best(args):
    if run("git", "status", "--porcelain", "--", "controller.py"):
        raise SystemExit("controller.py has uncommitted changes. Finish or revert your experiment first.")
    remotes = run("git", "remote").split()
    up = "upstream" if "upstream" in remotes else "origin"
    run("git", "fetch", "-q", up, "main")
    ahead = run("git", "rev-list", "--count", f"{up}/main..HEAD", "--", "controller.py")
    if ahead != "0" and "--force" not in args:
        raise SystemExit(f"You have {ahead} kept change(s) to controller.py that are not upstream. Run "
                         "`python3 tools/collab.py submit` first (or add --force to drop them).")
    run("git", "checkout", "-q", "-B", "exp", f"{up}/main")
    print(f"On branch exp at {up}/main ({run('git', 'rev-parse', '--short', 'HEAD')}). Scoring the baseline...")
    print(run(sys.executable, "tools/experiment.py", "baseline"))


def cmd_start(args):
    force = "--force" in args
    idea = " ".join(a for a in args if a != "--force").strip()
    if not idea:
        raise SystemExit('usage: collab.py start "idea, citing the manual section"')
    if load_state().get("issue"):
        raise SystemExit("An experiment is already claimed. Run `collab.py finish` first.")
    rows = issues("open") + issues("closed", 200)
    near = [(s, r) for s, r in similar(idea, rows) if r["kind"] != "idea"]
    for s, r in near[:5]:
        print(f"  similar ({s:.0%}): #{r['number']} [{r['kind']}{' ' + r['cost'] if r['cost'] else ''}] {r['idea']}")
    if near and near[0][0] >= SIMILAR and not force:
        raise SystemExit("Someone already tried or is trying this. Pick something different, or rerun with "
                         "--force if yours really differs (say how in the idea).")
    body = ("Claimed with `tools/collab.py`. This issue is updated with the result when the experiment "
            "finishes, or closed automatically after 45 minutes.")
    url = run("gh", "issue", "create", "--repo", UPSTREAM, "--title", f"[claim] {idea}"[:250], "--body", body)
    num = int(url.rstrip("/").split("/")[-1])
    for s, r in similar(idea, issues("open")):
        if r["kind"] == "idea" and s >= SIMILAR:
            run("gh", "issue", "comment", str(r["number"]), "--repo", UPSTREAM, "--body", f"Trying this in #{num}.",
                check=False)
    save_state(issue=num, idea=idea, started=time.time())
    print(f"claimed #{num}: {url}")
    print(run(sys.executable, "tools/experiment.py", "start", idea))


def fmt_result(row, diff, best):
    def cell(k):
        return manual(row) if k == "manual" else row.get(k)
    lines = ["| | this experiment | upstream best |", "|---|---|---|"]
    for k in ("cost", "hard", "manual", "rule", "go_arounds", "delay_min_per_scenario"):
        b = (manual(best) if k == "manual" else best.get(k)) if best else "–"
        lines.append(f"| {k} | {cell(k)} | {b} |")
    lines += ["", f"By rule: `{json.dumps(row.get('violations_by_rule', {}))}`", "",
              f"Result: **{row['status']}** against the contributor's local best "
              f"(`tools/experiment.py`, training scenarios).", ""]
    if diff:
        d = diff if len(diff) < 50000 else diff[:50000] + "\n... (truncated)"
        lines += ["<details><summary>controller.py diff</summary>", "", "```diff", d, "```", "", "</details>"]
    return "\n".join(lines)


def cmd_finish(_):
    st = load_state()
    diff = run("git", "diff", "--", "controller.py")
    r = subprocess.run([sys.executable, "tools/experiment.py", "finish"], cwd=HERE, capture_output=True, text=True)
    print(r.stdout + r.stderr)
    rows = [json.loads(line) for line in open(JSONL)] if os.path.exists(JSONL) else []
    row = rows[-1] if rows and r.returncode == 0 else None
    if not st.get("issue"):
        print("(no claimed issue: nothing published)")
        return
    best = upstream_best()
    if row is None:
        kind, title_cost = "crash", ""
        body = "The experiment crashed:\n\n```\n" + (r.stdout + r.stderr)[-3000:] + "\n```"
        if diff:
            body += "\n\n<details><summary>controller.py diff</summary>\n\n```diff\n" + diff[:50000] + "\n```\n\n</details>"
        run("git", "checkout", "--", "controller.py", check=False)
    else:
        kind, title_cost = row["status"], f" {row['cost']}"
        body = fmt_result(row, diff, best)
    n = str(st["issue"])
    run("gh", "issue", "comment", n, "--repo", UPSTREAM, "--body", body)
    run("gh", "issue", "edit", n, "--repo", UPSTREAM, "--title", f"[{kind}{title_cost}] {st['idea']}"[:250])
    run("gh", "issue", "close", n, "--repo", UPSTREAM, "--reason", "completed")
    os.remove(STATE)
    print(f"published to #{n}")
    if row and row["status"] == "kept" and best and verdict(row, best)[0]:
        print("\nThis beats the upstream best. Run `python3 tools/collab.py submit` to open a pull request.")


def cmd_submit(_):
    remotes = run("git", "remote").split()
    up = "upstream" if "upstream" in remotes else "origin"
    run("git", "fetch", "-q", up, "main")
    run("git", "checkout", "-q", f"{up}/main", "--", "leaderboard/")
    rc = subprocess.run([sys.executable, "tools/ci_score.py"], cwd=HERE).returncode
    run("git", "checkout", "-q", "HEAD", "--", "leaderboard/", check=False)
    if rc != 0:
        raise SystemExit("Not better than the upstream best: nothing submitted.")
    changed = run("git", "diff", "--name-only", f"{up}/main...HEAD").split()
    if changed != ["controller.py"]:
        raise SystemExit(f"A submission may only change controller.py; this branch changes {changed}. "
                         "Start from `collab.py pull-best`.")
    log = run("git", "log", "--format=%s", f"{up}/main..HEAD")
    title = log.splitlines()[0].split(": cost")[0] if log else "controller improvement"
    branch = "submit-" + run("git", "rev-parse", "--short", "HEAD")
    run("git", "push", "-q", "-f", "origin", f"HEAD:refs/heads/{branch}")
    me = run("gh", "api", "user", "--jq", ".login")
    body = ("Kept experiments in this submission:\n\n" + "\n".join(f"- {line}" for line in log.splitlines())
            + "\n\nSubmitted with `tools/collab.py submit`.")
    url = run("gh", "pr", "create", "--repo", UPSTREAM, "--base", "main", "--head", f"{me}:{branch}",
              "--title", title[:200], "--body", body)
    print(f"pull request: {url}")


def cmd_idea(args):
    idea = " ".join(args).strip()
    if not idea:
        raise SystemExit('usage: collab.py idea "idea, citing the manual section"')
    url = run("gh", "issue", "create", "--repo", UPSTREAM, "--title", f"[idea] {idea}"[:250],
              "--body", "Left with `tools/collab.py idea` for anyone to try.")
    print(url)


CMDS = {"status": cmd_status, "pull-best": cmd_pull_best, "start": cmd_start, "finish": cmd_finish,
        "submit": cmd_submit, "idea": cmd_idea}

if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in CMDS:
        raise SystemExit(__doc__)
    CMDS[sys.argv[1]](sys.argv[2:])
