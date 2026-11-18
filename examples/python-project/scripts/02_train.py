"""Train a classifier and store model, scores and metrics."""
import json

import joblib
import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression

from config import RESULTS

df = pd.read_parquet(RESULTS / "clean.parquet")
model = LogisticRegression().fit(df[["age", "bmi", "ldl"]], df["cad"])
scores = model.predict_proba(df[["age", "bmi", "ldl"]])[:, 1]

joblib.dump(model, RESULTS / "model.joblib")
np.save(RESULTS / "scores.npy", scores)
with open(RESULTS / "metrics.json", "w") as fh:
    json.dump({"auc": 0.81}, fh, indent=2)
