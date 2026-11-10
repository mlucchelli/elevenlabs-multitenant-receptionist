#!/usr/bin/env python3
"""Per-test pass/fail across recent CI runs, from the workflow logs.

These are LLM simulations judged by an LLM, and the suite's per-test round-0
failure rate sits around 13-20%. At that noise level a single run cannot tell
you whether a change helped: the failures move around between runs while the
total barely shifts. Reading one green run as proof is a mistake this table
exists to prevent.

What it is good for is the shape of the column. A test that fails in every leg
of every run is structurally broken and worth reading the rationale for. A test
that fails in two legs out of eight is the noise floor doing its thing.

Usage:
  python3 ci/test_stability.py             # last 4 CI runs on this branch
  python3 ci/test_stability.py --runs 8
  python3 ci/test_stability.py --run 34873844014 --run 34871614161
"""

import argparse
import collections
import json
import re
import subprocess
import sys

REPO = "mlucchelli/elevenlabs-multitenant-receptionist"
VERDICT = re.compile(r"\s*([✓✗~]) (.+?): (passed|failed|no verdict)")
TIMESTAMP = re.compile(r"^[0-9T:.Z-]+Z ?")


def gh(*args):
    out = subprocess.run(("gh",) + args, capture_output=True, text=True)
    if out.returncode:
        raise SystemExit(f"gh {' '.join(args)} failed:\n{out.stderr[:500]}")
    return out.stdout


def recent_runs(n, branch):
    raw = gh("run", "list", "--repo", REPO, "--workflow=ci.yml", "--limit", str(n * 3),
             "--json", "databaseId,headBranch,status")
    runs = [r for r in json.loads(raw)
            if r["status"] == "completed" and (not branch or r["headBranch"] == branch)]
    return [str(r["databaseId"]) for r in runs[:n]]


def leg_results(run_id, leg):
    """{test name: '.' | 'X'} for round 0 only.

    Round 0 is the honest sample. Later rounds re-roll only what already failed,
    so counting them would weight the flaky tests several times over and make
    every run look worse than it was.
    """
    raw = gh("run", "view", run_id, "--repo", REPO, "--json", "jobs")
    job = next((j for j in json.loads(raw)["jobs"] if j["name"] == leg), None)
    if not job:
        return {}
    log = gh("api", f"repos/{REPO}/actions/jobs/{job['databaseId']}/logs")
    out = {}
    for line in log.splitlines():
        line = TIMESTAMP.sub("", line)
        if "re-running" in line and "round 1" in line:
            break
        m = VERDICT.match(line)
        if m and m.group(2) not in out:
            out[m.group(2)] = "." if m.group(1) == "✓" else "X"
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=4)
    ap.add_argument("--run", action="append", default=[], help="explicit run id; repeatable")
    ap.add_argument("--branch", default=None, help="only runs on this branch")
    ap.add_argument("--legs", default="standard,hipaa")
    args = ap.parse_args()

    run_ids = args.run or recent_runs(args.runs, args.branch)
    if not run_ids:
        raise SystemExit("no completed CI runs found")
    legs = args.legs.split(",")

    res, cols = collections.defaultdict(dict), []
    for rid in reversed(run_ids):
        for leg in legs:
            col = f"{rid[-4:]}/{leg[:3]}"
            cols.append(col)
            for name, verdict in leg_results(rid, leg).items():
                res[name][col] = verdict

    if not res:
        raise SystemExit("no test verdicts found in those runs")

    width = max(len(n) for n in res) + 2
    print(" " * width + "".join(f"{c:>10}" for c in cols) + "   fails")
    rows = sorted(res.items(), key=lambda kv: (-sum(v == "X" for v in kv[1].values()), kv[0]))
    for name, d in rows:
        fails = sum(1 for c in cols if d.get(c) == "X")
        seen = sum(1 for c in cols if d.get(c))
        flag = "  <- structural?" if seen and fails == seen else ""
        print(f"{name:{width}}" + "".join(f"{d.get(c, '-'):>10}" for c in cols)
              + f"   {fails}/{seen}{flag}")

    total = sum(1 for n in res for c in cols if res[n].get(c))
    bad = sum(1 for n in res for c in cols if res[n].get(c) == "X")
    print(f"\n  round-0 failure rate: {bad}/{total} = {100 * bad / total:.1f}%")
    print("  '-' means the test did not run in that leg (skipped, or not yet provisioned).")
    print("  A column that is X everywhere is a broken test. A scatter is the noise floor.")


if __name__ == "__main__":
    sys.exit(main())
