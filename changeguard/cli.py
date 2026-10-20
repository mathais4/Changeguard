from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .gitutil import GitError


def _cmd_report(a):
    from .model import DEFAULT_WEIGHTS
    from .report import build_report, render_markdown
    weights = {**DEFAULT_WEIGHTS, **json.load(open(a.weights))} if a.weights else None
    ev = json.load(open(a.eval_file)) if a.eval_file else None
    if ev and not weights and ev.get("fitted_weights") and a.use_fitted:
        weights = {**DEFAULT_WEIGHTS, **ev["fitted_weights"]}
    r = build_report(a.repo, a.base, a.head, weights, ev, a.window)
    text = json.dumps(r, indent=2) if a.format == "json" else render_markdown(r)
    if a.output:
        Path(a.output).write_text(text)
    else:
        print(text)
    if a.github_repo:
        from .github import upsert_comment
        token = os.environ.get("GITHUB_TOKEN")
        if not token or not a.pr:
            raise SystemExit("--github-repo needs --pr and GITHUB_TOKEN")
        upsert_comment(a.github_repo, a.pr, render_markdown(r), token)
        print(f"Posted to {a.github_repo}#{a.pr}", file=sys.stderr)
    if a.fail_above is not None and r["score"] > a.fail_above:
        print(f"score {r['score']} > {a.fail_above}", file=sys.stderr)
        return 1
    return 0


def _cmd_evaluate(a):
    from .evaluate import format_evaluation, run_evaluation
    ev = run_evaluation(a.repo, a.rev, a.labels, a.max_fixes, a.embargo_days, log=lambda m: print(m, file=sys.stderr))
    print(format_evaluation(ev))
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(json.dumps(ev, indent=2))
        print(f"\nWrote {a.out}", file=sys.stderr)
    return 0


def _cmd_impact(a):
    from .depgraph import build_graph
    g = build_graph(a.repo, a.rev)
    for f in a.files:
        dep = g.dependents([f], a.depth)
        print(f"{f}: {len(dep)} dependent file(s)")
        for p, d in sorted(dep.items(), key=lambda kv: (kv[1], kv[0])):
            print(f"  [{d}] {p}")
    return 0


def _cmd_demo(a):
    from .demo import make_demo_repo
    labels = make_demo_repo(a.out, a.commits, a.seed)
    print(f"Created {a.out} with {len(labels['bug_commits'])} seeded bug commits; labels at {a.out}.labels.json")
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(prog="changeguard", description="Explainable risk analysis for Git changes.")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("report", help="risk report for base..head")
    r.add_argument("--repo", default=".")
    r.add_argument("--base", default="origin/main")
    r.add_argument("--head", default="HEAD")
    r.add_argument("--format", choices=["md", "json"], default="md")
    r.add_argument("--output")
    r.add_argument("--weights", help="JSON file overriding signal weights")
    r.add_argument("--eval-file", help="output of `evaluate`, quoted in the report")
    r.add_argument("--use-fitted", action="store_true", help="use weights fitted by `evaluate`")
    r.add_argument("--window", type=int, default=300, help="past changes used for the percentile")
    r.add_argument("--github-repo")
    r.add_argument("--pr", type=int)
    r.add_argument("--fail-above", type=float, help="exit 1 if score exceeds this (advisory by default)")
    r.set_defaults(fn=_cmd_report)

    e = sub.add_parser("evaluate", help="replay history and measure ranking quality")
    e.add_argument("--repo", default=".")
    e.add_argument("--rev", default="HEAD")
    e.add_argument("--labels", help="JSON {\"bug_commits\": [sha, ...]} instead of SZZ-lite")
    e.add_argument("--max-fixes", type=int)
    e.add_argument("--embargo-days", type=int, default=0, help="drop the newest N days (bugs not yet discovered)")
    e.add_argument("--out", default=".changeguard/eval.json")
    e.set_defaults(fn=_cmd_evaluate)

    i = sub.add_parser("impact", help="show dependents of files")
    i.add_argument("files", nargs="+")
    i.add_argument("--repo", default=".")
    i.add_argument("--rev", default="HEAD")
    i.add_argument("--depth", type=int, default=4)
    i.set_defaults(fn=_cmd_impact)

    d = sub.add_parser("demo", help="generate a synthetic repo with seeded bugs")
    d.add_argument("--out", default="demo-repo")
    d.add_argument("--commits", type=int, default=320)
    d.add_argument("--seed", type=int, default=7)
    d.set_defaults(fn=_cmd_demo)

    a = p.parse_args(argv)
    try:
        return a.fn(a)
    except (GitError, ValueError) as ex:
        print(f"error: {ex}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
