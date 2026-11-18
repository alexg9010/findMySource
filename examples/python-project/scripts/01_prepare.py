"""Clean the raw measurements."""
import json

import pandas as pd

from config import DATA, RESULTS

cfg = json.load(open(DATA / "raw" / "config.json"))
df = pd.read_csv(DATA / "raw" / "measurements.csv")
ref = pd.read_csv("~/downloads/reference_ranges.csv")  # external input, outside the project

df = df.dropna().query("age >= @cfg['min_age']")
df.to_parquet(RESULTS / "clean.parquet")
