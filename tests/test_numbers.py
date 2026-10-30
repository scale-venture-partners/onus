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


@pytest.mark.parametrize("text, expected", [
    ("$2-3M", [("$2M", "currency", 2e6), ("$3M", "currency", 3e6)]),
    ("a $2\u20133M round", [("$2M", "currency", 2e6), ("$3M", "currency", 3e6)]),
    ("$2-$3M", [("$2M", "currency", 2e6), ("$3M", "currency", 3e6)]),
    ("2-3 million users", [("2 million", "amount", 2e6), ("3 million", "amount", 3e6)]),
    ("between 2 and 3 million", [("2 million", "amount", 2e6), ("3 million", "amount", 3e6)]),
    ("between approximately 15 and 20 million", [("15 million", "amount", 1.5e7), ("20 million", "amount", 2e7)]),
    ("margins of 2-3%", [("2%", "percent", 2.0), ("3%", "percent", 3.0)]),
    ("over 18-24 months", [("18 months", "duration", 18.0), ("24 months", "duration", 24.0)]),
    ("grew 2-3x", [("2x", "multiple", 2.0), ("3x", "multiple", 3.0)]),
    ("US$2-3M", [("$2M", "currency", 2e6), ("$3M", "currency", 3e6)]),
    ("$2M-3M", [("$2M", "currency", 2e6), ("$3M", "currency", 3e6)]),
    ("$2M-3", [("$2M", "currency", 2e6), ("$3M", "currency", 3e6)]),
    ("$18-24 months", [("18 months", "duration", 18.0), ("24 months", "duration", 24.0)]),
])
def test_a_range_reads_its_shared_tail_at_both_ends(text, expected):
    assert kinds(text) == expected


@pytest.mark.parametrize("text, expected", [
    ("We serve 2000 users.", [("2000", "count", 2000.0)]),
    ("2048-bit keys", [("2048", "count", 2048.0)]),
    ("the 2048-bit keys", [("2048", "count", 2048.0)]),
    ("In 2000 we launched.", [("2000", "year", 2000.0)]),
    ("the 2023 flood closures", [("2023", "year", 2023.0)]),
    ("2023 saw a dip", [("2023", "year", 2023.0)]),
    ("by 2026", [("2026", "year", 2026.0)]),
    ("In 2024-Q1 we shipped", [("2024", "year", 2024.0), ("Q1", "quarter", 1.0)]),
    ("Since 2015-dated filings went missing", [("2015", "year", 2015.0)]),
    ("2024-Q1 revenue", [("2024", "year", 2024.0), ("Q1", "quarter", 1.0)]),
    ("We employ 2000 staff.", [("2000", "count", 2000.0)]),
    ("Our 2026 goals", [("2026", "year", 2026.0)]),
    ("Riverside's 2019 figures", [("2019", "year", 2019.0)]),
    ("2000 LPs backed us", [("2000", "count", 2000.0)]),
    ("2050 its own thing", [("2050", "year", 2050.0)]),
    ("The order arrived late. 2000 units shipped.", [("2000", "count", 2000.0)]),
    ("FY2025 results", [("FY2025", "year", 2025.0)]),
])
def test_the_year_band_is_read_from_the_text_around_it(text, expected):
    assert kinds(text) == expected


def test_a_number_in_the_year_band_is_checked_as_both_readings():
    (users,) = find("2000 users")
    (spelled,) = find("2,000 users")
    assert users.matches(spelled), "a count is a count however it is punctuated"
    (plans,) = find("Our 2026 plans")
    (expanding,) = find("expanding in 2026")
    assert plans.matches(expanding) and expanding.matches(plans)
    (launched,) = find("in 2000 we launched")
    assert launched.matches(spelled), "the cost of that: a stated count clears a year of the same number"


def test_a_determiner_does_not_settle_a_number_as_a_year():
    (staff,) = find("We employ the 2000 staff.")
    (spelled,) = find("We employ 2,000 staff.")
    assert staff.matches(spelled), "'the' can introduce either reading, so it settles nothing"


@pytest.mark.parametrize("text, expected", [
    ("2019-2024 was the period", [("2019", "year", 2019.0), ("2024", "year", 2024.0)]),
    ("an 18-month contract", [("18-month", "duration", 18.0)]),
    ("we have 2 and 3 million", [("2", "count", 2.0), ("3 million", "amount", 3e6)]),
    ("$2 to \u20ac3M", [("$2", "currency", 2.0), ("\u20ac3M", "currency", 3e6)]),
    ("a 10-20x30 cm panel", [("10", "count", 10.0)]),
    ("the 2024-2025 year", [("2024", "year", 2024.0), ("2025 year", "duration", 2025.0)]),
])
def test_with_nothing_shared_there_is_no_range(text, expected):
    assert kinds(text) == expected


@pytest.mark.parametrize("text, expected", [
    ("emissions of 3-4 t per unit", [("3", "count", 3.0)]),
    ("a 2-3 m cable", [("2", "count", 2.0)]),
    ("18-24 m build", [("18", "count", 18.0)]),
])
def test_a_bare_scale_letter_is_not_a_scale_without_a_currency_symbol(text, expected):
    assert kinds(text) == expected, "the scanner reads a standalone '3 t' as 3, and a range may not differ"
    assert kinds("a 3 m cable") == [("3", "count", 3.0)]


@pytest.mark.parametrize("dash", ["-", "\u2010", "\u2011", "\u2012", "\u2013", "\u2014", "\u2015", "\u2212"])
def test_any_dash_a_document_can_hold_splits_a_range(dash):
    # A claim and its evidence must split the same way whatever typed the dash.
    assert kinds(f"margins of 2{dash}3%") == [("2%", "percent", 2.0), ("3%", "percent", 3.0)]


@pytest.mark.parametrize("text, expected", [
    ("2-3 years over the plan", [("2", "count", 2.0)]),
    ("2-3 months on from launch", [("2", "count", 2.0)]),
    ("01-02 million", [("02 million", "amount", 2e6)]),
    ("01-02%", [("02%", "percent", 2.0)]),
])
def test_a_range_keeps_the_guards_the_other_patterns_carry(text, expected):
    assert kinds(text) == expected


NOTATIONS = {
    "money": ["$2-3M", "$2\u20133M", "$2M-3M", "US$2-3M", "$2M to $3M", "between $2M and $3M"],
    "percent": ["2-3%", "2\u20133%", "2-3 percent", "2 to 3 percent", "between 2% and 3%", "churn,2-3%,steady"],
    "duration": ["18-24 months", "18 to 24 months", "between 18 and 24 months"],
    "count": ["2000 users", "2,000 users", "2000 staff", "the 2000 staff"],
}


@pytest.mark.parametrize("family", sorted(NOTATIONS))
def test_every_notation_of_one_fact_states_what_the_others_do(family):
    parsed = {text: find(text) for text in NOTATIONS[family]}
    for text, claim in parsed.items():
        assert claim, f"{text!r} parsed to nothing"
        for other, evidence in parsed.items():
            unmatched = [q.raw for q in claim if not any(q.matches(e) for e in evidence)]
            assert not unmatched, f"{text!r} claims {unmatched}, which {other!r} does not state"


@pytest.mark.parametrize("text, expected", [
    ("the 2024-2025 year", [("2024", "year", 2024.0), ("2025 year", "duration", 2025.0)]),
    ("the 2024-25 year", [("2024", "year", 2024.0), ("25 year", "duration", 25.0)]),
    ("Exhibit 7-3 appendices", [("7", "count", 7.0)]),
])
def test_a_range_runs_upward_and_a_span_of_years_is_not_a_duration(text, expected):
    assert kinds(text) == expected


@pytest.mark.parametrize("text, expected", [
    ("Customers grew 20% to 1,200 last quarter.", [("20%", "percent", 20.0), ("1,200", "count", 1200.0)]),
    ("The team grew 3x to 45 people this year.", [("3x", "multiple", 3.0), ("45", "count", 45.0)]),
    ("NRR 120% and 2-3x expansion", [("120%", "percent", 120.0), ("2x", "multiple", 2.0), ("3x", "multiple", 3.0)]),
])
def test_a_change_to_a_bare_number_is_not_a_range_and_does_not_hide_one(text, expected):
    assert kinds(text) == expected


def test_a_range_stays_on_one_line_though_its_tail_may_wrap():
    assert kinds("- 5\n- 10 million") == [("5", "count", 5.0), ("10 million", "amount", 1e7)], \
        "a bullet's dash is not a range dash"
    assert kinds("between 18 and 24\nmonths") == [("18 months", "duration", 18.0), ("24 months", "duration", 24.0)], \
        "hard-wrapped evidence states the same range as a single line does"
    assert kinds("Deals closed: 2-3\nmillion-dollar accounts") == [("2", "count", 2.0),
                                                                   ("3\nmillion", "amount", 3e6)], \
        "'million-dollar' is a compound, not a scale the range shares"
    assert kinds("covered docks 2-3\nMonths of telemetry") == [("2", "count", 2.0),
                                                              ("3\nMonths", "duration", 3.0)], \
        "a capital starts the next line's sentence; a wrapped tail is lowercase"


@pytest.mark.parametrize("text, expected", [
    ("Headcount 120 - 15% attrition", [("120", "count", 120.0), ("15%", "percent", 15.0)]),
    ("Churn 2.1% - 3 accounts lost", [("2.1%", "percent", 2.1), ("3", "count", 3.0)]),
    ("Revenue 2019 - 24% CAGR", [("2019", "year", 2019.0), ("24%", "percent", 24.0)]),
    ("$4.2M - 3 deals closed", [("$4.2M", "currency", 4.2e6), ("3", "count", 3.0)]),
    ("Headcount 12 - 15% attrition", [("12", "count", 12.0), ("15%", "percent", 15.0)]),
    ("Team 8 \u2013 10% growth", [("8", "count", 8.0), ("10%", "percent", 10.0)]),
    ("Exhibit 4 - 12 months of data", [("4", "count", 4.0), ("12 months", "duration", 12.0)]),
])
def test_a_spaced_dash_joins_a_label_to_a_value_not_a_range(text, expected):
    assert kinds(text) == expected


def test_both_ends_of_a_range_are_checked_separately():
    low, high = find("raised $2-3M")
    (two,) = find("$2M")
    (three,) = find("$3M")
    assert low.matches(two) and high.matches(three)
    assert not low.matches(three), "the ends are two quantities to check, not a span to fall inside"
    months = find("over 18-24 months")
    assert [q.unit for q in months] == ["month", "month"], "the unit is stated once and meant twice"


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


def test_a_claim_is_matched_at_its_own_precision():
    (precise,) = find("$2.4B")
    (vague,) = find("about 2 billion")
    assert not precise.matches(vague), "the source never said 2.4"
    (coarse,) = find("$2B")
    (exact,) = find("$2,412M")
    assert coarse.matches(exact), "2,412M rounds to the $2B the claim did make"
    (decimal,) = find("42.3%")
    (whole,) = find("42%")
    assert whole.matches(decimal) and not decimal.matches(whole), "one-way on purpose: the claim sets the bar"


def test_a_fraction_in_the_evidence_keeps_its_rounding_slack():
    (sixty_six,) = find("66% of docks")
    (two_thirds,) = find("Two thirds of docks")
    assert sixty_six.matches(two_thirds), "66% and 67% are both honest renderings of two thirds"
    assert two_thirds.matches(sixty_six)
    (fifty_two,) = find("52%")
    (half,) = find("a half")
    assert not fifty_two.matches(half), "the slack is the fraction's rounding, not an open door"
    (precise,) = find("50.9%")
    assert not precise.matches(half), "a claim keeps its own precision: the source never said .9"
    (seventy_six,) = find("76%")
    (quarters,) = find("three quarters")
    assert find("75%")[0].matches(quarters)
    assert not seventy_six.matches(quarters), "three quarters is exactly 75%, so it needs no slack at all"


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


def test_the_spread_of_one_range_is_not_a_stated_change():
    assert derived(find("Revenue is $2-3M this year.")) == [], "a spread is not a rise from one end to the other"
    assert derived(find("Margins hold at 2-3%.")) == []


def test_a_year_is_not_arithmetic_whatever_else_it_could_count():
    assert derived(find("We hold three plans for 2026.")) == []
    assert derived(find("Revenue grew over 2019-2024.")) == []


def test_a_range_that_states_a_change_still_licenses_it():
    (fifty,) = find("ARR grew 50%")
    assert any(fifty.matches(d) for d in derived(find("ARR went from $2M to $3M"))), "a rise is a stated change"
    (third,) = find("backlog fell 33%")
    assert derived(find("Unit costs ran from 2-3% of revenue.")) == [], "'from' a spread is still a spread"
    assert not any(third.matches(d) for d in derived(find("Guidance ranges from $2-3M")))


def test_arithmetic_needs_to_know_what_its_numbers_are():
    counts = find("2019 closures cost less than 2024 closures")
    assert [(q.kind, q.alt) for q in counts] == [("count", "year"), ("count", "year")]
    assert derived(counts) == [], "a number that could be a year or a count is no basis for arithmetic"
    assert derived(find("Riverside's 2019 figures and the board's 2024 minutes")) == []


def test_sentences_split_on_semicolons_too():
    assert sentences("ARR $2.4B; NRR 118%. Backlog 1.4x\nNext") == ["ARR $2.4B;", "NRR 118%.", "Backlog 1.4x", "Next"]


def test_a_chart_value_with_no_format_matches_any_kind_at_its_magnitude():
    ev = find("fare revenue $2.4M, up 42%; renewal 88%; backlog 1.4x; 24 stations; two thirds east; in 2026")

    def plain(text):
        return find(text, plain=True)[-1]

    assert plain("Fare revenue: 2400000").kind == "number"
    for text, stated in (("Fare revenue: 2400000", ["$2.4M"]), ("Renewal: 0.88", ["88%"]), ("Renewal: 88", ["88%"]),
                         ("Backlog: 1.4", ["1.4x"]), ("East: 0.67", ["two thirds"]), ("Stations: 25", []),
                         ("Founded: 2026", [])):
        assert [e.raw for e in ev if plain(text).matches(e)] == stated, text
    (claim,) = find("fare revenue of $2.4M")
    assert claim.matches(plain("Fare revenue: 2400000")), "a chart used as evidence states a text claim too"
    (claim,) = find("renewal at 88%")
    assert claim.matches(plain("Renewal: 0.88")) and not claim.matches(plain("Renewal: 0.6"))
    year, value = find("Backlog, 2026 target: 1.2", plain=True)
    assert year.kind != "number" and value.kind == "number", "only the value ends the line; a category's year is a year"
