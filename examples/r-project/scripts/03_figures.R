# Figures for the paper.
source("scripts/utils.R")
library(ggplot2)

de <- read.delim(file.path(results_dir, "de_table.tsv"))

p <- ggplot(de, aes(estimate, -log10(p.value))) + geom_point()
ggsave(file.path(fig_dir, "volcano.pdf"), p)

for (g in c("GATA4", "MYH6", "TNNT2")) {
  png(file.path(fig_dir, paste0("heatmap_", g, ".png")))
  plot(de$estimate)
  dev.off()
}

fig_dir <- "figures/qc"            # later figures go to a sub-folder
ggsave(file.path(fig_dir, "pca.png"))
