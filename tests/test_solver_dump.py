"""Reading a solver dump past the flop.

M255. These exist because a walker that follows only `childrens` reports
a 24 MB two-round dump as 47 nodes with no turn — which is what happened
on first contact, and is one layer away from the reason M237's blocker
stood unexamined for fourteen milestones.

Fixtures are synthetic and tiny: the shape is what matters, and a test
that needed the 24 MB artefact would not run.
"""
from bench.solver_dump import (
    children_of,
    normalise_combo,
    populated_rounds,
    strategy_at,
    walk,
)


def _action(player, actions, rows, children=None):
    return {"node_type": "action_node", "player": player, "actions": actions,
            "strategy": {"actions": actions, "strategy": rows},
            "childrens": children or {}}


def _chance(dealcards=None):
    node = {"node_type": "chance_node", "deal_number": 52}
    if dealcards is not None:
        node["dealcards"] = dealcards
    return node


TURN = _action(1, ["CHECK", "BET 5.000000"], {"6c5c": [0.72, 0.28]})
FLOP_ONE_ROUND = _action(
    1, ["CHECK", "BET 3.000000"], {"AhKh": [0.4, 0.6]},
    children={"BET 3.000000": _action(0, ["CALL", "FOLD"],
                                      {"AhKh": [0.9, 0.1]},
                                      children={"CALL": _chance()})})
FLOP_TWO_ROUNDS = _action(
    1, ["CHECK", "BET 3.000000"], {"AhKh": [0.4, 0.6]},
    children={"BET 3.000000": _action(
        0, ["CALL", "FOLD"], {"AhKh": [0.9, 0.1]},
        children={"CALL": _chance({"2c": TURN, "2d": TURN})})})


def test_a_chance_node_hides_its_subtree_under_dealcards():
    """The trap this module exists for.

    Action nodes file children under `childrens` and chance nodes under
    `dealcards`. A walk that knows only the first sees a two-round dump
    as a one-round one — silently, with no error and a plausible node
    count.
    """
    chance = FLOP_TWO_ROUNDS["childrens"]["BET 3.000000"]["childrens"]["CALL"]
    assert chance["node_type"] == "chance_node"
    assert "childrens" not in chance, (
        "this fixture must reproduce the real shape: the turn lives under "
        "dealcards and nowhere else"
    )
    assert set(children_of(chance)) == {"2c", "2d"}

    naive = len([1 for _p, _n in walk(FLOP_ONE_ROUND)])
    full = len([1 for _p, _n in walk(FLOP_TWO_ROUNDS)])
    assert full > naive, (
        "the two-round dump must yield more nodes than the one-round one; "
        "equal counts are exactly how this defect presents"
    )


def test_it_reports_the_rounds_actually_in_the_file():
    """What was requested and what landed are different things.

    A three-round dump that ran out of disk is indistinguishable from a
    two-round one by its filename, and identical by any check that trusts
    the parameter used to request it.
    """
    assert populated_rounds(FLOP_ONE_ROUND) == 1
    assert populated_rounds(FLOP_TWO_ROUNDS) == 2
    assert populated_rounds(_chance()) == 1
    assert populated_rounds({}) == 1


def test_a_strategy_row_is_zipped_against_the_action_list():
    """Frequencies are stored positionally, not as a mapping.

    Reading them as a dict of their own returns nothing; reading them in
    the wrong order returns a confident wrong answer, which is worse.
    """
    rows = strategy_at(TURN)
    assert rows == {"6c5c": {"CHECK": 0.72, "BET 5.000000": 0.28}}
    assert strategy_at(_chance()) == {}
    assert strategy_at({"strategy": {"actions": ["CHECK"], "strategy": []}}) == {}

    turn = next(n for _p, n in walk(FLOP_TWO_ROUNDS)
                if n.get("node_type") == "action_node"
                and "BET 5.000000" in (n.get("actions") or []))
    assert abs(sum(strategy_at(turn)["6c5c"].values()) - 1.0) < 1e-9


def test_combo_keys_are_matched_order_independently():
    """The solver writes its own card order, and a mismatched spelling
    finds nothing silently — the failure M174 recorded."""
    assert normalise_combo("Kh6h") == normalise_combo("6hKh")
    assert normalise_combo("AsAd") == normalise_combo("AdAs")
    assert normalise_combo("Kh6h") != normalise_combo("Kh6s")
