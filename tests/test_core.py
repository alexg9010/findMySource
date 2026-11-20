"""Run with `pytest` or plain `python tests/test_core.py`."""
import csv
import datetime
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # run without installing

from findmysource.cli import (Index, evaluate, generic_scan, git_dates, last_change, make_norm, parse_script,
                              path_batches, read_code, statements, ts, walk)

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def run_fms(root, out="fms-report"):
    """run the CLI in root; -> manifest rows by output path"""
    subprocess.run([sys.executable, "-m", "findmysource", "--out", out], cwd=root, check=True,
                   env={**os.environ, "PYTHONPATH": REPO}, capture_output=True)
    with open(os.path.join(root, out, "manifest.tsv")) as fh:
        return {r["output"]: r for r in csv.DictReader(fh, delimiter="\t")}


def write(path, text=""):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh: fh.write(text)


def git(root, *args, env=None):
    subprocess.run(["git", "-c", "user.email=t@example.com", "-c", "user.name=t", *args], cwd=root,
                   env={**os.environ, **(env or {})}, check=True, capture_output=True)


def test_generic():
    v = {"FIGURES": "figures", "RESULTS": "results"}
    assert evaluate('f"{FIGURES}/hist_{feature}.svg"', v) == "figures/hist_*.svg"
    assert evaluate('RESULTS / "raw" / "clean.parquet"', v) == "results/raw/clean.parquet"
    assert evaluate('os.path.join(FIGURES, "umap.pdf")', v) == "figures/umap.pdf"
    assert evaluate('"{}/x_{}.csv".format(RESULTS, k)', v) == "results/x_*.csv"
    assert evaluate('glue::glue("{RESULTS}/a.rds")', v) == "results/a.rds"
    assert evaluate('"${RESULTS}/b.bam"', v) == "results/b.bam"
    assert evaluate('str(Path("data") / "c.csv")', v) == "data/c.csv"
    # braces do not join lines; brackets do; comments and docstrings are dropped
    assert statements('"""doc (\n"""\nfor (g in x) {\n  a <- 1  # (\n}\nf(1,\n  2)') == \
        ['""', "for (g in x) {", "a <- 1", "}", "f(1,\n  2)"]
    g = generic_scan('''
from config import RESULTS
with open(RESULTS / "metrics.json", "w") as fh:
    json.dump(m, fh)
df = pd.read_csv("data/raw/m.csv")   # saveRDS("commented.rds")
save_table(de, "de_table")
c("SampleID01")
fig.savefig(
    FIGURES / "roc.png", dpi=150)
png(file.path(FIGURES, "plain_device.png"))
''', v)
    ev = {(p, k) for p, k, _ in g["ev"]}
    assert ("results/metrics.json", "write") in ev   # open(..., "w")
    assert ("figures/roc.png", "write") in ev        # multi-line call
    assert ("data/raw/m.csv", "read") in ev
    assert ("figures/plain_device.png", "mention") in ev  # no verb: R fallback upgrades it
    assert not any("commented" in p for p, _ in ev)
    assert ("figures/roc.png", "write", 'fig.savefig( … FIGURES / "roc.png", dpi=150)') in g["ev"]  # code line kept
    assert g["names"] == {"de_table"}                      # only from writing calls, not c(...)
    assert g["uses"] == {"config.py"}


def test_core():
    code = '''
outdir <- "/data/x/out"
sfx = "CAD"
saveRDS(obj, file.path(outdir, "a.rds"))
ggsave(file.path(outdir, paste0("mBias_", sample, ".png")))
write_csv(df, file=paste0(outdir, "/t_", sfx, ".csv"))
x <- readRDS(paste0(outdir, "/a.rds"))
source("~/p/helper.R")
# saveRDS(a, "/commented.rds")
pdf(sprintf("%s/p_%s.pdf", outdir, sfx))
if (a == b) print(1)
outdir <- "/data/y"
saveRDS(obj,
        file = file.path(outdir, "late.rds"))
write.csv(d, here::here("tables", "t.csv"))
'''
    r = parse_script(code)
    for w in ["/data/x/out/a.rds", "/data/x/out/mBias_*.png", "/data/x/out/t_CAD.csv", "/data/x/out/p_CAD.pdf",
              "/data/y/late.rds", "tables/t.csv"]:
        assert w in r["writes"], (w, r)
    assert r["reads"] == ["/data/x/out/a.rds"], r  # resolved with outdir as it was *at the call*
    assert r["sources"] == ["~/p/helper.R"], r
    assert not any("commented" in w for w in r["writes"]), r

    idx = Index(["/data/x/out/mBias_s1.png", "/data/x/out/a.rds", "/other/mBias_s1.png"])
    assert idx.match("/data/x/out/mBias_*.png")[0] == ["/data/x/out/mBias_s1.png"]
    assert idx.match("*/mBias_*.png")[0] == ["/data/x/out/mBias_s1.png", "/other/mBias_s1.png"]
    assert idx.match("results/a.rds") == ([], "exact")

    with tempfile.TemporaryDirectory() as t:  # relative + symlinked path resolves to the real file
        t = os.path.realpath(t)
        os.makedirs(f"{t}/real/RDS"); os.makedirs(f"{t}/proj/scripts"); open(f"{t}/real/RDS/m.rds", "w").close()
        os.symlink(f"{t}/real", f"{t}/proj/results")
        resolve = make_norm({"/old/proj": f"{t}/proj"}, f"{t}/proj")
        assert f"{t}/real/RDS/m.rds" in resolve("results/RDS/m.rds", f"{t}/proj/scripts/s.R"), resolve("results/RDS/m.rds", "")
        assert resolve("/old/proj/results/RDS/m.rds", "") == [f"{t}/real/RDS/m.rds"]
        rmd = f"{t}/proj/r.Rmd"
        open(rmd, "w").write("---\ntitle: x\nparams:\n  out:\n    value: /o\n  sfx: \"A\"\n---\n```{R}\nsaveRDS(x, file.path(params$out, paste0(params$sfx, '.rds')))\n```\n")
        assert parse_script(*read_code(rmd))["writes"] == ["/o/A.rds"]

        for d in (".git", ".hidden/sub", "shown/.cache"): os.makedirs(f"{t}/w/{d}")
        for f in (".git/x", ".hidden/sub/y", "shown/.cache/z", "shown/a", ".dotfile"): open(f"{t}/w/{f}", "w").close()
        assert sorted(os.path.relpath(f, f"{t}/w") for f in walk([f"{t}/w"], [])) == [".dotfile", "shown/a"]
        assert len(list(walk([f"{t}/w"], [], hidden=True))) == 5
        assert list(walk([f"{t}/w/.hidden"], [])) == [f"{t}/w/.hidden/sub/y"]  # explicit hidden root is scanned


def test_report_is_not_scanned_on_a_repeat_run():
    with tempfile.TemporaryDirectory() as root:
        write(f"{root}/results/answer.csv")
        write(f"{root}/make_results.py", 'save_table(data, "results/answer.csv")\n')
        run_fms(root)
        rows = run_fms(root)
        assert "results/answer.csv" in rows
        assert not any(p.startswith("fms-report/") for p in rows)


def test_report_goes_to_a_hidden_folder_or_the_user_cache():
    env = lambda cache: {**os.environ, "PYTHONPATH": REPO, "XDG_CACHE_HOME": cache}
    fms = lambda cwd, cache, *args: subprocess.run([sys.executable, "-m", "findmysource", *args], cwd=cwd,
                                                   env=env(cache), capture_output=True, text=True)
    with tempfile.TemporaryDirectory() as t:
        t = os.path.realpath(t)
        for proj in ("rw", "ro"):
            write(f"{t}/{proj}/results/answer.csv")
            write(f"{t}/{proj}/make_results.py", 'save_table(data, "results/answer.csv")\n')
        fms(f"{t}/rw", f"{t}/cache")
        assert open(f"{t}/rw/.fms/.gitignore").read() == "*\n"  # ignores itself: never committed
        assert "make_results.py" in fms(f"{t}/rw/results", f"{t}/cache", "answer.csv").stdout  # found from a subfolder
        os.remove(f"{t}/rw/.fms/.gitignore")
        assert "no changes" in fms(f"{t}/rw", f"{t}/cache").stderr
        assert os.path.exists(f"{t}/rw/.fms/.gitignore")  # restored, also when nothing else changed

        os.chmod(f"{t}/rw/.fms", 0o555)  # a report folder this user cannot write to
        try:
            write(f"{t}/rw/results/other.csv")
            write(f"{t}/rw/make_other.py", 'save_table(data, "results/other.csv")\n')
            assert fms(f"{t}/rw", f"{t}/cache").returncode == 0  # the scan goes to the user cache instead
            assert any(os.path.exists(f"{t}/cache/fms/{d}/manifest.tsv") for d in os.listdir(f"{t}/cache/fms")
                       if d.startswith("rw-"))
            assert "make_other.py" in fms(f"{t}/rw", f"{t}/cache", "results/other.csv").stdout  # the newest report
        finally:
            os.chmod(f"{t}/rw/.fms", 0o755)
        os.remove(f"{t}/rw/results/other.csv"); os.remove(f"{t}/rw/make_other.py")  # back to what .fms/ has
        assert "no changes" in fms(f"{t}/rw", f"{t}/cache").stderr
        assert "make_other.py" not in fms(f"{t}/rw", f"{t}/cache", "results/other.csv").stdout  # this scan, not the cache
        # an unchanged report owned by someone else (--out): its time cannot be set, the scan still succeeds
        import argparse, findmysource.cli as cli
        def deny(*_): raise PermissionError("not the owner")
        utime, cwd, os.utime = os.utime, os.getcwd(), deny
        try:
            os.chdir(f"{t}/rw")
            cli.scan(argparse.Namespace(config="fms.json", out=".fms", path=None))
        finally:
            os.utime = utime; os.chdir(cwd)

        os.chmod(f"{t}/ro", 0o555)  # e.g. a shared project folder of a colleague
        try:
            fms(f"{t}/ro", f"{t}/cache")
            assert not os.path.exists(f"{t}/ro/.fms")
            cached = [d for d in os.listdir(f"{t}/cache/fms") if d.startswith("ro-")]
            assert len(cached) == 1 and os.path.exists(f"{t}/cache/fms/{cached[0]}/index.html")
            assert "make_results.py" in fms(f"{t}/ro/results", f"{t}/cache", "answer.csv").stdout
        finally:
            os.chmod(f"{t}/ro", 0o755)


def test_build_copies_of_scripts_are_skipped_but_outputs_there_are_kept():
    with tempfile.TemporaryDirectory() as root:
        write(f"{root}/results/answer.csv")
        write(f"{root}/build/report.pdf")
        for s in ("make_results.py", "build/lib/make_results.py"):
            write(f"{root}/{s}", 'save_table(data, "results/answer.csv")\nsave_pdf(fig, "build/report.pdf")\n')
        rows = run_fms(root)
        assert rows["results/answer.csv"]["sources"] == "make_results.py"
        assert rows["build/report.pdf"]["sources"] == "make_results.py"


def test_paths_into_another_clone_and_chunk_figures():
    with tempfile.TemporaryDirectory() as t:
        root = os.path.join(os.path.realpath(t), "x_files", "proj")  # an ancestor ending in _files
        write(f"{root}/out/plots/pca.pdf")
        write(f"{root}/out/aliased/pca.pdf")
        write(f"{root}/nb/a_files/figure-gfm/chunk-1.png")
        write(f"{root}/nb/a.qmd")  # shares a_files/ with a.Rmd
        write(f"{root}/nb/a.Rmd", '```{r}\noutplot <- "/home/colleague/proj/out/plots/"\n'
                                  'ggsave(file = file.path(outplot, "pca.pdf"))\n'
                                  'ggsave(file = "/old/proj/out/aliased/pca.pdf")\n```\n')
        write(f"{root}/fms.json", '{"aliases": {"/old/proj": "/nowhere/proj"}}')
        rows = run_fms(root)
        assert rows["out/plots/pca.pdf"]["sources"] == "nb/a.Rmd"
        assert rows["out/plots/pca.pdf"]["evidence"] == "write"
        assert rows["out/aliased/pca.pdf"]["evidence"] == "unmatched"  # the alias wins, even pointing nowhere
        chunk = rows["nb/a_files/figure-gfm/chunk-1.png"]
        assert chunk["evidence"] == "chunk-folder;chunk-folder"
        assert sorted(chunk["sources"].split(";")) == ["nb/a.Rmd", "nb/a.qmd"]


def test_chunk_folders_yield_to_reads_and_explicit_writers():
    with tempfile.TemporaryDirectory() as root:
        write(f"{root}/nb/a_files/input.rds")
        write(f"{root}/nb/a.Rmd", '```{r}\nd <- readRDS("a_files/input.rds")\n```\n')  # an input, not an output
        write(f"{root}/nb/b_files/y.png")
        write(f"{root}/nb/b.Rmd")
        write(f"{root}/s.R", 'png("nb/b_files/y.png")\n')  # weak, but explicit: beats the folder-name guess
        rows = run_fms(root)
        assert rows["nb/a_files/input.rds"]["evidence"] == "unmatched"
        assert rows["nb/b_files/y.png"]["sources"] == "s.R"


def test_repeated_project_names_and_nested_notebooks():
    with tempfile.TemporaryDirectory() as t:
        root = os.path.join(os.path.realpath(t), "proj")
        write(f"{root}/out/x.pdf")
        write(f"{root}/out/proj/y.pdf")  # an output folder named like the project
        write(f"{root}/nb/a_files/b_files/fig.png")
        write(f"{root}/nb/a.Rmd")
        write(f"{root}/nb/a_files/b.Rmd")  # a notebook inside another notebook's _files/
        write(f"{root}/s.R", 'saveRDS(x, "/home/c/proj/proj/out/x.pdf")\n'  # cloned into a folder of the same name
                             'saveRDS(x, "/home/c/proj/out/proj/y.pdf")\n')
        rows = run_fms(root)
        assert rows["out/x.pdf"]["evidence"] == "write"
        assert rows["out/proj/y.pdf"]["evidence"] == "write"
        assert rows["nb/a_files/b_files/fig.png"]["sources"] == "nb/a_files/b.Rmd"


def test_git_dates_keep_the_time_zone():
    with tempfile.TemporaryDirectory() as root:
        root = os.path.realpath(root)  # git reports resolved paths (/tmp is a symlink on macOS)
        write(f"{root}/script.py")
        stamp = "2024-03-31T01:30:00+02:00"
        git(root, "init", "-q"); git(root, "add", "script.py")
        git(root, "commit", "-qm", "add", env={"GIT_AUTHOR_DATE": stamp, "GIT_COMMITTER_DATE": stamp})
        assert git_dates(root)[0][f"{root}/script.py"] == stamp


def test_git_dates_ignore_paths_outside_the_repository():
    with tempfile.TemporaryDirectory() as t:
        t = os.path.realpath(t)
        write(f"{t}/repo/a.R"); write(f"{t}/outside.R")
        git(f"{t}/repo", "init", "-q"); git(f"{t}/repo", "add", "a.R"); git(f"{t}/repo", "commit", "-qm", "a")
        dates, _ = git_dates(f"{t}/repo", [f"{t}/repo/a.R", f"{t}/outside.R"])
        assert list(dates) == [f"{t}/repo/a.R"]  # one outside path must not cost the others their dates


def test_last_change_of_a_script():
    with tempfile.TemporaryDirectory() as root:
        root = os.path.realpath(root)
        s = f"{root}/script.R"
        write(s, "x <- 1\n")
        git(root, "init", "-q"); git(root, "add", "script.R")
        stamp = "2024-01-01T12:00:00+00:00"
        git(root, "commit", "-qm", "add", env={"GIT_AUTHOR_DATE": stamp, "GIT_COMMITTER_DATE": stamp})
        committed = datetime.datetime.fromisoformat(stamp).timestamp()
        # fresh clone: file time = clone time, much later than the commit -> the commit date counts,
        # otherwise every output of a freshly cloned project would look stale
        assert last_change(s, *git_dates(root)) == committed
        os.utime(s, (committed - 86400,) * 2)  # file older than its commit (committed later): file time counts
        assert last_change(s, *git_dates(root)) == committed - 86400
        write(s, "x <- 2\n")  # uncommitted edit: file time counts
        assert last_change(s, *git_dates(root)) == os.stat(s).st_mtime


def test_small_helpers():
    assert ts(0)[-6] in "+-"  # local time with its offset: unambiguous
    assert list(path_batches(["aaa", "bb", "cccc"], 5)) == [["aaa"], ["bb"], ["cccc"]]


if __name__ == "__main__":
    test_generic()
    test_core()
    test_report_is_not_scanned_on_a_repeat_run()
    test_report_goes_to_a_hidden_folder_or_the_user_cache()
    test_build_copies_of_scripts_are_skipped_but_outputs_there_are_kept()
    test_paths_into_another_clone_and_chunk_figures()
    test_chunk_folders_yield_to_reads_and_explicit_writers()
    test_repeated_project_names_and_nested_notebooks()
    test_git_dates_keep_the_time_zone()
    test_git_dates_ignore_paths_outside_the_repository()
    test_last_change_of_a_script()
    test_small_helpers()
    print("ok")
