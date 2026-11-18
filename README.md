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

1. **Parse scripts.**
   - `fms` finds file-writing calls (`saveRDS`, `ggsave`, `pdf`, `write_csv`, …), file-reading calls (`readRDS`, `read_csv`, `fread`, …) and `source()`.
   - It reconstructs their paths from variables, `file.path()`, `paste0()`, `sprintf()`, `here()` and Rmd `params:`, in script order.
   - Parts that can't be known statically, such as loop variables or command-line arguments, become wildcards.
2. **Resolve paths.**
   - Relative paths are tried against the script's folder and the project root.
   - Symlinks and aliases are resolved, so that `results/x.rds` and `/data/…/x.rds` count as the same file.
3. **Match.** Each output file found on disk is linked to the scripts whose paths match it:
   - `exact`: the path matches with nothing unknown.
   - `glob`: the path matches only through wildcards.
   - `stem-rule`: a rendered report, such as `analysis.html`, next to its `analysis.Rmd`.
4. **Date.**
   - An output's date is its file modification time.
   - A script's date is its last git commit, or its file time if it has uncommitted changes.

### Limits

- This is static analysis. A file written under a name that is fully computed at
  run time can only be matched through wildcards, or not at all.
- Assignments spanning several lines are not followed.
- Only R-family scripts are parsed today. Python, shell and Snakemake are planned.
- The Network tab loads `vis-network` from a CDN. All other tabs work offline.

## Roadmap

- Parsers for Python, shell and Snakemake/Nextflow.
- Hard evidence from **DVC** and **git-lfs**. Content hashes would confirm exactly
  which version of an input produced an output, on top of today's pattern matching.
- Incremental re-runs for very large output trees.

## Development

```bash
python tests/test_core.py        # or: pytest
```

## License

MIT
