"""Figures for the paper."""
import os

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from config import FIGURES, RESULTS

scores = np.load(RESULTS / "scores.npy")
df = pd.read_parquet(RESULTS / "clean.parquet")

fig, ax = plt.subplots()
ax.hist(scores)
fig.savefig(FIGURES / "roc.png", dpi=150)

for feature in ["age", "bmi", "ldl"]:
    plt.figure()
    plt.hist(df[feature])
    plt.savefig(f"{FIGURES}/hist_{feature}.svg")

fig.savefig(os.path.join(FIGURES, "umap.pdf"))
