import pytest
from hypothesis import given, strategies as st

from filingLens.domain.units import fmt_indian, rounding_factor, to_crore


@pytest.mark.parametrize("x,dec,expected", [
    (240893, 0, "2,40,893"),
    (12345678.5, 1, "1,23,45,678.5"),
    (999, 0, "999"),
    (1000, 0, "1,000"),
    (100000, 0, "1,00,000"),
    (-240893, 0, "-2,40,893"),
    (0, 0, "0"),
    (1234.567, 2, "1,234.57"),
    (45.5, 2, "45.50"),
])
def test_fmt_indian(x, dec, expected):
    assert fmt_indian(x, dec) == expected


def test_no_negative_zero():
    assert fmt_indian(-0.001, 2) == "0.00"


@given(st.integers(min_value=-10**12, max_value=10**12))
def test_fmt_indian_roundtrip(n):
    assert int(fmt_indian(n).replace(",", "")) == n


@given(st.integers(min_value=1000, max_value=10**12))
def test_fmt_indian_groups(n):
    parts = fmt_indian(n).split(",")
    assert len(parts[-1]) == 3
    assert all(len(p) == 2 for p in parts[1:-1])
    assert 1 <= len(parts[0]) <= 2


def test_rounding_factor():
    assert rounding_factor("Crores") == 1e7
    assert rounding_factor("Lakhs") == 1e5
    assert rounding_factor("In Millions") == 1e6
    assert rounding_factor(None) is None
    assert rounding_factor("nonsense") is None


def test_to_crore():
    assert to_crore(2_408_930_000_000, 1.0) == pytest.approx(240_893.0)
    assert to_crore(240_893, 1e7) == pytest.approx(240_893.0)
    assert to_crore(24_089_300, 1e5) == pytest.approx(240_893.0)
