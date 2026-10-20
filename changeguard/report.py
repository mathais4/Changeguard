"""Build and render the pull-request risk report."""
from __future__ import annotations

from collections import Counter

from .depgraph import build_graph
from .features import Change, HistoryIndex, compute_signals
from .gitutil import GitError, git, merge_base, numstat_between, read_commits
from .model import DEFAULT_WEIGHTS, REPLAY, score
from .testmap import TestIndex, is_source, is_test

MARKER = "<!-- changeguard:report -->"
TITLES = {
    "size": "Large change", "spread": "Spread across directories",
    "defect_history": "Files with a bug-fix history", "churn_recent": "Recently volatile files",
    "unfamiliarity": "Author new to these files", "test_gap": "No tests changed",
    "sensitive": "Sensitive paths", "blast_radius": "Wide blast radius",
    "coverage_gap": "Changed code no test reaches",
}


def replay_scores(commits, weights, window):
    idx, out, start = HistoryIndex(), [], max(0, len(commits) - window)
    for i, c in enumerate(commits):
        if i >= start and any(is_source(p) for p in c.files):
            sig = compute_signals(Change(c.files, c.author, c.ts), idx)
            out.append(score(sig, weights, only=REPLAY)[0])
        idx.add(c)
    return out, idx


def _band(total, pct):
    if pct is not None:
        return "HIGH" if pct >= 90 else "ELEVATED" if pct >= 70 else "NORMAL"
    return "HIGH" if total >= 45 else "ELEVATED" if total >= 25 else "LOW"


def build_report(repo, base, head="HEAD", weights=None, eval_data=None, window=300) -> dict:
    weights = weights or DEFAULT_WEIGHTS
    try:
        mb = merge_base(repo, base, head)
    except GitError as e:
        raise GitError(f"{e}\nHint: in CI use `fetch-depth: 0` so the base branch history is available.")
    files = numstat_between(repo, mb, head)
    if not files:
        raise GitError(f"no changes between {base} and {head}")
    commits = read_commits(repo, mb)
    hist_scores, index = replay_scores(commits, weights, window)
    email, ts, short, subject = git(repo, "log", "-1", "--format=%ae%x1f%at%x1f%h%x1f%s", head).strip().split("\x1f", 3)
    change = Change(files, email.lower(), int(ts))

    graph = build_graph(repo, head)
    tidx = TestIndex(graph.files)
    sigs = compute_signals(change, index, graph, tidx)
    total, points = score(sigs, weights)
    replay_total = score(sigs, weights, only=REPLAY)[0]
    pct = (100 * sum(1 for s in hist_scores if s < replay_total) / len(hist_scores)) if len(hist_scores) >= 30 else None

    src = [p for p in files if is_source(p)]
    dep = graph.dependents(src, 4)
    impacted = sorted((d, p) for p, d in dep.items() if not is_test(p) and p not in files)
    reaching = {p for s in src for p in tidx.tests_for(s, graph)}

    owners = Counter()
    for p in src:
        st = index.stats(p)
        if st:
            owners.update(st.by_author)
    owners.pop(change.author, None)
    total_owned = sum(owners.values()) or 1
    reviewers = [{"author": a, "share": n / total_owned} for a, n in owners.most_common(3)]

    per_file = []
    for p, (a, d) in sorted(files.items(), key=lambda kv: -sum(kv[1])):
        st = index.stats(p)
        per_file.append({"path": p, "added": a, "deleted": d, "prior_commits": st.commits if st else 0,
                         "prior_fixes": st.fixes if st else 0,
                         "tests_reaching": len(tidx.tests_for(p, graph)) if is_source(p) else None})

    evidence = [{"signal": k, "title": TITLES[k], "value": round(sigs[k].value, 3), "weight": weights.get(k, 0),
                 "points": round(points[k], 1), "headline": sigs[k].headline, "details": sigs[k].details}
                for k in sorted(points, key=lambda k: -points[k]) if points[k] >= 0.5]
    return {
        "base": base, "head": head, "head_sha": short, "head_subject": subject, "author": change.author,
        "score": round(total, 1), "percentile": None if pct is None else round(pct, 1),
        "history_window": len(hist_scores), "band": _band(total, pct),
        "lines_added": sum(a for a, _ in files.values()), "lines_deleted": sum(d for _, d in files.values()),
        "files": per_file, "evidence": evidence,
        "impacted_modules": [{"distance": d, "path": p} for d, p in impacted],
        "tests_reaching_change": sorted(reaching), "tests_changed": sorted(p for p in files if is_test(p)),
        "suggested_reviewers": reviewers, "weights": weights,
        "evaluation": None if not eval_data else {
            "label_source": eval_data["label_source"], "test_commits": eval_data["test"],
            "positives_test": eval_data["positives_test"],
            "auc": eval_data["results"].get("heuristic (default weights)", {}).get("auc"),
            "auc_ci95": eval_data["results"].get("heuristic (default weights)", {}).get("auc_ci95")},
    }


def render_markdown(r: dict) -> str:
    L = [MARKER, "## 🛡️ ChangeGuard review", ""]
    pct = (f" — scores higher than **{r['percentile']:.0f}%** of the last {r['history_window']} source changes in this repo"
           if r["percentile"] is not None else " — not enough history for a relative ranking (absolute bands used)")
    L += [f"**Review priority: {r['band']}** (score {r['score']:.0f}/100){pct}.", "",
          f"`{r['head_sha']}` {r['head_subject']} · {len(r['files'])} files, +{r['lines_added']}/-{r['lines_deleted']}", ""]
    L.append("### Why")
    if not r["evidence"]:
        L.append("No signal contributed meaningfully. That is an observation about these signals, not a guarantee.")
    for i, e in enumerate(r["evidence"], 1):
        L.append(f"{i}. **{e['title']}** (+{e['points']:.0f} pts) — {e['headline']}")
        L += [f"   - {d}" for d in e["details"]]
    L += ["", "### Blast radius"]
    if r["impacted_modules"]:
        L.append(f"{len(r['impacted_modules'])} non-test module(s) import the changed code, directly or transitively:")
        L += [f"- `{m['path']}` (distance {m['distance']})" for m in r["impacted_modules"][:8]]
        if len(r["impacted_modules"]) > 8:
            L.append(f"- … and {len(r['impacted_modules']) - 8} more")
    else:
        L.append("No other module imports the changed code (per static imports).")
    L += ["", "### Tests"]
    L.append("Changed in this PR: " + (", ".join(f"`{t}`" for t in r["tests_changed"]) or "none"))
    reach = r["tests_reaching_change"]
    L.append(f"Tests that import or are named after the changed code: {len(reach)}"
             + (" — " + ", ".join(f"`{t}`" for t in reach[:5]) if reach else ""))
    if r["suggested_reviewers"]:
        L += ["", "### Who knows this code",
              ", ".join(f"{v['author'].split('@')[0]} ({v['share']:.0%} of prior changes)" for v in r["suggested_reviewers"])]
    L += ["", "<details><summary>Per-file detail</summary>", "",
          "| file | +/- | prior commits | prior fixes | tests reaching |", "|---|---|---|---|---|"]
    for f in r["files"][:15]:
        tr = "–" if f["tests_reaching"] is None else f["tests_reaching"]
        L.append(f"| `{f['path']}` | +{f['added']}/-{f['deleted']} | {f['prior_commits']} | {f['prior_fixes']} | {tr} |")
    L += ["", "</details>", "", "<sub>"]
    L.append("This is a transparent heuristic that ranks changes for review attention. The score is **not** a probability "
             "of defects. Static imports only (no dynamic imports, DI or reflection); history signals need ≥3 prior commits per file.")
    ev = r.get("evaluation")
    if ev and ev["auc"] is not None:
        ci = ev["auc_ci95"]
        L.append(f" Measured on this repo's history ({ev['label_source']}): AUC {ev['auc']:.2f}"
                 + (f" [{ci[0]:.2f}–{ci[1]:.2f}]" if ci and ci[0] is not None else "")
                 + f", {ev['positives_test']} positives in the test split.")
    else:
        L.append(" No evaluation has been run for this repo, so treat the ranking as unvalidated (`changeguard evaluate`).")
    L.append("</sub>")
    return "\n".join(L)
