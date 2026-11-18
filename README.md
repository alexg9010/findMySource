# findMySource

**Trace every output of a project back to the script that made it.**

Coming back to an analysis after months away, or preparing a figure for a paper,
you often need to answer: *which script produced this file, from which inputs,
and is it still up to date?* `findMySource` (`fms` on the command line) scans
your scripts and output folders, matches the two up, and writes:

- `fms-report/manifest.tsv`: one row per output file, with its source script(s),
  match confidence, creation and modification dates, and the inputs of those scripts.
- `fms-report/index.html`: an interactive report you can open in any browser:
  - **Flow**: a left-to-right map of the pipeline. Click a box to highlight
    everything upstream and downstream of it.
  - **Timeline**: when each output was last written, compared with its script's
    last change. Outputs that are older than the script are flagged as possibly stale.
  - **Matrix**: which scripts write into which folders.
  - **Table**: a searchable, sortable version of the manifest.
  - **Network**: a force-directed overview.
- `fms-report/graph.json`: the same graph, for your own tooling.

`fms` needs no running pipeline, no workflow manager and no changes to your
code. It reads what is already there.

## Install

`fms` is a single pure-Python package with **no dependencies** (Python ≥ 3.8).

**pip**
```bash
pip install git+https://github.com/<org>/findMySource
```

**uv**
```bash
uv tool install git+https://github.com/<org>/findMySource   # puts `fms` on your PATH
uvx --from git+https://github.com/<org>/findMySource fms    # one-off run, nothing installed
```

**From source**
```bash
git clone https://github.com/<org>/findMySource
cd findMySource
pip install -e .          # or: uv tool install -e .
```
An editable install (`-e`) picks up changes to the code without reinstalling.

## Use

Run it from the root of the project you want to map:

```bash
fms                        # uses ./fms.json if present, else scans the current folder
fms --config fms.json --out fms-report
```

Then open `fms-report/index.html`. Add `fms-report/` to your `.gitignore`.

### Configuration (`fms.json`, optional)

```json
{
  "scripts": ["scripts", "analysis", "notebooks"],
  "outputs": ["results", "figures", "data"],
  "aliases": { "/local": "/data/local" },
  "exclude": ["*.RData", "*_cache"],
  "hidden": false,
  "extensions": [".r", ".rmd", ".qmd", ".ipynb"]
}
```

| key | meaning | default |
|---|---|---|
| `scripts` | folders (or single files) to search for scripts | `["."]` |
| `outputs` | folders to search for output files; symlinks listed here are followed | `["."]` |
| `aliases` | path prefixes that mean the same place, e.g. a mount visible as both `/local` and `/data/local`. Symlinks are resolved automatically and need no alias. | `{}` |
| `exclude` | file and folder name globs to skip | RStudio, cache and editor files |
| `hidden` | also scan hidden folders (`.git`, `.snakemake`, …). A hidden folder listed in `scripts`/`outputs` is always scanned. | `false` |
| `extensions` | script types to parse | R, Rmd, qmd, ipynb |

## How it works

`fms` doesn't need to know your language's save functions. It reads scripts as text.

1. **Find paths.** Every string literal is a candidate, including f-strings,
   `${var}`, `glue()` and `.format()`. Paths are assembled from variables and
   from joins recognised by their shape: `file.path()`, `os.path.join()`,
   `Path(...)`, the `/` operator, `paste0()` and `sprintf()`.
   - Variables are followed in script order.
   - A script inherits the variables of files it `source()`s or imports, such as `config.py` or `utils.R`.
   - Parts that can only be known at run time, such as a loop variable, become wildcards:
     `f"{FIGURES}/hist_{feature}.svg"` becomes `figures/hist_*.svg`.
2. **Decide the direction** from the name of the call that holds the path, in any language:
   - *save, write, dump, export, `to_…`* → **write**
   - *read, load, open, import, include* → **read**
   - an `open()` mode such as `"w"` overrides the name.
   - Anything else is a weaker **mention**.
3. **Resolve paths.**
   - Relative paths are tried against the script's folder and the project root.
   - Symlinks and aliases are resolved, so that `results/x.rds` and `/data/…/x.rds` count as the same file.
4. **Match and rank.** Every output file found on disk is linked to the scripts with
   the **strongest** evidence for it:

   | evidence | meaning |
   |---|---|
   | `write` | writes to exactly this path |
   | `stem-rule` | rendered report (`analysis.html`) next to its `analysis.Rmd` / `.qmd` / `.ipynb` |
   | `name` | passes this file name to a writing function, e.g. `save_table(df, "de_table")` |
   | `write-glob` | writes to a path that matches only through wildcards |
   | `lang-write`, `lang-glob` | language-specific fallback, currently R: known write functions without a write verb in their name, such as `pdf()` and `png()` |
   | `mention`, `mention-glob` | the path appears in the script, but no read or write was recognised |

   A script that *reads* a file is never reported as its source.
5. **Date.**
   - An output's date is its file modification time.
   - A script's date is its last git commit, or its file time if it has uncommitted changes.
     The timeline flags outputs that are older than their script.

Scripts are recognised by extension. The default list covers R, R Markdown, Quarto, Python,
Jupyter, shell, Snakemake, Julia, Nextflow, Perl and MATLAB, plus files named `Snakefile`
and `Makefile`. Set `extensions` in `fms.json` to change it.

### Limits

- This is static analysis. A file whose name is fully computed at run time,
  or passed in as a command-line argument, can only be matched through wildcards, or not at all.
- Variables are inherited one level deep. If `a.R` sources `b.R`, which sources `c.R`,
  then `a.R` gets the variables of `b.R` only.
- The Network tab loads `vis-network` from a CDN. All other tabs work offline.

## Roadmap

- Rule-aware Snakemake/Nextflow support (inputs and outputs declared per rule).
- Hard evidence from **DVC** and **git-lfs**. Content hashes would confirm exactly
  which version of an input produced an output, on top of today's pattern matching.
- Incremental re-runs for very large output trees.

## Development

```bash
pytest                           # or: python tests/test_core.py
python tests/test_examples.py    # score on the example projects (expected.json = ground truth)
```

Browser check of the report: it takes screenshots of every tab, tests click-to-trace,
the staleness filter and the network tab, and fails on any console error.
[uv](https://docs.astral.sh/uv/) installs Playwright on the fly:
```bash
uvx playwright@1.64.1 install chromium     # once (add --with-deps on Debian/Ubuntu)
uv run tests/visual.py                     # example project -> tests/screenshots/
uv run tests/visual.py path/to/fms-report/index.html   # any report
CHROME=$(command -v chromium) uv run tests/visual.py   # or use an installed Chromium
```

## License

MIT
