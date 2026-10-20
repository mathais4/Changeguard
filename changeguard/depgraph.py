"""Static import graph for Python and JS/TS, built from a Git revision (no checkout needed)."""
from __future__ import annotations

import ast
import posixpath
import re
from collections import defaultdict, deque

from .gitutil import ls_tree, read_blobs
from .testmap import CODE_EXT

JS_EXT = [".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"]
_JS_RE = re.compile(
    r"""(?:import|export)\s[^'"]*?from\s*['"]([^'"]+)['"]"""
    r"""|import\s*['"]([^'"]+)['"]"""
    r"""|require\(\s*['"]([^'"]+)['"]\s*\)"""
    r"""|import\(\s*['"]([^'"]+)['"]\s*\)"""
)


class Graph:
    def __init__(self, files):
        self.files = set(files)
        self.imports = defaultdict(set)    # a -> files a imports
        self.importers = defaultdict(set)  # b -> files importing b

    def add_edge(self, a, b):
        if a != b:
            self.imports[a].add(b)
            self.importers[b].add(a)

    def dependents(self, seeds, max_depth=4) -> dict:
        """Files that (transitively) import any seed -> distance. Seeds excluded."""
        seeds = set(seeds)
        dist, q = {}, deque((s, 0) for s in seeds)
        seen = set(seeds)
        while q:
            node, d = q.popleft()
            if d >= max_depth:
                continue
            for imp in self.importers.get(node, ()):
                if imp not in seen:
                    seen.add(imp)
                    dist[imp] = d + 1
                    q.append((imp, d + 1))
        return dist


class _PyModules:
    def __init__(self, paths):
        self.exact, self.suffix = {}, defaultdict(set)
        for p in paths:
            if not p.endswith(".py"):
                continue
            parts = p[:-3].split("/")
            if parts[-1] == "__init__":
                parts = parts[:-1]
            if not parts:
                continue
            self.exact[".".join(parts)] = p
            for i in range(len(parts)):
                self.suffix[".".join(parts[i:])].add(p)

    def lookup(self, dotted, from_file=None, exact=False):
        if exact:
            return self.exact.get(dotted)
        cands = self.suffix.get(dotted)
        if not cands:
            return None
        if len(cands) == 1:
            return next(iter(cands))
        base = from_file.split("/")[:-1] if from_file else []

        def shared(c):
            n = 0
            for x, y in zip(base, c.split("/")):
                if x != y:
                    break
                n += 1
            return n
        return max(sorted(cands), key=shared)


def _py_imports(path, src, mods: _PyModules) -> set:
    try:
        tree = ast.parse(src)
    except (SyntaxError, ValueError):
        return set()
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                t = mods.lookup(a.name, path)
                if t:
                    out.add(t)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0:
                base = node.module or ""
                for a in node.names:
                    t = (mods.lookup(f"{base}.{a.name}", path) if base else mods.lookup(a.name, path)) \
                        or (mods.lookup(base, path) if base else None)
                    if t:
                        out.add(t)
            else:
                pkg = path.split("/")[:-1]
                if node.level > 1:
                    pkg = pkg[:len(pkg) - (node.level - 1)]
                base = ".".join(pkg + (node.module.split(".") if node.module else []))
                for a in node.names:
                    full = f"{base}.{a.name}" if base else a.name
                    t = mods.lookup(full, path, exact=True) or (mods.lookup(base, path, exact=True) if base else None)
                    if t:
                        out.add(t)
    return out


def _js_imports(path, src, files: set) -> set:
    out, here = set(), posixpath.dirname(path)
    for m in _JS_RE.finditer(src):
        spec = next(g for g in m.groups() if g)
        if not spec.startswith("."):
            continue  # bare specifiers (packages, tsconfig aliases) are not resolved
        base = posixpath.normpath(posixpath.join(here, spec))
        cands = [base] + [base + e for e in JS_EXT] + [f"{base}/index{e}" for e in JS_EXT]
        if base.endswith((".js", ".jsx")):  # TS ESM style: import './a.js' -> a.ts
            stem = base.rsplit(".", 1)[0]
            cands += [stem + e for e in (".ts", ".tsx")]
        for c in cands:
            if c in files:
                out.add(c)
                break
    return out


def build_graph(repo, rev="HEAD") -> Graph:
    paths = [p for p in ls_tree(repo, rev) if posixpath.splitext(p)[1] in CODE_EXT]
    g = Graph(paths)
    blobs = read_blobs(repo, rev, paths)
    mods, fileset = _PyModules(paths), set(paths)
    for p, src in blobs.items():
        targets = _py_imports(p, src, mods) if p.endswith(".py") else _js_imports(p, src, fileset)
        for t in targets:
            g.add_edge(p, t)
    return g
