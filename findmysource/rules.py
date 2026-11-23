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
SCRIPT_ONLY_EXCLUDE = ["build", "dist"]
# scripts that run other code from strings (Rscript -e "...", python -c "...", Snakemake shell: "..."): calls
# inside their string literals count. In all other scripts a string is text: 'saveRDS(x, "a.rds")' is no call.
CODE_IN_STRINGS = (".sh", ".smk", ".nf", "snakefile", "makefile",
                   ".jl",  # Julia runs $(...) inside strings: "$(write(out, data))"
                   ".m")  # MATLAB: ' is also the transpose operator (y = x'), so its quotes cannot be paired  # copies of scripts made by Python packaging; outputs may live there

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
STRING_EXAMPLES = [  # same format as the evidence examples: a call inside a string is text, except in shell scripts
    {"why": "a call written inside a string is text in R and Python",
     "files": {"a.R": "example <- 'saveRDS(x, \"out/a.rds\")'\n", "b.py": "doc = 'df.to_csv(\"out/b.csv\")'\n",
               "out/a.rds": "", "out/b.csv": ""},
     "links": {"out/a.rds": "", "out/b.csv": ""}},
    {"why": "in MATLAB ' is also the transpose operator, so it cannot start a string to blank",
     "files": {"m.m": "y = x'; save('out/m.csv', 'y', '-ascii');\n", "out/m.csv": ""},
     "links": {"out/m.csv": "m.m write"}},
    {"why": "an f-string's {...} fields are code; its text is not",
     "files": {"f.py": "print(f\"{df.to_csv('out/f.csv')}\")\nnote = f\"df.to_csv('out/g.csv') for {name}\"\n",
               "out/f.csv": "", "out/g.csv": ""},
     "links": {"out/f.csv": "f.py write", "out/g.csv": ""}},
    {"why": "a field with a nested format spec is code too",
     "files": {"n.py": "width = 10\nprint(f\"{df.to_csv('out/n.csv'):{width}}\")\n", "out/n.csv": ""},
     "links": {"out/n.csv": "n.py write"}},
    {"why": "strings inside an f-string field are text: their braces and calls do not count",
     "files": {"fms.json": '{"write_functions": {"convert_all": 0}}',
               "p.py": "print(f\"{df.to_csv('out/p.csv') if '{' else 0}\")\npath = \"plots\"\nprint(f\"{'convert_all(path)'}\")\n",
               "out/p.csv": "", "plots/c.png": ""},
     "links": {"out/p.csv": "p.py write", "plots/c.png": ""}},
    {"why": "quotes in an f-string's text do not pair with quotes inside its fields",
     "files": {"s.py": "print(f\"Saved '{df.to_csv('out/s.csv')}'\")\n", "out/s.csv": ""},
     "links": {"out/s.csv": "s.py write"}},
    {"why": "Julia runs $(...) inside strings",
     "files": {"j.jl": "out = \"out/j.csv\"\nprintln(\"$(write(out, data))\")\n", "out/j.csv": ""},
     "links": {"out/j.csv": "j.jl write"}},
    {"why": "a triple-quoted string spanning lines is text too",
     "files": {"t.py": "doc = \"\"\"Example:\nsaveRDS(x, 'out/t.rds')\n\"\"\"\n", "out/t.rds": ""},
     "links": {"out/t.rds": ""}},
    {"why": "a configured write function is a call like any other: quoted, it is text",
     "files": {"fms.json": '{"write_functions": {"convert_all": 0}}',
               "w.R": "example <- 'convert_all(\"figs\")'\nconvert_all(\"plots\")\n", "figs/a.png": "", "plots/b.png": ""},
     "links": {"figs/a.png": "", "plots/b.png": "w.R write-glob"}},
    {"why": "a shell script runs code from strings",
     "files": {"run.sh": "python -c \"df.to_csv('out/c.csv')\"\n", "out/c.csv": ""},
     "links": {"out/c.csv": "run.sh write"}},
]

# shell command lines (shell scripts, Snakemake shell:, Makefile recipes): no calls, so words are read like a shell
SHELL_SCRIPTS = (".sh", ".smk", "snakefile", "makefile")
SHELL_WRITE_FLAGS = ["-o", "-O", "--output", "--out", "--outfile", "--output-file", "--output-document"]
SHELL_WRITE_COMMANDS = ["tee"]  # every file argument is written
SHELL_EXAMPLES = [  # same format as the evidence examples
    {"why": "a redirection writes (> >> &> 2>) or reads (<) the next word",
     "files": {"run.sh": "sort < data/in.txt > out/sorted.txt 2>> out/err.log\n",
               "data/in.txt": "", "out/sorted.txt": "", "out/err.log": ""},
     "links": {"data/in.txt": "", "out/sorted.txt": "run.sh write", "out/err.log": "run.sh write"}},
    {"why": "an output flag writes its argument, also as --flag=path; a URL is never a file",
     "files": {"get.sh": "curl -L -o data/genome.fa.gz https://x.org/genome.fa.gz\n"
                         "wget -O data/genes.gtf https://x.org/genes.gtf\nsamtools sort --output=out/a.bam in.bam\n",
               "data/genome.fa.gz": "", "data/genes.gtf": "", "out/a.bam": "", "in.bam": ""},
     "links": {"data/genome.fa.gz": "get.sh write", "data/genes.gtf": "get.sh write", "out/a.bam": "get.sh write",
               "in.bam": "get.sh mention"}},
    {"why": "shell variables are expanded, and an unquoted assignment is text, not an expression",
     "files": {"v.sh": "OUT=results/qc\nfastqc x.fq -o $OUT\necho done | tee \"${OUT}/done.txt\"\n",
               "results/qc/done.txt": ""},
     "links": {"results/qc/done.txt": "v.sh write"}},
    {"why": "a Snakemake shell: command is a command line too",
     "files": {"Snakefile": "rule a:\n    shell: \"bedtools sort -i x.bed > out/s.bed\"\n", "out/s.bed": ""},
     "links": {"out/s.bed": "Snakefile write"}},
    {"why": "a redirection needs no spaces around it",
     "files": {"p.sh": "printf x>out/result.txt\n", "out/result.txt": ""},
     "links": {"out/result.txt": "p.sh write"}},
    {"why": "a quoted argument is one word: a file name with spaces, or text that only looks like a command",
     "files": {"q.sh": "printf x > \"out/my report.txt\"\necho \"example > out/report.txt\"\n",
               "out/my report.txt": "", "out/report.txt": ""},
     "links": {"out/my report.txt": "q.sh write", "out/report.txt": ""}},
    {"why": "bash -c runs its quoted argument as a command line",
     "files": {"c.sh": "bash -c \"sort x.txt > out/c.txt\"\n", "out/c.txt": ""},
     "links": {"out/c.txt": "c.sh write"}},
    {"why": "tee writes only when it is the command, not an argument",
     "files": {"t.sh": "echo tee data/input.txt\n", "data/input.txt": ""},
     "links": {"data/input.txt": "t.sh mention"}},
    {"why": "a Snakefile's Python assignments stay expressions: OUT = BASE is BASE's value",
     "files": {"Snakefile": "BASE = \"results\"\nOUT = BASE\ndf.to_csv(OUT + \"/t.csv\")\n", "results/t.csv": ""},
     "links": {"results/t.csv": "Snakefile write"}},
    {"why": "an escaped operator is part of its word",
     "files": {"e.sh": "echo out/a\\>b.txt\n", "b.txt": ""},
     "links": {"b.txt": ""}},
    {"why": "a command substitution with spaces is one run-time part of the word",
     "files": {"d.sh": "printf x > out/$(date +%F).txt\n", "out/2026-01-01.txt": ""},
     "links": {"out/2026-01-01.txt": "d.sh write-glob"}},
    {"why": "a command substitution is unknown, whatever the script's variables are called",
     "files": {"m.sh": "__=out/wrong.txt\nprintf x > \"$(date)\"\n", "out/wrong.txt": ""},
     "links": {"out/wrong.txt": "m.sh mention"}},
    {"why": "an assignment followed by ; holds for the rest of the line",
     "files": {"n.sh": "OUT=old\nOUT=new; printf x > \"$OUT/done.txt\"\n", "new/done.txt": "", "old/done.txt": ""},
     "links": {"new/done.txt": "n.sh write", "old/done.txt": ""}},
    {"why": "an assignment in a child shell (bash -c, shell:) does not change the script's variables",
     "files": {"k.sh": "OUT=old\nbash -c 'OUT=new; true'\nprintf x > \"$OUT/a.txt\"\n", "old/a.txt": "", "new/a.txt": ""},
     "links": {"old/a.txt": "k.sh write", "new/a.txt": ""}},
    {"why": "each child shell starts from the script's variables at that point",
     "files": {"j.sh": "OUT=old\nOUT=new; bash -c \"printf x > $OUT/a.txt\"\n"
                       "bash -c 'X=first; true'; bash -c 'printf x > \"out/$X/r.txt\"'\n",
               "new/a.txt": "", "old/a.txt": "", "out/first/r.txt": ""},
     "links": {"new/a.txt": "j.sh write", "old/a.txt": "", "out/first/r.txt": ""}},
    {"why": "the last of several assignments on a line holds",
     "files": {"y.sh": "OUT=old; OUT=new\nprintf x > \"$OUT/a.txt\"\n", "new/a.txt": "", "old/a.txt": ""},
     "links": {"new/a.txt": "y.sh write", "old/a.txt": ""}},
    {"why": "an assignment is expanded once, with the values before it",
     "files": {"o.sh": "OUT=out\nOUT=$OUT/sub\nprintf x > \"$OUT/a.txt\"\n", "out/sub/a.txt": ""},
     "links": {"out/sub/a.txt": "o.sh write"}},
    {"why": "prefix assignments and redirections keep the command position",
     "files": {"l.sh": "LC_ALL=C tee out/result.txt\n> out/log.txt tee out/r2.txt\n",
               "out/result.txt": "", "out/log.txt": "", "out/r2.txt": ""},
     "links": {"out/result.txt": "l.sh write", "out/log.txt": "l.sh write", "out/r2.txt": "l.sh write"}},
    {"why": "only shell-like scripts: in R and Python > is a comparison",
     "files": {"c.R": "if (n > 3) x <- 1\nprint(n > out.txt)\n", "out.txt": ""},
     "links": {"out.txt": ""}},
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
