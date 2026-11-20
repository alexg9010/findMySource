"""Benchmark fms: speed, memory and a fingerprint of the results, to compare two versions of the code.

    python tests/bench.py                          # examples + a synthetic project of 20,000 files
    python tests/bench.py --size 100000 ~/proj     # bigger synthetic project, plus a real project (left untouched)
    python tests/bench.py --save before.json       # ... then change the code ...
    python tests/bench.py --save after.json
    python tests/bench.py --compare before.json after.json

Per project:
  full     scan without a cache (median of --repeat runs, in seconds)
  rerun    nothing changed: the fast path
  edit     one script's cache entry dropped, as if it had been edited (real projects are never touched)
  parse/files/link/write   phases of the full scan, from cache.json
  rss      peak memory of the full scan in MB: the largest single process (the main one or a parser)
  results  fingerprint of the matches (output, sources, evidence, code line): a refactor must keep it
  correct  outputs that match expected.json (examples only)
"""
import argparse
import csv
import hashlib
import json
import os
import shutil
import statistics
import subprocess
import sys
import tempfile
from collections import Counter

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "examples"))
sys.path.insert(0, os.path.join(REPO, "tests"))
import set_dates  # noqa: E402

# one scan in a fresh, isolated interpreter (-I: nothing is imported from the scanned project): wall time and the
# peak memory of the largest single process
RUN = """import json, resource, sys, time
sys.path.insert(0, sys.argv[2])
sys.argv = ["fms", "--out", sys.argv[1]]
from findmysource import cli
t = time.perf_counter(); cli.main(); t = time.perf_counter() - t
r = lambda w: resource.getrusage(w).ru_maxrss / (1024 if sys.platform != "darwin" else 1024 * 1024)
print(json.dumps({"s": t, "rss": max(r(resource.RUSAGE_SELF), r(resource.RUSAGE_CHILDREN))}), file=sys.stderr)
"""


def scan(root, out):
    p = subprocess.run([sys.executable, "-I", "-c", RUN, out, REPO], cwd=root, capture_output=True, text=True)
    if p.returncode: sys.exit(f"fms failed in {root}:\n{p.stderr}")
    return json.loads(p.stderr.strip().splitlines()[-1])


def synthetic(d, size):
    """A project of about `size` files, the same for the same size: R, Python and R Markdown scripts that write
    fixed names, loop-generated names (wildcards) and chunk figures; most files are raw data no script writes."""
    w = lambda p, txt="x": (os.makedirs(os.path.dirname(os.path.join(d, p)), exist_ok=True),
                            open(os.path.join(d, p), "w").write(txt))
    n = max(4, size // 1000)  # scripts per language
    per = size * 3 // 10 // (3 * n)  # loop outputs per script: 30% of the files are written by a script
    for i in range(n):
        w(f"scripts/r{i:03}.R", f'source("scripts/utils.R")\nout <- file.path(RES, "r{i}")\n'
          f'tab <- read.csv("results/r{max(i - 1, 0)}/table.csv")\n'
          f'write.csv(tab, file.path(out, "table.csv"))\nsaveRDS(tab, "results/r{i}/model.rds")\n'
          f'for (g in genes) ggsave(paste0(out, "/plot_", g, ".png"))\n')
        w(f"results/r{i}/table.csv"); w(f"results/r{i}/model.rds")
        for j in range(per): w(f"results/r{i}/plot_G{j}.png")
        w(f"scripts/py{i:03}.py", f'from pathlib import Path\nOUT = Path("results/py{i}")\n'
          f'df.to_csv(OUT / "summary.tsv")\nfor s in samples:\n    plt.savefig(f"{{OUT}}/fig_{{s}}.pdf")\n')
        w(f"results/py{i}/summary.tsv")
        for j in range(per): w(f"results/py{i}/fig_S{j}.pdf")
        w(f"notebooks/nb{i:03}.Rmd", f'```{{r}}\nplot(read.csv("results/py{i}/summary.tsv"))\n```\n')
        w(f"notebooks/nb{i:03}.html")
        for j in range(per): w(f"notebooks/nb{i:03}_files/figure-html/chunk-{j}.png")
    w("scripts/utils.R", 'RES <- "results"\ngenes <- c("GATA4", "MYH6")\n')
    for j in range(size - size * 3 // 10):  # raw data, unmatched
        w(f"data/raw/sample_{j // 200:03}/reads_{j % 200:03}.fastq.gz", "")
    json.dump({"scripts": ["scripts", "notebooks"], "outputs": ["results", "notebooks", "data"]},
              open(os.path.join(d, "fms.json"), "w"))


def results(out):
    """fingerprint and counts of the manifest, without dates (they differ between copies of a project)"""
    rows = list(csv.DictReader(open(os.path.join(out, "manifest.tsv")), delimiter="\t"))
    keys = sorted((r["output"], r["sources"], r["evidence"], r["evidence_code"]) for r in rows)
    ev = Counter(e for r in rows for e in r["evidence"].split(";"))
    return {"results": hashlib.sha1(json.dumps(keys).encode()).hexdigest()[:12], "outputs": len(rows),
            "matched": len(rows) - ev.pop("unmatched", 0), "stale": sum(bool(r["stale_days"]) for r in rows),
            "evidence": dict(sorted(ev.items()))}


def bench(name, root, repeat, example=False):
    tmp = tempfile.mkdtemp(prefix="fms-bench-")
    out = os.path.join(tmp, "out")
    full, phases, rss = [], [], 0
    for _ in range(repeat):
        shutil.rmtree(out, ignore_errors=True)
        r = scan(root, out)
        full.append(r["s"]); rss = max(rss, r["rss"])
        phases.append(json.load(open(os.path.join(out, "cache.json")))["timings"])
    rerun = [scan(root, out)["s"] for _ in range(repeat)]
    edit = []
    for _ in range(repeat):
        c = json.load(open(os.path.join(out, "cache.json")))
        if not c["scripts"]: break  # no scripts: nothing to edit
        c.pop("fingerprint", None)
        c["scripts"].pop(sorted(c["scripts"])[len(c["scripts"]) // 2])  # one script, as if edited
        json.dump(c, open(os.path.join(out, "cache.json"), "w"))
        edit.append(scan(root, out)["s"])
    m = statistics.median
    res = {"full": m(full), "rerun": m(rerun), "edit": m(edit) if edit else None, "rss": rss,
           **{k: m(p[k] for p in phases) for k in phases[0]}, **results(out)}
    if example:
        import test_examples
        bad, n = test_examples.run(name)
        res["correct"] = f"{n - len(bad)}/{n}"
    shutil.rmtree(tmp)
    return res


def show(runs, ref=None):
    cols = ["full", "rerun", "edit", "parse", "files", "link", "write", "rss", "outputs", "matched", "stale",
            "correct", "results"]
    print(f"{'project':22}" + "".join(f"{c:>{16 if c == 'results' else 13}}" for c in cols))
    for name, r in runs.items():
        cells = []
        for c in cols:
            v, o = r.get(c, ""), (ref or {}).get(name, {}).get(c)
            txt = f"{v:.3f}" if isinstance(v, float) else "-" if v is None else str(v)
            if isinstance(v, float) and o: txt += f" {v / o - 1:+.0%}" if abs(v / o - 1) >= 0.005 else " ="
            elif o is not None and v != o: txt += " !"  # a result changed
            cells.append(f"{txt:>{16 if c == 'results' else 13}}")
        print(f"{name[:22]:22}" + "".join(cells))
    if ref is not None:
        for name, r in runs.items():
            o = ref.get(name)
            if o and o.get("evidence") != r.get("evidence"):
                print(f"{name}: evidence {o.get('evidence')} -> {r.get('evidence')}")
        changed = [n for n, r in runs.items() if n in ref and r.get("results") != ref[n].get("results")]
        gone, new = sorted(set(ref) - set(runs)), sorted(set(runs) - set(ref))
        print("times in s (rss in MB), change relative to the first file")
        if gone: print(f"only in the first run: {', '.join(gone)}")
        if new: print(f"only in the second run: {', '.join(new)}")
        if changed: print(f"results changed: {', '.join(changed)}")
        if not changed and not gone and not new: print("results identical")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("projects", nargs="*", help="real projects to add (scanned into a temporary report folder)")
    ap.add_argument("--size", type=int, default=20000, help="files in the synthetic project (0: none)")
    ap.add_argument("--repeat", type=int, default=3)
    ap.add_argument("--save", help="write the results to this JSON file")
    ap.add_argument("--compare", nargs=2, metavar=("BEFORE", "AFTER"), help="compare two saved runs")
    a = ap.parse_args()
    if a.compare:
        before, after = (json.load(open(f)) for f in a.compare)
        return show(after, before)
    runs = {}
    with tempfile.TemporaryDirectory() as tmp:
        for p in ("r-project", "python-project"):
            root = os.path.join(tmp, p)
            shutil.copytree(os.path.join(REPO, "examples", p), root)
            set_dates.main(root)
            runs[p] = bench(p, root, a.repeat, example=True)
        if a.size:
            root = os.path.join(tmp, "synthetic")
            synthetic(root, a.size)
            runs[f"synthetic-{a.size}"] = bench("synthetic", root, a.repeat)
    paths = [os.path.abspath(p) for p in a.projects]
    names = [os.path.basename(p) for p in paths]
    for p, n in zip(paths, names):  # a name shared with another project or an example: the full path, in any order
        runs[n if names.count(n) == 1 and n not in runs else p] = bench(p, p, a.repeat)
    show(runs)
    if a.save: json.dump(runs, open(a.save, "w"), indent=1)


if __name__ == "__main__":
    main()
