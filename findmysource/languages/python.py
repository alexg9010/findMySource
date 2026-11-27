"""Python and Jupyter."""
EXTENSIONS = (".py", ".ipynb")
NOTEBOOKS = (".ipynb",)  # rendered by Jupyter
CALLS = {
    "join": "join", "joinpath": "join",  # os.path.join, Path.joinpath
    **{n: "wrap" for n in "str expanduser abspath realpath fspath resolve Path".split()},
    "glob": "glob", "iglob": "glob",
    "map": "apply 0",  # map(f, xs)
}
