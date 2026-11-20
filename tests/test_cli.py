"""fms <file> / <folder> / <script> on a copy of examples/r-project.  python tests/test_cli.py  or  pytest"""
import os, shutil, subprocess, sys, tempfile, urllib.request

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "examples"))
import set_dates


def fms(cwd, *args):
    p = subprocess.run([sys.executable, "-m", "findmysource", *args], cwd=cwd, capture_output=True, text=True,
                       env={**os.environ, "PYTHONPATH": REPO})
    return p.returncode, p.stdout


def test_cli():
    d = os.path.join(tempfile.mkdtemp(), "rp")
    shutil.copytree(os.path.join(REPO, "examples", "r-project"), d)
    set_dates.main(d)
    code, out = fms(d)
    assert code == 0 and "9 of 12 outputs traced" in out, out
    code, out = fms(d, "figures/qc/pca.png")
    assert code == 0 and "<- scripts/03_figures.R" in out and 'ggsave(file.path(fig_dir, "pca.png"))' in out, out
    assert "data/raw/counts.csv  (raw input)" in out, out          # upstream chain reaches the raw data
    code, out = fms(os.path.join(d, "figures"), "qc/pca.png")      # relative to a subfolder
    assert "<- scripts/03_figures.R" in out, out
    code, out = fms(d, ".")
    assert code == 1 and "figures/qc/pca.png" in out, out          # stale -> exit 1
    code, out = fms(d, "results")
    assert code == 0 and "nothing possibly stale" in out, out
    code, out = fms(d, "scripts/02_model.Rmd")
    assert "results/model.rds" in out and "results/clean.rds" in out, out


def test_rerun_uses_the_cache():
    d = os.path.join(tempfile.mkdtemp(), "rp")
    shutil.copytree(os.path.join(REPO, "examples", "r-project"), d)
    run = lambda: subprocess.run([sys.executable, "-m", "findmysource"], cwd=d, capture_output=True, text=True,
                                 env={**os.environ, "PYTHONPATH": REPO}).stderr
    assert "(4 read, 0 from cache)" in run()
    assert "no changes since the last scan" in run()             # nothing changed: report kept
    with open(os.path.join(d, "scripts", "01_load.R"), "a") as fh: fh.write("\n# edit\n")
    err = run()
    assert "(1 read, 3 from cache)" in err and "outputs matched" in err, err  # only the edited script is re-read



def test_serve_from_a_subfolder():
    d = os.path.join(tempfile.mkdtemp(), "rp")
    shutil.copytree(os.path.join(REPO, "examples", "r-project"), d)
    fms(d)
    p = subprocess.Popen([sys.executable, "-m", "findmysource", "--serve", "0"], cwd=os.path.join(d, "figures"),
                         stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, env={**os.environ, "PYTHONPATH": REPO})
    try:
        line = next(l for l in p.stderr if l.startswith("serving"))
        assert os.path.join(os.path.realpath(d), ".fms") in line, line                  # the project's report, not figures/
        page = urllib.request.urlopen(line.split(" at ")[1].split()[0], timeout=10).read().decode()
        assert "<title>" in page, page[:200]
    finally:
        p.kill()


if __name__ == "__main__":
    test_cli()
    test_rerun_uses_the_cache()
    test_serve_from_a_subfolder()
    print("ok")
