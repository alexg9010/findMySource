"""findMySource (fms): trace every output of a project back to the script(s) that made it.

Usage (run from the project root):
    fms [--config fms.json] [--out fms-report]

Writes <out>/manifest.tsv, <out>/graph.json and <out>/index.html (tabs: flow, timeline, matrix, table, network).
Static analysis of R / Rmd / qmd / ipynb (R kernel): write/read calls, `source()`, simple
variable propagation (<-, file.path, paste0, paste, sprintf, here, params:). Unknown pieces become
wildcards. Relative paths are tried against the script's folder and the project root; symlinks
are resolved automatically, so aliases are only needed for mount points with two spellings.
Ceilings: loops/computed names -> wildcard matches; multi-line assignments ignored; paths from
command-line args are unknown; only the Network tab needs internet (vis-network from a CDN).
Config keys (JSON, all optional): scripts, outputs, exclude, aliases, extensions.
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
TOK_RE = re.compile(r"""\s*("(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*'|[A-Za-z_.][\w.$@]*(?:::[A-Za-z_.][\w.$@]*)?|[(),=]|[^\s\w(),="']+|\d+\.?\d*)""")


# ---------- tiny R-expression evaluator -> pattern string ('*' = unknown) ----------
def tokenize(s):
    return [m.group(1) for m in TOK_RE.finditer(s)]

def _prim(tok, i, v):
    if i >= len(tok): return "*", i
    t = tok[i]
    if t[0] in "\"'": return t[1:-1], i + 1
    if re.match(r"[A-Za-z_.]", t):
        if i + 1 < len(tok) and tok[i + 1] == "(":
            args, i = _args(tok, i + 2, v)
            return _apply(t, args), i
        return v.get(t, "*"), i + 1
    return "*", i + 1

def _expr(tok, i, v):
    val, i = _prim(tok, i, v)
    depth = 0
    while i < len(tok) and not (depth == 0 and tok[i] in ",)"):  # operators/indexing: give up -> unknown
        depth += tok[i] == "("; depth -= tok[i] == ")"
        val, i = "*", i + 1
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

def _apply(fn, args):
    pos = [a for n, a in args if n is None]
    if fn.endswith("file.path"): return "/".join(pos)
    if fn.endswith("paste0"): return "".join(pos)
    if fn.endswith("paste"):
        sep = dict((n, a) for n, a in args if n).get("sep", " ")
        return sep.join(pos)
    if fn.endswith("sprintf") and pos:
        it = iter(pos[1:])
        return re.sub(r"%[-\d.]*[sdfg]", lambda m: next(it, "*"), pos[0])
    if fn.endswith("here"): return "/".join(pos)  # here::here() is relative to the project root
    if fn.endswith(("normalizePath", "path.expand", "I")) and pos: return pos[0]
    return "*"

def evaluate(expr, v):
    return _expr(tokenize(expr), 0, v)[0]


# ---------- script parsing ----------
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

def split_call(code, start):
    """code[start] is just after '(' -> (list of top-level arg strings, end index)."""
    depth, q, args, cur, i = 1, None, [], [], start
    while i < len(code) and depth:
        c = code[i]
        if q:
            if c == "\\": cur.append(c); i += 1; c = code[i] if i < len(code) else ""
            elif c == q: q = None
        elif c in "\"'": q = c
        elif c in "([{": depth += 1
        elif c in ")]}": depth -= 1
        if depth == 0: break
        if c == "," and depth == 1 and not q: args.append("".join(cur)); cur = []
        else: cur.append(c)
        i += 1
    args.append("".join(cur))
    return [a.strip() for a in args], i

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
        for f in self.files:
            self.by_base[os.path.basename(f)].append(f)
            self.by_dir[os.path.dirname(f)].append(f)
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
        else: cands = self.files
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

def walk(roots, exclude):
    ex = re.compile("|".join(fnmatch.translate(e) for e in exclude) or "(?!)")
    for r in roots:
        real = os.path.realpath(r)
        if os.path.isfile(real): yield real; continue
        for d, ds, fs in os.walk(real):
            ds[:] = [x for x in ds if not ex.match(x)]
            for f in fs:
                if not ex.match(f): yield os.path.join(d, f)


# ---------- main ----------
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
    exts = tuple(e.lower() for e in cfg.get("extensions", [".r", ".rmd", ".qmd", ".ipynb"]))
    exclude = cfg.get("exclude", [".git", ".Rproj.user", ".ipynb_checkpoints", "rstudio-server-db", "rstudio_state",
                                  "*.RData", ".Rhistory", "#*#", "%", ".DS_Store", "*_cache"])
    aliases = {k: (v if v.startswith("/") else os.path.normpath(os.path.join(root, v)))
               for k, v in cfg.get("aliases", {}).items()}
    resolve = make_norm(aliases, root)
    rel = lambda p: os.path.relpath(p, root) if p.startswith(root + "/") else p

    # 1. scripts
    commit, dirty = git_dates(root)
    scripts = sorted({s for s in walk(cfg.get("scripts", ["."]), exclude) if s.lower().endswith(exts)})
    info = {}
    for s in scripts:
        code, params = read_code(s)
        try: st = os.stat(s)
        except OSError: continue
        # git date unless the file has uncommitted changes (then its mtime is the real last change)
        mod = commit.get(s) if s not in dirty else None
        info[s] = {**parse_script(code, params), "modified": mod or ts(st.st_mtime), "mtime": st.st_mtime}
    scripts = [s for s in scripts if s in info]
    print(f"parsed {len(scripts)} scripts", file=sys.stderr)

    # 2. files (one stat each)
    stat = {}
    for f in walk(cfg.get("outputs", ["."]), exclude):
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

    # 3. link
    src_of = defaultdict(dict)  # output -> {script: confidence}
    edges, reads_of = [], defaultdict(set)
    for s, d in info.items():
        for p in d["writes"]:
            hit, conf = match(p, s)
            for f in hit:
                if f not in info: src_of[f].setdefault(s, conf)
        for p in d["reads"]:
            hit, _ = match(p, s)
            for f in hit: reads_of[s].add(f); edges.append((f, s, "reads"))
            if not hit and "*" not in p:  # external input that does not exist in the scanned dirs
                edges.append((resolve(p, s)[0], s, "reads"))
        for p in d["sources"]:
            for f in match(p, s)[0]:
                if f in info and f != s: edges.append((f, s, "source"))
    for f, ss in rep.items():
        for s in ss: src_of[f][s] = "stem-rule"
    rank = {"exact": 0, "stem-rule": 1, "glob": 2}

    # 4. manifest
    os.makedirs(a.out, exist_ok=True)
    rows, outputs = [], sorted(set(stat) - set(info))
    with open(os.path.join(a.out, "manifest.tsv"), "w") as m:
        m.write("output\tsources\tconfidence\toutput_created\toutput_modified\tsource_modified\tinputs\n")
        for f in outputs:
            st, ss = stat[f], src_of.get(f, {})
            # best confidence first; ties -> script whose last change is closest before the output
            order = sorted(ss, key=lambda s: (rank[ss[s]], info[s]["mtime"] > st.st_mtime, -info[s]["mtime"]))
            m.write("\t".join([
                rel(f), ";".join(rel(s) for s in order), ";".join(ss[s] for s in order) or "unmatched",
                ts(created(st)), ts(st.st_mtime), ";".join(info[s]["modified"] for s in order),
                ";".join(sorted({rel(i) for s in order for i in reads_of[s]}))[:2000]]) + "\n")
            if order: rows.append({"o": rel(f), "s": [rel(x) for x in order], "c": [ss[x] for x in order],
                                   "m": ts(st.st_mtime), "sm": [info[x]["modified"] for x in order]})

    # 5. graph: linked files only; >15 files from one script into one dir collapse into a group node
    nodes, nid, member = [], {}, {}
    def node(key, label, group, title):
        if key not in nid:
            nid[key] = len(nodes)
            nodes.append({"id": nid[key], "label": label, "group": group, "title": title})
        return nid[key]
    snode = lambda s: node(s, os.path.basename(s), "script", f"{rel(s)}\nlast change {info[s]['modified']}")
    grp = defaultdict(list)
    for f, ss in src_of.items():
        for s in ss: grp[(s, os.path.dirname(f))].append(f)
    gedges = set()
    for (s, d), fl in sorted(grp.items()):
        sn = snode(s)
        if len(fl) > 15:
            g = node((s, d), f"{os.path.basename(d)}/ ({len(fl)} files)", "group",
                     rel(d) + "\n" + "\n".join(sorted(os.path.basename(x) for x in fl)[:60]))
            gedges.add((sn, g, "writes"))
            for f in fl: member.setdefault(f, g)
        else:
            for f in fl:
                gedges.add((sn, node(f, os.path.basename(f), "file", f"{rel(f)}\nmodified {ts(stat[f].st_mtime)}"), "writes"))
    for f, s, k in edges:
        fid = nid.get(f, member.get(f))
        if fid is None and s not in nid and k == "reads": continue  # input of an unconnected script
        if fid is None:
            fid = snode(f) if f in info else node(f, os.path.basename(f), "input", rel(f) + "\n(not a tracked output)")
        if fid != snode(s): gedges.add((fid, snode(s), k))
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
