"""Analytics on hand-made facts; expected values worked out by hand."""
import pytest
from conftest import make_fact

from filingLens.analytics.compare import build_comparison
from filingLens.analytics.derived import compute, load_series
from filingLens.analytics.transforms import cagr, index100, ratio, yoy
from filingLens.analytics.trend import build_trend
from filingLens.domain.registry import load_registry

REG = load_registry()


def series(values, metric="revenue_from_operations", ticker="TCS", **kw):
    """{"FY22": 100, ...} -> facts (None values are left out, i.e. a gap)."""
    return [make_fact(ticker, metric, fy, v, **kw) for fy, v in values.items() if v is not None]


# ---- transforms -------------------------------------------------------------------------------
def test_cagr_known_example_and_edges():
    assert cagr(100, 200, 5) == pytest.approx(0.1487, abs=1e-4)      # plan's check
    assert cagr(100, 121, 2) == pytest.approx(0.10)
    for bad in [(0, 200, 5), (-50, 200, 5), (100, -1, 5), (100, 200, 0), (None, 200, 5)]:
        assert cagr(*bad) is None


def test_yoy_ratio_index100_edges():
    assert yoy(225458, 240893) == pytest.approx(0.06846, abs=1e-5)  # TCS FY23 -> FY24
    assert yoy(0, 10) is None and yoy(-5, 10) is None and yoy(None, 10) is None
    assert yoy(100, -20) == pytest.approx(-1.2)                       # fall into a loss: defined
    assert ratio(46099, 240893) == pytest.approx(0.19137, abs=1e-5)
    assert ratio(5, 0) is None and ratio(5, -1) is None
    assert index100([None, 0, 50, 75, None]) == [None, None, 100, 150, None]
    assert index100([None, -1]) == [None, None]


# ---- trend -------------------------------------------------------------------------------------
def test_trend_gap_is_shown_not_filled_and_yoy_not_across_gap():
    t = build_trend(series({"FY21": 100, "FY22": 110, "FY23": None, "FY24": 133.1}))
    assert [(r.label, r.value) for r in t.rows] == [
        ("FY21", 100), ("FY22", 110), ("FY23", None), ("FY24", 133.1)]
    assert t.rows[1].change == pytest.approx(0.10)
    assert t.rows[2].change is None and t.rows[3].change is None   # no YoY across the gap
    assert t.cagr == pytest.approx(0.10) and t.cagr_span == ("FY21", "FY24")   # 3 real years
    assert "FY23 missing (not interpolated)" in t.notes
    assert t.to_frame().columns.tolist() == ["period", "value", "yoy"]


def test_trend_loss_base_single_point_and_mixed_series():
    t = build_trend(series({"FY23": -10, "FY24": 20}, metric="net_profit"))
    assert t.rows[1].change is None and t.cagr is None
    assert any("negative base" in n for n in t.notes) and any("CAGR" in n for n in t.notes)
    one = build_trend(series({"FY24": 5}))
    assert one.cagr is None and "only one data point: no growth figures" in one.notes
    with pytest.raises(ValueError):
        build_trend(series({"FY24": 1}) + series({"FY24": 1}, ticker="INFY"))
    with pytest.raises(ValueError):
        build_trend([])


def test_quarterly_trend_compares_same_quarter_last_year():
    q = {"Q1": ("-04-01", "-06-30"), "Q2": ("-07-01", "-09-30")}
    facts = [make_fact(fy=fy, value=v, ptype="Q", quarter=qq,
                       start=f"{2000 + int(fy[2:]) - 1}{q[qq][0]}",
                       end=f"{2000 + int(fy[2:]) - 1}{q[qq][1]}")
             for fy, qq, v in [("FY23", "Q1", 50), ("FY23", "Q2", 60), ("FY24", "Q1", 55)]]
    t = build_trend(facts)
    assert [r.label for r in t.rows] == ["FY23 Q1", "FY23 Q2", "FY23 Q3", "FY23 Q4", "FY24 Q1"]
    assert t.rows[-1].change == pytest.approx(0.10) and t.cagr is None


# ---- derived ---------------------------------------------------------------------------------
def test_derived_margin_in_percent_with_pp_change_and_inputs_tagged():
    num = series({"FY23": 42303, "FY24": 46099}, metric="net_profit")
    den = series({"FY23": 225458, "FY24": 240893, "FY22": 0})
    out, notes = compute("net_margin", num, den)
    assert [round(f.value, 2) for f in out] == [18.76, 19.14] and out[0].unit == "%"
    assert out[0].method == "derived" and out[0].metric_id == "net_margin"
    t = build_trend(out)
    assert t.is_ratio and t.rows[1].change == pytest.approx(0.3736, abs=1e-3) and t.cagr is None
    out, notes = compute("net_margin", series({"FY22": 5, "FY21": 5}, metric="net_profit"), den)
    assert out == [] and any("zero or negative" in n for n in notes) and any(
        "missing" in n for n in notes)


def test_load_series_derived_and_stock_metrics_via_injected_getter():
    store = {("TCS", "net_profit", "FY"): series({"FY24": 46099}, metric="net_profit"),
             ("TCS", "revenue_from_operations", "FY"): series({"FY24": 240893}),
             ("TCS", "total_assets", "INSTANT"): series({"FY24": 146449}, metric="total_assets",
                                                        ptype="INSTANT", start="")}
    calls = []

    def get(t, m, s, p):
        calls.append((t, m, s, p))
        return store.get((t, m, p), [])

    facts, _ = load_series(get, REG, "TCS", "net_margin")
    assert facts[0].value == pytest.approx(19.137, abs=1e-3)
    facts, _ = load_series(get, REG, "TCS", "total_assets")          # FY request -> INSTANT
    assert facts[0].value == 146449 and calls[-1][3] == "INSTANT"
    assert load_series(get, REG, "INFY", "net_profit")[1] == [
        "INFY net_profit (consolidated, FY): no data"]
    with pytest.raises(KeyError):
        load_series(get, REG, "TCS", "ebitda_made_up")


# ---- compare --------------------------------------------------------------------------------
def test_compare_companies_aligns_and_notes_missing():
    c = build_comparison({"TCS": series({"FY23": 225458, "FY24": 240893}),
                          "INFY": series({"FY24": 153670}, ticker="INFY"),
                          "HUL": []})
    assert c.frame.index.tolist() == ["FY23", "FY24"]
    assert c.frame.loc["FY24", "INFY"] == 153670 and c.frame["INFY"].isna()["FY23"]
    assert "INFY FY23 missing" in c.notes and "HUL: no data" in c.notes
    share = build_comparison({"A": series({"FY24": 75}), "B": series({"FY24": 25}, ticker="B")},
                             normalise="share")
    assert share.frame.loc["FY24"].tolist() == [75.0, 25.0] and share.unit == "% of total"
    idx = build_comparison({"A": series({"FY23": 50, "FY24": 60}),
                            "B": series({"FY23": 10, "FY24": 15}, ticker="B")},
                           normalise="index100")
    assert idx.frame.loc["FY24"].tolist() == [120.0, 150.0]       # B grew faster


def test_compare_refuses_mixed_units_and_handles_periods_axis():
    rev = series({"FY22": 100, "FY23": 110, "FY24": 121})
    eps = series({"FY24": 12.5}, metric="eps_basic").copy()
    eps = [f.model_copy(update={"unit": "INR per share"}) for f in eps]
    with pytest.raises(ValueError, match="different units"):
        build_comparison({"rev": rev, "eps": eps}, axis="metrics")
    c = build_comparison({"TCS": rev}, axis="periods", periods=["FY22", "FY24", "FY19"])
    assert c.frame.index.tolist() == ["FY22", "FY24"]
    assert c.frame["change_vs_first"].tolist() == pytest.approx([0.0, 0.21])
    assert "FY19: no data for any series" in c.notes
    with pytest.raises(ValueError, match="share only"):
        build_comparison({"a": rev, "b": rev}, axis="metrics", normalise="share")


def test_cli_trend_and_compare(tmp_path, capsys):
    from filingLens.cli import main
    from filingLens.storage.db import connect
    from filingLens.storage.repos import FactRepo

    db = tmp_path / "f.db"
    con = connect(db)
    FactRepo(con).upsert_facts(series({"FY23": 225458, "FY24": 240893})
                               + series({"FY23": 42303, "FY24": 46099}, metric="net_profit")
                               + series({"FY24": 153670}, ticker="INFY"))
    con.close()
    assert main(["trend", "TCS", "revenue_from_operations", "--db", str(db)]) == 0
    out = capsys.readouterr().out
    assert "2,40,893" in out and "+6.85%" in out and "CAGR FY23-FY24: +6.85%" in out
    assert main(["trend", "tcs", "net_margin", "--db", str(db)]) == 0
    assert "19.14%" in capsys.readouterr().out
    assert main(["compare", "revenue_from_operations", "--ticker", "TCS", "--ticker", "INFY",
                 "--db", str(db)]) == 0
    out = capsys.readouterr().out
    assert "1,53,670" in out and "note: INFY FY23 missing" in out
    assert main(["compare", "revenue_from_operations", "eps_basic", "--ticker", "TCS",
                 "--db", str(db)]) in (0, 2)       # eps has no data here: compared or refused
    assert main(["trend", "TCS", "made_up", "--db", str(db)]) == 2


def test_fy_balance_sheet_uses_year_end_only():
    facts = [make_fact(metric="total_assets", fy=fy, value=v, ptype="INSTANT", start="", end=end)
             for fy, v, end in [("FY26", 175219, "2025-12-31"), ("FY26", 182372, "2026-03-31"),
                                ("FY27", 190782, "2026-09-30"), ("FY25", 159629, "2025-03-31")]]
    got, _ = load_series(lambda t, m, s, p: facts, REG, "TCS", "total_assets")
    assert [(f.fy, f.value) for f in got] == [("FY26", 182372), ("FY25", 159629)]
