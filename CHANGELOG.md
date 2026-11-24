# Changelog

Changes that users of `fms` notice. Pull requests are linked on the
[GitHub releases](https://github.com/alexg9010/findMySource/releases).
Versions before 1.0: a minor bump (0.x.0) means something changed that users have to act on,
a patch bump (0.x.y) covers everything else, new features included.

## Unreleased

- Snakemake: files in a rule's `output:`, `log:` and `benchmark:` are writes and `input:` files reads, also over
  several lines and inside `temp()`/`expand()`; `{wildcards}` match any name.
- Directory listings (`list.files(dir, pattern =)`, `fs::dir_ls()`, `Sys.glob()`, `glob.glob()`) stand for the files
  they list, and apply-style calls (`lapply(files, readRDS)`, `map(pd.read_csv, files)`) read or write with the
  function they apply. Such reads now appear as inputs and in the Flow order.
- Perl `open($fh, '>', path)` and `open(FH, ">path")` are read as writes (`>>`, `+>` and `+<` too), and `'<'` as a read.
- A call written inside a string in R or Python (`'saveRDS(x, "a.rds")'`) no longer counts as a real call;
  the `{...}` fields of Python f-strings still do. Shell scripts, Snakemake, Nextflow, Makefiles, Julia and
  MATLAB still count calls in strings: they run code from strings, or (MATLAB) use `'` as an operator.
- R functions named verb.object (`read.csv`, `write.fst`, `import.bed`) are read as reads and writes by the
  general engine, not only when the R fallback knows them. A script that reads a file through one of them
  is no longer listed as its source.

## 0.3.2 (2026-11-23)

- `write_functions` in `fms.json`: your own functions that write files without a write verb in their name,
  e.g. `{"convert_all": "out_dir"}`. A folder argument covers the files directly in it.
- `fms <file>` lists the scripts that were considered but not chosen, with the reason ("reads this
  file", "weaker than write"); `manifest.tsv` has them in a new last column, `not_chosen`.
- Scripts that share an output with the same date (e.g. `a.Rmd` and `a.qmd` sharing `a_files/`) are
  listed in a fixed order; before, the order could change from one run to the next.
- Report: the title links to the findMySource repository, and the subtitle names the fms version
  that wrote the report.

## 0.3.1 (2026-11-20)

- `fms --serve [PORT]`: rescan, then serve the report on localhost, to view the report of a project
  on a remote machine through a forwarded port.
- Report: Flow, Steps, Timeline and Table can be used with the keyboard.
- Report: no `file://` links when the report is served over http.
- Timeline: outputs written at the same moment no longer overlap.
- Flow: the trace stays when a selected folder group is expanded.

## 0.3.0 (2026-11-20)

- **The report moves to `.fms/`**, which ignores itself in git. If the project is not writable,
  it goes to `~/.cache/fms/` instead. Projects scanned before keep `fms-report/` until it is deleted.
- Report: label filters, the selection survives a redraw, shared inputs are counted once.

## 0.2.4 (2026-11-20)

- Paths into another copy of the project (a colleague's folder, an old server path) are matched.
- Chunk figures in `<notebook>_files/` are linked to their `.Rmd` / `.qmd` / `.ipynb`
  (evidence `chunk-folder`).
- Script dates from git work on Python < 3.11. (0.2.3, never released on its own.)

## 0.2.2 (2026-11-19)

- Reruns read only changed scripts and keep the report when nothing changed; scripts are parsed
  in parallel. The run time is printed.

## 0.2.1 (2026-11-19)

- The report folder is never scanned; commit time zones are kept; each script gets its own git date.

## 0.2.0 (2026-11-19)

- Queries: `fms <file>`, `fms <script>`, `fms <folder>` (exit code 1 if anything is possibly stale),
  and a summary after each scan.
- The code line behind each match is recorded (`evidence_code` in manifest.tsv).
- Report: details panel in Flow, rows open from Table and Timeline, new Unmatched tab,
  Steps drawn without a CDN, so the report works offline.

## 0.1.0 (2026-11-19)

- First release: language-independent scan of scripts with an R fallback, evidence ranking,
  `manifest.tsv`, `graph.json` and the interactive report (Flow, Timeline, Matrix, Table, Steps).
