"""The quantity parser: the deterministic core every other check stands on."""

import pytest

from onus.numbers import derived, find, sentences


def kinds(text):
    return [(q.raw, q.kind, round(q.value, 2)) for q in find(text)]


@pytest.mark.parametrize("text, expected", [
    ("$2.4B", [("$2.4B", "currency", 2.4e9)]),
    ("$2,400M", [("$2,400M", "currency", 2.4e9)]),
    ("€15 million", [("€15 million", "currency", 1.5e7)]),
    ("2.4 billion in ARR", [("2.4 billion", "amount", 2.4e9)]),
    ("up 42% year over year", [("42%", "percent", 42.0)]),
    ("12.5 percent", [("12.5 percent", "percent", 12.5)]),
    ("two thirds of new docks", [("two thirds", "fraction", 66.67)]),
    ("repair backlog 1.4x", [("1.4x", "multiple", 1.4)]),
    ("24 active companies", [("24", "count", 24.0)]),
    ("Eight of our companies", [("Eight", "count", 8.0)]),
    ("the 2023 contraction", [("2023", "year", 2023.0)]),
    ("FY2025 results", [("FY2025", "year", 2025.0)]),
    ("by 15 March", [("15 March", "date", 315.0)]),
    ("on March 15th", [("March 15th", "date", 315.0)]),
    ("over 18 months", [("18 months", "duration", 18.0)]),
    ("in two years", [("two years", "duration", 2.0)]),
])
def test_each_kind_parses_and_normalises(text, expected):
    assert kinds(text) == expected


def test_a_quarter_keeps_its_year():
    (q,) = find("Riverside internal data, Q1 2025.")
    assert (q.kind, q.value, q.year) == ("quarter", 1.0, 2025)
    (bare,) = find("before Q2")
    assert bare.year is None


@pytest.mark.parametrize("text", [
    "Contact pat@example.com", "see example.com", "https://x.io/2023/report", "Card 01", "Chapter VIII",
    "one ask", "year over year",
])
def test_noise_is_not_a_quantity(text):
    assert find(text) == []


def test_precision_decides_a_match():
    (claimed,) = find("$2.4B")
    (exact,) = find("$2,412M")
    (off,) = find("$2.6B")
    assert claimed.matches(exact), "2.4B is 2,412M rounded"
    assert not claimed.matches(off)
    (forty_two,) = find("42%")
    (forty_one,) = find("41%")
    assert not forty_two.matches(forty_one)


def test_kinds_match_only_within_their_group():
    (dollars,) = find("$2.4B")
    (words,) = find("2.4 billion")
    (percent,) = find("67%")
    (fraction,) = find("two thirds")
    (count,) = find("24 stations")
    (year,) = find("in 2024")
    assert dollars.matches(words) and percent.matches(fraction)
    assert not count.matches(year)


def test_durations_match_on_unit_and_quarters_on_year():
    (months,) = find("18 months")
    (years,) = find("18 years")
    assert not months.matches(years)
    (q1_2025,) = find("Q1 2025")
    (q1_2024,) = find("Q1 2024")
    (q1,) = find("Q1")
    assert not q1_2025.matches(q1_2024) and q1_2025.matches(q1)


def test_derived_values_come_from_pairs_stated_together():
    made = derived(find("median repair backlog 1.4x, down from 2.1x"))
    (claim,) = find("improved 33%")
    assert any(claim.matches(d) for d in made), "1.4 from 2.1 is a 33% fall"
    (fifty,) = find("50%")
    assert any(fifty.matches(d) for d in made), "and 2.1 is 50% above 1.4: magnitude either way, not direction"
    (forty,) = find("40%")
    assert not any(forty.matches(d) for d in made)
    assert derived(find("in 2023 and 2025")) == [], "years are not arithmetic"


def test_sentences_split_on_semicolons_too():
    assert sentences("ARR $2.4B; NRR 118%. Backlog 1.4x\nNext") == ["ARR $2.4B;", "NRR 118%.", "Backlog 1.4x", "Next"]
