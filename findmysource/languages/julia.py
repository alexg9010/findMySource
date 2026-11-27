"""Julia."""
EXTENSIONS = (".jl",)
CODE_IN_STRINGS = True  # Julia runs $(...) inside strings: "$(write(out, data))"
CALLS = {"joinpath": "join", "abspath": "wrap", "realpath": "wrap"}
