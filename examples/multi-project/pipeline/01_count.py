"""Count reads per gene for every sample."""
import json
import pandas as pd
from paths import COUNTS, SAMPLES

genes = pd.read_csv("data/reference/genes.gtf", sep="\t", comment="#", header=None)

summary = {}
for s in SAMPLES:
    counts = genes.groupby(8).size()
    counts.to_csv(f"{COUNTS}/{s}.tsv", sep="\t")
    summary[s] = int(counts.sum())

with open(f"{COUNTS}/summary.json", "w") as fh:
    json.dump(summary, fh)
