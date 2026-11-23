"""The rules fms uses to link outputs to scripts, in one place, each with its reason and examples.

cli.py applies them; tests/test_rules.py checks every example and that the README evidence table matches
EVIDENCE. Matching is static analysis of the scripts as text, so these are heuristics: the examples say what
each rule is meant to catch and what it must leave alone.
"""

# ---------- which files are scripts ----------
SCRIPT_EXT = (".r", ".rmd", ".qmd", ".py", ".ipynb", ".sh", ".smk", ".jl", ".nf", ".pl", ".m")
SCRIPT_NAMES = ("snakefile", "makefile")  # matched as the end of the file name, like the extensions
NOTEBOOK_EXT = (".rmd", ".qmd", ".ipynb")  # rendered by knitr/Quarto/Jupyter: stem-rule and chunk-folder
# skipped everywhere unless fms.json sets "exclude": editor, RStudio and cache files
EXCLUDE = ["rstudio-server-db", "rstudio_state", "__pycache__", "*_cache", "*.RData", ".Rhistory", "#*#", "%",
           ".DS_Store"]
SCRIPT_ONLY_EXCLUDE = ["build", "dist"]  # copies of scripts made by Python packaging; outputs may live there

# ---------- read or write: from the name of the call that holds the path, in any language ----------
# regular expressions, matched anywhere in the last part of the call name (pd.DataFrame.to_csv -> to_csv)
WRITE_VERBS = ["save", "write", "dump", "export", "^to_", "store", "sink", "serial"]
READ_VERBS = ["read", "load", "open", "source", "import", "input", "scan", "include", "fetch"]
# R names verb.object (read.csv, write.fst, save.image): when the last part has no verb, a first part that is
# exactly one of these words decides. Exact words only, so Python objects such as reader.plot() stay mentions.
DOTTED_WRITE_WORDS = ["write", "save", "export", "dump"]
DOTTED_READ_WORDS = ["read", "load", "import"]
# an open()-style mode argument overrides the name: open(p, "w") writes although "open" reads
WRITE_MODES = ["w", "wb", "wt", "w+", "a", "ab", "a+", "x"]
READ_MODES = ["r", "rb", "rt", "r+"]
DIRECTION_EXAMPLES = [  # (call, direction): checked by tests/test_rules.py
    ('saveRDS(x, "out/x.rds")', "write"),
    ('df.to_csv("out/x.csv")', "write"),
    ('json.dump(d, open("out/x.json", "w"))', "write"),
    ('open("out/log.txt", mode="a")', "write"),
    ('readRDS("data/x.rds")', "read"),
    ('read.csv("data/x.csv")', "read"),  # R verb.object: the first part decides
    ('write.fst(df, "out/x.fst")', "write"),
    ('read.dcf("DESCRIPTION.dcf")', "read"),
    ('reader.plot("out/x.png")', "mention"),  # an object, not a verb: only exact words count
    ('write$plot("out/x.png")', "mention"),  # R list member, not verb.object: only the dot form counts
    ('read$plot("out/x.png")', "mention"),
    ('np.load("data/x.npy")', "read"),
    ('open("data/x.txt")', "read"),
    ('plot_volcano("out/x.pdf")', "mention"),  # unknown verb: only a mention
]

# ---------- path builders, recognised by the shape of their name in any language ----------
JOIN_CALLS = ["file.path", "path", "join", "joinpath", "path_join", "here"]  # parts joined with "/"
CONCAT_CALLS = ["paste0", "str_c", "stri_c", "concat", "stri_paste"]  # parts glued together
WRAP_CALLS = ["str", "as.character", "normalizePath", "path.expand", "expanduser", "abspath", "realpath",
              "fspath", "resolve", "I", "toString", "Path"]  # one argument, passed through
FORMAT_CALLS = ["paste", "sprintf", "glue", "str_glue"]  # paste(sep=), sprintf %s, glue {name}
BUILDER_EXAMPLES = [  # (expression, variables, path pattern): "*" is a part only known at run time
    ('file.path(out, "pca.png")', {"out": "figures"}, "figures/pca.png"),
    ('os.path.join(OUT, f"hist_{feature}.svg")', {"OUT": "figures"}, "figures/hist_*.svg"),
    ('Path(OUT) / "model.pkl"', {"OUT": "results"}, "results/model.pkl"),
    ('paste0(out, "/plot_", g, ".png")', {"out": "figures"}, "figures/plot_*.png"),
    ('sprintf("%s/table_%d.csv", out, i)', {"out": "results"}, "results/table_*.csv"),
    ('"{}/x.csv".format(out)', {"out": "results"}, "results/x.csv"),
]
# format calls are case-sensitive (GLUE() is not glue()); the other builders are not
# a string is a candidate file path when it ends in an extension of up to 8 characters
FILE_EXT_RE = r"\.[A-Za-z][A-Za-z0-9]{0,7}$"

# ---------- R fallback: known R functions that do not say "write" or "read" in their name ----------
# call name -> index of the positional file argument (a named file=/filename=/path=/con= argument wins)
R_WRITE = {**{n: 1 for n in "saveRDS write_rds write_csv write.csv write.csv2 write.table write_tsv write_delim "
                             "fwrite writeLines write.xlsx saveWidget write_xlsx write_parquet".split()},
           **{n: 0 for n in "ggsave pdf png jpeg tiff svg bmp sink save.image".split()},
           "save": None}  # save(): file= only
R_READ = {n: 0 for n in "readRDS read_rds read_csv read.csv read.csv2 read.table read_tsv read_delim read.delim "
                        "read_excel fread load readLines read_xlsx read_parquet import source".split()}
R_FILE_ARGS = ("file", "filename", "path", "con")

# ---------- linking: evidence that a script made a file, strongest first ----------
# Only the strongest kind found for an output is reported; ties keep every script with that kind.
# "files": a tiny project; "links": output -> "script label" expected in the manifest ("" = no source).
EVIDENCE = [
    {"label": "write", "strength": "strong", "meaning": "writes to exactly this path",
     "example": {"files": {"a.R": 'saveRDS(x, "out/x.rds")\n', "out/x.rds": ""},
                 "links": {"out/x.rds": "a.R write"}}},
    {"label": "stem-rule", "strength": "strong",
     "meaning": "rendered report (`analysis.html`) next to its `analysis.Rmd` / `.qmd` / `.ipynb`",
     "example": {"files": {"analysis.Rmd": "```{r}\nx <- 1\n```\n", "analysis.html": "", "other.html": ""},
                 "links": {"analysis.html": "analysis.Rmd stem-rule", "other.html": ""}}},
    {"label": "name", "strength": "strong",
     "meaning": 'passes this file name to a writing function, e.g. `save_table(df, "de_table")`',
     "example": {"files": {"a.R": 'save_table(df, "de_table")\n', "out/de_table.tsv": "", "out/de.tsv": ""},
                 "links": {"out/de_table.tsv": "a.R name", "out/de.tsv": ""}}},
    {"label": "write-glob", "strength": "medium", "meaning": "writes to a path that matches only through wildcards",
     "example": {"files": {"a.py": 'for g in genes:\n    plt.savefig(f"figs/{g}.png")\n', "figs/GATA4.png": "",
                           "figs/notes.txt": ""},
                 "links": {"figs/GATA4.png": "a.py write-glob", "figs/notes.txt": ""}}},
    {"label": "lang-write", "strength": "medium",
     "meaning": "language-specific fallback, currently R: a known write function without a write verb in its "
                "name, such as `pdf()` and `png()`",
     "example": {"files": {"a.R": 'pdf("figs/volcano.pdf")\n', "figs/volcano.pdf": ""},
                 "links": {"figs/volcano.pdf": "a.R lang-write"}}},
    {"label": "lang-glob", "strength": "medium",
     "meaning": "R fallback: a known write function such as `png()`, matched through wildcards",
     "example": {"files": {"a.R": 'for (g in genes) png(paste0("figs/", g, ".png"))\n', "figs/MYH6.png": ""},
                 "links": {"figs/MYH6.png": "a.R lang-glob"}}},
    {"label": "chunk-folder", "strength": "medium",
     "meaning": "in `analysis_files/` next to `analysis.Rmd` / `.qmd` / `.ipynb`, where knitr and Quarto put chunk "
                "figures",
     "example": {"files": {"analysis.Rmd": "```{r}\nplot(1)\n```\n", "analysis_files/figure-html/p-1.png": "",
                           "other_files/p-1.png": ""},
                 "links": {"analysis_files/figure-html/p-1.png": "analysis.Rmd chunk-folder",
                           "other_files/p-1.png": ""}}},
    {"label": "mention", "strength": "weak",
     "meaning": "the path appears in the script, but no read or write was recognised",
     "example": {"files": {"a.R": 'plot_volcano(res, "figs/volcano.pdf")\n', "figs/volcano.pdf": ""},
                 "links": {"figs/volcano.pdf": "a.R mention"}}},
    {"label": "mention-glob", "strength": "weak",
     "meaning": "the path appears with wildcards, but no read or write was recognised",
     "example": {"files": {"a.R": 'for (g in genes) plot_gene(g, paste0("figs/", g, ".pdf"))\n',
                           "figs/GATA4.pdf": ""},
                 "links": {"figs/GATA4.pdf": "a.R mention-glob"}}},
]
RANK = {e["label"]: i for i, e in enumerate(EVIDENCE)}

# ---------- deciding between scripts ----------
# A script that reads a file is not its source just because it also names it: its weaker evidence is dropped.
DROPPED_FOR_READERS = ("mention", "name", "chunk-folder")
ARBITRATION_EXAMPLES = [  # same format as the evidence examples
    {"why": "explicit evidence from another script beats a guess from the folder name",
     "files": {"analysis.Rmd": "```{r}\nx <- 1\n```\n", "plot.R": 'ggsave("analysis_files/p.png")\n',
               "analysis_files/p.png": ""},
     "links": {"analysis_files/p.png": "plot.R write"}},
    {"why": "a script that reads a file is not its source",
     "files": {"make.R": 'saveRDS(x, "out/x.rds")\n', "use.R": 'x <- readRDS("out/x.rds")\nmessage("out/x.rds")\n',
               "out/x.rds": ""},
     "links": {"out/x.rds": "make.R write"}},
]

# ---------- dates ----------
# an output is "possibly stale" when its script was edited more than this after it was written
# (edits within the hour are usually "ran it, then fixed a comment"). The report gets it from the scan data.
STALE_SECONDS = 3600
