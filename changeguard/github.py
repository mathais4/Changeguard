"""Post (or update) the report as a single sticky PR comment. Stdlib only."""
from __future__ import annotations

import json
import urllib.request

from .report import MARKER


def _call(method, url, token, data=None):
    req = urllib.request.Request(url, method=method, data=None if data is None else json.dumps(data).encode(),
                                 headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                                          "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "changeguard"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read() or b"null")


def upsert_comment(repo: str, pr: int, body: str, token: str, api="https://api.github.com", call=_call):
    base = f"{api}/repos/{repo}/issues"
    page, existing = 1, None
    while existing is None:
        comments = call("GET", f"{base}/{pr}/comments?per_page=100&page={page}", token)
        existing = next((c for c in comments if MARKER in (c.get("body") or "")), None)
        if len(comments) < 100:
            break
        page += 1
    if existing:
        return call("PATCH", f"{base}/comments/{existing['id']}", token, {"body": body})
    return call("POST", f"{base}/{pr}/comments", token, {"body": body})
