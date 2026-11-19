"""Run fms on the example projects and compare with their expected.json ground truth.

    pytest tests/test_examples.py      or      python tests/test_examples.py   (prints a score)
"""
import csv
import json
import os
import shutil
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "examples"))
import set_dates  # noqa: E402


def run(project):
    """-> (list of mismatch messages, number of outputs checked)."""
    with tempfile.TemporaryDirectory() as tmp:
        root, out = os.path.join(tmp, project), os.path.join(tmp, "out")
        shutil.copytree(os.path.join(REPO, "examples", project), root)  # set_dates changes file dates
        set_dates.main(root)
        exp = json.load(open(os.path.join(root, "expected.json")))
        subprocess.run([sys.executable, "-m", "findmysource", "--out", out], cwd=root, check=True,
                       env={**os.environ, "PYTHONPATH": REPO}, capture_output=True)
        rows = {r["output"]: r for r in csv.DictReader(open(os.path.join(out, "manifest.tsv")), delimiter="\t")}
    bad = []
    for f, want in exp["outputs"].items():
        r = rows.get(f)
        got = r["sources"].split(";") if r and r["sources"] else []
        if sorted(got) != sorted(want):
            bad.append(f"{project}: {f}: expected {want or 'no source'}, got {got or 'no source'}")
        elif want:  # dates: only the 'stale' outputs may be older than their script's last change
            older = r["output_modified"] < max(r["source_modified"].split(";"))
            if older != (f in exp.get("stale", [])):
                bad.append(f"{project}: {f}: stale={older}, expected {f in exp.get('stale', [])}")
    return bad, len(exp["outputs"])


def test_r_project():
    bad, _ = run("r-project")
    assert not bad, "\n".join(bad)


def test_python_project():
    bad, _ = run("python-project")
    assert not bad, "\n".join(bad)


if __name__ == "__main__":
    for p in ["r-project", "python-project"]:
        bad, n = run(p)
        print(f"{p}: {n - len(bad)}/{n} correct")
        for b in bad: print("   ", b)
