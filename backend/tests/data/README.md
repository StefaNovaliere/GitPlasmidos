# Test fixtures

`puc19_annotated.gb`
: pUC19 (2686 bp, circular) with 19 annotated features - lacZalpha, bla/AmpR,
  the pMB1 origin, the MCS and the lac promoter elements. Sourced from
  <https://github.com/ityonemo/opengenepool/blob/master/testfile/pUC19.gb>.
  Kept byte-for-byte as downloaded, including the one malformed location line
  (`promoter      complement(1450..1455)`, misaligned by two columns) that
  Biopython cannot parse - the importer is expected to skip it with a warning.

`puc19_M77789.gb`
: The NCBI record for pUC19 (accession M77789.2, 2686 bp, circular), 8 features,
  several on the complement strand. Sourced from
  <https://github.com/jperkel/gb_read/blob/master/puc19.gb>.

The task asked for accession L09136 straight from NCBI; `eutils.ncbi.nlm.nih.gov`
is blocked by this environment's egress policy, so the equivalent 2686 bp pUC19
records above were mirrored from public GitHub repositories instead.
