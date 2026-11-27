"""MATLAB."""
EXTENSIONS = (".m",)
CODE_IN_STRINGS = True  # ' is also the transpose operator (y = x'), so its quotes cannot be paired
CALLS = {"sprintf": "format"}
