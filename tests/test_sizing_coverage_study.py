"""`bench/studies/sizing_coverage.py` - M302's reading rule, audit R3.

Two of these tests pin mistakes this study made before it produced a
figure, because both would have returned a plausible number rather than
an error: turning the wrong entry of `raise_sizes`, and naming the
all-in as a sized raise.
"""
import pytest

from bench.studies import sizing_coverage as study


def _row(shipped_all_in=0.9, sized_all_in=0.2, shipped_check=0.1, sized_check=0.3,
         sized_sized=0.5, percentile=0.5, **extra):
    return {"note_fired": True, "sized_offered": True,
            "shipped_all_in": shipped_all_in, "sized_all_in": sized_all_in,
            "shipped_check": shipped_check, "sized_check": sized_check,
            "sized_sized": sized_sized, "percentile": percentile, **extra}


# -- the menu knob, which this study got wrong first ---------------------

def test_the_second_entry_is_the_one_hero_reads():
    """M233: after the first entry, `raise_sizes` multiplies the PREVIOUS
    BET, and hero facing villain's opening bet is making raise number
    TWO. Replacing the last entry - the third raise - left 40 of 50
    constructed spots with no size offered, because the knob being turned
    was not the one the node reads."""
    assert study.smaller_reraise_menu(((0.33, 0.75, 2.5), 3.0, 2.2)) == (
        (0.33, 0.75, 2.5), study.SMALLER_RERAISE, 2.2)


def test_the_opening_menu_is_never_touched():
    """Changing the opening sizes would change what villain can bet, so
    the two arms would no longer face the same bet."""
    menu = study.smaller_reraise_menu(((0.33, 0.75, 2.5), 3.0, 2.2))
    assert menu[0] == (0.33, 0.75, 2.5)


def test_a_menu_with_no_reraise_gains_one():
    assert study.smaller_reraise_menu(((0.33, 0.75),)) == ((0.33, 0.75), study.SMALLER_RERAISE)


def test_the_replacement_multiple_is_a_legal_raise():
    """M233 again: a multiple <= 1.0 builds a raise SMALLER than the bet
    it faces, which the tree accepts and every legality invariant
    passes."""
    assert study.SMALLER_RERAISE > 1.0


# -- rule 1: what counts ------------------------------------------------

def test_a_row_where_the_note_stayed_silent_is_not_usable():
    assert study.usable([_row(note_fired=False)]) == []


def test_a_row_where_no_size_was_offered_is_not_usable():
    """The sized arm has to actually change the tree, or the comparison
    is a solve against itself."""
    assert study.usable([_row(sized_offered=False)]) == []


def test_unusable_rows_are_still_counted():
    summary = study.summarise([_row(), _row(note_fired=False)])
    assert summary["n_priced"] == 2 and summary["n_usable"] == 1


# -- rule 2: the headline ------------------------------------------------

def test_the_claim_survives_when_the_all_in_frequency_collapses():
    rows = [_row(shipped_all_in=0.9, sized_all_in=0.1 + 0.001 * i) for i in range(20)]
    assert study.verdict(study.summarise(rows))["play_is_distorted"] is True


def test_the_claim_dies_when_adding_a_size_changes_nothing():
    rows = [_row(shipped_all_in=0.5, sized_all_in=0.5 + 0.0001 * (1 if i % 2 else -1))
            for i in range(20)]
    assert study.verdict(study.summarise(rows))["play_is_distorted"] is False


def test_the_claim_needs_enough_rows():
    rows = [_row(shipped_all_in=0.9, sized_all_in=0.1 + 0.001 * i) for i in range(3)]
    assert study.verdict(study.summarise(rows))["play_is_distorted"] is False


def test_the_headline_reports_how_much_the_new_size_is_actually_used():
    """If hero barely takes the size that was missing, the note's claim
    that its absence distorts the play is hard to sustain whatever the
    all-in frequency does."""
    summary = study.summarise([_row(sized_sized=0.0) for _ in range(10)])
    assert summary["headline"]["new_size_used"]["mean"] == 0.0


# -- rule 3: the two published examples -----------------------------------

def test_a_row_reproducing_the_published_strong_hand_case_is_counted():
    rows = [_row(percentile=0.9, shipped_check=0.99, sized_check=0.4)]
    assert study.named_cases(rows)["strong_hand_checks_almost_always"] == 1


def test_a_row_reproducing_the_published_weak_hand_case_is_counted():
    rows = [_row(percentile=0.2, shipped_all_in=0.99, sized_all_in=0.3)]
    assert study.named_cases(rows)["weak_hand_shoves_almost_always"] == 1


def test_an_example_that_does_not_move_when_a_size_appears_is_not_counted():
    """The published cases are about what the MISSING size does, so a row
    that checks 99% either way does not reproduce one."""
    rows = [_row(percentile=0.9, shipped_check=0.99, sized_check=0.99)]
    assert study.named_cases(rows)["strong_hand_checks_almost_always"] == 0


# -- rule 4: both directions ----------------------------------------------

def test_both_directions_needs_both_halves():
    strong = [_row(percentile=0.9, shipped_check=0.9, sized_check=0.2 + 0.001 * i)
              for i in range(10)]
    weak = [_row(percentile=0.2, shipped_all_in=0.9, sized_all_in=0.2 + 0.001 * i)
            for i in range(10)]
    assert study.verdict(study.summarise(strong + weak))["both_directions"] is True


def test_one_half_alone_does_not_carry_the_wording():
    strong = [_row(percentile=0.9, shipped_check=0.9, sized_check=0.2 + 0.001 * i)
              for i in range(10)]
    weak = [_row(percentile=0.2, shipped_all_in=0.5, sized_all_in=0.5 + 0.001 * i)
            for i in range(10)]
    verdict = study.verdict(study.summarise(strong + weak))
    assert verdict["strong_hands_over_check"] is True
    assert verdict["weak_hands_over_shove"] is False
    assert verdict["both_directions"] is False


# -- the masses ----------------------------------------------------------

def test_the_masses_read_the_rows_rather_than_the_constants():
    row = {"fold": 0.1, "call_or_check": 0.2, "raise:4.95": 0.3, "all_in:17.50": 0.4}
    assert study.all_in_mass(row) == pytest.approx(0.4)
    assert study.check_mass(row) == pytest.approx(0.2)
    assert study.sized_mass(row) == pytest.approx(0.3)


def test_a_row_with_no_sized_raise_is_what_the_gate_describes():
    """`fold / call_or_check / all_in` with nothing between is exactly
    the shape `_has_no_intermediate_bet_size` fires on."""
    row = {"fold": 0.3, "call_or_check": 0.2, "all_in:17.50": 0.5}
    assert study.sized_mass(row) == 0.0 and study.all_in_mass(row) == pytest.approx(0.5)


# -- the committed rows --------------------------------------------------

import json
import pathlib

DATA = pathlib.Path(__file__).parent / "data"


def _rows(name):
    return [json.loads(line) for line in (DATA / name).read_text().splitlines() if line.strip()]


def test_the_shipped_constants_reproduce_from_the_constructed_rows():
    from api import config as cfg

    summary = study.summarise(_rows("sizing_coverage_m302.jsonl"))
    assert summary["n_usable"] == cfg.SIZING_COVERAGE_ROWS
    assert round(-summary["headline"]["all_in_change"]["mean"], 4) == cfg.SIZING_COVERAGE_ALL_IN_CHANGE
    assert round(summary["headline"]["new_size_used"]["mean"], 3) == cfg.SIZING_COVERAGE_NEW_SIZE_USED


def test_every_published_claim_dies_on_the_recorded_rows():
    """The verdict that rewrote the copy, re-derived rather than recalled."""
    verdict = study.verdict(study.summarise(_rows("sizing_coverage_m302.jsonl")))
    assert verdict["named_cases_reproduce"] is False
    assert verdict["both_directions"] is False
    assert verdict["strong_hands_over_check"] is False
    assert verdict["weak_hands_over_shove"] is False


def test_the_missing_size_is_barely_used_when_it_is_offered():
    """The reason the distortion claim cannot stand: hero puts under 2%
    on the action whose absence the note is about."""
    summary = study.summarise(_rows("sizing_coverage_m302.jsonl"))
    assert summary["headline"]["new_size_used"]["mean"] < 0.05


def test_the_all_in_change_is_recorded_as_borderline_not_rounded_up():
    """It lands at 2.00 sigma, fractionally under the pre-registered bar.
    Rounding that into a claim is the kind of edge M232 exists to stop,
    so the copy quotes the SIZE of the change instead."""
    summary = study.summarise(_rows("sizing_coverage_m302.jsonl"))
    sigma = abs(summary["headline"]["all_in_change"]["sigma"])
    assert 1.9 < sigma < study.MIN_SIGMA
    assert study.verdict(summary)["play_is_distorted"] is False


def test_the_real_firing_rows_agree_with_the_constructed_ones():
    """Different populations, same behaviour - which is what lets the
    constructed arm describe what happens when the note fires."""
    real = _rows("sizing_coverage_real_m302.jsonl")
    assert real, "the real-hand arm found firing spots"
    assert max(r["sized_sized"] for r in real) < 0.10, (
        "in real firing spots too, the missing size is barely taken")
