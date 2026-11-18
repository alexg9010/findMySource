# Load raw counts and sample sheet, merge, keep a clean object for modelling.
source("scripts/utils.R")
library(readxl)

counts  <- read.csv(file.path(data_dir, "raw", "counts.csv"))
samples <- read_excel(file.path(data_dir, "raw", "samples.xlsx"))
genes   <- read.csv("/shared/annotation/genes.csv")   # external reference, outside the project

clean <- merge(counts, samples, by = "sample")
saveRDS(clean, file.path(results_dir, "clean.rds"))
