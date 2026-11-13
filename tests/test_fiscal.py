import datetime as dt

import pytest

from copilot.domain.fiscal import (
    fy_int, fy_label, infer_fy_from_name, ptype, quarter_of, to_date,
)


@pytest.mark.parametrize("d,expected", [
    (dt.date(2024, 3, 31), "FY24"),
    (dt.date(2024, 4, 1), "FY25"),
    (dt.date(2023, 12, 31), "FY24"),
    (dt.date(1999, 6, 30), "FY00"),
])
def test_fy_label_boundaries(d, expected):
    assert fy_label(d) == expected


def test_fy_int():
    assert fy_int("FY24") == 24


@pytest.mark.parametrize("d,q", [
    (dt.date(2023, 6, 30), "Q1"), (dt.date(2023, 9, 30), "Q2"),
    (dt.date(2023, 12, 31), "Q3"), (dt.date(2024, 3, 31), "Q4"),
    (dt.date(2023, 7, 31), None),
])
def test_quarter_of(d, q):
    assert quarter_of(d) == q


@pytest.mark.parametrize("start,end,expected", [
    (dt.date(2023, 4, 1), dt.date(2023, 6, 30), "Q"),
    (dt.date(2023, 4, 1), dt.date(2023, 9, 30), "6M"),
    (dt.date(2023, 4, 1), dt.date(2023, 12, 31), "9M"),
    (dt.date(2023, 4, 1), dt.date(2024, 3, 31), "FY"),   # leap year, 366 days
    (dt.date(2022, 4, 1), dt.date(2023, 3, 31), "FY"),   # 365 days
    (dt.date(2023, 4, 1), dt.date(2023, 4, 30), "other"),
    (dt.date(2023, 4, 1), dt.date(2024, 8, 1), "other"),
])
def test_ptype(start, end, expected):
    assert ptype(start, end) == expected


def test_ptype_edges():
    s = dt.date(2023, 1, 1)
    for days, exp in [(79, "other"), (80, "Q"), (100, "Q"), (101, "other"),
                      (354, "other"), (355, "FY"), (372, "FY"), (373, "other")]:
        assert ptype(s, s + dt.timedelta(days=days - 1)) == exp


@pytest.mark.parametrize("name,expected", [
    ("TCS_annual_FY25.pdf", "FY25"),
    ("report_FY2024.pdf", "FY24"),
    ("AR_24011_TCS_2023_2024_09052024003541.pdf", None),
    ("annual_2023-24.pdf", "FY24"),
    ("random.pdf", None),
])
def test_infer_fy_from_name(name, expected):
    assert infer_fy_from_name(name) == expected


def test_to_date():
    assert to_date("2024-03-31") == dt.date(2024, 3, 31)
    assert to_date("2024-03-31T00:00:00") == dt.date(2024, 3, 31)
    assert to_date("garbage") is None
    assert to_date(None) is None
