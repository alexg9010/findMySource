"""R, R Markdown and Quarto."""
EXTENSIONS = (".r", ".rmd", ".qmd")
NOTEBOOKS = (".rmd", ".qmd")  # rendered by knitr/Quarto
# argument names that hold the file, whatever their position (the R fallback, parse_script)
FILE_ARGS = ("file", "filename", "path", "con")
CALLS = {
    # path builders
    **{n: "join" for n in "file.path path path_join".split()},
    **{n: "concat" for n in "paste0 str_c stri_c stri_paste".split()},
    **{n: "wrap" for n in "as.character normalizePath path.expand I toString".split()},
    **{n: "format" for n in "paste sprintf glue str_glue".split()},
    # directory listings: list.files(dir, pattern = regex), fs::dir_ls(dir, glob =, regexp =), Sys.glob(pattern)
    "list.files": "list", "dir_ls": "list", "Sys.glob": "glob",
    # apply-style calls: the function at this position decides (purrr's map(.x, .f))
    **{n: "apply 1" for n in "lapply sapply vapply map_df map_dfr map_dfc walk imap pmap future_map "
                             "future_lapply mclapply map".split()},
    "mapply": "apply 0", "Map": "apply 0", "parLapply": "apply 2",
    # known functions that do not say "write" or "read" in their name, with the position of the file
    **{n: "write 1" for n in "saveRDS write_rds write_csv write.csv write.csv2 write.table write_tsv write_delim "
                             "fwrite writeLines write.xlsx saveWidget write_xlsx write_parquet".split()},
    **{n: "write 0" for n in "ggsave pdf png jpeg tiff svg bmp sink save.image".split()},
    "save": "write",  # save(): file= only
    **{n: "read 0" for n in "readRDS read_rds read_csv read.csv read.csv2 read.table read_tsv read_delim read.delim "
                            "read_excel fread load readLines read_xlsx read_parquet import source".split()},
}
