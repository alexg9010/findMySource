"""Every rule in findmysource/rules.py does what its examples say, and the README describes the rules as they are.

    pytest tests/test_rules.py      or      python tests/test_rules.py [--fix]   (--fix rewrites the README table)
"""
import csv
import os
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
from findmysource import cli, rules  # noqa: E402


def links(files):
    """scan a tiny project -> {output: "script label"} ("" when no script was linked)"""
    with tempfile.TemporaryDirectory() as d:
        for p, txt in files.items():
            os.makedirs(os.path.dirname(os.path.join(d, p)) or d, exist_ok=True)
            open(os.path.join(d, p), "w").write(txt)
        subprocess.run([sys.executable, "-m", "findmysource", "--out", os.path.join(d, ".fms")], cwd=d, check=True,
                       capture_output=True, env={**os.environ, "PYTHONPATH": REPO})
        rows = csv.DictReader(open(os.path.join(d, ".fms", "manifest.tsv")), delimiter="\t")
        return {r["output"]: (f"{r['sources']} {r['evidence']}" if r["sources"] else "") for r in rows}


def check(example):
    got = links(example["files"])
    return [f"{o}: expected {want or 'no source'}, got {got.get(o) or 'no source'}"
            for o, want in example["links"].items() if got.get(o, "") != want]


def test_evidence_examples():
    bad = [f"{e['label']}: {b}" for e in rules.EVIDENCE for b in check(e["example"])]
    assert not bad, "\n".join(bad)


def test_arbitration_examples():
    bad = [f"{x['why']}: {b}" for x in rules.ARBITRATION_EXAMPLES for b in check(x)]
    assert not bad, "\n".join(bad)


def test_direction_examples():
    def direction(call):
        fn = cli.CALL_ANY.search(call)
        return cli.direction(fn.group(1), cli.split_call(call, fn.end())[0])
    bad = [f"{c}: expected {want}, got {direction(c)}" for c, want in rules.DIRECTION_EXAMPLES if direction(c) != want]
    assert not bad, "\n".join(bad)


def test_builder_examples():
    bad = [f"{e}: expected {want}, got {cli.evaluate(e, v)}" for e, v, want in rules.BUILDER_EXAMPLES
           if cli.evaluate(e, v) != want]
    assert not bad, "\n".join(bad)
    # format calls are case-sensitive: GLUE() is some other function, and its path stays a mention
    assert cli.generic_scan('GLUE("out/x.csv")\n')["ev"] == [("out/x.csv", "mention", 'GLUE("out/x.csv")')]


def test_configured_write_functions():
    """fms.json write_functions: the project's own helpers, by argument name or position; a folder covers its files"""
    got = links({"fms.json": '{"write_functions": {"convert_all": "out_dir", "keep_fig": 1}}',
                 "a.R": 'out <- "plots"\nconvert_all(in_dir = out, out_dir = file.path(out, "converted"), cl = 1)\n'
                        'keep_fig(p, "figs/umap.pdf")\n',
                 "plots/converted/x.png": "", "plots/converted/y.png": "", "plots/other.png": "", "figs/umap.pdf": ""})
    got.pop("fms.json")
    assert got == {"plots/converted/x.png": "a.R write-glob", "plots/converted/y.png": "a.R write-glob",
                   "plots/other.png": "", "figs/umap.pdf": "a.R write"}, got


def table():
    return "   | evidence | meaning |\n   |---|---|\n" + "".join(
        f"   | `{e['label']}` | {e['meaning']} |\n" for e in rules.EVIDENCE)


def test_readme_table_matches_rules():
    readme = open(os.path.join(REPO, "README.md")).read()
    assert table() in readme, "README evidence table differs from rules.EVIDENCE: run python tests/test_rules.py --fix"


if __name__ == "__main__":
    if "--fix" in sys.argv:
        p = os.path.join(REPO, "README.md")
        s = open(p).read()
        i = s.index("   | evidence | meaning |\n")
        j = s.index("\n\n", i)
        open(p, "w").write(s[:i] + table().rstrip("\n") + s[j:])
        print("README table rewritten")
    for name, fn in list(globals().items()):
        if name.startswith("test_"): fn(); print("ok", name)
