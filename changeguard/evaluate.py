"""Replay-based evaluation: score every historical commit using only the past, compare to labels."""
from __future__ import annotations

import json
import math
import random
from dataclasses import dataclass

from .features import Change, HistoryIndex, compute_signals, DAY
from .gitutil import blame_range, is_fix_subject, old_side_hunks, read_commits
from .model import DEFAULT_WEIGHTS, REPLAY, fit_logistic, score
from .testmap import is_source


@dataclass
class Row:
    sha: str
    ts: int
    lines: int
    feats: dict
    y: int = 0


def szz_labels(repo, commits, max_files=25, max_hunks=60, max_fixes=None, log=None):
    """SZZ-lite: a commit is 'bug-inducing' if a later fix commit removed/changed lines it wrote.
    Fix commits are found by subject keywords, so labels are noisy; treat results accordingly."""
    shas = {c.sha for c in commits}
    fixes = [c for c in commits if is_fix_subject(c.subject) and c.parents]
    if max_fixes:
        fixes = fixes[-max_fixes:]
    inducing = set()
    for i, f in enumerate(fixes):
        if log and i % 50 == 0:
            log(f"  tracing fix {i + 1}/{len(fixes)}")
        if len(f.files) > max_files:
            continue
        hunks = [h for h in old_side_hunks(repo, f.parents[0], f.sha) if is_source(h[0])]
        if not hunks or len(hunks) > max_hunks:
            continue
        for path, start, length in hunks:
            for s in blame_range(repo, f.parents[0], path, start, length):
                if s in shas and s != f.sha:
                    inducing.add(s)
    return inducing, len(fixes)


def build_rows(commits, labels, embargo_days=0):
    idx, rows = HistoryIndex(), []
    cutoff = commits[-1].ts - embargo_days * DAY if commits else 0
    for c in commits:
        if any(is_source(p) for p in c.files) and c.ts <= cutoff:
            sig = compute_signals(Change(c.files, c.author, c.ts), idx)
            rows.append(Row(c.sha, c.ts, sum(a + d for a, d in c.files.values()),
                            {k: sig[k].value for k in REPLAY}, int(c.sha in labels)))
        idx.add(c)
    return rows


def auc(scores, y):
    n1 = sum(y)
    n0 = len(y) - n1
    if n1 == 0 or n0 == 0:
        return None
    order = sorted(range(len(y)), key=lambda i: scores[i])
    ranks, i = [0.0] * len(y), 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and scores[order[j + 1]] == scores[order[i]]:
            j += 1
        for k in range(i, j + 1):
            ranks[order[k]] = (i + j) / 2 + 1
        i = j + 1
    return (sum(r for r, yi in zip(ranks, y) if yi) - n1 * (n1 + 1) / 2) / (n1 * n0)


def auc_ci(scores, y, reps=300, seed=0):
    rnd, n, vals = random.Random(seed), len(y), []
    for _ in range(reps):
        idx = [rnd.randrange(n) for _ in range(n)]
        a = auc([scores[i] for i in idx], [y[i] for i in idx])
        if a is not None:
            vals.append(a)
    vals.sort()
    return (vals[int(0.025 * len(vals))], vals[int(0.975 * len(vals)) - 1]) if len(vals) > 20 else (None, None)


def top_k(scores, y, frac=0.2):
    k = max(1, math.ceil(frac * len(y)))
    order = sorted(range(len(y)), key=lambda i: (-scores[i], i))[:k]
    tp, n1 = sum(y[i] for i in order), sum(y)
    base = n1 / len(y)
    prec = tp / k
    return {"k": k, "precision": prec, "recall": tp / n1 if n1 else None,
            "lift": prec / base if base else None}


def run_evaluation(repo, rev="HEAD", labels_path=None, max_fixes=None, embargo_days=0,
                   split=0.7, seed=1, log=print):
    commits = read_commits(repo, rev)
    if labels_path:
        with open(labels_path) as fh:
            labels = set(json.load(fh)["bug_commits"])
        label_source, n_fixes = f"labels file {labels_path}", None
    else:
        log("Tracing bug-fix commits back to the changes that introduced the bugs (SZZ-lite)...")
        labels, n_fixes = szz_labels(repo, commits, max_fixes=max_fixes, log=log)
        label_source = "SZZ-lite from fix-keyword commits"
    rows = build_rows(commits, labels, embargo_days)
    if len(rows) < 20:
        raise ValueError(f"only {len(rows)} evaluable commits; need more history")
    cut = int(len(rows) * split)
    train, test = rows[:cut], rows[cut:]
    ytest = [r.y for r in test]
    rnd = random.Random(seed)

    def row_score(r, w):
        return score({k: type("S", (), {"value": v})() for k, v in r.feats.items()}, w, only=REPLAY)[0]

    fitted = None
    methods = {
        "random": [rnd.random() for _ in test],
        "lines-changed only": [float(r.lines) for r in test],
        "heuristic (default weights)": [row_score(r, DEFAULT_WEIGHTS) for r in test],
    }
    if sum(r.y for r in train) >= 5 and sum(r.y for r in train) < len(train):
        w, _ = fit_logistic([[r.feats[k] for k in REPLAY] for r in train], [r.y for r in train])
        tot = sum(w) or 1.0
        scale = sum(DEFAULT_WEIGHTS[k] for k in REPLAY)
        fitted = {k: round(wj / tot * scale, 4) for k, wj in zip(REPLAY, w)}
        methods["heuristic (weights fitted on train split)"] = [row_score(r, {**DEFAULT_WEIGHTS, **fitted}) for r in test]

    results = {}
    for name, sc in methods.items():
        lo, hi = auc_ci(sc, ytest)
        results[name] = {"auc": auc(sc, ytest), "auc_ci95": [lo, hi], **top_k(sc, ytest)}
    pos_test = sum(ytest)
    return {
        "label_source": label_source, "fix_commits": n_fixes,
        "commits_evaluated": len(rows), "train": len(train), "test": len(test),
        "positives_train": sum(r.y for r in train), "positives_test": pos_test,
        "base_rate_test": pos_test / len(test), "embargo_days": embargo_days,
        "results": results, "fitted_weights": fitted,
        "warning": "Fewer than 30 positive examples in the test split: results are inconclusive." if pos_test < 30 else None,
    }


def format_evaluation(ev) -> str:
    L = [f"Labels: {ev['label_source']}" + (f" ({ev['fix_commits']} fix commits)" if ev["fix_commits"] else ""),
         f"Commits evaluated: {ev['commits_evaluated']} (train {ev['train']} / test {ev['test']}); "
         f"positives in test: {ev['positives_test']} (base rate {ev['base_rate_test']:.1%})", "",
         f"{'method':44} {'AUC [95% CI]':24} {'prec@20%':9} {'recall@20%':11} lift"]
    for name, r in ev["results"].items():
        a = r["auc"]
        ci = r["auc_ci95"]
        a_s = "n/a" if a is None else f"{a:.2f} [{ci[0]:.2f}, {ci[1]:.2f}]" if ci[0] is not None else f"{a:.2f}"
        rec = "n/a" if r["recall"] is None else f"{r['recall']:.0%}"
        lift = "n/a" if r["lift"] is None else f"{r['lift']:.1f}x"
        L.append(f"{name:44} {a_s:24} {r['precision']:<9.0%} {rec:11} {lift}")
    if ev["fitted_weights"]:
        L += ["", "Fitted weights: " + ", ".join(f"{k}={v}" for k, v in ev["fitted_weights"].items())]
    if ev["warning"]:
        L += ["", "WARNING: " + ev["warning"]]
    return "\n".join(L)
