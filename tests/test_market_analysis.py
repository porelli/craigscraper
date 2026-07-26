import pandas as pd
from craigscraper.market_analysis import (
    MIN_POINTS_PER_BUCKET, monthly_median_rent, new_listings_per_month,
    pct_change_vs, active_listings_per_month
)

def _prices(rows):
    return pd.DataFrame(rows, columns=['month', 'bedrooms', 'price'])

def test_monthly_median_basic():
    # 5 one-bed rows in 2025-03 -> median kept; 2 one-bed rows in 2025-04 -> dropped (< 5)
    rows = [('2025-03', 1.0, p) for p in [2000, 2100, 2200, 2300, 2400]]
    rows += [('2025-04', 1.0, 3000), ('2025-04', 1.0, 3100)]
    out = monthly_median_rent(_prices(rows))
    kept = out[(out['month'] == '2025-03') & (out['bedrooms'] == 1.0)]
    assert len(kept) == 1
    assert kept.iloc[0]['median_price'] == 2200
    assert kept.iloc[0]['n'] == 5
    # 2025-04 bucket dropped by sparsity guard
    assert out[(out['month'] == '2025-04')].empty

def test_monthly_median_separates_bedrooms():
    rows = [('2025-03', 1.0, p) for p in [2000]*5]
    rows += [('2025-03', 2.0, p) for p in [3000]*5]
    out = monthly_median_rent(_prices(rows))
    assert set(out['bedrooms']) == {1.0, 2.0}
    assert out[out['bedrooms'] == 2.0].iloc[0]['median_price'] == 3000

def test_new_listings_per_month():
    out = new_listings_per_month(pd.Series(['2025-03', '2025-03', '2025-04']))
    assert list(out['month']) == ['2025-03', '2025-04']
    assert list(out['count']) == [2, 1]

def test_pct_change_vs_present():
    df = pd.DataFrame({'month': ['2025-01', '2025-04'], 'median_price': [2000, 2200]})
    # latest is 2025-04 (2200); 3 months back is 2025-01 (2000) -> +10%
    assert round(pct_change_vs(df, 3), 1) == 10.0

def test_pct_change_vs_missing_returns_none():
    df = pd.DataFrame({'month': ['2025-04'], 'median_price': [2200]})
    assert pct_change_vs(df, 3) is None

def test_active_listings_per_month_spans_inclusive():
    # listing A active 2025-01..2025-03; listing B active 2025-02..2025-02 (posted+removed same month)
    spans = pd.DataFrame(
        [('2025-01', '2025-03'), ('2025-02', '2025-02')],
        columns=['posted_month', 'last_month']
    )
    out = active_listings_per_month(spans)
    got = dict(zip(out['month'], out['active']))
    assert got == {'2025-01': 1, '2025-02': 2, '2025-03': 1}

def test_active_listings_per_month_year_boundary():
    spans = pd.DataFrame([('2025-11', '2026-01')], columns=['posted_month', 'last_month'])
    out = active_listings_per_month(spans)
    assert list(out['month']) == ['2025-11', '2025-12', '2026-01']
    assert list(out['active']) == [1, 1, 1]
