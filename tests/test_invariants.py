"""Properties of the matching that hold for any project, checked on the examples and a synthetic project.

    pytest tests/test_invariants.py      or      python tests/test_invariants.py

- readers: a script that reads an output is never its source through weak evidence (rules.DROPPED_FOR_READERS)
- order: the result does not depend on the order in which folders are listed
- location: copying the project to another path, under another name, does not change the result
- traceable: every match names a known evidence kind, and evidence found in code shows the line it came from
- strongest wins: every script that was not chosen either reads the output or has weaker evidence than the sources
"""
import argparse
import csv
import json
import os
import random
import shutil
import sys
import tempfile
from unittest import mock

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [REPO, os.path.join(REPO, "examples"), os.path.join(REPO, "tests")]
import bench  # noqa: E402
import set_dates  # noqa: E402
from findmysource import cli, rules  # noqa: E402

FROM_CODE = {"write", "write-glob", "name", "lang-write", "lang-glob", "mention", "mention-glob"}


def projects(tmp):
    """the example projects and a small synthetic one, with realistic dates"""
    for p in ("r-project", "python-project"):
        shutil.copytree(os.path.join(REPO, "examples", p), os.path.join(tmp, p))
        set_dates.main(os.path.join(tmp, p))
    bench.synthetic(os.path.join(tmp, "synthetic"), 2000)
    shared = os.path.join(tmp, "shared")  # outputs with several sources: their order must not depend on listing
    for p, txt in {**{f"s{i}.R": 'saveRDS(x, "out/all.rds")\nggsave("out/plot.png")\n' for i in range(6)},
                   "a.Rmd": "```{r}\nplot(1)\n```\n", "a.qmd": "```{r}\nplot(2)\n```\n",
                   "out/all.rds": "", "out/plot.png": "", "a_files/figure-html/p-1.png": ""}.items():
        os.makedirs(os.path.dirname(os.path.join(shared, p)), exist_ok=True)
        open(os.path.join(shared, p), "w").write(txt)
        os.utime(os.path.join(shared, p), (1.7e9, 1.7e9))  # one moment: the dates cannot order the sources
    return [os.path.join(tmp, p) for p in ("r-project", "python-project", "synthetic", "shared")]


def scan(root, walk=None):
    """scan in this process (so os.walk can be patched) -> report folder"""
    out = tempfile.mkdtemp(prefix="fms-inv-")
    cwd = os.getcwd()
    os.chdir(root)
    try:
        with mock.patch("os.walk", walk or os.walk), mock.patch("sys.stdout", open(os.devnull, "w")), \
                mock.patch("sys.stderr", open(os.devnull, "w")):
            cli.scan(argparse.Namespace(config="fms.json", out=out, path=None, serve=None))
    finally:
        os.chdir(cwd)
    return out


def rows(out):
    return list(csv.DictReader(open(os.path.join(out, "manifest.tsv")), delimiter="\t"))


def test_invariants():
    real_walk = os.walk

    def shuffled(top, *a, **k):  # the same tree, listed in a random order
        rnd = random.Random(top)
        for d, ds, fs in real_walk(top, *a, **k):
            rnd.shuffle(ds); rnd.shuffle(fs)
            yield d, ds, fs

    with tempfile.TemporaryDirectory() as tmp:
        bad = []
        for root in projects(tmp):
            name, out = os.path.basename(root), scan(root)
            ref = bench.results(out)

            # readers: from graph.json, which lists reads as file -> script edges
            g = json.load(open(os.path.join(out, "graph.json")))
            path = {n["id"]: n["title"].split("\n")[0] for n in g["nodes"]}
            reads = {(path[e["from"]], path[e["to"]]) for e in g["edges"] if e["kind"] == "reads"}
            for r in rows(out):
                for s, ev in zip(r["sources"].split(";"), r["evidence"].split(";")):
                    if r["sources"] and (r["output"], s) in reads and ev.startswith(rules.DROPPED_FOR_READERS):
                        bad.append(f"{name}: {s} reads {r['output']} but is its source ({ev})")

            # traceable
            for r in rows(out):
                if not r["sources"]: continue
                for ev, code in zip(r["evidence"].split(";"), r["evidence_code"].split(" ;; ")):
                    if ev not in rules.RANK: bad.append(f"{name}: {r['output']}: unknown evidence {ev}")
                    elif ev in FROM_CODE and not code.strip():
                        bad.append(f"{name}: {r['output']}: {ev} without the line of code behind it")

            # strongest wins (not_chosen: "script label: reason")
            for r in rows(out):
                best = min((rules.RANK[e] for e in r["evidence"].split(";") if e in rules.RANK), default=99)
                for x in filter(None, r["not_chosen"].split(" ;; ")):
                    s, label, why = x.split(" ", 2)[0], x.split(" ", 2)[1].rstrip(":"), x.split(": ", 1)[1]
                    if why != "reads this file" and rules.RANK[label] <= best:
                        bad.append(f"{name}: {r['output']}: {s} ({label}) not chosen over {r['evidence']}")

            # order
            got = bench.results(scan(root, walk=shuffled))
            if got != ref: bad.append(f"{name}: different result when folders are listed in another order")

            # location: another path and another folder name
            moved = os.path.join(tmp, "elsewhere", name + "-copy")
            shutil.copytree(root, moved, copy_function=shutil.copy2)  # keeps the file dates
            got = bench.results(scan(moved))
            if got != ref: bad.append(f"{name}: different result after copying the project to {moved}")
        assert not bad, "\n".join(bad)


if __name__ == "__main__":
    test_invariants()
    print("ok")
