"""The benchmark's edge cases (tests/bench.py): a project without scripts, comparing runs, project names."""
import contextlib
import io
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bench  # noqa: E402


def test_project_without_scripts_has_no_edit_timing():
    with tempfile.TemporaryDirectory() as d:
        os.makedirs(os.path.join(d, "data"))
        open(os.path.join(d, "data", "raw.txt"), "w").write("x")
        r = bench.bench("data-only", d, repeat=1)
    assert r["edit"] is None and r["full"] > 0 and r["outputs"] == 1, r


def test_compare_names_projects_missing_from_either_run():
    run = {"results": "abc", "full": 1.0}
    for ref, runs, want in [({}, {"a": run}, "only in the second run: a"),
                            ({"a": run, "b": run}, {"a": run}, "only in the first run: b"),
                            ({"a": run}, {"a": run}, "results identical"),
                            ({"a": run}, {"a": {**run, "results": "xyz"}}, "results changed: a")]:
        out = io.StringIO()
        with contextlib.redirect_stdout(out): bench.show(runs, ref)
        assert want in out.getvalue(), (ref, runs, out.getvalue())


def test_project_names_do_not_depend_on_argument_order():
    a, b, c = "/w/a/proj", "/w/b/proj", "/w/c/other"
    assert dict(zip([a, b, c], bench.keys([a, b, c]))) == dict(zip([b, c, a], bench.keys([b, c, a])))
    assert bench.keys([a, c]) == ["proj", "other"]
    assert bench.keys([a, b]) == [a, b]
    assert bench.keys(["/w/r-project"], taken=["r-project"]) == ["/w/r-project"]
