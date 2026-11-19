"""Run with `pytest` or plain `python tests/test_core.py`."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # run without installing

from findmysource.cli import Index, evaluate, generic_scan, make_norm, parse_script, read_code, statements, walk


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
    assert ("figures/roc.png", "write", 'fig.savefig(\n    FIGURES / "roc.png", dpi=150)') in g["ev"]  # code line kept
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


if __name__ == "__main__":
    test_generic()
    test_core()
    print("ok")
