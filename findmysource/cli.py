"""findMySource (fms): trace every output of a project back to the script(s) that made it.

Usage (run from the project root):
    fms [--config fms.json] [--out DIR]   scan, write the report (.fms/), print a summary
    fms <file>      where it came from (reads the last scan)
    fms <script>    what it writes and reads
    fms <folder>    possibly stale outputs in it (exit code 1 if any)
    fms --serve [PORT]   rescan, then serve the report on localhost (for a project on a remote machine)

Writes <out>/manifest.tsv, <out>/graph.json and <out>/index.html (tabs: flow, timeline, matrix, table, steps, unmatched).

Two engines, combined on one evidence scale (see RANK). The rules themselves, with reasons and examples, are
in rules.py; this file applies them:
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
Config keys (JSON, all optional): scripts, outputs, exclude, aliases, extensions, hidden, write_functions.
  {"scripts": ["scripts"], "outputs": ["results", "figures"], "aliases": {"/local": "/data/local"}}
"""
import argparse, bisect, csv, datetime, fnmatch, hashlib, json, os, re, subprocess, sys, time
from collections import Counter, defaultdict

from . import __version__, rules
from .rules import RANK, SCRIPT_EXT, STALE_SECONDS

WRITE, READ, FILE_KEYS = rules.R_WRITE, rules.R_READ, rules.R_FILE_ARGS
CALL_RE = re.compile(r"(?<![\w.])(" + "|".join(re.escape(n) for n in {**WRITE, **READ}) + r")\s*\(")
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
names_re = lambda ns, flags=re.I: re.compile(r"(?:^|[.:])(?:" + "|".join(map(re.escape, ns)) + r")$", flags)  # pkg::f
JOIN_RE, CONCAT_RE, WRAP_RE = map(names_re, (rules.JOIN_CALLS, rules.CONCAT_CALLS, rules.WRAP_CALLS))
FORMAT_RE = names_re(rules.FORMAT_CALLS, flags=0)  # case-sensitive, as these always were
def is_builder(fn):
    return bool(JOIN_RE.search(fn) or CONCAT_RE.search(fn) or WRAP_RE.search(fn) or FORMAT_RE.search(fn))

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
WRITE_VERB, READ_VERB = (re.compile("|".join(vs), re.I) for vs in (rules.WRITE_VERBS, rules.READ_VERBS))
WRITE_MODE, READ_MODE = (re.compile(r"""^\s*(?:mode\s*=\s*)?["'](?:""" + "|".join(map(re.escape, ms)) + r""")["']\s*$""")
                         for ms in (rules.WRITE_MODES, rules.READ_MODES))
CALL_ANY = re.compile(r"([A-Za-z_.][\w.$]*(?:::[A-Za-z_.][\w.$]*)?)\s*\(")
GEN_ASG = re.compile(r"^\s*(?:export\s+|local\s+)?([A-Za-z_.][\w.$]*)\s*(?:<<-|<-|:=|=(?!=))\s*(.+)$", re.S)
FILE_LIKE = re.compile(rules.FILE_EXT_RE)
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

# a string literal with its prefix (f"", r''): triple-quoted ones may span lines, others end at the line
QUOTED = re.compile(r"""(?<!\w)([rRbBuUfF]{0,2})("{3}[\s\S]*?"{3}|'{3}[\s\S]*?'{3}|"(?:[^"\\\n]|\\.)*"|'(?:[^'\\\n]|\\.)*')""")
def blank(m):
    """the string's text as spaces, same length and lines (offsets stay valid); an f-string keeps its {...} fields"""
    p, q = m.groups()
    keep = r"(\{\{|\}\})|(\{[^{}]*\})|[^\n]" if "f" in p.lower() else r"()()[^\n]"
    return p + q[0] + re.sub(keep, lambda x: x.group(2) or " " * len(x.group()), q[1:-1]) + q[-1]


def direction(fn, args):
    if any(WRITE_MODE.match(a) for a in args): return "write"
    if any(READ_MODE.match(a) for a in args): return "read"
    name = re.split(r"[.:$]", fn)[-1]
    if WRITE_VERB.search(name): return "write"
    if READ_VERB.search(name): return "read"
    return "mention"

def snippet(st, p):
    """first line of statement st, plus the line holding most literal pieces of path p's file name"""
    keys, lines = [k for k in os.path.basename(p).split("*") if len(k) > 2], [" ".join(l.split()) for l in st.split("\n")]
    hit = max(lines, key=lambda l: sum(k in l for k in keys))  # the line with most pieces of the name (ties: first)
    return (hit if hit == lines[0] else f"{lines[0][:60]} … {hit}")[:200]

def generic_scan(code, v0=None, stmts=None, vars_only=False, writers=None, code_in_strings=False):
    """-> dict(ev=[(pattern, write|read|mention, statement)], names={literal}, uses={script name}, vars={}, stmts=[]).
    vars_only: only follow assignments and script references (cheap first pass).
    writers: the project's own write functions from fms.json, {name: argument name or position of the path};
    a folder there means every file in it.
    code_in_strings: calls inside string literals count too (shell scripts, Snakemake: rules.CODE_IN_STRINGS)."""
    writers = writers or {}
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
            for cm in CALL_ANY.finditer(st if code_in_strings else QUOTED.sub(blank, st)):  # 'f("x.csv")' is text
                fn = cm.group(1)
                # configured: wins over every guess below, builders included. Full name first (save.plots), then
                # without its package or module (pkg::convert_all, converters.convert_all)
                w = next((writers[k] for k in (fn, fn.rsplit("::", 1)[-1], re.split(r"[.:$]", fn)[-1]) if k in writers), None)
                if w is None and is_builder(fn): continue  # evaluated as part of the enclosing argument
                args, _ = split_call(st, cm.end())
                if w is not None:
                    pos = [x for x in args if not NAMED.match(x)]
                    a = (pos[w] if 0 <= w < len(pos) else None) if isinstance(w, int) else next(
                        (NAMED.sub("", x, count=1) for x in args if re.match(rf"\s*{re.escape(w)}\s*=(?!=)", x)), None)
                    p = evaluate(a, v) if a else "*"
                    if p.replace("*", "").strip("/") and "://" not in p:  # any explicit name, even "qc" or "."
                        # no extension: maybe a folder ("/*" ends no other evidence); the link step decides
                        ev.append((p if FILE_LIKE.search(p) else p.rstrip("/") + "/*", "write", snippet(st, p)))
                    continue
                kind = direction(fn, args)
                for a in args:
                    a = NAMED.sub("", a, count=1)
                    p = evaluate(a, v)
                    if FILE_LIKE.search(p) and len(p.replace("*", "")) >= 3 and "://" not in p:
                        ev.append((p, kind, snippet(st, p)))
                    lit = re.fullmatch(r"""\s*["']([\w.\-]{4,})["']\s*""", a)
                    if lit and kind == "write" and not FILE_LIKE.search(lit.group(1)):
                        names.add(lit.group(1))  # save_table(df, "de_table") -> de_table.*
        m = GEN_ASG.match(st)
        if m:
            val = evaluate(m.group(2), v)
            v[m.group(1)] = val
            if not vars_only and FILE_LIKE.search(val) and len(val.replace("*", "")) >= 3 and "://" not in val:
                ev.append((val, "mention", snippet(st, val)))
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

def parse_script(code, params=None, code_in_strings=False):
    """-> dict(writes=[pattern], reads=[pattern], sources=[pattern]).
    Assignments and calls are processed in file order, so a reassigned outdir resolves correctly.
    code_in_strings: as in generic_scan, calls inside string literals count too."""
    code = re.sub(r"(?m)^\s*#.*$", "", code)
    v = dict(params or {})
    out = {"writes": [], "reads": [], "sources": []}
    events = [(m.start(), 0, m) for m in ASG_RE.finditer(code)] + [(m.start(), 1, m) for m in CALL_RE.finditer(code if code_in_strings else QUOTED.sub(blank, code))]
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
    name = "/" + os.path.basename(root) + "/"
    def moved(p):  # another clone of this project (a colleague's home, an old server path): map it here
        if p.startswith(root + "/") or os.path.exists(p.split("*", 1)[0].rpartition("/")[0]): return [p]
        cands, i = [], p.find(name)
        while i >= 0:  # /c/proj/proj/out/x.pdf: either /proj/ may be the project; the matcher tries each in turn
            cands.append(root + p[i + len(name) - 1:]); i = p.find(name, i + 1)
        return cands or [p]
    def resolve(p, script):
        p0 = os.path.expanduser(p); p = alias(p0)
        if p == p0 and p.startswith("/"): return [real(c) for c in moved(p)]  # a configured alias wins
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
        for f in self.files:  # one split per path (108k files: os.path.* calls add up)
            d, _, b = f.rpartition("/")
            i = b.rfind(".")
            self.by_base[b].append(f)
            self.by_dir[d].append(f)
            self.by_ext[b[i:] if i > 0 else ""].append(f)  # same as os.path.splitext
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

def ts(t):
    """local time with its offset, e.g. 2026-11-19T12:05:00+01:00 (strftime: ~10x faster than datetime.astimezone)"""
    s = time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(t))
    return s[:-2] + ":" + s[-2:]
def created(st): return getattr(st, "st_birthtime", st.st_mtime)

def path_batches(paths, max_chars=32768):
    """split paths into batches that stay well below the OS limit for command-line length"""
    batch, size = [], 0
    for p in paths:
        if batch and size + len(p) + 1 > max_chars: yield batch; batch, size = [], 0
        batch.append(p); size += len(p) + 1
    if batch: yield batch

def git_dates(root, paths=None):
    """-> (last-commit date per tracked path, set of dirty/untracked paths); empty if not a git repo.
    paths: only ask git about these files (much faster than the whole history). Paths outside the
    repository are left out, so they cannot make git fail for the rest."""
    def git(*a):
        try: return subprocess.run(["git", "--literal-pathspecs", *a], cwd=top or root, capture_output=True, text=True).stdout
        except OSError: return ""
    top = None
    top = git("rev-parse", "--show-toplevel").strip()
    if not top: return {}, set()
    if paths is None: batches = [[]]
    else:
        inside = [os.path.relpath(p, top) for p in map(os.path.realpath, paths) if p.startswith(top + os.sep)]
        if not inside: return {}, set()
        batches = list(path_batches(inside))
    dates, dirty, cur = {}, set(), None
    for b in batches:
        for ln in git("log", "--format=@%cI", "--name-only", "--", *b).splitlines():  # newest first; first hit wins
            if ln.startswith("@"): cur = ln[1:]  # keep the time-zone offset
            elif ln: dates.setdefault(os.path.join(top, ln), cur)
        dirty |= {os.path.join(top, ln[3:].split(" -> ")[-1]) for ln in git("status", "--porcelain", "-uall", "--", *b).splitlines()}
    return dates, dirty

def last_change(path, commit, dirty):
    """Timestamp of a script's last real edit.
    Uncommitted edits: file mtime. Otherwise the earlier of commit date and mtime: the content cannot have
    changed after either, and this handles both a fresh clone (mtime = clone time -> commit date wins) and
    files committed long after they were written (mtime is older -> mtime wins)."""
    mtime = os.stat(path).st_mtime
    d = None if path in dirty else commit.get(path)
    # git writes UTC as "...Z", which fromisoformat only reads from Python 3.11 on
    return min(mtime, datetime.datetime.fromisoformat(d.replace("Z", "+00:00")).timestamp()) if d else mtime

def walk(roots, exclude, hidden=False, skip=()):
    """Yield files under roots. Hidden folders (.git, .snakemake, ...) are skipped unless hidden=True;
    a hidden folder listed explicitly as a root is still scanned. skip: folders never to enter
    (the report folder, so that a report never becomes an output of the next scan)."""
    ex = re.compile("|".join(fnmatch.translate(e) for e in exclude) or "(?!)")
    skip = {os.path.realpath(p) for p in skip}
    for r in roots:
        real = os.path.realpath(r)
        if real in skip: continue
        if os.path.isfile(real): yield real; continue
        for d, ds, fs in os.walk(real):
            ds[:] = [x for x in ds if not ex.match(x) and (hidden or not x.startswith("."))
                     and os.path.join(d, x) not in skip]
            for f in fs:
                if not ex.match(f): yield os.path.join(d, f)


# ---------- main ----------

def _parse1(s):
    """first pass over one script: own variables, script references, statements, R fallback"""
    src, params = read_code(s)
    g = generic_scan(src, params, vars_only=True)
    return {"params": params, "uses": sorted(g["uses"]), "vars": g["vars"], "stmts": g["stmts"],
            "lang": parse_script(src, params, code_in_strings=s.lower().endswith(rules.CODE_IN_STRINGS))}

def _parse2(job):
    """second pass: evidence, with the variables inherited from source()d / imported scripts"""
    s, v0, stmts, writers = job
    g = generic_scan(read_code(s)[0], v0, stmts=stmts, writers=writers,
                     code_in_strings=s.lower().endswith(rules.CODE_IN_STRINGS))
    return [list(e) for e in g["ev"]], sorted(g["names"])

def pmap(fn, items):
    """map over processes (parsing is CPU-bound); serial for a few items, or where processes are unavailable"""
    if len(items) < 8: return [fn(x) for x in items]
    from concurrent.futures import ProcessPoolExecutor
    try:
        with ProcessPoolExecutor() as ex:
            return list(ex.map(fn, items, chunksize=max(1, len(items) // (4 * (os.cpu_count() or 1)))))
    except (OSError, ImportError, NotImplementedError):  # e.g. no /dev/shm in some sandboxes
        return [fn(x) for x in items]

def arbitrate(cands, readers):
    """Choose the sources of one output. cands: {script: evidence label}, the best label per script; readers:
    scripts that read the output. -> ({script: label} of the winners, [(script, label, reason)] of the others).
    A reader's weak evidence is dropped (it names the file because it reads it); then the strongest kind of
    evidence wins, and every script with that kind is a source."""
    keep, lost = {}, []
    for s, l in cands.items():
        if s in readers and l.startswith(rules.DROPPED_FOR_READERS): lost.append((s, l, "reads this file"))
        else: keep[s] = l
    best = min((RANK[l] for l in keep.values()), default=None)
    top = next((l for l in keep.values() if RANK[l] == best), None)  # the winning label, looked up once
    lost += [(s, l, f"weaker than {top}") for s, l in keep.items() if RANK[l] != best]
    return {s: l for s, l in keep.items() if RANK[l] == best}, sorted(lost, key=lambda x: (RANK[x[1]], x[0]))


def took(t0):
    s = time.monotonic() - t0
    return f"{s:.1f} s" if s < 60 else f"{int(s // 60)} min {int(s % 60)} s"

def scan(a):
    """Scan the project in the current folder; write manifest.tsv, graph.json, index.html; print a summary."""
    t0 = time.monotonic()
    try: cfg = json.load(open(a.config)) if os.path.exists(a.config) else {}
    except ValueError as e: sys.exit(f"bad config {a.config}: {e}")
    tpl_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "view.html")
    if not os.path.exists(tpl_path): sys.exit(f"missing template {tpl_path} (broken install?)")
    root = os.path.realpath(".")
    exts = tuple(e.lower() for e in cfg.get("extensions", list(SCRIPT_EXT + rules.SCRIPT_NAMES)))
    exclude = cfg.get("exclude", rules.EXCLUDE)
    script_exclude = exclude if "exclude" in cfg else exclude + rules.SCRIPT_ONLY_EXCLUDE
    hidden = cfg.get("hidden", False)  # scan hidden folders too
    writers = cfg.get("write_functions", {})  # the project's own write functions: {name: path argument}
    chosen, out = a.out, os.path.realpath(report_dir(root, a.out, write=True))
    a.out = out
    if out == os.path.join(root, ".fms"):  # ignores itself, so it is never committed; also restored on a rerun
        os.makedirs(out, exist_ok=True)
        if not os.path.exists(os.path.join(out, ".gitignore")): open(os.path.join(out, ".gitignore"), "w").write("*\n")
    if not chosen and out == os.path.join(root, "fms-report"):
        print("report in fms-report/ (fms < 0.3); delete it to move the report to .fms/", file=sys.stderr)
    aliases = {k: (v if v.startswith("/") else os.path.normpath(os.path.join(root, v)))
               for k, v in cfg.get("aliases", {}).items()}
    resolve = make_norm(aliases, root)
    pre = root + "/"
    rel = lambda p: p[len(pre):] if p.startswith(pre) else p  # paths are already absolute and normalised

    # cache in the report folder: the parse of every script (keyed by its mtime and size) and a fingerprint of the
    # whole scan. Thrown away when fms itself changes.
    cache_path = os.path.join(out, "cache.json")
    tool = [__version__, os.path.getmtime(__file__), os.path.getmtime(rules.__file__), os.path.getmtime(tpl_path)]
    try:
        old = json.load(open(cache_path))
        if old.get("tool") != tool: old = {}
    except (OSError, ValueError): old = {}
    cached, parsed = old.get("scripts", {}), {}

    # 1. scripts: generic engine twice (own variables first, then with variables of source()d/imported scripts)
    scripts = sorted({s for s in walk(cfg.get("scripts", ["."]), script_exclude, hidden, skip=[out])
                      if s.lower().endswith(exts)})
    commit, dirty = git_dates(root, scripts)
    info, keys = {}, {}
    for s in scripts:
        try: st = os.stat(s)
        except OSError: continue
        keys[s] = [st.st_mtime_ns, st.st_size]
        if s in cached and cached[s]["key"] == keys[s]: parsed[s] = cached[s]
    todo = [s for s in keys if s not in parsed]  # new or changed scripts (unchanged ones are not even read)
    for s, c in zip(todo, pmap(_parse1, todo)): parsed[s] = {"key": keys[s], **c}
    for s in keys:
        c = parsed[s]
        # git date unless the file has uncommitted changes (then its mtime is the real last change)
        changed = last_change(s, commit, dirty)
        info[s] = {"modified": ts(changed), "mtime": changed, **{k: c[k] for k in ("uses", "vars", "stmts", "lang")}}
    scripts = [s for s in scripts if s in info]
    by_name = defaultdict(list)
    for s in scripts: by_name[os.path.basename(s)].append(s)
    jobs = []
    for s in scripts:
        used = []
        for u in info[s]["uses"]:
            cands = [x for x in by_name.get(u, []) if x != s]
            same = [x for x in cands if os.path.dirname(x) == os.path.dirname(s)]
            used += same or cands[:1]  # ponytail: one level deep; follow chains if configs source configs
        c = parsed[s]
        v0 = {**{k: val for u in used for k, val in info[u]["vars"].items()}, **c["params"]}
        vh = hashlib.sha1(json.dumps([v0, writers], sort_keys=True).encode()).hexdigest()
        if c.get("vh") != vh: jobs.append((s, vh, v0))  # new script, or the variables it inherits changed
        info[s]["used"] = used
    for (s, vh, _), (ev, names) in zip(jobs, pmap(_parse2, [(s, v0, parsed[s]["stmts"], writers) for s, _, v0 in jobs])):
        parsed[s].update(vh=vh, ev=ev, names=names)
    for s in scripts: info[s].update(ev=parsed[s]["ev"], names=parsed[s]["names"])
    print(f"parsed {len(scripts)} scripts ({len(todo)} read, {len(scripts) - len(todo)} from cache) in {took(t0)}", file=sys.stderr)
    tm = {"parse": time.monotonic() - t0}  # seconds per phase, kept in cache.json for tests/bench.py

    # 2. files (one stat each)
    stat = {}
    for f in walk(cfg.get("outputs", ["."]), exclude, hidden, skip=[out]):
        try: stat[f] = os.stat(f)
        except OSError: pass
    rep = defaultdict(set)  # rendered reports next to their Rmd/qmd/ipynb
    for s in scripts:
        if s.lower().endswith(rules.NOTEBOOK_EXT):
            stem = os.path.splitext(s)[0]
            for suf in (".html", ".nb.html", ".md", ".pdf", ".docx"):
                if os.path.exists(stem + suf):
                    rep[stem + suf].add(s)
                    stat.setdefault(stem + suf, os.stat(stem + suf))
    nb = defaultdict(set)  # a.Rmd and a.qmd may share a_files/
    for s in scripts:
        if s.lower().endswith(rules.NOTEBOOK_EXT): nb[os.path.splitext(s)[0]].add(s)
    chunk = {}  # chunk figures that knitr/Quarto put in <stem>_files/: the deepest notebook stem wins
    for f in stat:
        i = f.rfind("_files/")
        while i > 0 and f[:i] not in nb: i = f.rfind("_files/", 0, i)
        if i > 0: chunk[f] = nb[f[:i]]
    # nothing changed since the last scan (scripts, their dates, every file and its timestamp, config)? then the
    # report on disk is still right
    fp = hashlib.sha1(json.dumps([tool, cfg, root, [(s, info[s]["modified"], parsed[s]["key"]) for s in scripts]]).encode())
    for f in sorted(stat): fp.update(f"{f}\0{stat[f].st_mtime_ns}\0{stat[f].st_size}\n".encode())
    fp = fp.hexdigest()
    tm["files"] = time.monotonic() - t0 - sum(tm.values())
    if fp == old.get("fingerprint") and all(os.path.exists(os.path.join(a.out, x)) for x in ("index.html", "manifest.tsv")):
        try: os.utime(os.path.join(a.out, "manifest.tsv"))  # queries read the newest report: this one, even unchanged
        except OSError: pass  # someone else's report (--out): only a hint for choosing between reports
        print(f"no changes since the last scan ({old.get('generated', '?')}); report is up to date ({took(t0)}): {rel(os.path.join(a.out, 'index.html'))}", file=sys.stderr)
        print(old.get("summary", ""))
        return
    idx = Index(set(stat) | set(scripts))

    def match(p, s, folder=False):
        """resolved candidates first; bare-relative suffix match only as fallback.
        folder: p is "<folder>/*", a configured write function's path without an extension. Per candidate: a file
        of that name, else the files directly in that folder; a candidate with neither falls through to the next"""
        cands = resolve(p, s)
        def one(c):
            if not (folder and c.endswith("/*")): return idx.match(c)
            files, conf = idx.match(c[:-2])  # an existing file without an extension
            if files and "*" not in c[:-2]: return files, conf  # a literal name: that file, not a folder
            r = to_regex(c[:-2])  # the resolved folder: symlinks and wildcards as in the candidate itself
            hit, conf = idx.match(c)
            # a wildcard name stands for several paths: some may be files, others folders
            return files + [f for f in hit if r.fullmatch(f.rpartition("/")[0])], conf
        for c in cands[:-1] if len(cands) > 1 else cands:
            hit, conf = one(c)
            if hit: return hit, conf
        return one(cands[-1]) if len(cands) > 1 else ([], "exact")

    # 3. link: every piece of evidence gets a RANK; an output's sources are the scripts with the best rank
    src_of = defaultdict(dict)  # output -> {script: label}
    why = {}  # (output, script) -> code line of the best evidence
    def add(f, s, label, code=""):
        if f in info or f == s: return
        if RANK[label] < RANK.get(src_of[f].get(s), 99): src_of[f][s] = label; why[f, s] = code
    edges, reads_of = set(), defaultdict(set)
    by_stem = defaultdict(list)
    for f in stat: by_stem[f.rpartition("/")[2].split(".")[0]].append(f)
    for s, d in info.items():
        for p, kind, code in d["ev"]:
            hit, conf = match(p, s, folder=p.endswith("/*"))  # "/*": a configured path without an extension
            if kind == "read":
                for f in hit: reads_of[s].add(f); edges.add((f, s, "reads"))
                if not hit and "*" not in p: edges.add((resolve(p, s)[0], s, "reads"))  # input outside the scan
                continue
            for f in hit: add(f, s, kind + ("-glob" if conf == "glob" else ""), code)
        for n in d["names"]:
            for f in by_stem.get(n, []): add(f, s, "name", f'"{n}"')
        for p in d["lang"]["writes"]:  # R-specific fallback: only wins where the generic evidence is weaker
            hit, conf = match(p, s)
            key = max(os.path.basename(p).split("*"), key=len)  # find the code line by the pattern's literal part
            code = next((snippet(st, p) for st in d["stmts"] if key and key in st), "")
            for f in hit: add(f, s, "lang-write" if conf == "exact" else "lang-glob", code)
        for p in d["lang"]["reads"]:
            for f in match(p, s)[0]: reads_of[s].add(f); edges.add((f, s, "reads"))
        for u in d["used"]: edges.add((u, s, "source"))
        for p in d["lang"]["sources"]:
            for f in match(p, s)[0]:
                if f in info and f != s: edges.add((f, s, "source"))
    for f, ss in rep.items():
        for s in ss: add(f, s, "stem-rule")
    for f, ss in chunk.items():  # a guess from the folder name: any explicit evidence from another script wins
        for s in ss: add(f, s, "chunk-folder")
    readers = defaultdict(set)
    for s, fs in reads_of.items():
        for f in fs: readers[f].add(s)
    lost = {}  # output -> scripts with evidence that did not win, and why: shown by fms <file>
    for f in list(src_of):
        src_of[f], lost[f] = arbitrate(src_of[f], readers[f])
        if not src_of[f]: del src_of[f]

    tm["link"] = time.monotonic() - t0 - sum(tm.values())
    # 4. manifest
    os.makedirs(a.out, exist_ok=True)
    rows, outputs, stale = [], sorted(set(stat) - set(info)), {}
    with open(os.path.join(a.out, "manifest.tsv"), "w") as m:
        m.write("output\tsources\tevidence\toutput_created\toutput_modified\tsource_modified\tstale_days\tinputs\tevidence_code"
                "\tnot_chosen\n")
        for f in outputs:
            st, ss = stat[f], src_of.get(f)
            c = created(st)
            t = ts(c)  # created == modified on Linux (no birth time): format once
            dates = t + "\t" + (t if c == st.st_mtime else ts(st.st_mtime))
            # every script that lost, uncut: at most one entry per script (34 rows, 8 kB in a 30 MB manifest of 108k files)
            others = " ;; ".join(f"{rel(s)} {l}: {why_not}" for s, l, why_not in lost.get(f, ()))
            if not ss:  # most files: short path
                m.write(f"{rel(f)}\t\tunmatched\t{dates}\t\t\t\t\t{others}\n"); continue
            # best confidence first; ties -> script whose last change is closest before the output
            order = sorted(ss, key=lambda s: (info[s]["mtime"] > st.st_mtime, -info[s]["mtime"], s))  # same date: by path
            gap = max((info[s]["mtime"] for s in order), default=0) - st.st_mtime
            if order and gap > STALE_SECONDS: stale[f] = round(gap / 86400, 1)
            m.write("\t".join([
                rel(f), ";".join(rel(s) for s in order), ";".join(ss[s] for s in order) or "unmatched",
                dates, ";".join(info[s]["modified"] for s in order),
                str(stale.get(f, "")),
                ";".join(sorted({rel(i) for s in order for i in reads_of[s]}))[:2000],
                " ;; ".join(" ".join(why.get((f, s), "").split()) for s in order), others]) + "\n")
            if order: rows.append({"o": rel(f), "s": [rel(x) for x in order], "c": [ss[x] for x in order],
                                   "m": ts(st.st_mtime), "sm": [info[x]["modified"] for x in order],
                                   "x": [why.get((f, x), "") for x in order]})

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

    unm = Counter(rel(f).rpartition("/")[0] or "." for f in outputs if f not in src_of)
    data = {"graph": graph, "rows": rows, "unm": unm, "scripts": [{"p": rel(x), "m": info[x]["modified"]} for x in scripts],
            "unmatched": len(outputs) - len(rows), "root": root,
            "evidence": [{k: e[k] for k in ("label", "strength", "meaning")} for e in rules.EVIDENCE],
            "stale_seconds": STALE_SECONDS, "version": __version__, "generated": datetime.datetime.now().astimezone().isoformat(timespec="minutes")}
    html = open(tpl_path).read().replace("__DATA__", json.dumps(data).replace("</", "<\\/"))
    open(os.path.join(a.out, "index.html"), "w").write(html)
    print(f"{len(rows)}/{len(outputs)} outputs matched; {len(nodes)} nodes, {len(gedges)} edges in {took(t0)}",
          file=sys.stderr)
    print(f"report: {rel(os.path.join(a.out, 'index.html'))}", file=sys.stderr)
    summary = (f"\n{len(rows)} of {len(outputs)} outputs traced to a script, {len(stale)} possibly stale"
               f" (fms <folder> lists them)")
    top = Counter(s for r in rows for s in r["s"]).most_common(5)
    if top: summary += "\nmost outputs:\n" + "\n".join(f"  {n:6}  {s}" for s, n in top)
    if unm: summary += ("\nmost unmatched (add to outputs/exclude in fms.json?):\n"
                        + "\n".join(f"  {n:6}  {d}/" for d, n in unm.most_common(5)))
    print(summary)
    blob = json.dumps(parsed)  # the bulk of cache.json, so the write phase includes it
    tm["write"] = time.monotonic() - t0 - sum(tm.values())
    with open(cache_path, "w") as fh:
        head = json.dumps({"tool": tool, "fingerprint": fp, "generated": data["generated"], "summary": summary,
                           "timings": tm})
        fh.write(head[:-1] + ', "scripts": ' + blob + "}")


def cache_dir(root):
    """per-user report folder for a project that is not writable, e.g. a shared one: ~/.cache/fms/<name>-<hash>"""
    base = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
    root = os.path.realpath(root)  # the same project under any path that leads to it
    return os.path.join(base, "fms", os.path.basename(root) + "-" + hashlib.sha1(root.encode()).hexdigest()[:8])


def report_dir(root, out=None, write=False):
    """--out if given. Else an existing report (.fms/, fms-report/ from before 0.3, the user cache): for a scan
    the first one it can write to, for a query the newest one. Else .fms/, or the user cache if the project
    is not writable."""
    if out: return os.path.join(root, out)
    found = [d for d in existing_reports(root) if not write or os.access(d, os.W_OK)]
    if not write: found.sort(key=lambda d: -os.path.getmtime(os.path.join(d, "manifest.tsv"))
                             if os.path.exists(os.path.join(d, "manifest.tsv")) else 0)
    if found: return found[0]
    fms = os.path.join(root, ".fms")  # an existing .fms/ that is not writable: the user cache
    return fms if not os.path.exists(fms) and os.access(root, os.W_OK) else cache_dir(root)


def existing_reports(root):
    return [d for d in (os.path.join(root, ".fms"), os.path.join(root, "fms-report"), cache_dir(root)) if os.path.isdir(d)]


def find_root(start, out):
    """first folder upwards with fms.json or a previous report, else None"""
    d = os.path.abspath(start)
    while True:
        if os.path.exists(os.path.join(d, "fms.json")) or (os.path.isdir(os.path.join(d, out)) if out else existing_reports(d)):
            return d
        if os.path.dirname(d) == d: return None
        d = os.path.dirname(d)


def query(a):
    """fms <folder>: possibly stale outputs under it (exit 1 if any); fms <file>: where it came from;
    fms <script>: what it writes and reads. Reads the last scan; scans first if there is none."""
    target = os.path.abspath(a.path)
    # the project we are in; else the one the path is in (never scan a random folder)
    root = find_root(".", a.out) or find_root(target if os.path.isdir(target) else os.path.dirname(target), a.out)
    if not root: return "no fms project here: run fms in the project folder first (no fms.json or report found)"
    out = a.out = report_dir(root, a.out)
    if not os.path.exists(os.path.join(out, "manifest.tsv")):
        print(f"no scan yet in {root}, scanning first", file=sys.stderr)
        cwd = os.getcwd(); os.chdir(root); scan(a); os.chdir(cwd)
    csv.field_size_limit(2**31 - 1)  # not_chosen is uncut; the default limit is 128 kB (2**31-1: Windows C long)
    with open(os.path.join(out, "manifest.tsv")) as fh:
        M = {r["output"]: r for r in csv.DictReader(fh, delimiter="\t")}
    made = os.path.getmtime(os.path.join(out, "manifest.tsv"))
    scripts = {x for r in M.values() for x in r["sources"].split(";") if x}
    if any(os.path.exists(os.path.join(root, x)) and os.path.getmtime(os.path.join(root, x)) > made for x in scripts):
        print(f"(scan from {ts(made)}; scripts changed since, run fms in {root} to refresh)\n")
    rel = os.path.relpath(target, root)
    real = os.path.realpath(target)  # outputs reached through a symlink are stored under their real path
    if rel not in M and real in M: rel = real
    srcs = lambda r: [x for x in r["sources"].split(";") if x]
    stale = lambda r: f"  ⚠ possibly stale: script edited {r['stale_days']} days later" if r["stale_days"] else ""

    if rel in M:  # a file
        r = M[rel]
        print(f"{rel}\n  modified {r['output_modified']}{stale(r)}")
        others = [x for x in r.get("not_chosen", "").split(" ;; ") if x]
        not_chosen = lambda: print("  not chosen:" + "".join(f"\n    {x}" for x in others)) if others else None
        if not srcs(r):
            print("  no script found that writes it (unmatched)"); not_chosen(); return 0
        for s, ev, sm, code in zip(srcs(r), r["evidence"].split(";"), r["source_modified"].split(";"),
                                   r.get("evidence_code", "").split(" ;; ") + [""] * 9):
            print(f"  <- {s}  ({ev}, script last change {sm})" + (f"\n       {code}" if code else ""))
        not_chosen()
        print("\nupstream:")
        def trace(f, ind, seen, depth):
            r = M.get(f)
            if f in seen: print(f"{ind}{f}  (see above)"); return
            seen.add(f)
            if not r or not srcs(r):
                print(f"{ind}{f}" + ("  (raw input)" if r else "  (outside the scan)")); return
            print(f"{ind}{f}  <- {', '.join(srcs(r))}{' ⚠' if r['stale_days'] else ''}")
            # upstream = inputs that are not code and not written by the same script(s)
            ins = [i for i in (r["inputs"].split(";") if r["inputs"] else []) if not i.lower().endswith(SCRIPT_EXT)
                   and not set(srcs(M[i]) if i in M else []) & set(srcs(r))]
            if depth >= 5 and ins: print(f"{ind}  ..."); return
            for i in ins[:8]: trace(i, ind + "  ", seen, depth + 1)
            if len(ins) > 8: print(f"{ind}  + {len(ins) - 8} more inputs")
        trace(rel, "  ", set(), 0)
        return 0

    if os.path.isdir(target):  # a folder: what is possibly stale under it
        pre = () if rel == "." else (rel.rstrip("/") + "/", real + "/")
        rows = [r for o, r in M.items() if not pre or o.startswith(pre)]
        bad = [r for r in rows if r["stale_days"]]
        if not bad:
            print(f"nothing possibly stale under {rel}/ ({sum(bool(srcs(r)) for r in rows)} of {len(rows)} outputs traced)")
            return 0
        print(f"{len(bad)} possibly stale outputs under {rel}/ (script edited after the output was written):")
        by = defaultdict(list)
        for r in bad: by[srcs(r)[0]].append(r)
        for s, rs in sorted(by.items()):
            print(f"\n{s}  (last change {rs[0]['source_modified'].split(';')[0]})")
            for r in sorted(rs, key=lambda r: -float(r["stale_days"])):
                print(f"  {float(r['stale_days']):7.1f} d  {r['output']}")
        return 1

    if rel in scripts or os.path.exists(target):  # a script (or a file the scan did not see)
        writes = sorted(o for o, r in M.items() if rel in srcs(r))
        try:
            g = json.load(open(os.path.join(out, "graph.json")))
            title = {n["id"]: n["title"].split("\n")[0] for n in g["nodes"]}
            me = [i for i, t in title.items() if t == rel]
            reads = sorted({title[e["from"]] for e in g["edges"] if me and e["to"] == me[0] and e["kind"] != "writes"})
        except (OSError, ValueError): reads = []
        if not writes and not reads:
            print(f"{rel}: not linked to anything by the last scan (excluded, unmatched or new?)"); return 0
        print(f"{rel}\n  writes {len(writes)}:" + "".join(f"\n    {o}{' ⚠' if M[o]['stale_days'] else ''}" for o in writes))
        print(f"  reads {len(reads)}:" + "".join(f"\n    {x}" for x in reads))
        return 0
    print(f"{a.path}: no such file or folder"); return 2


def serve(a):
    """fms --serve [PORT]: refresh the report, then serve it on localhost only; forward the port to view it from
    another machine (VS Code Remote does that by itself when it sees the URL)"""
    import functools, http.server
    os.chdir(find_root(".", a.out) or ".")
    scan(a)
    h = functools.partial(http.server.SimpleHTTPRequestHandler, directory=a.out)
    try: srv = http.server.ThreadingHTTPServer(("127.0.0.1", a.serve), h)
    except OSError as e: return f"cannot serve on port {a.serve}: {e.strerror} (try fms --serve {a.serve + 1})"
    print(f"serving {a.out} at http://127.0.0.1:{srv.server_port}/  (Ctrl+C to stop)", file=sys.stderr, flush=True)
    with srv:
        try: srv.serve_forever()
        except KeyboardInterrupt: pass


def main():
    ap = argparse.ArgumentParser(prog="fms", description="findMySource: trace outputs back to the scripts that made them.",
        epilog="fms: scan and summarise.  fms <folder>: possibly stale outputs in it.  fms <file>: where it came from.")
    ap.add_argument("path", nargs="?", help="file or folder to ask about (needs a previous scan)")
    ap.add_argument("--config", default="fms.json")
    ap.add_argument("--out", help="report folder (default: .fms/ in the project, or ~/.cache/fms/ if not writable)")
    ap.add_argument("--serve", nargs="?", const=8000, type=int, metavar="PORT",
                    help="rescan, then serve the report at http://127.0.0.1:PORT/ (default 8000)")
    ap.add_argument("--version", action="version", version=__version__)
    a = ap.parse_args()
    if a.serve is not None: sys.exit(serve(a))
    if a.path: sys.exit(query(a))
    scan(a)


if __name__ == "__main__":
    main()
