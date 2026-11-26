"""Production Stata-compatible ISMI, using public BEA prices and expenditures."""
from __future__ import annotations

import numpy as np
import pandas as pd


def groups(categories: list[str]) -> dict[str, list[str]]:
    excluded = set('nlgom nlglm nofum noflm nfofm nfonm nfoldm nfolem nfolbm nfefm slem slgm'.split())
    goods = categories[:categories.index('nrpm') + 1]
    return {
        'headline': categories, 'goods': goods,
        'services': [c for c in categories[categories.index('sbom'):] if c not in {'shrm', 'shtm', 'srdm', 'shom'}],
        'housing': 'shrm shtm srdm shom'.split(),
        'core': [c for c in categories if c not in excluded],
        'coregoods': [c for c in goods if c not in excluded],
    }


def calculate(panel: pd.DataFrame, categories: list[str], dates: list[int]) -> pd.DataFrame:
    """Replicate construct.do, including float32 intermediates and inclusive window.

    Each reference month refits on dates t-120 through t. The last k residuals
    all use that month's fitted model. Shares use lagged spending, category
    order and subgroup denominators from the production Stata code.
    """
    panel = panel.sort_index()
    if panel.index.has_duplicates or len(categories) != 130 or len(set(categories)) != 130:
        raise ValueError('Duplicate months or invalid category mapping')
    if not np.all(np.diff(panel.index) == 1):
        raise ValueError('Input must contain consecutive calendar months')
    prices = panel[categories].to_numpy(dtype=float)
    previous = np.vstack([np.full((1, len(categories)), np.nan), prices[:-1]])
    with np.errstate(invalid='ignore', divide='ignore'):
        inflation = np.log(prices / previous)
    inflation[~np.isfinite(inflation)] = np.nan
    inflation = inflation.astype('float32').astype(float)
    lagged = np.full((len(panel), len(categories), 12), np.nan)
    for lag in range(1, 13):
        lagged[lag:, :, lag - 1] = inflation[:-lag]
    expense = panel[[c + '_spend' for c in categories]].to_numpy(dtype=float)
    aggregate = panel.pce_spend.to_numpy(dtype=float)
    weights = np.vstack([np.full((1, len(categories)), np.nan), expense[:-1] / aggregate[:-1, None]]).astype('float32')
    grouped = {name: [categories.index(c) for c in subset] for name, subset in groups(categories).items()}
    result = []
    for date in dates:
        t = panel.index.get_loc(date)
        start = max(0, t - 120)
        raw = inflation[start:t + 1]
        sd = np.nanstd(raw, axis=0, ddof=1)
        if not np.isfinite(sd).all() or (sd <= 0).any():
            raise ValueError(f'Insufficient category price history at month {date}')
        y = (raw / sd).astype('float32').astype(float)
        x_lags = (lagged[start:t + 1] / sd[None, :, None]).astype('float32').astype(float)
        wt = weights[t]
        if not np.isfinite(wt).all() or abs(float(wt.astype(float).sum()) - 1) > 0.0001:
            raise ValueError(f'Invalid expenditure weights at month {date}')
        if date - 12 not in panel.index:
            raise ValueError('Missing annual inflation history')
        # Revised paper: percent change relative to the price level twelve months ago.
        annual = 100 * (panel.at[date, 'pce'] / panel.at[date - 12, 'pce'] - 1)
        row = {'date': str(pd.Period('1960-01', freq='M') + int(date)), 'pce_yoy': float(annual)}
        for ar in (1, 3, 12):
            residuals = np.empty((len(categories), 4))
            for c in range(len(categories)):
                x = np.column_stack([np.ones(len(raw)), x_lags[:, c, :ar]])
                valid = np.isfinite(y[:, c]) & np.isfinite(x).all(axis=1)
                if valid.sum() <= ar + 1 or not np.isfinite(x[-4:]).all() or not np.isfinite(y[-4:, c]).all():
                    raise ValueError(f'Insufficient AR({ar}) history for {categories[c]} at {date}')
                beta = np.linalg.lstsq(x[valid], y[valid, c], rcond=None)[0]
                residuals[c] = y[-4:, c] - x[-4:] @ beta
            for k in (2, 3, 4):
                pos = (residuals[:, -k:] > 0).all(axis=1)
                neg = (residuals[:, -k:] < 0).all(axis=1)
                for name, idx in grouped.items():
                    # Sequential float32 accumulation matches Stata replace statements.
                    total = np.cumsum(wt[idx], dtype='float32')[-1]
                    if not np.isfinite(total) or total <= 0:
                        raise ValueError(f'Invalid subgroup denominator for {name} at {date}')
                    p = np.cumsum(wt[idx] * pos[idx], dtype='float32')[-1]
                    n = np.cumsum(wt[idx] * neg[idx], dtype='float32')[-1]
                    if name != 'headline':
                        p, n = np.float32(p / total), np.float32(n / total)
                    suffix = f'_ar{ar}_k{k}'
                    row[f'ismi_{name}_positive{suffix}'] = float(p)
                    row[f'ismi_{name}_negative{suffix}'] = float(n)
                    # construct2's exported net retains the difference of the
                    # stored shares; an extra float32 cast can cross a .0005 tie.
                    row[f'ismi_{name}{suffix}'] = float(p) - float(n)
        result.append(row)
    out = pd.DataFrame(result)
    if not np.isfinite(out.select_dtypes('number').to_numpy()).all():
        raise ValueError('ISMI calculation produced missing or infinite values')
    return out


def rebuild_required(previous: str, latest: str, force: bool = False) -> bool:
    """Benchmark follows September DATA, including a missed-release catch-up."""
    old, new = pd.Period(previous, freq='M'), pd.Period(latest, freq='M')
    if new < old:
        raise ValueError('BEA data would move the dashboard backward')
    return force or new.month == 9 or any(p.month == 9 for p in pd.period_range(old + 1, new, freq='M'))


def combine(previous: pd.DataFrame, candidate: pd.DataFrame, full: bool) -> pd.DataFrame:
    """Keep all earlier baseline observations in ordinary monthly updates."""
    if set(previous.columns) != set(candidate.columns):
        raise ValueError('Dashboard schema has changed')
    candidate = candidate[previous.columns].copy()
    latest = candidate.date.max()
    if latest < previous.date.max():
        raise ValueError('Refusing to roll back published data')
    if full:
        out = candidate
        if out.date.min() != previous.date.min():
            raise ValueError('Full benchmark would truncate the historical series')
    else:
        out = pd.concat([previous[previous.date < candidate.date.min()], candidate], ignore_index=True)
    out = out.sort_values('date').reset_index(drop=True)
    if out.date.duplicated().any():
        raise ValueError('Duplicate reference months')
    periods = pd.PeriodIndex(out.date, freq='M')
    if not np.all(np.diff(periods.asi8) == 1):
        raise ValueError('Missing reference months')
    cols = [c for c in out if c.startswith('ismi_')]
    out[cols] = out[cols].round(3)
    if not np.isfinite(out.drop(columns='date').to_numpy()).all():
        raise ValueError('Output contains missing values')
    if (out[cols].abs() > 1.02).any().any():
        raise ValueError('Output shares outside valid range')
    for ar in (1, 3, 12):
        for k in (2, 3, 4):
            for group in ('headline', 'goods', 'services', 'housing', 'core', 'coregoods'):
                suffix = f'_ar{ar}_k{k}'
                error = out[f'ismi_{group}{suffix}'] - out[f'ismi_{group}_positive{suffix}'] + out[f'ismi_{group}_negative{suffix}']
                if (error.abs() > 0.00101).any():
                    raise ValueError('Positive-minus-negative identity failed')
    return out
