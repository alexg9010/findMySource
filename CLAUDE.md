# findMySource

`fms` traces every output file of a research project back to the script that wrote it, by
static analysis of the scripts as text, and writes `.fms/` (manifest.tsv, graph.json,
index.html; the user cache if the project is not writable). README.md describes behaviour for users; keep it in sync with the code.

## Layout

- `findmysource/rules.py`: every matching rule as data, with its reason and examples (direction words, evidence
  kinds and their order, staleness). `tests/test_rules.py` checks the examples.
- `findmysource/languages/`: one module per language with its extensions and known calls (`"saveRDS": "write 1"`,
  `"file.path": "join"`; roles in `languages/__init__.py`), plus its examples. rules.py combines them.
- `findmysource/cli.py`: the engine that applies them, and the CLI. Generic path finder (`generic_scan`), R fallback
  (`parse_script`), path resolution (`make_norm`), matching and ranking (`RANK`), `scan()` writes the
  report, `query()` answers `fms <file|folder|script>`.
- `findmysource/view.html`: the report, one self-contained page; `scan()` injects the graph.
- `examples/{r-project,python-project,multi-project}`: test projects; `expected.json` is the ground truth.
  `examples/set_dates.py` gives their files realistic dates (git does not keep mtimes).
- `docs/site.py`: the GitHub Pages site, with a report per example built by the current code (`pages.yml`, on push to main).
- `tests/`: `test_core.py` (engine), `test_cli.py` (queries), `test_examples.py` (ground truth),
  `visual.py` (report in a real browser).

## Rules

- No runtime dependencies, standard library only. Must run on Python 3.8.
- The report must work offline: no CDN, no network requests (`tests/visual.py` fails on any).
- A rule (a word list, a threshold, an evidence kind) belongs in rules.py (or a language module), with an example; cli.py and the
  report read it from there. After changing `EVIDENCE`, `python tests/test_rules.py --fix` rewrites the README table.
- A matching change needs a case in `rules.py` (an example), `tests/test_rules.py`, `tests/test_core.py`,
  or an example project plus its `expected.json`. See TESTING.md. Never weaken a test or expected.json to make a change pass.
- Keep the existing dense style of cli.py; prefer a small change in the shared function over
  special cases in callers.

## Check before committing

TESTING.md explains the test layers and how to change matching code safely.

```bash
uvx pytest -q                 # all tests; CI also runs Python 3.8-3.14
uv run tests/visual.py        # if view.html or graph output changed
python tests/bench.py --save before.json   # before and after a change to matching or performance;
python tests/bench.py --compare before.json after.json   # a refactor must report "results identical"
```

## Release

Versions before 1.0: bump the minor version (0.x.0) only when users have to act (a breaking change,
like a moved report folder or a changed manifest column); everything else, new features included,
is a patch (0.x.y).

A PR with a change users notice adds a line under `## Unreleased` in CHANGELOG.md. To release, rename
that heading to `## X.Y.Z (date)` and add a new empty `## Unreleased` above it, bump `__version__` in
`findmysource/__init__.py` and commit on `main`. Then push `main` before the tag, so the branch has the
release commit too: `git tag -a vX.Y.Z -m vX.Y.Z && git push origin main && git push origin vX.Y.Z`.
`.github/workflows/release.yml` runs CI, checks the tag matches the version, builds and creates
the GitHub Release.

## Commits

Short subject line only, no body.
