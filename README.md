# ChangeGuard

Explainable risk analysis for Git changes. Point it at a pull request and it tells you **which changes deserve
extra review and shows the evidence**: files with a bug-fix history, authors new to the code, missing tests, wide
blast radius. It also **measures itself** against your repo's own history, so you can see how much to trust it.

> It ranks changes for review attention. It does **not** predict production bugs. The score is a transparent
> weighted heuristic, and the evaluation harness exists to show you how well it ranks on *your* history.

Pure Python 3.10+, standard library only, analyses Python and JS/TS repositories.

## Quick start

```bash
pip install .                       # or: PYTHONPATH=. python -m changeguard ...

changeguard report --base origin/main --head HEAD          # Markdown PR report
changeguard report --base origin/main --format json        # machine-readable
changeguard impact src/billing/invoice.py                  # who depends on this file?
changeguard evaluate                                       # replay history, measure ranking quality
changeguard report --base origin/main --eval-file .changeguard/eval.json   # quote the measured quality in the report

changeguard demo --out demo-repo                           # synthetic repo with seeded bugs (see caveat below)
```

GitHub: `.github/workflows/changeguard.yml` posts/updates one sticky PR comment via the REST API
(`--github-repo owner/name --pr N`, token from `GITHUB_TOKEN`). Needs `fetch-depth: 0`. Fork PRs get a read-only token.
`--fail-above N` can gate CI, but it is advisory by default.

## What it looks at

| Signal | Evidence shown | Needs |
|---|---|---|
| Size / spread | lines, files, directories touched | diff |
| Defect history | per-file share of past commits that were fixes (`fix`, `bug`, `regression`, …) | history |
| Recent churn | commits per file in the last 90 days | history |
| Author unfamiliarity | author's share of each file's past changed lines, who owns it instead | history |
| Test gap | source changed, no test file changed (scaled by size) | diff |
| Sensitive paths | auth, security, payment, migration, CI workflow, … | diff |
| Blast radius | transitive dependents from a static import graph (Python AST, JS/TS imports) | graph |
| Coverage gap | changed files that no test imports and no test is named after | graph |

Score = weighted average of the available signals × 100, with per-signal **points** so the report can say
"+12 pts from X". The headline is a **percentile against the last 300 source changes in the same repo**, because an
absolute 0-100 number has no intrinsic meaning but "riskier than 94% of what this team usually ships" does.

## Evaluation (the part that keeps it honest)

`changeguard evaluate` replays every commit in order, computes signals from **only the history before it** (no
leakage), and compares ranking quality on a held-out last 30% of commits against baselines (random, lines-changed
only). Reported: AUC with a bootstrap 95% CI, precision/recall/lift at the top 20% (a "review budget").

Labels, one of:
- **SZZ-lite (default):** a fix commit (found by subject keywords) is traced with `git blame` on the lines it removed
  to the commit that introduced them; that commit is labelled bug-inducing.
- **Seeded bugs:** `--labels file.json` with `{"bug_commits": ["<sha>", ...]}` for deliberately injected bugs.

Known limits, stated plainly:
- Fix-keyword labels are noisy: missed fixes, mislabelled refactors, and recently introduced bugs not yet found
  (use `--embargo-days` to drop the newest commits).
- Replay can't use the import graph (no historical graphs), so blast radius and coverage gap are scored in review mode
  but **not evaluated**. Only the 7 history/diff signals are.
- Small repos give few positives; the harness warns below 30 test positives. Wide CIs mean "inconclusive".
- Static imports only: dynamic imports, dependency injection, reflection and tsconfig path aliases are not seen.
- Renames are not followed (`--no-renames`), so a moved file starts with no history.

### Result on the bundled synthetic repo (seed 7, 320 commits, 22 seeded-bug positives in test)

| method | AUC [95% CI] | precision@20% | lift |
|---|---|---|---|
| random | 0.52 [0.38, 0.63] | 20% | 0.9x |
| lines-changed only | 0.85 [0.77, 0.92] | 50% | 2.2x |
| heuristic (default weights) | 0.74 [0.61, 0.86] | 45% | 2.0x |
| heuristic (weights fitted on train split) | 0.89 [0.80, 0.96] | 60% | 2.6x |

**Read this carefully:** the generator (`changeguard/demo.py`) seeds bugs more often in big, untested, hot-module
commits, so the signals are *built* to work here. This validates the pipeline (leak-free replay, labelling, metrics),
not the heuristic. It also shows something useful: the default weights **lose to a plain lines-changed baseline**, and
fitting weights on history fixes that. That is exactly the check you should run on real repos before trusting a score.
SZZ-lite recovers most of the seeded bugs on this repo (67 fix commits traced).

## Layout

```
changeguard/
  gitutil.py   git CLI wrapper, numstat/blame parsing      depgraph.py  Python + JS/TS import graph
  features.py  signals + evidence, leak-free history index  model.py     weights, scoring, logistic fit
  evaluate.py  SZZ-lite, replay, AUC/CI/top-k               report.py    PR report (md/json)
  github.py    sticky PR comment                            demo.py      seeded-bug repo generator
tests/         unittest suite (python -m unittest discover -s tests -t .)
```

## Ideas to extend

Follow renames; per-language coverage reports as a real test signal; temporal coupling (files that usually change
together); cross-repo evaluation on public datasets (e.g. Defects4J-style); a calibration plot; an SZZ variant that
filters comment/format-only lines.
