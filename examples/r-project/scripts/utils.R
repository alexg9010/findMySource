# Shared settings and helpers, source()d by every script.
data_dir    <- "data"
results_dir <- "results"
fig_dir     <- "figures"

# helper that hides the actual write call: the file name is chosen by the caller
save_table <- function(df, name) {
  readr::write_tsv(df, file.path(results_dir, paste0(name, ".tsv")))
}
