"""Check BEA, calculate ISMI, validate candidates, then export atomically."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import tempfile

import numpy as np
import pandas as pd

from bea import BeaClient, fingerprint, inputs
from engine import calculate, combine, rebuild_required
from release_calendar import release_metadata

ROOT = Path(__file__).resolve().parents[1]
MODEL_VERSION = '2026-11-nonhousing-corrected-yoy-v1'


def csv_fingerprint(raw: bytes) -> str:
    """Git can check out the same CSV with LF on Linux and CRLF on Windows."""
    return hashlib.sha256(raw.replace(b'\r\n', b'\n')).hexdigest()


def write_excel(frame: pd.DataFrame, path: Path) -> None:
    with pd.ExcelWriter(path, engine='openpyxl') as writer:
        for k in (2, 3, 4):
            for ar in (1, 3, 12):
                suffix = f'_ar{ar}_k{k}'
                cols = [c for c in frame if c.endswith(suffix)]
                first = [f'ismi_{group}{kind}{suffix}' for group in ('headline', 'core') for kind in ('', '_positive', '_negative')]
                ordered = first + [c for c in cols if c not in first]
                frame[['date'] + ordered].to_excel(writer, sheet_name=f'k{k}_AR{ar}', index=False)
                sheet = writer.sheets[f'k{k}_AR{ar}']
                sheet.freeze_panes = 'B2'
                sheet.column_dimensions['A'].width = 12
                for cells in sheet.iter_rows(min_row=2, min_col=2):
                    for cell in cells:
                        cell.number_format = '0.000'
    # Check the exact date coverage and sheet dimensions before installing the file.
    with pd.ExcelFile(path) as workbook:
        if len(workbook.sheet_names) != 9:
            raise ValueError('Excel export does not contain all nine AR/k combinations')
        for sheet in workbook.sheet_names:
            check = workbook.parse(sheet)
            if list(check.date) != list(frame.date) or len(check.columns) != 19:
                raise ValueError('Excel export coverage or schema does not match the CSV')


def run(root: Path, mode: str, full_history: bool, cached_inputs: Path | None, output: Path,
        expected_month: str | None = None, release_utc: str | None = None) -> dict:
    mapping = pd.read_csv(root / 'automation/categories.csv')
    previous_bytes = (root / 'ism_data.csv').read_bytes()
    previous = pd.read_csv(BytesIO(previous_bytes))
    previous_hash = csv_fingerprint(previous_bytes)
    state_path = root / 'automation/state.json'
    state = json.loads(state_path.read_text()) if state_path.exists() else {}
    baseline_changed = state.get('published_csv_sha256') != previous_hash
    previous_latest = previous.date.max()
    now = datetime.now(timezone.utc)
    probe_hash = None
    if cached_inputs:
        if mode != 'validate':
            raise ValueError('Cached inputs are permitted only for read-only validation')
        panel = pd.read_csv(cached_inputs, index_col='time_month')
    else:
        client = BeaClient(os.environ.get('BEA_API_KEY', ''))
        years = f'{now.year - 1},{now.year}'
        try:
            prices = client.table('U20404', years)
            spending = client.table('U20405', years)
        except RuntimeError:
            # Early January can precede publication of any observations for the new year.
            if now.month != 1:
                raise
            years = str(now.year - 1)
            prices = client.table('U20404', years)
            spending = client.table('U20405', years)
        probe = inputs(prices, spending, mapping)
        probe_hash = fingerprint(probe)
        latest_probe = str(pd.Period('1960-01', freq='M') + int(probe.index.max()))
        if expected_month and latest_probe < expected_month:
            return {'status': 'waiting_for_release', 'latest_month': previous_latest,
                    'available_month': latest_probe, 'expected_month': expected_month,
                    'changed': False, 'data_changed': False}
        if latest_probe < previous_latest:
            raise ValueError('BEA is serving an older release; refusing to roll back the dashboard')
        if (mode == 'update' and not full_history and not baseline_changed and state.get('model_version') == MODEL_VERSION
                and probe_hash == state.get('probe_sha256') and latest_probe == previous_latest):
            return {'status': 'unchanged', 'latest_month': previous_latest, 'changed': False, 'data_changed': False}
        print('Downloading full BEA history for current-vintage model inputs…', flush=True)
        prices = client.table('U20404', 'ALL')
        spending = client.table('U20405', 'ALL')
        panel = inputs(prices, spending, mapping)
        # A release can arrive between the two table downloads. Fail closed and
        # try again on the next run if either table changed during collection.
        confirmation = inputs(client.table('U20404', years), client.table('U20405', years), mapping)
        if fingerprint(panel.reindex(confirmation.index)) != fingerprint(confirmation):
            raise ValueError('BEA release changed during download; waiting for a consistent release')
        # Recompute from the same full-history download to avoid mixed-vintage hashes.
        first_probe = (now.year - 1 - 1960) * 12
        probe_hash = fingerprint(panel.loc[panel.index >= first_probe])
    latest_number = int(panel.index.max())
    latest = str(pd.Period('1960-01', freq='M') + latest_number)
    full = rebuild_required(previous_latest, latest, mode == 'benchmark' or full_history
                            or state.get('model_version') != MODEL_VERSION or baseline_changed)
    first_number = (pd.Period(previous.date.min(), freq='M') - pd.Period('1960-01', freq='M')).n if full else latest_number - 15
    dates = list(range(first_number, latest_number + 1))
    print(f'Calculating {len(dates)} months; {"full September benchmark" if full else "last 16 months"}; latest {latest}', flush=True)
    candidate = calculate(panel, list(mapping.haver_code), dates)
    merged = combine(previous, candidate, full)
    cols = [c for c in previous if c.startswith('ismi_')]
    overlap = candidate.set_index('date')[cols].round(3).join(previous.set_index('date')[cols], lsuffix='_new', rsuffix='_old', how='inner')
    mismatches = 0
    for c in cols:
        a, b = overlap[c + '_new'], overlap[c + '_old']
        if a.isna().any() or b.isna().any():
            raise ValueError('Missing observations in validation comparison')
        mismatches += int(((a - b).abs() > 0.000001).sum())
    data_changed = list(previous.date) != list(merged.date)
    if not data_changed:
        data_changed = not np.allclose(previous.drop(columns='date'), merged.drop(columns='date'), atol=0.000001, rtol=0)
    report = {
        'status': 'validated' if mode == 'validate' else 'updated', 'source': 'BEA NIUnderlyingDetail U20404 and U20405',
        'latest_month': latest, 'first_recalculated_month': candidate.date.min(), 'full_history': full,
        'recalculated_months': len(candidate), 'index_series': len(cols), 'overlap_comparisons': len(overlap) * len(cols),
        'rounded_differences_from_previous_release': mismatches,
        'inputs_sha256': fingerprint(panel), 'changed': False, 'data_changed': bool(data_changed),
        'model_version': MODEL_VERSION,
    }
    # Revisions and the documented model corrections can legitimately change
    # the prior release. Numerical equivalence uses the fixed Stata test fixture.
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.ismi-export-', dir=root) as temp:
        temp = Path(temp)
        merged.to_csv(temp / 'ism_data.csv', index=False, float_format='%.9g', lineterminator='\n')
        write_excel(merged, temp / 'ismi_data.xlsx')
        names = ['ism_data.csv', 'ismi_data.xlsx']
        if mode != 'validate' and data_changed:
            metadata = release_metadata(latest, now, release_utc if latest == expected_month else None)
            (temp / 'data-update.json').write_text(json.dumps(metadata, indent=2) + '\n', encoding='utf-8')
            names.append('data-update.json')
        # Always retain candidates as reviewable workflow artifacts.
        for name in names:
            (output / name).write_bytes((temp / name).read_bytes())
        if mode != 'validate':
            updated_state = {
                'latest_month': latest, 'probe_sha256': probe_hash, 'inputs_sha256': fingerprint(panel),
                'model_version': MODEL_VERSION,
                'published_csv_sha256': csv_fingerprint((temp / 'ism_data.csv').read_bytes()) if data_changed else previous_hash,
                'last_full_rebuild_data_month': latest if full else state.get('last_full_rebuild_data_month'),
                'last_successful_change_utc': now.isoformat(),
            }
            # No on-disk changes happen until download, calculation and both exports pass.
            if (root / 'ism_data.csv').read_bytes() != previous_bytes:
                raise ValueError('Dashboard CSV changed during calculation; refusing to overwrite concurrent edits')
            if data_changed:
                for name in names:
                    (temp / name).replace(root / name)
            state_path.write_text(json.dumps(updated_state, indent=2) + '\n', encoding='utf-8')
            report['changed'] = True
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--mode', choices=('update', 'validate', 'benchmark'), default='validate')
    parser.add_argument('--full-history', action='store_true')
    parser.add_argument('--cached-inputs', type=Path)
    parser.add_argument('--output', type=Path, default=ROOT / 'update-artifacts')
    parser.add_argument('--expected-month', help='Wait without publishing until this scheduled reference month is available')
    parser.add_argument('--release-utc', help='Official calendar timestamp for the expected reference month')
    args = parser.parse_args()
    report = run(args.root, args.mode, args.full_history, args.cached_inputs, args.output,
                 args.expected_month, args.release_utc)
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / 'report.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, indent=2))
    if os.environ.get('GITHUB_OUTPUT'):
        with open(os.environ['GITHUB_OUTPUT'], 'a', encoding='utf-8') as stream:
            for key in ('changed', 'data_changed', 'latest_month'):
                stream.write(f'{key}={str(report[key]).lower()}\n')


if __name__ == '__main__':
    main()
