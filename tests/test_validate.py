"""Each validation rule: one passing and one failing case, plus qa flag logic."""
import pytest
from conftest import make_fact

from filingLens.ingest.validate import CheckResult, apply_qa, qa_flags, run_checks

Q = {"Q1": ("2023-04-01", "2023-06-30"), "Q2": ("2023-07-01", "2023-09-30"),
     "Q3": ("2023-10-01", "2023-12-31"), "Q4": ("2024-01-01", "2024-03-31")}


def fy(**vals):
    return [make_fact(metric=m, value=v) for m, v in vals.items()]


def status(results, check):
    hits = [r for r in results if r.check == check]
    assert len(hits) == 1, hits
    return hits[0].status


@pytest.mark.parametrize("ti,expected", [(1050, "pass"), (1052, "pass"), (1200, "fail")])
def test_income_sum(ti, expected):
    r = run_checks(fy(revenue_from_operations=1000, other_income=50, total_income=ti))
    assert status(r, "income_sum") == expected


@pytest.mark.parametrize("pbt,expected", [(250, "pass"), (230, "pass"), (150, "warn")])
def test_pbt_bridge(pbt, expected):
    r = run_checks(fy(total_income=1050, total_expenses=800, profit_before_tax=pbt))
    assert status(r, "pbt_bridge") == expected


@pytest.mark.parametrize("np_,expected", [(190, "pass"), (100, "warn")])
def test_pat_bridge(np_, expected):
    r = run_checks(fy(profit_before_tax=250, tax_expense=60, net_profit=np_))
    assert status(r, "pat_bridge") == expected


@pytest.mark.parametrize("rev,expected", [(1000, "pass"), (0, "fail"), (-5, "fail")])
def test_revenue_positive(rev, expected):
    assert status(run_checks(fy(revenue_from_operations=rev)), "revenue_positive") == expected


def _quarters(values, fy_value, metric="revenue_from_operations"):
    out = [make_fact(metric=metric, value=fy_value)]
    for (q, (s, e)), v in zip(Q.items(), values):
        out.append(make_fact(metric=metric, value=v, ptype="Q", start=s, end=e, quarter=q))
    return out


@pytest.mark.parametrize("fy_value,expected", [(1000, "pass"), (1003, "pass"), (1100, "fail")])
def test_quarter_sum(fy_value, expected):
    r = run_checks(_quarters([250, 250, 250, 250], fy_value))
    assert status(r, "quarter_sum") == expected


def test_quarter_sum_needs_all_four_quarters():
    facts = _quarters([250, 250, 250, 250], 9999)[:-1]   # drop Q4
    assert not [r for r in run_checks(facts) if r.check == "quarter_sum"]


def test_checks_skip_missing_and_null_values():
    facts = fy(revenue_from_operations=1000, other_income=50)   # no total_income
    facts.append(make_fact(metric="total_income").model_copy(update={"value": None}))
    assert not [r for r in run_checks(facts) if r.check == "income_sum"]


def test_checks_grouped_by_period_and_scope():
    facts = fy(revenue_from_operations=1000, other_income=50, total_income=1050)
    facts += [make_fact(metric="total_income", value=9999, scope="standalone")]
    r = [x for x in run_checks(facts) if x.check == "income_sum"]
    assert [x.status for x in r] == ["pass"]   # standalone group lacks rev/other


def test_qa_flags_fail_beats_warn_and_apply():
    res = [CheckResult("TCS", "consolidated", "s", "e", "a", "warn", "", ("m1",)),
           CheckResult("TCS", "consolidated", "s", "e", "b", "fail", "", ("m1", "m2")),
           CheckResult("TCS", "consolidated", "s", "e", "c", "warn", "", ("m2",)),
           CheckResult("TCS", "consolidated", "s", "e", "d", "pass", "", ("m3",))]
    flags = qa_flags(res)
    assert flags == {("TCS", "consolidated", "s", "e", "m1"): "fail",
                     ("TCS", "consolidated", "s", "e", "m2"): "fail"}


def test_apply_qa_marks_failing_facts():
    facts = fy(revenue_from_operations=1000, other_income=50, total_income=2000,
               net_profit=10)
    out = {f.metric_id: f.qa for f in apply_qa(facts, run_checks(facts))}
    assert out == {"revenue_from_operations": "fail", "other_income": "fail",
                   "total_income": "fail", "net_profit": "ok"}
