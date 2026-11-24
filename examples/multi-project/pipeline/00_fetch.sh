#!/usr/bin/env bash
# Download the reference genome and its annotation.
set -euo pipefail
REF=data/reference
mkdir -p "$REF"
curl -L -o "$REF/genome.fa.gz" https://example.org/genome.fa.gz
wget -O "$REF/genes.gtf" https://example.org/genes.gtf
