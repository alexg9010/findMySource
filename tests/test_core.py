"""Run with `pytest` or plain `python tests/test_core.py`."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # run without installing

from findmysource.cli import Index, make_norm, parse_script, read_code


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


if __name__ == "__main__":
    test_core()
    print("ok")
