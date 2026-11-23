# Survival model on the clinical table.
using CSV, DataFrames
clinical = CSV.read("data/raw/clinical.csv", DataFrame)
fit = combine(groupby(clinical, :arm), :months => mean)
CSV.write("results/stats/survival.csv", fit)
