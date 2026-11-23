"""Drop genes with few reads."""
import os
import pandas as pd
from paths import COUNTS, FILTERED, SAMPLES

for s in SAMPLES:
    df = pd.read_csv(os.path.join(COUNTS, s + ".tsv"), sep="\t")
    df[df.iloc[:, 1] >= 10].to_parquet(os.path.join(FILTERED, s + ".parquet"))
