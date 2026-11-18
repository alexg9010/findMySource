"""Give an example project realistic file dates (git does not store them).

Every output is dated one hour after the last change of the script that writes it,
except outputs listed as "stale", which are dated 30 days *before* it, and files
without a source (raw data, orphans), which are dated 60 days before the oldest script.

    python examples/set_dates.py examples/r-project
"""
import datetime
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from findmysource.cli import git_dates  # same notion of "last change" as fms itself

HOUR, DAY = 3600, 86400


def main(project):
    root = os.path.realpath(project)
    exp = json.load(open(os.path.join(root, "expected.json")))
    commit, dirty = git_dates(root)

    def changed(script):
        p = os.path.join(root, script)
        d = None if p in dirty else commit.get(p)
        return datetime.datetime.fromisoformat(d).timestamp() if d else os.stat(p).st_mtime

    scripts = {s for srcs in exp["outputs"].values() for s in srcs}
    oldest = min(changed(s) for s in scripts)
    for out, srcs in exp["outputs"].items():
        if not srcs: t = oldest - 60 * DAY
        elif out in exp.get("stale", []): t = max(map(changed, srcs)) - 30 * DAY
        else: t = max(map(changed, srcs)) + HOUR
        os.utime(os.path.join(root, out), (t, t))


if __name__ == "__main__":
    for p in sys.argv[1:] or ["examples/r-project", "examples/python-project"]:
        main(p)
