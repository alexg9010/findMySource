# Testing findMySource

`fms` matches outputs to scripts by static analysis, so most of what it does is heuristics. A heuristic
can't be proven right, but its intent, the properties it must keep, and the effect of a change on real
projects can all be checked. Each layer below checks one of those.

## The layers

| Layer | File | What it guards | Runtime |
|---|---|---|---|
| Engine unit tests | `tests/test_core.py` | parsing, path evaluation, resolution, report location, caching | seconds |
| CLI | `tests/test_cli.py` | `fms <file\|folder\|script>`, `--serve`, reruns from the cache | seconds |
| Ground truth | `tests/test_examples.py` | both example projects against their `expected.json`: every output's true source and staleness | seconds |
| Rule examples | `tests/test_rules.py` | every rule in `findmysource/rules.py` does what its examples say: direction words, path builders, each evidence kind, arbitration, `write_functions`; and the README evidence table matches `rules.EVIDENCE` | seconds |
| Invariants | `tests/test_invariants.py` | properties that hold for *any* project: see below | ~20 s |
| Benchmark | `tests/bench.py` (+ `tests/test_bench.py` for its own edge cases) | speed, memory, and a fingerprint of all matches on the examples, a synthetic project and real projects | ~15 s, more with real projects |
| Browser | `tests/visual.py` | the report in a real browser: every tab, click-to-trace, details panel, filters; fails on any console error or network request | ~30 s |

```bash
uvx pytest -q                       # everything except the benchmark and the browser check
python tests/test_rules.py --fix    # rewrite the README evidence table after changing rules.EVIDENCE
uv run tests/visual.py              # after changes to view.html or the graph data
python tests/bench.py --save before.json [~/real/project ...]
python tests/bench.py --save after.json  [~/real/project ...]
python tests/bench.py --compare before.json after.json
```

### Rules as data, examples as tests

Every rule lives in `findmysource/rules.py` with its reason and examples:
- tiny projects for each evidence kind, with what must match and what must not;
- calls with their expected direction;
- expressions with the path they must evaluate to.

`tests/test_rules.py` runs all of them. A new rule or a changed rule comes with an example; a bug found
in a rule becomes an example first. The examples also record known, accepted behaviour, so a change to
it is deliberate.

### Invariants

`tests/test_invariants.py` checks, on both examples, a 2,000-file synthetic project and a small project
where several scripts write the same files at the same moment:

| Invariant | Meaning |
|---|---|
| readers | a script that reads an output is never its source through weak evidence |
| strongest wins | every script in `not_chosen` reads the output or has weaker evidence than the chosen sources |
| order | the result is the same when every folder is listed in a random order |
| location | the result is the same after copying the project to another path under another name |
| traceable | every match has a known evidence kind; evidence from code carries its code line; the manifest's per-source fields have matching counts |
| tied sources | sources with the same date are listed by path, under ten fixed hash seeds (Python randomises string hashing per process) |

**Every invariant has been shown to fail.** For each one, the code was broken on purpose (no reader
veto, weakest wins, code line dropped, paths keeping the root, scripts in listing order, no path
tie-breaker) and the test caught it. A check that has never failed may be checking nothing: when you
add one, break the code once and watch it fail.

### The benchmark

`tests/bench.py` scans each project in a separate, isolated Python process (`-I`, so nothing from the
scanned project can be imported) and records:

| Metric | Meaning |
|---|---|
| `full`, `rerun`, `edit` | median seconds: no cache; nothing changed; one script's cache entry dropped |
| `parse`, `files`, `link`, `write` | phases of the full scan, from `cache.json` |
| `rss` | peak memory of the largest single process, in MB |
| `outputs`, `matched`, `stale`, evidence counts | what was found |
| `results` | fingerprint of every (output, sources, evidence, code line); dates are left out |
| `correct` | against `expected.json` (examples) |

Real projects are scanned into a temporary report folder and never modified.

## How to change matching code

**A refactor** (moving rules, restructuring code) must not change any match:
`bench.py --compare` has to report **"results identical"** on the examples, the synthetic project and
at least one real project. Timings within about ±10% are noise.

**A behaviour change** (a fix or a new rule) will change matches, and that's the point. Then:
1. Add a case that fails before the change: an example in `rules.py`, a case in `test_rules.py` or
   `test_core.py`, or a file in an example project plus its line in `expected.json` (CLAUDE.md asks for
   one of these with every matching change).
2. Run `bench.py --compare` on real projects and look at **every changed row**, not just the counts.
   Scan the project with both versions into temporary folders (`--out`) and diff the two `manifest.tsv`
   files row by row. Each change should be explainable; the `not_chosen` column usually says why a link
   disappeared. A lower `matched` count can be a fix: a reader that was wrongly listed as a source.
3. Never weaken a test or `expected.json` to make a change pass.

**Checking that a new test can fail:** commit your work, the new test included, *first*. Then break the
code, run the test, and undo the break with `git restore <file>`. Don't use `git stash` for this: it
would put the new test away too (so the run checks nothing), and popping it onto a broken file can
conflict. Undoing a break with `git checkout <file>` or `git restore <file>` on uncommitted work throws
that work away too.

## Lessons from past bugs

- **Set and dict order.** Python randomises string hashes per process, so iterating a set of paths
  gives a different order in each run. Anything that reaches the output must be sorted, with the path
  as the final tie-breaker.
- **Code inside strings** (#29, PR #31). In R and Python a quoted call is text; shell scripts run code
  from strings (`Rscript -e "..."`). Test both kinds.
- **Unbounded fields.** `not_chosen` has one entry per competing script; readers raise
  `csv.field_size_limit`, because the default (128 kB) is reachable with a few thousand scripts.
- **Configured folders** (`write_functions`) cover the files directly in the folder. Check them per
  candidate path, not after one candidate was chosen: script-relative paths, symlinks, names without an
  extension and wildcard folder names each broke a version that filtered afterwards.
- **R names with dots** (#24, PR #30). `read.csv` is a verb plus an object; a Python `reader.plot` is not,
  and neither is R's `write$plot`. Only exact verb words before a dot count.

## Review

PRs are reviewed by CodeRabbit, which only reviews PRs into `main`: stacked PRs get reviewed once
they're retargeted. Treat each comment as a claim to verify:
- a bug: fix it, add a case that fails without the fix, and reply with the commit;
- valid but broader than the PR: open an issue and reply with its link;
- style that conflicts with this repo (one-line `if` bodies, short names): skip it, and say so in the reply.
