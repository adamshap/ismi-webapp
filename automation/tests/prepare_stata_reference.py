"""Prepare the exact independent Stata validation in a chosen scratch folder.
Run: python automation/tests/prepare_stata_reference.py /path/to/scratch
Then run Stata: cd that folder; do corrected_check.do
Compare corrected_stata.dta against fixtures/stata_corrected.csv.
"""
from pathlib import Path
import gzip
import sys
import pandas as pd
fixture = Path(__file__).resolve().parent / 'fixtures'
out = Path(sys.argv[1]).resolve()
out.mkdir(parents=True, exist_ok=True)
pd.read_csv(fixture/'bea_2026_10.csv.gz').to_stata(out/'bea_for_stata.dta', write_index=False, version=118)
(out/'corrected_check.do').write_bytes(gzip.decompress((fixture/'stata_reference.do.gz').read_bytes()))
print('Prepared public BEA input and independent Stata reference script in', out)
