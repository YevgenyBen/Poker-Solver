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

from bench.dump_ev import LeafEquity, Walk, parse_action, realisation

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
