"""One module per language: the files it covers and the calls it knows. rules.py combines them.

A module sets EXTENSIONS (lower case, matched as the end of the file name) and CALLS, {name: "<role> [<positions>]"}:
  write, read   the call writes / reads the file at these argument positions (none: only a named file= argument)
  join          parts joined with "/"              concat   parts glued together
  format        paste(sep=), sprintf %s, glue {}   wrap     one argument, passed through
  list, glob    stands for the files it lists      apply    the function at these positions decides the direction
Optional: NOTEBOOKS (rendered: stem-rule, chunk-folder), CODE_IN_STRINGS (calls inside strings count), SYNTAX
("shell": statements are also read as command lines), EXAMPLES (checked by tests/test_rules.py).
"""
from . import common, r, python, shell, julia, perl, matlab

LANGUAGES = [common, r, python, shell, julia, perl, matlab]


def calls(role, langs=LANGUAGES):
    """{name: positions} of every call with this role, positions merged across languages: "apply 0" + "apply 1" -> (0, 1)"""
    out = {}
    for lang in langs:
        for name, spec in getattr(lang, "CALLS", {}).items():
            r, *pos = spec.split()
            if r == role: out[name] = tuple(sorted(set(out.get(name, ()) + tuple(int(p) for x in pos for p in x.split(",")))))
    return out


def extensions(langs=LANGUAGES, key="EXTENSIONS", when=lambda lang: True):
    return tuple(e for lang in langs if when(lang) for e in getattr(lang, key, ()))
