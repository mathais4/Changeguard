"""Classify files as source/test and map source files to tests by naming convention."""
from __future__ import annotations

import posixpath
import re

CODE_EXT = {".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"}
TEST_RE = re.compile(
    r"(^|/)(tests?|__tests__|spec)/|(^|/)test_[^/]*\.py$|_test\.py$|\.(test|spec)\.[jt]sx?$|(^|/)conftest\.py$"
)


def is_test(path: str) -> bool:
    return bool(TEST_RE.search(path))


def is_source(path: str) -> bool:
    return posixpath.splitext(path)[1] in CODE_EXT and not is_test(path)


def _stem(path: str) -> str:
    return posixpath.splitext(posixpath.basename(path))[0]


class TestIndex:
    """Name-convention matching: foo.py <-> test_foo.py, foo_test.py, foo.test.ts, foo.spec.ts."""
    __test__ = False  # not a test class for runners

    def __init__(self, paths):
        self.by_stem = {}
        for p in paths:
            if is_test(p):
                self.by_stem.setdefault(_stem(p), set()).add(p)

    def by_name(self, src: str) -> set:
        s = _stem(src)
        found = set()
        for cand in (f"test_{s}", f"{s}_test", f"{s}.test", f"{s}.spec"):
            found |= self.by_stem.get(cand, set())
        return found

    def tests_for(self, src: str, graph, depth: int = 3) -> set:
        reach = {p for p in graph.dependents([src], depth) if is_test(p)}
        return reach | self.by_name(src)
