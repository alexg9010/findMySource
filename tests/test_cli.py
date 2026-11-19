"""fms <file> / <folder> / <script> on a copy of examples/r-project.  python tests/test_cli.py  or  pytest"""
import os, shutil, subprocess, sys, tempfile

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


if __name__ == "__main__":
    test_cli()
    print("ok")
