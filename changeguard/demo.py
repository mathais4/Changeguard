"""Deterministic synthetic repo with *seeded* bugs, for smoke-testing the pipeline.

The generator decides how bugs are seeded (more likely in hot modules, untested and large
commits), so results on it prove the pipeline works end to end, NOT that the heuristic
predicts real defects. Use real history for that.
"""
from __future__ import annotations

import json
import os
import random
import subprocess
from pathlib import Path


def make_demo_repo(out, commits=320, seed=7) -> dict:
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    rnd = random.Random(seed)
    people = ["alice@example.com", "bob@example.com", "carol@example.com"]
    mods = [f"m{i}" for i in range(8)]
    hot = {"m1", "m4"}
    owner = {m: people[i % 3] for i, m in enumerate(mods)}
    deps = {m: rnd.sample(mods[:i], min(i, rnd.randint(0, 2))) for i, m in enumerate(mods)}
    tested = set(mods[:6])
    ts = 1_700_000_000
    sha_bug, state = [], {m: [] for m in mods}  # lines: [fn_no, buggy]
    counter = {m: 0 for m in mods}

    def sh(*a, author=None):
        env = {**os.environ, "GIT_AUTHOR_DATE": f"{ts} +0000", "GIT_COMMITTER_DATE": f"{ts} +0000",
               "GIT_AUTHOR_NAME": (author or people[0]).split("@")[0], "GIT_AUTHOR_EMAIL": author or people[0],
               "GIT_COMMITTER_NAME": (author or people[0]).split("@")[0], "GIT_COMMITTER_EMAIL": author or people[0]}
        return subprocess.run(["git", "-C", str(out), *a], env=env, check=True, capture_output=True, text=True).stdout

    def write(m):
        head = "".join(f"from pkg import {d}\n" for d in deps[m]) or "# no deps\n"
        body = "".join(f"def f{n}(x): return x {'-' if b else '+'} {n}\n" for n, b in state[m])
        (out / "pkg" / f"{m}.py").write_text(head + body)

    def write_test(m, n):
        p = out / "tests" / f"test_{m}.py"
        old = p.read_text() if p.exists() else f"from pkg import {m}\n"
        p.write_text(old + f"def test_f{n}(): assert {m}.f{n}(1) == {1 + n}\n")

    sh("init", "-q", "-b", "main")
    (out / "pkg").mkdir(exist_ok=True)
    (out / "tests").mkdir(exist_ok=True)
    (out / "pkg" / "__init__.py").write_text("")
    for m in mods:
        counter[m] = 1
        state[m].append([1, False])
        write(m)
        if m in tested:
            write_test(m, 1)
    sh("add", "-A")
    sh("commit", "-q", "-m", "chore: scaffold")
    pending = []  # (due_index, module, fn_no)
    for k in range(commits):
        ts += rnd.randint(3600, 2 * 86400)
        due = [p for p in pending if p[0] <= k]
        if due:
            _, m, n = due[0]
            pending.remove(due[0])
            for line in state[m]:
                if line[0] == n:
                    line[1] = False
            write(m)
            sh("commit", "-q", "-am", f"fix: wrong sign in {m}.f{n}", author=rnd.choice(people))
            continue
        m = rnd.choices(mods, [3 if x in hot else 1 for x in mods])[0]
        author = owner[m] if rnd.random() < 0.8 else rnd.choice(people)
        n_new = rnd.choice([1, 1, 1, 2, 3, 6, 9])
        for _ in range(n_new):
            counter[m] += 1
            state[m].append([counter[m], False])
        with_test = m in tested and rnd.random() < 0.35
        p_bug = min(0.6, 0.06 * (3 if m in hot else 1) * (1 if with_test or m not in tested and False else 2.5)
                    * (1.8 if n_new >= 6 else 1) * (1.6 if author != owner[m] else 1))
        if with_test:
            p_bug = min(0.6, 0.06 * (3 if m in hot else 1) * (1.8 if n_new >= 6 else 1) * (1.6 if author != owner[m] else 1))
        clean = [ln for ln in state[m][:-n_new] if not ln[1]]
        bug = rnd.random() < p_bug and bool(clean)
        if bug:
            victim = rnd.choice(clean)
            victim[1] = True
            pending.append((k + rnd.randint(4, 25), m, victim[0]))
        write(m)
        if with_test:
            write_test(m, counter[m])
        sh("commit", "-q", "-am", f"feat: extend {m} with {n_new} function(s)", author=author)
        if bug:
            sha_bug.append(sh("rev-parse", "HEAD").strip())
    labels = {"bug_commits": sha_bug,
              "note": "Seeded bugs: ground truth from the generator. Synthetic data, see changeguard/demo.py."}
    (out.parent / f"{out.name}.labels.json").write_text(json.dumps(labels, indent=2))
    return labels
