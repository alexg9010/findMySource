# findMySource

`fms` traces every output file of a research project back to the script that wrote it, by
static analysis of the scripts as text, and writes `.fms/` (manifest.tsv, graph.json,
index.html; the user cache if the project is not writable). README.md describes behaviour for users; keep it in sync with the code.

## Layout

- `findmysource/cli.py`: the whole engine and CLI. Generic path finder (`generic_scan`), R fallback
  (`parse_script`), path resolution (`make_norm`), matching and ranking (`RANK`), `scan()` writes the
  report, `query()` answers `fms <file|folder|script>`.
- `findmysource/view.html`: the report, one self-contained page; `scan()` injects the graph.
- `examples/{r-project,python-project}`: test projects; `expected.json` is the ground truth.
  `examples/set_dates.py` gives their files realistic dates (git does not keep mtimes).
- `tests/`: `test_core.py` (engine), `test_cli.py` (queries), `test_examples.py` (ground truth),
  `visual.py` (report in a real browser).

## Rules

- No runtime dependencies, standard library only. Must run on Python 3.8.
- The report must work offline: no CDN, no network requests (`tests/visual.py` fails on any).
- `STALE_SECONDS` in cli.py and `STALE_MS` in view.html are the same rule; change both.
- New evidence kinds go into `RANK` and the evidence table in README.md.
- A matching change needs a case in `tests/test_core.py` or in an example project plus its
  `expected.json`. Never weaken a test or expected.json to make a change pass.
- Keep the existing dense style of cli.py; prefer a small change in the shared function over
  special cases in callers.

## Check before committing

```bash
uvx pytest -q                 # all tests; CI also runs Python 3.8-3.14
uv run tests/visual.py        # if view.html or graph output changed
```

## Release

Bump `__version__` in `findmysource/__init__.py`, commit, then tag and push `vX.Y.Z`.
`.github/workflows/release.yml` runs CI, checks the tag matches the version, builds and creates
the GitHub Release.

## Commits

Short subject line only, no body.
