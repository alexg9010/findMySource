# Normalise the counts of all samples.
library(DESeq2)
files <- list.files("results/counts/filtered", pattern = "\\.parquet$", full.names = TRUE)
counts <- do.call(cbind, lapply(files, arrow::read_parquet))
dds <- DESeqDataSetFromMatrix(counts, data.frame(s = basename(files)), ~1)
dds <- estimateSizeFactors(dds)
saveRDS(dds, "results/stats/dds.rds")
write.csv(sizeFactors(dds), "results/stats/size_factors.csv")
