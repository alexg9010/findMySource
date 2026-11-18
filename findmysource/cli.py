"""findMySource (fms): trace every output of a project back to the script(s) that made it.

Usage (run from the project root):
    fms [--config fms.json] [--out fms-report]

Writes <out>/manifest.tsv, <out>/graph.json and <out>/index.html (tabs: flow, timeline, matrix, table, network).

Two engines, combined on one evidence scale (see RANK):
  * generic (default, any language): string literals, f-strings / ${var} / glue / .format,
    path joins recognised by shape (file.path, os.path.join, Path, `/`), assignments, and
    variables inherited from source()d / imported scripts. Read or write is taken from the
    name of the call that holds the path (save/write/dump/export/to_ vs read/load/open/...)
    or an open() mode; anything else is a weaker "mention".
  * R-specific fallback: known R write/read functions (e.g. pdf(), png()), used where the
    generic engine found nothing stronger.
Relative paths are tried against the script's folder and the project root; symlinks are
resolved automatically, so aliases are only needed for mount points with two spellings.
Ceilings: names computed at run time -> wildcard matches; variables inherited one level deep;
paths from command-line args are unknown; only the Network tab needs internet (vis-network).
Config keys (JSON, all optional): scripts, outputs, exclude, aliases, extensions, hidden.
  {"scripts": ["scripts"], "outputs": ["results", "figures"], "aliases": {"/local": "/data/local"}}
"""
import argparse, bisect, datetime, fnmatch, json, os, re, subprocess, sys
from collections import defaultdict

from . import __version__

# call name -> index of the positional file argument (named file=/filename=/path= wins)
WRITE = {n: 1 for n in "saveRDS write_rds write_csv write.csv write.csv2 write.table write_tsv write_delim "
        "fwrite writeLines write.xlsx saveWidget write_xlsx write_parquet".split()}
WRITE.update({n: 0 for n in "ggsave pdf png jpeg tiff svg bmp sink save.image".split()})
WRITE["save"] = None  # file= only
READ = {n: 0 for n in "readRDS read_rds read_csv read.csv read.csv2 read.table read_tsv read_delim read.delim "
        "read_excel fread load readLines read_xlsx read_parquet import source".split()}
CALL_RE = re.compile(r"(?<![\w.])(" + "|".join(re.escape(n) for n in {**WRITE, **READ}) + r")\s*\(")
FILE_KEYS = ("file", "filename", "path", "con")
TOK_RE = re.compile(r"""\s*((?:[rRbBfFuU]{1,2}(?=["']))?(?:"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')|[A-Za-z_.][\w.$@]*(?:::[A-Za-z_.][\w.$@]*)?|[(),=]|[^\s\w(),="']+|\d+\.?\d*)""")


# ---------- tiny R-expression evaluator -> pattern string ('*' = unknown) ----------
def tokenize(s):
    return [m.group(1) for m in TOK_RE.finditer(s)]

STR_RE = re.compile(r"""([rRbBfFuU]{0,2})(["'])(.*)\2$""", re.S)

def _interp(body, v):
    """{name} -> value of name, anything else in braces -> '*'."""
    return re.sub(r"\{([^{}]*)\}", lambda m: v.get(m.group(1).strip(), "*")
                  if re.fullmatch(r"\s*[A-Za-z_.][\w.$]*\s*", m.group(1)) else "*", body)

def _string(t, v):
    m = STR_RE.match(t)
    if not m: return "*"
    pre, body = m.group(1).lower(), m.group(3)
    if "f" in pre: body = _interp(body, v)  # Python f-string
    # shell / Snakemake style ${name}; unknown -> '*'
    return re.sub(r"\$\{(\w+)\}", lambda x: v.get(x.group(1), "*"), body)

def _prim(tok, i, v):
    if i >= len(tok): return "*", i
    t = tok[i]
    if t[-1:] in "\"'" and STR_RE.match(t): return _string(t, v), i + 1
    if re.match(r"[A-Za-z_.]", t):
        if i + 1 < len(tok) and tok[i + 1] == "(":
            args, i = _args(tok, i + 2, v)
            return _apply(t, args, v), i
        return v.get(t, "*"), i + 1
    return "*", i + 1

def _expr(tok, i, v):
    val, i = _prim(tok, i, v)
    while i < len(tok) and tok[i] not in ",)":
        t = tok[i]
        if t in ("/", "+"):  # Path / "x", "a" + "b"
            rhs, i = _prim(tok, i + 1, v)
            val = val + ("/" if t == "/" else "") + rhs
        elif t == ".format" and i + 1 < len(tok) and tok[i + 1] == "(":
            args, i = _args(tok, i + 2, v)
            it = iter(a for n, a in args if n is None)
            kw = {n: a for n, a in args if n}
            val = re.sub(r"\{([^{}]*)\}", lambda m: kw.get(m.group(1), "*") if m.group(1) else next(it, "*"), val)
        else:  # any other operator / indexing: the value is unknown up to the end of this argument
            depth = 0
            while i < len(tok) and not (depth == 0 and tok[i] in ",)"):
                depth += tok[i] == "("; depth -= tok[i] == ")"
                i += 1
            return "*", i
    return val, i

def _args(tok, i, v):
    args = []
    while i < len(tok) and tok[i] != ")":
        name = None
        if i + 1 < len(tok) and tok[i + 1] == "=" and re.match(r"[A-Za-z_.]", tok[i]):
            name, i = tok[i], i + 2
        val, i = _expr(tok, i, v)
        args.append((name, val))
        if i < len(tok) and tok[i] == ",": i += 1
    return args, i + 1

# path builders, recognised by the shape of their name in any language
JOIN_RE = re.compile(r"(?:^|[.:])(?:file\.path|path|join|joinpath|path_join|here)$", re.I)
CONCAT_RE = re.compile(r"(?:^|[.:])(?:paste0|str_c|stri_c|concat|stri_paste)$", re.I)
WRAP_RE = re.compile(r"(?:^|[.:])(?:str|as\.character|normalizePath|path\.expand|expanduser|abspath|realpath|"
                     r"fspath|resolve|I|toString|Path)$", re.I)
def is_builder(fn):
    return bool(JOIN_RE.search(fn) or CONCAT_RE.search(fn) or WRAP_RE.search(fn)
                or re.search(r"(?:^|[.:])(?:paste|sprintf|glue|str_glue)$", fn))

def _apply(fn, args, v=None):
    pos = [a for n, a in args if n is None]
    if JOIN_RE.search(fn): return "/".join(pos)  # file.path, os.path.join, fs::path, Path("a", "b"), here()
    if CONCAT_RE.search(fn): return "".join(pos)
    if re.search(r"(?:^|[.:])paste$", fn):
        return dict((n, a) for n, a in args if n).get("sep", " ").join(pos)
    if re.search(r"(?:^|[.:])sprintf$", fn) and pos:
        it = iter(pos[1:])
        return re.sub(r"%[-\d.]*[sdfgi]", lambda m: next(it, "*"), pos[0])
    if re.search(r"(?:^|[.:])(?:glue|str_glue)$", fn) and pos: return _interp(pos[0], v or {})
    if WRAP_RE.search(fn) and len(pos) == 1: return pos[0]
    return "*"

def evaluate(expr, v):
    return _expr(tokenize(expr), 0, v)[0]


# ---------- generic engine (any language) ----------
WRITE_VERB = re.compile(r"save|write|dump|export|^to_|store|sink|serial", re.I)
READ_VERB = re.compile(r"read|load|open|source|import|input|scan|include|fetch", re.I)
WRITE_MODE = re.compile(r"""^\s*(?:mode\s*=\s*)?["'](?:w|wb|wt|w\+|a|ab|a\+|x)["']\s*$""")
READ_MODE = re.compile(r"""^\s*(?:mode\s*=\s*)?["'](?:r|rb|rt|r\+)["']\s*$""")
CALL_ANY = re.compile(r"([A-Za-z_.][\w.$]*(?:::[A-Za-z_.][\w.$]*)?)\s*\(")
GEN_ASG = re.compile(r"^\s*(?:export\s+|local\s+)?([A-Za-z_.][\w.$]*)\s*(?:<<-|<-|:=|=(?!=))\s*(.+)$", re.S)
FILE_LIKE = re.compile(r"\.[A-Za-z][A-Za-z0-9]{0,7}$")
SCRIPT_EXT = (".r", ".rmd", ".qmd", ".py", ".ipynb", ".sh", ".smk", ".jl", ".nf", ".pl", ".m")
NAMED = re.compile(r"^\s*[A-Za-z_.][\w.]*\s*=(?!=)\s*")

LEX_RE = re.compile(r""""(?:[^"\\]|\\.)*"?|'(?:[^'\\]|\\.)*'?|#.*|[()\[\]]""")

def statements(code):
    """Split code into statements: lines, joined while ( or [ is open; comments (#) removed.
    Braces do not join lines, so the body of a for/if/function block stays separate statements."""
    code = re.sub(r'"""[\s\S]*?"""|\'\'\'[\s\S]*?\'\'\'', '""', code)  # Python docstrings
    out, cur, depth = [], [], 0
    for line in code.split("\n"):
        cut = len(line)
        for m in LEX_RE.finditer(line):  # strings are skipped, so brackets/# inside them do not count
            t = m.group()
            if t[0] == "#": cut = m.start(); break
            if t in "([": depth += 1
            elif t in ")]": depth -= 1
        cur.append(line[:cut])
        if depth <= 0:
            st = "\n".join(cur).strip()
            if st: out.append(st)
            cur, depth = [], 0
    if cur and "".join(cur).strip(): out.append("\n".join(cur).strip())
    return out

def direction(fn, args):
    if any(WRITE_MODE.match(a) for a in args): return "write"
    if any(READ_MODE.match(a) for a in args): return "read"
    name = re.split(r"[.:$]", fn)[-1]
    if WRITE_VERB.search(name): return "write"
    if READ_VERB.search(name): return "read"
    return "mention"

def generic_scan(code, v0=None, stmts=None, vars_only=False):
    """-> dict(ev=[(pattern, write|read|mention)], names={literal}, uses={script name}, vars={}, stmts=[]).
    vars_only: only follow assignments and script references (cheap first pass)."""
    v = dict(v0 or {})
    ev, names, uses = [], set(), set()
    stmts = statements(code) if stmts is None else stmts
    for st in stmts:
        for m in re.finditer(r"""["']([^"'\n]+)["']""", st):  # scripts referenced by name (source("x.R"), "python y.py")
            words = m.group(1).split()
            if words and words[-1].lower().endswith(SCRIPT_EXT): uses.add(os.path.basename(words[-1]))
        for m in re.finditer(r"^\s*(?:from\s+([\w.]+)\s+import|import\s+([\w.]+))", st):
            uses.add((m.group(1) or m.group(2)).split(".")[-1] + ".py")
        if not vars_only:
            for cm in CALL_ANY.finditer(st):
                fn = cm.group(1)
                if is_builder(fn): continue  # evaluated as part of the enclosing argument
                args, _ = split_call(st, cm.end())
                kind = direction(fn, args)
                for a in args:
                    a = NAMED.sub("", a, count=1)
                    p = evaluate(a, v)
                    if FILE_LIKE.search(p) and len(p.replace("*", "")) >= 3 and "://" not in p:
                        ev.append((p, kind))
                    lit = re.fullmatch(r"""\s*["']([\w.\-]{4,})["']\s*""", a)
                    if lit and kind == "write" and not FILE_LIKE.search(lit.group(1)):
                        names.add(lit.group(1))  # save_table(df, "de_table") -> de_table.*
        m = GEN_ASG.match(st)
        if m:
            val = evaluate(m.group(2), v)
            v[m.group(1)] = val
            if not vars_only and FILE_LIKE.search(val) and len(val.replace("*", "")) >= 3 and "://" not in val:
                ev.append((val, "mention"))
    return {"ev": sorted(set(ev)), "names": names, "uses": uses, "vars": v, "stmts": stmts}


# ---------- R-specific fallback ----------
def read_code(path):
    """Return (code, params-dict). Rmd/qmd -> chunks; ipynb -> code cells; else whole file."""
    try: txt = open(path, errors="replace").read()
    except OSError: return "", {}
    ext = path.lower().rsplit(".", 1)[-1]
    params = {}
    if ext in ("rmd", "qmd", "rmarkdown"):
        m = re.match(r"\s*---\n(.*?)\n---", txt, re.S)
        if m and "params:" in m.group(1):
            last = None
            for line in m.group(1).split("params:", 1)[1].splitlines()[1:]:
                if line.strip() and not line[0].isspace(): break
                k = re.match(r"\s+([\w.]+):\s*[\"']?(.*?)[\"']?\s*$", line)
                if not k: continue
                if k.group(1) == "value" and last: params["params$" + last] = k.group(2)  # name:\n  value: x
                else: last = k.group(1); params["params$" + last] = k.group(2)
        txt = "\n".join(re.findall(r"```\s*\{[rR][^\n]*\}\n(.*?)```", txt, re.S))
    elif ext == "ipynb":
        try: nb = json.loads(txt)
        except ValueError: return "", {}
        txt = "\n".join("".join(c["source"]) if isinstance(c["source"], list) else c["source"]
                        for c in nb.get("cells", []) if c.get("cell_type") == "code")
    return txt, params

SPLIT_RE = re.compile(r""""(?:[^"\\]|\\.)*"?|'(?:[^'\\]|\\.)*'?|[()\[\]{},]""")

def split_call(code, start):
    """code[start] is just after '(' -> (list of top-level arg strings, end index)."""
    depth, args, last = 1, [], start
    for m in SPLIT_RE.finditer(code, start):
        t = m.group()
        if t in "([{": depth += 1
        elif t in ")]}":
            depth -= 1
            if depth == 0:
                args.append(code[last:m.start()])
                return [a.strip() for a in args], m.start()
        elif t == "," and depth == 1:
            args.append(code[last:m.start()]); last = m.end()
    args.append(code[last:])
    return [a.strip() for a in args], len(code)

def file_arg(args, idx):
    pos = 0
    for a in args:
        m = re.match(r"([A-Za-z_.]+)\s*=(?!=)\s*(.*)", a, re.S)
        if m:
            if m.group(1) in FILE_KEYS: return m.group(2)
        else:
            if idx is not None and pos == idx: return a
            pos += 1
    return None

ASG_RE = re.compile(r"^[ \t]*([A-Za-z_.][\w.$]*)[ \t]*(?:<-|=(?!=))[ \t]*(.+?)[ \t]*$", re.M)

def parse_script(code, params=None):
    """-> dict(writes=[pattern], reads=[pattern], sources=[pattern]).
    Assignments and calls are processed in file order, so a reassigned outdir resolves correctly."""
    code = re.sub(r"(?m)^\s*#.*$", "", code)
    v = dict(params or {})
    out = {"writes": [], "reads": [], "sources": []}
    events = [(m.start(), 0, m) for m in ASG_RE.finditer(code)] + [(m.start(), 1, m) for m in CALL_RE.finditer(code)]
    for _, kind, m in sorted(events, key=lambda e: (e[0], e[1])):
        if kind == 0:
            if not CALL_RE.match(m.group(2)): v[m.group(1)] = evaluate(m.group(2), v)  # x <- readRDS(..) is not a path
            continue
        name = m.group(1)
        args, _ = split_call(code, m.end())
        a = file_arg(args, WRITE[name] if name in WRITE else READ[name])
        if a is None: continue
        p = evaluate(a, v)
        if p.replace("*", "").strip() == "": continue
        out["writes" if name in WRITE else "sources" if name == "source" else "reads"].append(p)
    for k in out: out[k] = sorted(set(out[k]))
    return out


# ---------- paths / matching ----------
def make_norm(aliases, root):
    """-> resolve(pattern, script) -> list of absolute candidate patterns (relative fallback last)."""
    al = sorted(((os.path.expanduser(k), v) for k, v in aliases.items()), key=lambda kv: -len(kv[0]))
    def alias(p):
        for _ in range(5):  # aliases may chain (old project path -> repo -> symlinked results)
            for k, val in al:
                if p == k or p.startswith(k + "/"): p = val + p[len(k):]; break
            else: break
        return p
    def real(p):  # resolve symlinks in the literal directory part, so results/ == /data/local/...
        pre = p.split("*", 1)[0]
        d = pre[:pre.rfind("/")]
        return (os.path.realpath(d) if d else "") + p[len(d):] if p.startswith("/") else p
    def resolve(p, script):
        p = alias(os.path.expanduser(p))
        if p.startswith(("/", "*")): return [real(p)]
        rel = re.sub(r"^(\./)+", "", p)
        cands = [real(alias(os.path.normpath(os.path.join(b, rel)))) for b in (os.path.dirname(script), root)]
        return list(dict.fromkeys(cands)) + [rel]  # bare relative = suffix match, used only if nothing else hits
    return resolve

def to_regex(p):
    body = ".*".join(re.escape(x) for x in p.split("*"))
    return re.compile(body if p.startswith("/") else r"(?:.*/)?" + body)

class Index:
    def __init__(self, files):
        self.files = sorted(files)
        self.by_base = defaultdict(list)
        self.by_dir = defaultdict(list)
        self.by_ext = defaultdict(list)
        for f in self.files:
            self.by_base[os.path.basename(f)].append(f)
            self.by_dir[os.path.dirname(f)].append(f)
            self.by_ext[os.path.splitext(f)[1]].append(f)
    def match(self, pat):
        """-> (list of files, 'exact'|'glob')"""
        if "*" not in pat:
            base = self.by_base.get(os.path.basename(pat), [])
            return [f for f in base if f == pat or (not pat.startswith("/") and f.endswith("/" + pat.lstrip("/")))], "exact"
        if len(pat.replace("*", "")) < 8: return [], "glob"  # too vague
        d, _, b = pat.rpartition("/")
        if d.startswith("/") and "*" not in d:  # fast path: wildcard only in file name
            rx = re.compile(".*".join(re.escape(x) for x in b.split("*")))
            return [f for f in self.by_dir.get(d, []) if rx.fullmatch(os.path.basename(f))], "glob"
        rx = to_regex(pat)
        if pat.startswith("/"):
            pre = pat.split("*", 1)[0]
            pre = pre[:pre.rfind("/") + 1]
            lo = bisect.bisect_left(self.files, pre)
            hi = bisect.bisect_left(self.files, pre + "\U0010ffff")
            cands = self.files[lo:hi]
        else:
            ext = os.path.splitext(pat)[1]
            cands = self.by_ext.get(ext, []) if ext and "*" not in ext else self.files
        tail = pat.rsplit("*", 1)[1]  # cheap literal-suffix filter before the regex
        return [f for f in cands if f.endswith(tail) and rx.fullmatch(f)], "glob"

def ts(t): return datetime.datetime.fromtimestamp(t).isoformat(timespec="seconds")
def created(st): return getattr(st, "st_birthtime", st.st_mtime)

def git_dates(root):
    """-> (last-commit date per tracked path, set of dirty/untracked paths); empty if not a git repo."""
    def git(*a):
        try: return subprocess.run(["git", *a], cwd=root, capture_output=True, text=True).stdout
        except OSError: return ""
    top = git("rev-parse", "--show-toplevel").strip()
    if not top: return {}, set()
    dates, cur = {}, None
    for ln in git("log", "--format=@%cI", "--name-only").splitlines():  # newest first; first hit wins
        if ln.startswith("@"): cur = ln[1:20]
        elif ln: dates.setdefault(os.path.join(top, ln), cur)
    dirty = {os.path.join(top, ln[3:].split(" -> ")[-1]) for ln in git("status", "--porcelain", "-uall").splitlines()}
    return dates, dirty

def last_change(path, commit, dirty):
    """Timestamp of a script's last real edit.
    Uncommitted edits: file mtime. Otherwise the earlier of commit date and mtime: the content cannot have
    changed after either, and this handles both a fresh clone (mtime = clone time -> commit date wins) and
    files committed long after they were written (mtime is older -> mtime wins)."""
    mtime = os.stat(path).st_mtime
    d = None if path in dirty else commit.get(path)
    return min(mtime, datetime.datetime.fromisoformat(d).timestamp()) if d else mtime

def walk(roots, exclude, hidden=False):
    """Yield files under roots. Hidden folders (.git, .snakemake, ...) are skipped unless hidden=True;
    a hidden folder listed explicitly as a root is still scanned."""
    ex = re.compile("|".join(fnmatch.translate(e) for e in exclude) or "(?!)")
    for r in roots:
        real = os.path.realpath(r)
        if os.path.isfile(real): yield real; continue
        for d, ds, fs in os.walk(real):
            ds[:] = [x for x in ds if not ex.match(x) and (hidden or not x.startswith("."))]
            for f in fs:
                if not ex.match(f): yield os.path.join(d, f)


# ---------- main ----------
# an output is "possibly stale" when its script was edited more than this after it was written
# (edits within the hour are usually "ran it, then fixed a comment"). Mirrored by STALE_MS in view.html.
STALE_SECONDS = 3600
# evidence that a script made a file, strongest first. Only the best kind found for a file is reported.
RANK = {"write": 0, "stem-rule": 1, "name": 2, "write-glob": 3, "lang-write": 4, "lang-glob": 5,
        "mention": 6, "mention-glob": 7}

def main():
    ap = argparse.ArgumentParser(prog="fms", description="findMySource: trace outputs back to the scripts that made them.")
    ap.add_argument("--config", default="fms.json")
    ap.add_argument("--out", default="fms-report")
    ap.add_argument("--version", action="version", version=__version__)
    a = ap.parse_args()
    try: cfg = json.load(open(a.config)) if os.path.exists(a.config) else {}
    except ValueError as e: sys.exit(f"bad config {a.config}: {e}")
    tpl_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "view.html")
    if not os.path.exists(tpl_path): sys.exit(f"missing template {tpl_path} (broken install?)")
    root = os.path.realpath(".")
    exts = tuple(e.lower() for e in cfg.get("extensions", list(SCRIPT_EXT) + ["snakefile", "makefile"]))
    exclude = cfg.get("exclude", ["rstudio-server-db", "rstudio_state", "__pycache__", "*_cache",
                                  "*.RData", ".Rhistory", "#*#", "%", ".DS_Store"])
    hidden = cfg.get("hidden", False)  # scan hidden folders too
    aliases = {k: (v if v.startswith("/") else os.path.normpath(os.path.join(root, v)))
               for k, v in cfg.get("aliases", {}).items()}
    resolve = make_norm(aliases, root)
    rel = lambda p: os.path.relpath(p, root) if p.startswith(root + "/") else p

    # 1. scripts: generic engine twice (own variables first, then with variables of source()d/imported scripts)
    commit, dirty = git_dates(root)
    scripts = sorted({s for s in walk(cfg.get("scripts", ["."]), exclude, hidden) if s.lower().endswith(exts)})
    info, code_of = {}, {}
    for s in scripts:
        try: st = os.stat(s)
        except OSError: continue
        code_of[s] = read_code(s)
        # git date unless the file has uncommitted changes (then its mtime is the real last change)
        changed = last_change(s, commit, dirty)
        info[s] = {"modified": ts(changed), "mtime": changed,
                   **generic_scan(*code_of[s], vars_only=True)}
    scripts = [s for s in scripts if s in info]
    by_name = defaultdict(list)
    for s in scripts: by_name[os.path.basename(s)].append(s)
    for s in scripts:
        used = []
        for u in info[s]["uses"]:
            cands = [x for x in by_name.get(u, []) if x != s]
            same = [x for x in cands if os.path.dirname(x) == os.path.dirname(s)]
            used += same or cands[:1]  # ponytail: one level deep; follow chains if configs source configs
        code, params = code_of[s]
        inherited = {k: val for u in used for k, val in info[u]["vars"].items()}
        g = generic_scan(code, {**inherited, **params}, stmts=info[s]["stmts"])
        info[s].update(g, used=used, lang=parse_script(code, params))
    print(f"parsed {len(scripts)} scripts", file=sys.stderr)

    # 2. files (one stat each)
    stat = {}
    for f in walk(cfg.get("outputs", ["."]), exclude, hidden):
        try: stat[f] = os.stat(f)
        except OSError: pass
    rep = defaultdict(set)  # rendered reports next to their Rmd/qmd/ipynb
    for s in scripts:
        if s.lower().endswith((".rmd", ".qmd", ".ipynb")):
            stem = os.path.splitext(s)[0]
            for suf in (".html", ".nb.html", ".md", ".pdf", ".docx"):
                if os.path.exists(stem + suf):
                    rep[stem + suf].add(s)
                    stat.setdefault(stem + suf, os.stat(stem + suf))
    idx = Index(set(stat) | set(scripts))

    def match(p, s):
        """resolved candidates first; bare-relative suffix match only as fallback."""
        cands = resolve(p, s)
        for c in cands[:-1] if len(cands) > 1 else cands:
            hit, conf = idx.match(c)
            if hit: return hit, conf
        return idx.match(cands[-1]) if len(cands) > 1 else ([], "exact")

    # 3. link: every piece of evidence gets a RANK; an output's sources are the scripts with the best rank
    src_of = defaultdict(dict)  # output -> {script: label}
    def add(f, s, label):
        if f in info or f == s: return
        if RANK[label] < RANK.get(src_of[f].get(s), 99): src_of[f][s] = label
    edges, reads_of = set(), defaultdict(set)
    by_stem = defaultdict(list)
    for f in stat: by_stem[os.path.basename(f).split(".")[0]].append(f)
    for s, d in info.items():
        for p, kind in d["ev"]:
            hit, conf = match(p, s)
            if kind == "read":
                for f in hit: reads_of[s].add(f); edges.add((f, s, "reads"))
                if not hit and "*" not in p: edges.add((resolve(p, s)[0], s, "reads"))  # input outside the scan
                continue
            for f in hit: add(f, s, kind + ("-glob" if conf == "glob" else ""))
        for n in d["names"]:
            for f in by_stem.get(n, []): add(f, s, "name")
        for p in d["lang"]["writes"]:  # R-specific fallback: only wins where the generic evidence is weaker
            hit, conf = match(p, s)
            for f in hit: add(f, s, "lang-write" if conf == "exact" else "lang-glob")
        for p in d["lang"]["reads"]:
            for f in match(p, s)[0]: reads_of[s].add(f); edges.add((f, s, "reads"))
        for u in d["used"]: edges.add((u, s, "source"))
        for p in d["lang"]["sources"]:
            for f in match(p, s)[0]:
                if f in info and f != s: edges.add((f, s, "source"))
    for f, ss in rep.items():
        for s in ss: add(f, s, "stem-rule")
    for s, fs in reads_of.items():  # a script that reads a file is not its source because it also names it
        for f in fs:
            if src_of[f].get(s, "").startswith(("mention", "name")): del src_of[f][s]
    for f in list(src_of):  # keep only the strongest kind of evidence per output
        best = min((RANK[l] for l in src_of[f].values()), default=None)
        src_of[f] = {s: l for s, l in src_of[f].items() if RANK[l] == best}
        if not src_of[f]: del src_of[f]

    # 4. manifest
    os.makedirs(a.out, exist_ok=True)
    rows, outputs, stale = [], sorted(set(stat) - set(info)), {}
    with open(os.path.join(a.out, "manifest.tsv"), "w") as m:
        m.write("output\tsources\tevidence\toutput_created\toutput_modified\tsource_modified\tstale_days\tinputs\n")
        for f in outputs:
            st, ss = stat[f], src_of.get(f, {})
            # best confidence first; ties -> script whose last change is closest before the output
            order = sorted(ss, key=lambda s: (info[s]["mtime"] > st.st_mtime, -info[s]["mtime"]))
            gap = max((info[s]["mtime"] for s in order), default=0) - st.st_mtime
            if order and gap > STALE_SECONDS: stale[f] = round(gap / 86400, 1)
            m.write("\t".join([
                rel(f), ";".join(rel(s) for s in order), ";".join(ss[s] for s in order) or "unmatched",
                ts(created(st)), ts(st.st_mtime), ";".join(info[s]["modified"] for s in order),
                str(stale.get(f, "")),
                ";".join(sorted({rel(i) for s in order for i in reads_of[s]}))[:2000]]) + "\n")
            if order: rows.append({"o": rel(f), "s": [rel(x) for x in order], "c": [ss[x] for x in order],
                                   "m": ts(st.st_mtime), "sm": [info[x]["modified"] for x in order]})

    # 5. graph: linked files only. When one script writes >15 files into one folder, a "group" node is added
    #    and those files get "grp": <group id>; the page can show either the group or the files (toggle).
    nodes, nid = [], {}
    def node(key, label, group, title):
        if key not in nid:
            nid[key] = len(nodes)
            nodes.append({"id": nid[key], "label": label, "group": group, "title": title})
        return nid[key]
    snode = lambda s: node(s, os.path.basename(s), "script", f"{rel(s)}\nlast change {info[s]['modified']}")
    def fnode(f):
        tip = f"{rel(f)}\nmodified {ts(stat[f].st_mtime)}"
        if f in stale: tip += f"\n⚠ script edited {stale[f]:g} days later: possibly stale"
        fid = node(f, os.path.basename(f), "file", tip)
        if f in stale: nodes[fid]["stale"] = stale[f]
        return fid
    grp = defaultdict(list)
    for f, ss in src_of.items():
        for s in ss: grp[(s, os.path.dirname(f))].append(f)
    gedges = set()
    for (s, d), fl in sorted(grp.items()):
        sn = snode(s)
        if len(fl) > 15:
            n_stale = sum(f in stale for f in fl)
            g = node((s, d), f"{os.path.basename(d)}/ ({len(fl)} files)", "group",
                     rel(d) + (f"\n⚠ {n_stale} possibly stale" if n_stale else "") + "\n" +
                     "\n".join(sorted(os.path.basename(x) for x in fl)[:60]))
            if n_stale: nodes[g]["stale"] = n_stale
            gedges.add((sn, g, "writes"))
        for f in fl:
            fid = fnode(f)
            if len(fl) > 15: nodes[fid].setdefault("grp", g)
            gedges.add((sn, fid, "writes"))
    for f, s, k in sorted(edges):
        fid = nid.get(f)
        if fid is None and s not in nid and k == "reads": continue  # input of an unconnected script
        if fid is None:
            fid = snode(f) if f in info else node(f, os.path.basename(f), "input", rel(f) + "\n(not a tracked output)")
        if fid != snode(s): gedges.add((fid, snode(s), k))
    seen = defaultdict(list)
    for n in nodes:
        if n["group"] in ("file", "input"): seen[n["label"]].append(n)
    for same in seen.values():
        if len(same) > 1:  # four "deconvolution_results.RDS" -> CAD_vs_ACS/deconvolution_results.RDS, ...
            for n in same: n["label"] = "/".join(n["title"].split("\n")[0].split("/")[-2:])
    graph = {"nodes": nodes, "edges": [{"from": x, "to": y, "kind": k} for x, y, k in sorted(gedges)]}
    json.dump(graph, open(os.path.join(a.out, "graph.json"), "w"))

    data = {"graph": graph, "rows": rows, "scripts": [{"p": rel(x), "m": info[x]["modified"]} for x in scripts],
            "unmatched": len(outputs) - len(rows), "root": root,
            "generated": datetime.datetime.now().isoformat(timespec="minutes")}
    html = open(tpl_path).read().replace("__DATA__", json.dumps(data).replace("</", "<\\/"))
    open(os.path.join(a.out, "index.html"), "w").write(html)
    print(f"{len(rows)}/{len(outputs)} outputs matched; {len(nodes)} nodes, {len(gedges)} edges -> {a.out}/",
          file=sys.stderr)


if __name__ == "__main__":
    main()
