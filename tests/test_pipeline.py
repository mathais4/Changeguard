import json
import tempfile
import unittest
from pathlib import Path

from changeguard.demo import make_demo_repo
from changeguard.evaluate import auc, build_rows, run_evaluation, szz_labels, top_k
from changeguard.features import Change, HistoryIndex, compute_signals
from changeguard.gitutil import Commit, read_commits
from changeguard.github import upsert_comment
from changeguard.model import DEFAULT_WEIGHTS, REPLAY, score
from changeguard.report import MARKER, build_report, render_markdown


class MetricTests(unittest.TestCase):
    def test_auc_known_values(self):
        self.assertEqual(auc([0.1, 0.2, 0.8, 0.9], [0, 0, 1, 1]), 1.0)
        self.assertEqual(auc([0.9, 0.8, 0.2, 0.1], [0, 0, 1, 1]), 0.0)
        self.assertEqual(auc([0.5, 0.5, 0.5, 0.5], [0, 1, 0, 1]), 0.5)
        self.assertIsNone(auc([1, 2], [1, 1]))

    def test_top_k(self):
        r = top_k([5, 4, 3, 2, 1], [1, 0, 0, 0, 1], 0.4)
        self.assertEqual((r["k"], r["precision"], r["recall"]), (2, 0.5, 0.5))


class FeatureTests(unittest.TestCase):
    def test_no_future_leakage_and_signals(self):
        idx = HistoryIndex()
        for i in range(5):
            idx.add(Commit(f"s{i}", [], "a@x", 1000 + i, "fix: thing" if i < 3 else "feat", {"src/a.py": (3, 1)}))
        ch = Change({"src/a.py": (10, 2), "src/auth/login.py": (1, 0)}, "b@x", 2000)
        sig = compute_signals(ch, idx)
        self.assertGreater(sig["defect_history"].value, 0.5)
        self.assertEqual(sig["unfamiliarity"].value, 1.0)
        self.assertAlmostEqual(sig["test_gap"].value, 13 / 20)  # scaled by size: tiny untested edits matter less
        self.assertEqual(sig["sensitive"].value, 1.0)
        self.assertIsNone(sig["blast_radius"].value)
        with_tests = compute_signals(Change({"src/a.py": (10, 2), "tests/test_a.py": (5, 0)}, "b@x", 2000), idx)
        self.assertEqual(with_tests["test_gap"].value, 0.0)
        total, pts = score(sig, DEFAULT_WEIGHTS, only=REPLAY)
        self.assertAlmostEqual(total, sum(pts.values()))
        self.assertTrue(0 <= total <= 100)


class EndToEnd(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        cls.repo = cls.tmp / "demo"
        cls.labels = make_demo_repo(cls.repo, commits=140, seed=3)

    def test_szz_recovers_most_seeded_bugs_that_were_fixed(self):
        commits = read_commits(self.repo)
        found, n_fix = szz_labels(self.repo, commits)
        truth = set(self.labels["bug_commits"])
        self.assertGreater(n_fix, 5)
        self.assertEqual(len(found - truth), 0, "SZZ should not invent bug commits in the demo repo")
        self.assertGreater(len(found) / len(truth), 0.6)

    def test_replay_rows_use_only_replay_signals(self):
        rows = build_rows(read_commits(self.repo), set(self.labels["bug_commits"]))
        self.assertTrue(all(set(r.feats) == set(REPLAY) for r in rows))
        self.assertTrue(all(v is not None and 0 <= v <= 1 for r in rows for v in r.feats.values()))

    def test_evaluation_runs_both_label_modes(self):
        lp = self.tmp / "labels.json"
        lp.write_text(json.dumps(self.labels))
        for path in (str(lp), None):
            ev = run_evaluation(self.repo, labels_path=path, log=lambda m: None)
            self.assertIn("random", ev["results"])
            self.assertGreater(ev["results"]["heuristic (default weights)"]["auc"], 0.5)

    def test_report_is_explained_and_json_serialisable(self):
        r = build_report(self.repo, "HEAD~3", "HEAD")
        json.dumps(r)
        self.assertTrue(0 <= r["score"] <= 100)
        pts = [e["points"] for e in r["evidence"]]
        self.assertEqual(pts, sorted(pts, reverse=True))
        self.assertTrue(all(e["headline"] for e in r["evidence"]))
        md = render_markdown(r)
        self.assertIn(MARKER, md)
        self.assertIn("not** a probability", md)

    def test_empty_diff_is_an_error(self):
        from changeguard.gitutil import GitError
        with self.assertRaises(GitError):
            build_report(self.repo, "HEAD", "HEAD")


class GithubTests(unittest.TestCase):
    def test_updates_existing_sticky_comment_else_creates(self):
        calls = []

        def fake(existing):
            def call(method, url, token, data=None):
                calls.append((method, url))
                return existing if method == "GET" else {"ok": True}
            return call
        upsert_comment("o/r", 7, "body", "t", call=fake([{"id": 1, "body": "hi"}, {"id": 9, "body": MARKER + " old"}]))
        self.assertEqual(calls[-1][0], "PATCH")
        self.assertTrue(calls[-1][1].endswith("/comments/9"))
        calls.clear()
        upsert_comment("o/r", 7, "body", "t", call=fake([{"id": 1, "body": "hi"}]))
        self.assertEqual(calls[-1][0], "POST")


if __name__ == "__main__":
    unittest.main()
