import os
import subprocess
import tempfile
from pathlib import Path


def run(repo, *a):
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@x.io",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@x.io"}
    return subprocess.run(["git", "-C", str(repo), *a], env=env, check=True, capture_output=True, text=True).stdout


def make_repo(files: dict, msg="init"):
    d = Path(tempfile.mkdtemp())
    run(d, "init", "-q", "-b", "main")
    commit(d, files, msg)
    return d


def commit(d, files: dict, msg):
    for p, c in files.items():
        f = Path(d) / p
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(c)
    run(d, "add", "-A")
    run(d, "commit", "-q", "-m", msg)
