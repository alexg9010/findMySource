"""Every rule in findmysource/rules.py and findmysource/languages/ does what its examples say, and the README describes the rules as they are.

    pytest tests/test_rules.py      or      python tests/test_rules.py [--fix]   (--fix rewrites the README table)
"""
import csv
import os
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
from findmysource import cli, languages, rules  # noqa: E402


def links(files, symlinks=None):
    """scan a tiny project -> {output: "script label"} ("" when no script was linked). symlinks: {link: target}"""
    with tempfile.TemporaryDirectory() as d:
        for p, txt in files.items():
            os.makedirs(os.path.dirname(os.path.join(d, p)) or d, exist_ok=True)
            open(os.path.join(d, p), "w").write(txt)
        for link, target in (symlinks or {}).items(): os.symlink(os.path.join(d, target), os.path.join(d, link))
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


def test_string_examples():
    bad = [f"{x['why']}: {b}" for x in rules.STRING_EXAMPLES for b in check(x)]
    assert not bad, "\n".join(bad)


def test_language_examples():
    bad = [f"{lang.__name__}: {x['why']}: {b}" for lang in languages.LANGUAGES for x in getattr(lang, "EXAMPLES", [])
           for b in check(x)]
    assert not bad, "\n".join(bad)


def test_language_calls_are_well_formed():
    roles = ("write", "read", "join", "concat", "format", "wrap", "list", "glob", "apply")
    bad = [f"{lang.__name__}: {n}: {spec!r}" for lang in languages.LANGUAGES for n, spec in getattr(lang, "CALLS", {}).items()
           if spec.split()[0] not in roles or not all(p.isdigit() for x in spec.split()[1:] for p in x.split(","))]
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

    # a builder name can be configured; a position ignores named arguments (file= is the input here); a folder
    # whose name has a wildcard still covers only the files directly in it
    got = links({"fms.json": '{"write_functions": {"join": 0, "convert": 0, "save_all": "out_dir"}}',
                 "a.R": 'join("out/result.csv")\nconvert(file = "raw/input.csv", "figs/output.pdf")\n'
                        'for (b in batches) save_all(x, out_dir = file.path("plots", b, "converted"))\n',
                 "out/result.csv": "", "raw/input.csv": "", "figs/output.pdf": "",
                 "plots/run1/converted/new.png": "", "plots/run1/converted/archive/old.png": ""})
    got.pop("fms.json")
    assert got == {"out/result.csv": "a.R write", "raw/input.csv": "", "figs/output.pdf": "a.R write",
                   "plots/run1/converted/new.png": "a.R write-glob", "plots/run1/converted/archive/old.png": ""}, got


def test_configured_write_function_paths():
    """short names, a file without an extension, a symlinked folder under another name, and a script-relative
    folder that has only nested files (the project-root one must still be found)"""
    got = links({"fms.json": '{"write_functions": {"convert_all": 0}}',
                 "scripts/a.R": 'convert_all("qc")\nconvert_all("out/checkpoint")\nconvert_all("linked")\n'
                                'for (b in batches) convert_all(file.path("plots", b, "converted"))\n',
                 "qc/report.png": "", "out/checkpoint": "", "real_target/x.png": "",
                 "plots/run1/converted/new.png": "", "scripts/plots/run1/converted/archive/old.png": ""},
                symlinks={"linked": "real_target"})
    got.pop("fms.json")
    assert got == {"qc/report.png": "scripts/a.R write-glob", "out/checkpoint": "scripts/a.R write",
                   "real_target/x.png": "scripts/a.R write-glob", "plots/run1/converted/new.png": "scripts/a.R write-glob",
                   "scripts/plots/run1/converted/archive/old.png": ""}, got

    # a folder next to the script beats a file of that name at the project root; a qualified Python call
    got = links({"fms.json": '{"write_functions": {"convert_all": "out_dir"}}',
                 "scripts/a.R": 'convert_all(out_dir = "results")\n',
                 "scripts/b.py": 'converters.convert_all(out_dir="figs")\n',
                 "results": "", "scripts/results/new.png": "", "figs/f.png": ""})
    got.pop("fms.json")
    assert got == {"results": "", "scripts/results/new.png": "scripts/a.R write-glob",
                   "figs/f.png": "scripts/b.py write-glob"}, got

    # a wildcard folder name: one expansion is a file, another a folder; both count
    got = links({"fms.json": '{"write_functions": {"convert_all": 0}}',
                 "a.R": 'for (b in batches) convert_all(file.path("plots", b, "converted"))\n',
                 "plots/run1/converted/new.png": "", "plots/run2/converted": ""})
    got.pop("fms.json")
    assert got == {"plots/run1/converted/new.png": "a.R write-glob", "plots/run2/converted": "a.R write-glob"}, got


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
