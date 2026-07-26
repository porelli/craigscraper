"""Pure aggregation helpers for the market-over-time dashboard.

No scrapy/streamlit imports so this is unit-testable and importable from the UI process.
"""
import pandas as pd

MIN_POINTS_PER_BUCKET = 5  # drop (month, bedroom) medians backed by fewer price points


def monthly_median_rent(prices_df):
    # prices_df columns: month (str 'YYYY-MM'), bedrooms (float), price (int)
    if prices_df.empty:
        return pd.DataFrame(columns=['month', 'bedrooms', 'median_price', 'n'])
    grouped = prices_df.groupby(['month', 'bedrooms'])['price']
    out = grouped.agg(median_price='median', n='count').reset_index()
    out = out[out['n'] >= MIN_POINTS_PER_BUCKET]
    return out.sort_values(['month', 'bedrooms']).reset_index(drop=True)


def new_listings_per_month(posted_months):
    s = pd.Series(list(posted_months)).dropna()
    if s.empty:
        return pd.DataFrame(columns=['month', 'count'])
    out = s.value_counts().reset_index()
    out.columns = ['month', 'count']
    return out.sort_values('month').reset_index(drop=True)


def pct_change_vs(median_overall_df, months_back):
    # median_overall_df: columns month ('YYYY-MM'), median_price; one row per month
    if median_overall_df.empty:
        return None
    df = median_overall_df.sort_values('month').reset_index(drop=True)
    latest_month = df.iloc[-1]['month']
    latest_val = df.iloc[-1]['median_price']
    year, mon = int(latest_month[:4]), int(latest_month[5:7])
    total = (year * 12 + (mon - 1)) - months_back
    target = '%04d-%02d' % (total // 12, (total % 12) + 1)
    match = df[df['month'] == target]
    if match.empty:
        return None
    prev = match.iloc[0]['median_price']
    if prev == 0:
        return None
    return (latest_val - prev) / prev * 100


def _month_to_index(month_str):
    # 'YYYY-MM' -> integer month index (year*12 + month-1) for inclusive range math
    return int(month_str[:4]) * 12 + (int(month_str[5:7]) - 1)


def _index_to_month(idx):
    return '%04d-%02d' % (idx // 12, (idx % 12) + 1)


def active_listings_per_month(spans_df):
    # spans_df columns: posted_month ('YYYY-MM'), last_month ('YYYY-MM').
    # Count each listing as active in every month from posted_month through last_month inclusive.
    if spans_df.empty:
        return pd.DataFrame(columns=['month', 'active'])
    counts = {}
    for _, r in spans_df.dropna(subset=['posted_month', 'last_month']).iterrows():
        start = _month_to_index(r['posted_month'])
        end = _month_to_index(r['last_month'])
        if end < start:  # defensive: ignore inverted spans
            continue
        for idx in range(start, end + 1):
            m = _index_to_month(idx)
            counts[m] = counts.get(m, 0) + 1
    out = pd.DataFrame(sorted(counts.items()), columns=['month', 'active'])
    return out
