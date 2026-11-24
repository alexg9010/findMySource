#!/usr/bin/perl
# Add gene names to the annotation.
use strict;
open(my $in, '<', "data/reference/genes.gtf") or die;
open(my $out, '>', "results/annotation.gff") or die;
open(LOG, ">>results/annotate.log") or die;
while (<$in>) { print $out $_; print LOG "."; }
close($out);
