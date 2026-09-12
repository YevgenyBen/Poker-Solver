"""Hero's EV under a reference solver's own strategy pair.

M256. The load-bearing test is the PASSIVE CONTROL: if neither player
ever puts a chip in, hero must collect exactly `equity * pot`, so the
realisation factor is exactly 1.0. Every leaf in a real walk lands on a
four-card board, which this engine enumerates rather than samples, so
that identity holds to machine precision and not to a tolerance — a
control that could only be asserted loosely would not catch the errors
worth catching.

The second is the villain-branch weighting. Values returned by the walk
are already conditional on the reach they were computed with, so
villain's branches combine by their SHARE of the reach mass. Weighting
by a raw sum instead double-counts, returns a plausible number, and was
the first version's actual bug.
"""
import numpy as np
import pytest

from bench.dump_ev import (LeafEquity, Walk, map_row, parse_action,
                           realisation)
from bench.solver_dump import strategy_at

HERO, VILLAIN = 1, 0
POT = 10.0
#: Two villain combos. Nothing here evaluates cards - the equity is
#: injected - so the labels only have to be distinguishable.
VILLAIN_COMBOS = ("AsAd", "7c2h")


def _action(player, actions, rows, children=None):
    return {"node_type": "action_node", "player": player, "actions": actions,
            "strategy": {"actions": actions, "strategy": rows},
            "childrens": children or {}}


def _chance(dealcards=None):
    node = {"node_type": "chance_node", "deal_number": 52}
    if dealcards is not None:
        node["dealcards"] = dealcards
    return node


def _walk(equity, hero_key="KhKd"):
    leaf = LeafEquity(hero_key, VILLAIN_COMBOS,
                      equity_fn=lambda board: np.array(equity, dtype=float))
    return Walk(hero_index=HERO, hero_key=hero_key,
                villain_combos=VILLAIN_COMBOS, leaf=leaf, pot0=POT)


def _reach(*weights):
    return np.array(weights, dtype=float)


def test_a_passive_pair_collects_exactly_equity_times_pot():
    """The control the whole module rests on.

    Nobody bets, so nothing is invested and hero's EV must be its share
    of the pot that was already there. If this is off by anything at all,
    the pot accounting is wrong and every realisation figure built on it
    is wrong in the same direction.
    """
    tree = _action(HERO, ["CHECK"], {"KhKd": [1.0]},
                   children={"CHECK": _action(
                       VILLAIN, ["CHECK"],
                       {c: [1.0] for c in VILLAIN_COMBOS},
                       children={"CHECK": _chance()})})
    for equity in ([0.34, 0.34], [0.71, 0.71], [0.0, 0.0], [1.0, 1.0]):
        walk = _walk(equity)
        ev = walk.value(tree, ("8h", "6h", "2s", "Td"), _reach(1.0, 1.0))
        assert ev == pytest.approx(equity[0] * POT, abs=1e-12)
        if equity[0]:
            assert realisation(ev, equity[0], POT) == pytest.approx(1.0, abs=1e-12)


def test_villain_branches_combine_by_their_share_of_the_reach():
    """The first version's bug, pinned.

    One villain combo always folds and the other always calls, at equal
    reach. Hero's EV is the average of the two outcomes. Weighting by a
    raw reach sum instead of its share returns twice that - a number
    that looks like a plausible EV and is not.
    """
    showdown = _chance()
    tree = _action(VILLAIN, ["FOLD", "CALL"],
                   {"AsAd": [1.0, 0.0], "7c2h": [0.0, 1.0]},
                   children={"CALL": showdown})
    # Hero has bet 5 and villain is facing it: street_in is (villain 0,
    # hero 5). `total_in` carries only PREVIOUS streets, so it is zero.
    walk = _walk([0.20, 0.80])
    ev = walk.value(tree, ("8h", "6h", "2s", "Td"), _reach(1.0, 1.0),
                    street_in=(0.0, 5.0), total_in=(0.0, 0.0))

    folded = POT                            # hero takes the pot, 5 comes back
    called = 0.80 * (POT + 10.0) - 5.0      # only 7c2h remains, equity 0.80
    assert ev == pytest.approx((folded + called) / 2.0, abs=1e-12)
    assert ev != pytest.approx(folded + called, abs=1e-9), (
        "raw-sum weighting would double this - the bug this test exists for"
    )


def test_a_fold_hands_over_what_was_bet_on_this_street_too():
    """A villain who bets and is raised off the hand leaves that bet.

    Reading only the carried-over total loses it, and only in lines where
    someone folds after committing - which is most of them, so the error
    is large and systematic rather than rare.
    """
    tree = _action(VILLAIN, ["FOLD"], {c: [1.0] for c in VILLAIN_COMBOS})
    walk = _walk([0.5, 0.5])
    ev = walk.value(tree, ("8h", "6h", "2s", "Td"), _reach(1.0, 1.0),
                    street_in=(6.0, 18.0), total_in=(4.0, 4.0))
    assert ev == pytest.approx(POT + 4.0 + 6.0), (
        "hero should win the pot plus everything villain committed, on "
        "this street and before it"
    )


def test_a_fold_costs_only_what_was_put_in_after_the_flop():
    """The money already in the pot is dead and is not hero's to lose.

    `street_in` and `total_in` are SEPARATE - the current street and the
    ones before it - and passing the same figure as both double-counts
    every chip. That is a fixture error rather than a code one, and it
    is worth pinning because it produced a plausible number twice while
    this module was being written.
    """
    tree = _action(HERO, ["FOLD"], {"KhKd": [1.0]})
    walk = _walk([0.5, 0.5])
    assert walk.value(tree, ("8h", "6h", "2s", "Td"), _reach(1.0, 1.0)) == 0.0
    assert walk.value(tree, ("8h", "6h", "2s", "Td"), _reach(1.0, 1.0),
                      street_in=(7.0, 7.0), total_in=(0.0, 0.0)) == -7.0
    assert walk.value(tree, ("8h", "6h", "2s", "Td"), _reach(1.0, 1.0),
                      street_in=(7.0, 7.0), total_in=(4.0, 4.0)) == -11.0


def test_a_chance_node_averages_over_unblocked_cards_only():
    """A card villain holds cannot also come off the deck.

    The reach for a blocked combo goes to zero on that runout; a walk
    that skips the masking quietly lets villain hold a card that is on
    the board.
    """
    leafnode = _action(HERO, ["CHECK"], {"KhKd": [1.0]},
                       children={"CHECK": _action(
                           VILLAIN, ["CHECK"],
                           {c: [1.0] for c in VILLAIN_COMBOS},
                           children={"CHECK": _chance()})})
    tree = _chance({"As": leafnode, "9d": leafnode})
    # AsAd is blocked by the As runout, so that branch is decided against
    # 7c2h alone.
    walk = _walk([0.10, 0.90])
    ev = walk.value(tree, ("8h", "6h", "2s"), _reach(1.0, 1.0))
    both = 0.5 * (0.10 + 0.90) * POT
    only_trash = 0.90 * POT
    assert ev == pytest.approx((only_trash + both) / 2.0, abs=1e-12)


def test_the_amounts_are_street_totals_not_increments():
    """`BET 8` after `BET 3` on the same street means committed TO 8.

    Read as an increment it would be 11, and every pot downstream would
    be wrong by a compounding amount while still looking like money.
    """
    assert parse_action("CHECK") == ("CHECK", None)
    assert parse_action("CALL") == ("CALL", None)
    assert parse_action("FOLD") == ("FOLD", None)
    assert parse_action("BET 3.000000") == ("BET", 3.0)
    assert parse_action("RAISE 13.000000") == ("RAISE", 13.0)

    # Hero bets 8, villain calls: the pot is 10 + 8 + 8, hero put in 8.
    tree = _action(HERO, ["BET 8.000000"], {"KhKd": [1.0]},
                   children={"BET 8.000000": _action(
                       VILLAIN, ["CALL"], {c: [1.0] for c in VILLAIN_COMBOS},
                       children={"CALL": _chance()})})
    walk = _walk([0.60, 0.60])
    ev = walk.value(tree, ("8h", "6h", "2s", "Td"), _reach(1.0, 1.0))
    assert ev == pytest.approx(0.60 * (POT + 16.0) - 8.0, abs=1e-12)


def test_a_hand_that_never_wins_realises_nothing_and_one_that_always_wins_gains():
    """Sanity in both directions, because a sign error in the investment
    term is invisible at equity 0.5."""
    tree = _action(HERO, ["BET 8.000000"], {"KhKd": [1.0]},
                   children={"BET 8.000000": _action(
                       VILLAIN, ["CALL"], {c: [1.0] for c in VILLAIN_COMBOS},
                       children={"CALL": _chance()})})
    losing = _walk([0.0, 0.0]).value(tree, ("8h", "6h", "2s", "Td"),
                                     _reach(1.0, 1.0))
    winning = _walk([1.0, 1.0]).value(tree, ("8h", "6h", "2s", "Td"),
                                      _reach(1.0, 1.0))
    assert losing == pytest.approx(-8.0)
    assert winning == pytest.approx(POT + 8.0)


def test_the_two_players_split_the_pot_and_nothing_else():
    """The exact control, and the only one that caught the last two bugs.

    Fix hero's hand and put villain's whole reach on ONE combo. Then

        EV(hero | villain holds v) + EV(v | hero holds hero) == the pot

    for any strategies whatsoever, because at every terminal the two of
    them divide that pot and nothing else. No averaging, no blocking, no
    range weighting - the identity is arithmetic, so any violation is a
    bug and its size is unambiguous.

    **Both bugs this caught were invisible to the tests above.** Villain
    combos hero blocks started at full reach and were only dropped at the
    leaf, distorting every branch share (8.8% of the pot); and hero's own
    cards were left in the runout at chance nodes, so the two sides ended
    up conditioned on different decks (a further 0.8%). Averaged controls
    could not resolve either, because their own target is uncertain by
    about 2% from the same blocking they were trying to measure.
    """
    hero_a, hero_b = "AhKh", "AcKc"

    def tree():
        """Both check, a card comes, both check, showdown."""
        second = _action(0, ["CHECK"], {hero_b: [1.0]},
                         children={"CHECK": _chance()})
        first = _action(1, ["CHECK"], {hero_a: [1.0]},
                        children={"CHECK": second})
        # One runout is a card hero A holds and cannot come; the other is
        # live. Before the fix, A averaged over both and B over one.
        return _chance({"Ah": first, "2d": first})

    board = ("8s", "6s", "2h")

    # The equity MUST vary by runout. With one number for every board,
    # averaging over two cards and over one gives the same answer, the
    # identity holds either way, and the test passes while blind to the
    # bug it exists for - which is what the first version of it did.
    by_card = {"Ah": 0.90, "2d": 0.34}

    def walk_for(hero_key, villain_key, hero_index, flip):
        def equity_fn(b):
            base = by_card[str(b[-1])]
            return np.array([1.0 - base if flip else base])

        leaf = LeafEquity(hero_key, [villain_key], equity_fn=equity_fn)
        return Walk(hero_index=hero_index, hero_key=hero_key,
                    villain_combos=[villain_key], leaf=leaf, pot0=POT)

    node = tree()
    wa = walk_for(hero_a, hero_b, 1, flip=False)
    wb = walk_for(hero_b, hero_a, 0, flip=True)
    ev_a = wa.value(node, board, wa.initial_reach(board))
    ev_b = wb.value(node, board, wb.initial_reach(board))

    assert ev_a is not None and ev_b is not None
    assert ev_a + ev_b == pytest.approx(POT, abs=1e-9), (
        "the two players conjured chips out of the deck: %r + %r != %r"
        % (ev_a, ev_b, POT)
    )


def test_a_villain_combo_hero_blocks_starts_dead():
    """It cannot be held, so it must not weight a single branch.

    Leaving it at full reach and dropping it only at the leaf looks
    harmless and is not: it carries through every villain decision, and
    how much it distorts depends on how many combos hero happens to
    block, which differs for every hand.
    """
    leaf = LeafEquity("AhKh", ["AhQd", "AcQd"],
                      equity_fn=lambda _b: np.array([0.5, 0.5]))
    walk = Walk(hero_index=1, hero_key="AhKh",
                villain_combos=["AhQd", "AcQd"], leaf=leaf, pot0=POT)
    reach = walk.initial_reach(("8s", "6s", "2h"))
    assert list(reach) == [0.0, 1.0], (
        "AhQd shares the ace of hearts with hero and cannot be dealt"
    )
    onboard = Walk(hero_index=1, hero_key="AhKh",
                   villain_combos=["8sQd", "AcQd"], leaf=leaf, pot0=POT)
    assert list(onboard.initial_reach(("8s", "6s", "2h"))) == [0.0, 1.0], (
        "a combo holding a board card cannot be dealt either"
    )


def test_our_row_is_remapped_onto_the_sizes_the_reference_offers():
    """Two trees, two bet menus, one comparison.

    Our row cannot be compared action-for-action with the reference's, so
    a size maps to the nearest one offered - and **how much mass had to
    move is part of the result**, not an implementation detail. M209 set
    that convention after a comparison whose conclusion depended on a
    remapping nobody had reported.
    """
    actions = ["CHECK", "BET 3.000000", "BET 8.000000", "FOLD"]

    exact, moved = map_row({"call_or_check": 0.4, "raise:3.00": 0.6},
                           actions, facing=False)
    assert exact == {"CHECK": 0.4, "BET 3.000000": 0.6}
    assert moved == 0.0, "a size the reference also offers has not moved"

    approx, moved = map_row({"raise:5.00": 1.0}, actions, facing=False)
    assert approx == {"BET 3.000000": 1.0}, "5 is nearer 3 than 8"
    assert moved == pytest.approx(1.0), (
        "all of it landed on a size we did not name, and the caller has "
        "to be told so"
    )

    folds, _ = map_row({"fold": 0.7, "call_or_check": 0.3}, actions,
                       facing=True)
    assert folds == pytest.approx({"FOLD": 0.7, "CHECK": 0.3})


def test_a_remapped_row_still_sums_to_one():
    """Two of our actions can collapse onto one of theirs.

    Dropping the renormalisation leaves a row summing to less than 1, and
    `price_row` divides by the mass it used - so the EV would look fine
    and be an average over a fraction of the strategy.
    """
    actions = ["CHECK", "BET 9.000000", "FOLD"]
    row, moved = map_row({"raise:8.00": 0.5, "all_in:10.00": 0.5}, actions,
                         facing=False)
    assert set(row) == {"BET 9.000000"}
    assert sum(row.values()) == pytest.approx(1.0)
    assert moved == pytest.approx(1.0)


def test_pricing_one_row_changes_only_that_decision():
    """The whole point: the two arms differ in exactly one row.

    Priced against the same tree, the same opponent and the same
    continuation, so the difference cannot be the model preferring
    itself - which is what M244 measured happening (corr -0.745) when a
    disagreement was priced inside the model suspected of causing it.
    """
    tree = _action(HERO, ["CHECK", "BET 8.000000"], {"KhKd": [1.0, 0.0]},
                   children={
                       "CHECK": _action(VILLAIN, ["CHECK"],
                                        {c: [1.0] for c in VILLAIN_COMBOS},
                                        children={"CHECK": _chance()}),
                       "BET 8.000000": _action(
                           VILLAIN, ["CALL"],
                           {c: [1.0] for c in VILLAIN_COMBOS},
                           children={"CALL": _chance()})})
    walk = _walk([0.75, 0.75])
    board = ("8h", "6h", "2s", "Td")
    reach = _reach(1.0, 1.0)

    checking = walk.price_row(tree, board, reach, {"CHECK": 1.0})
    betting = walk.price_row(tree, board, reach, {"BET 8.000000": 1.0})
    assert checking == pytest.approx(0.75 * POT)
    assert betting == pytest.approx(0.75 * (POT + 16.0) - 8.0)
    assert betting > checking, "at 75% equity, betting should gain"

    half = walk.price_row(tree, board, reach,
                          {"CHECK": 0.5, "BET 8.000000": 0.5})
    assert half == pytest.approx((checking + betting) / 2.0)


def test_descending_carries_villains_reach_with_it():
    """Pricing a turn decision against a UNIFORM range prices it against
    an opponent who never made the bets that got there.

    One villain combo always bets and the other always checks. After the
    bet, only the bettor remains - and a walk that forgets this prices
    the turn against both, which is a different game and flatters or
    damns our row depending on the line.
    """
    turn = _action(HERO, ["CHECK"], {"KhKd": [1.0]},
                   children={"CHECK": _chance()})
    tree = _action(VILLAIN, ["CHECK", "BET 4.000000"],
                   {"AsAd": [0.0, 1.0], "7c2h": [1.0, 0.0]},
                   children={"BET 4.000000": _action(
                       HERO, ["CALL"], {"KhKd": [1.0]},
                       children={"CALL": _chance({"Td": turn})})})
    walk = _walk([0.2, 0.8])
    got = walk.descend(tree, ("8h", "6h", "2s"), _reach(1.0, 1.0),
                       ["BET 4.000000", "CALL", "Td"])
    assert got is not None
    node, board, reach, street_in, total_in = got
    assert list(reach) == [1.0, 0.0], (
        "only the combo that actually bets can be here"
    )
    assert board == ("8h", "6h", "2s", "Td")
    assert total_in == (4.0, 4.0), "the called bet carried into the pot"
    assert street_in == (0.0, 0.0), "a new street starts with nothing in"


def test_a_runout_zeroes_the_combos_it_blocks_on_the_way_down():
    """The turn card is a card nobody can also be holding."""
    turn = _action(HERO, ["CHECK"], {"KhKd": [1.0]},
                   children={"CHECK": _chance()})
    tree = _action(VILLAIN, ["CHECK"],
                   {c: [1.0] for c in VILLAIN_COMBOS},
                   children={"CHECK": _chance({"As": turn})})
    walk = _walk([0.5, 0.5])
    _n, _b, reach, _s, _t = walk.descend(
        tree, ("8h", "6h", "2s"), _reach(1.0, 1.0), ["CHECK", "As"])
    assert list(reach) == [0.0, 1.0], "AsAd cannot be held once As is dealt"


def test_a_path_that_does_not_exist_is_refused_rather_than_guessed():
    """A silently wrong node is the expensive failure here."""
    tree = _action(VILLAIN, ["CHECK"], {c: [1.0] for c in VILLAIN_COMBOS},
                   children={"CHECK": _chance()})
    walk = _walk([0.5, 0.5])
    assert walk.descend(tree, ("8h", "6h", "2s"), _reach(1.0, 1.0),
                        ["BET 9.000000"]) is None


def test_pricing_the_reference_row_reproduces_the_full_walk():
    """The strongest identity available, and it was found by accident.

    `price_row` handed the row the dump already holds must return exactly
    what `value` returns walking the same node - they are the same
    computation reached two ways. It catches any disagreement between the
    two code paths, which is where a pricing study's arms would silently
    diverge: one arm reads our row through `price_row` and the other is
    compared against a number from `value`.
    """
    tree = _action(HERO, ["CHECK", "BET 8.000000"], {"KhKd": [0.3, 0.7]},
                   children={
                       "CHECK": _action(VILLAIN, ["CHECK"],
                                        {c: [1.0] for c in VILLAIN_COMBOS},
                                        children={"CHECK": _chance()}),
                       "BET 8.000000": _action(
                           VILLAIN, ["CALL", "FOLD"],
                           {"AsAd": [0.6, 0.4], "7c2h": [0.1, 0.9]},
                           children={"CALL": _chance()})})
    walk = _walk([0.45, 0.62])
    board = ("8h", "6h", "2s", "Td")
    reach = walk.initial_reach(board)

    row = strategy_at(tree)["KhKd"]
    assert walk.price_row(tree, board, reach, row) == pytest.approx(
        walk.value(tree, board, reach), abs=1e-12), (
        "price_row and value disagree on the same row, so a pricing study "
        "would be comparing two different computations"
    )
