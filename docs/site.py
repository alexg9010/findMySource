"""Build the GitHub Pages site: docs/ plus a fresh report per example project, made by this checkout's fms.

    python docs/site.py _site      # .github/workflows/pages.yml runs this on every push to main
"""
import os
import shutil
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "examples"))
import set_dates  # noqa: E402

REPORTS = {"example-report": "r-project", "multi-project-report": "multi-project"}  # site folder: example


def main(site):
    site = os.path.abspath(site)  # fms runs inside each example
    shutil.copytree(os.path.join(REPO, "docs"), site, ignore=shutil.ignore_patterns("*.py"))
    for folder, name in REPORTS.items():
        tmp = tempfile.mkdtemp(prefix="fms-site-")
        proj = os.path.join(tmp, name)
        shutil.copytree(os.path.join(REPO, "examples", name), proj)
        set_dates.main(proj)  # realistic dates, so one output is possibly stale
        out = os.path.join(site, folder)
        subprocess.run([sys.executable, "-m", "findmysource", "--out", out], cwd=proj, check=True,
                       env={**os.environ, "PYTHONPATH": REPO}, stdout=subprocess.DEVNULL)
        os.remove(os.path.join(out, "cache.json"))  # fms's scan cache, not part of the report
        for f in os.listdir(out):  # show the project as it is in the repo, not the temporary copy
            p = os.path.join(out, f)
            txt = open(p).read().replace(os.path.realpath(proj), "examples/" + name)
            open(p, "w").write(txt)
        shutil.rmtree(tmp)
    print(f"site: {site}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "_site")
