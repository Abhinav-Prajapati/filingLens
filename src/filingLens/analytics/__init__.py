"""Analytics: pure calculations on Fact series (no database access).

    transforms.py  yoy, cagr, ratio/margin/share, index100
    trend.py       one series over time: YoY, CAGR, gaps (never interpolated)
    derived.py     ratio metrics (net_margin, ...) and load_series(get_series, ...)
    compare.py     companies / metrics / periods side by side, unit-safe
"""
