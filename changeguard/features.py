"""Evidence signals. Each signal is a value in [0,1] plus human-readable proof."""
from __future__ import annotations

import bisect
import math
import posixpath
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from .gitutil import Commit, is_fix_subject
from .testmap import is_source, is_test

DAY = 86400
IGNORE_RE = re.compile(r"(^|/)(package-lock\.json|yarn\.lock|pnpm-lock\.yaml|poetry\.lock|Pipfile\.lock)$|\.min\.(js|css)$|\.snap$")
SENSITIVE_RE = re.compile(
    r"(auth|security|crypto|payment|billing|migration|permission|session|token|secret|password|\.github/workflows/|dockerfile)",
    re.I)


@dataclass
class FileStats:
    commits: int = 0
    fixes: int = 0
    total: int = 0
    by_author: Counter = field(default_factory=Counter)
    times: list = field(default_factory=list)  # sorted


class HistoryIndex:
    """Per-file history accumulated commit by commit. Add commits in order; query before adding
    the commit under evaluation, so features never see the future."""

    def __init__(self):
        self.files = defaultdict(FileStats)

    def add(self, c: Commit):
        fix = is_fix_subject(c.subject)
        for p, (a, d) in c.files.items():
            s = self.files[p]
            s.commits += 1
            s.fixes += fix
            s.total += a + d
            s.by_author[c.author] += a + d
            bisect.insort(s.times, c.ts)

    def stats(self, path):
        return self.files.get(path)

    def recent(self, path, ts, days=90) -> int:
        s = self.files.get(path)
        if not s:
            return 0
        return bisect.bisect_right(s.times, ts) - bisect.bisect_left(s.times, ts - days * DAY)


@dataclass
class Change:
    files: dict  # path -> (added, deleted)
    author: str
    ts: int


@dataclass
class Signal:
    value: float | None
    headline: str = ""
    details: list = field(default_factory=list)


def _clip(x):
    return max(0.0, min(1.0, x))


def _paths(ps, n=4):
    ps = list(ps)
    s = ", ".join(f"`{p}`" for p in ps[:n])
    return s + (f" (+{len(ps) - n} more)" if len(ps) > n else "")


def _who(email):
    return email.split("@")[0]


def compute_signals(change: Change, index: HistoryIndex, graph=None, tests=None) -> dict:
    files = {p: v for p, v in change.files.items() if not IGNORE_RE.search(p)}
    lines = sum(a + d for a, d in files.values())
    src = [p for p in files if is_source(p)]
    src_lines = sum(sum(files[p]) for p in src)
    sig = {}

    # size
    top = sorted(files, key=lambda p: -sum(files[p]))[:3]
    sig["size"] = Signal(_clip(math.log1p(lines) / math.log1p(400)),
                         f"{lines} lines changed across {len(files)} files",
                         [f"largest: `{p}` ({sum(files[p])} line(s))" for p in top])

    # spread
    dirs = sorted({posixpath.dirname(p) or "." for p in files if not is_test(p)})
    sig["spread"] = Signal(_clip((len(dirs) - 1) / 5),
                           f"touches {len(dirs)} director{'y' if len(dirs) == 1 else 'ies'}",
                           [_paths(dirs, 6)] if len(dirs) > 1 else [])

    # defect history: share of past commits on the file that were fixes
    rows = []
    for p in src:
        s = index.stats(p)
        if s and s.commits >= 3:
            rows.append((s.fixes / (s.commits + 2), p, s))
    rows.sort(reverse=True)
    hot = [r for r in rows if r[0] >= 0.15][:5]
    sig["defect_history"] = Signal(
        _clip((rows[0][0] if rows else 0) / 0.4),
        "touches files with a record of bug-fix commits" if hot else "no file with a notable fix history",
        [f"`{p}`: {s.fixes} of {s.commits} past commits were fixes" for _, p, s in hot])

    # recent churn
    churn = sorted(((index.recent(p, change.ts), p) for p in src), reverse=True)
    busy = [(n, p) for n, p in churn if n >= 3][:5]
    sig["churn_recent"] = Signal(_clip((churn[0][0] if churn else 0) / 8),
                                 "touches files that changed a lot in the last 90 days" if busy else "no unusually hot files",
                                 [f"`{p}`: {n} commits in the last 90 days" for n, p in busy])

    # author unfamiliarity (line-weighted over files with enough history)
    num = den = 0.0
    unf = []
    for p in src:
        s = index.stats(p)
        if not s or s.commits < 3 or s.total == 0:
            continue
        share = s.by_author.get(change.author, 0) / s.total
        w = sum(files[p]) + 1
        num += w * (1 - share)
        den += w
        owner, owned = s.by_author.most_common(1)[0]
        if share < 0.15:
            unf.append((1 - share, f"`{p}`: {_who(change.author)} wrote {share:.0%} of its history; "
                                   f"main owner is {_who(owner)} ({owned / s.total:.0%})"))
    unf.sort(reverse=True)
    sig["unfamiliarity"] = Signal(_clip(num / den) if den else 0.0,
                                  f"{_who(change.author)} has little history in the touched files" if unf else "author is familiar with the touched files",
                                  [d for _, d in unf[:4]])

    # test gap: source changed, no test file changed
    tests_changed = [p for p in files if is_test(p)]
    if src and not tests_changed:
        sig["test_gap"] = Signal(_clip(src_lines / 20),
                                 f"{len(src)} source file(s), {src_lines} lines changed, no test files changed",
                                 [_paths(src)])
    else:
        sig["test_gap"] = Signal(0.0, "tests changed alongside source" if tests_changed else "no source changes", [])

    # sensitive paths
    sens = [p for p in files if not is_test(p) and SENSITIVE_RE.search(p)]
    sig["sensitive"] = Signal(1.0 if sens else 0.0,
                              "touches security/migration/CI-sensitive paths" if sens else "no sensitive paths",
                              [_paths(sens)] if sens else [])

    # graph-based signals (review mode only; replay leaves them None)
    if graph is not None:
        changed = set(files)
        dep = graph.dependents(src, 4)
        affected = sorted(((d, p) for p, d in dep.items() if not is_test(p) and p not in changed))
        direct = sum(1 for d, _ in affected if d == 1)
        sig["blast_radius"] = Signal(
            _clip(math.log1p(len(affected)) / math.log1p(25)),
            f"{len(affected)} other module(s) depend on the changed code ({direct} directly)" if affected else "nothing else imports the changed code",
            [f"distance {d}: `{p}`" for d, p in affected[:5]])
        if tests is not None and src:
            uncovered = [p for p in src if not tests.tests_for(p, graph)]
            unc_lines = sum(sum(files[p]) for p in uncovered)
            sig["coverage_gap"] = Signal(
                _clip(unc_lines / src_lines) if src_lines else 0.0,
                f"{len(uncovered)} changed file(s) have no test that imports them or matches their name" if uncovered else "every changed file is reached by a test",
                [_paths(uncovered)] if uncovered else [])
    else:
        sig["blast_radius"] = Signal(None)
        sig["coverage_gap"] = Signal(None)
    return sig
