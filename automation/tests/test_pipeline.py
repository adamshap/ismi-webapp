from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'automation'))
from engine import calculate, combine, groups, rebuild_required
from bea import inputs, to_panel
from update import run, write_excel

class PipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.panel = pd.read_csv(Path(__file__).parent / 'fixtures/bea_2026_10.csv.gz', index_col='time_month')
        cls.categories = list(pd.read_csv(ROOT / 'automation/categories.csv').haver_code)

    def test_stata_reference_all_series(self):
        reference = pd.read_csv(Path(__file__).parent / 'fixtures/stata_corrected.csv')
        dates = sorted(reference.time_month.unique())
        calculated = calculate(self.panel, self.categories, dates).set_index('date')
        count = 0
        for row in reference.itertuples():
            month = str(pd.Period('1960-01', freq='M') + int(row.time_month))
            for kind, expected in [('_positive', row.positive), ('_negative', row.negative), ('', row.net)]:
                column = f'ismi_{row.group}{kind}_ar{row.ar}_k{row.k}'
                self.assertAlmostEqual(calculated.at[month, column], expected, places=6, msg=f'{month} {column}')
                self.assertEqual(round(calculated.at[month, column], 3), round(expected, 3))
                count += 1
        self.assertEqual(count, len(dates) * 162)

    def test_revised_definitions(self):
        sets = groups(self.categories)
        self.assertFalse(set(sets['services']) & set(sets['housing']))
        self.assertEqual(set(sets['services']) | set(sets['housing']) | set(sets['goods']), set(self.categories))
        out = calculate(self.panel, self.categories, [801])
        expected = 100 * (self.panel.at[801, 'pce'] / self.panel.at[789, 'pce'] - 1)
        self.assertAlmostEqual(out.pce_yoy.iloc[0], expected, places=10)

    def test_september_benchmark_and_catchup(self):
        for old, new, expected in [('2026-08','2026-09',True), ('2026-08','2026-10',True),
                                   ('2026-09','2026-10',False), ('2026-10','2026-10',False),
                                   ('2025-10','2026-10',True)]:
            self.assertEqual(rebuild_required(old,new), expected)
        self.assertTrue(rebuild_required('2026-10','2026-10', True))
        with self.assertRaises(ValueError): rebuild_required('2026-10','2026-09')

    def test_baseline_freeze_and_export(self):
        old = pd.read_csv(ROOT / 'ism_data.csv')
        new = old.tail(16).copy()
        new.iloc[-1, new.columns.get_loc('ismi_headline_ar1_k2')] += .001
        merged = combine(old, new, False)
        pd.testing.assert_frame_equal(merged.iloc[:-16], old.iloc[:-16], check_exact=False, atol=1e-12, rtol=0)
        with self.assertRaises(ValueError): combine(old,new,True)
        with self.assertRaises(ValueError): combine(old,new.iloc[:-1],False)
        with tempfile.TemporaryDirectory() as folder:
            write_excel(merged, Path(folder)/'test.xlsx')

    def test_fail_closed_on_incomplete_or_conflicting_data(self):
        rows = [dict(SeriesCode='A',TimePeriod='2026M10',DataValue='1'),
                dict(SeriesCode='A',TimePeriod='2026M10',DataValue='2')]
        with self.assertRaises(ValueError): to_panel(rows)
        broken = self.panel.drop(index=800)
        with self.assertRaises(ValueError): calculate(broken,self.categories,[801])
        broken = self.panel.copy(); broken.loc[800,'pce_spend'] = np.nan
        with self.assertRaises(ValueError): calculate(broken,self.categories,[801])

    def test_download_failure_cannot_change_published_files(self):
        paths = [ROOT/'ism_data.csv',ROOT/'ismi_data.xlsx',ROOT/'automation/state.json']
        before = [p.read_bytes() for p in paths]
        with tempfile.TemporaryDirectory() as folder:
            with patch('update.BeaClient', side_effect=RuntimeError('simulated download failure')):
                with self.assertRaises(RuntimeError): run(ROOT,'update',False,None,Path(folder))
        self.assertEqual(before, [p.read_bytes() for p in paths])

if __name__ == '__main__': unittest.main()
