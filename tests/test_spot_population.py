"""The spot generator a benchmark asks its questions with.

M252. These tests exist because the previous generator's blind spot cost
a real defect: M251 found a multiway pot folded down to two telling 72o
to call a 4-bet 97% of the time, at a node no session could reach,
guarded by a threshold set just below the observed value.
"""
import random

import pytest

from bench.spot_population import (
    DEFAULT_RAISE_WEIGHT,
    SEATS,
    closing_path,
    preflop_walk,
)
from poker_solver.game_tree import (
    DecisionNode,
    GameConfig,
    StreetConfig,
    TerminalNode,
    build_game_tree,
    build_street_tree,
)


def _walk(path, players, stack_bb=100.0):
    """Replay an action-kind path against a fresh tree."""
    node = build_game_tree(GameConfig(positions=SEATS[players], stack_bb=stack_bb))
    for kind in path:
        action = next((a for a in node.legal_actions if a.kind == kind), None)
        assert action is not None, (
            f"{kind!r} is not legal here — the walk produced an illegal path, "
            "which is the whole thing walking the real tree is meant to prevent"
        )
        node = node.children[action]
    return node


@pytest.mark.parametrize("players", [2, 3, 6])
def test_every_path_the_walk_returns_is_legal(players):
    """Legal by construction, not by hoping.

    Paths assembled from action NAMES can be illegal in ways only the
    tree knows (a raise past the cap, an action after the round closed),
    and an illegal path is a 422 the benchmark records as a skip — a
    spot silently dropped rather than measured.
    """
    rng = random.Random(4242)
    for _ in range(25):
        walk = preflop_walk(rng, players, 100.0)
        for path in walk.decision_paths:
            assert isinstance(_walk(path, players), DecisionNode), (
                f"{path} was offered as a decision node and is not one"
            )
        if walk.closed_path is not None:
            node = _walk(walk.closed_path, players)
            assert isinstance(node, TerminalNode), (
                f"{walk.closed_path} was offered as closed and is not terminal"
            )
            live = sum(1 for p in SEATS[players] if p not in node.folded)
            assert live >= 2, (
                "a hand that folded out is not a postflop spot and must not "
                "be offered as one"
            )
            assert live == walk.live_players


def test_the_walk_reaches_depths_the_old_generator_could_not():
    """The reason this module exists.

    Every session in this project drew preflop action from a list whose
    deepest entry held ONE raise, so preflop advice was never asked for
    at a 3-bet node — the depth M251's defect lives at. This asserts the
    walk produces 3-bet AND 4-bet nodes, and pots folded down to two.
    """
    rng = random.Random(7)
    depths, two_live = [], 0
    for _ in range(120):
        walk = preflop_walk(rng, 6, 100.0)
        for path in walk.decision_paths:
            raises = sum(1 for k in path if k == "raise")
            depths.append(raises)
            if raises >= 3:
                node = _walk(path, 6)
                if sum(1 for p in SEATS[6] if p not in node.folded) == 2:
                    two_live += 1

    assert max(depths) >= 3, (
        f"deepest line held {max(depths)} raises — the generator cannot "
        "reach a 4-bet, which is where M251's defect lives"
    )
    assert sum(1 for d in depths if d >= 2) > 0, "no 3-bet node generated"
    assert two_live > 0, (
        "no pot folded down to two while facing a re-raise — M251's exact "
        "cell, and the one the old generator could not produce"
    )


def test_raising_more_often_is_what_reaches_the_deep_nodes():
    """The weight is load-bearing, not a tuning knob.

    An unweighted walk folds out early: this pins that the default
    actually changes the population, so a future "simplify" that drops
    it fails rather than silently shrinking what the benchmark covers.
    """
    def deepest(weight):
        rng = random.Random(11)
        return max(sum(1 for k in path if k == "raise")
                   for _ in range(80)
                   for path in preflop_walk(rng, 6, 100.0,
                                            raise_weight=weight).decision_paths)

    assert deepest(DEFAULT_RAISE_WEIGHT) >= deepest(0.05) + 1, (
        "weighting raises up does not deepen the population, so the "
        "default is doing nothing"
    )


def test_closing_a_street_takes_one_action_per_live_player():
    """The bug every session in this project carried.

    They closed a street with a hardcoded `["call_or_check"] * 2`, which
    is right only heads-up. Three-handed it leaves the betting open and
    the API refuses to deal the next card — so multiway turn and river
    spots were dropped as 422s rather than measured.
    """
    assert closing_path(2) == ["call_or_check"] * 2
    assert closing_path(3) == ["call_or_check"] * 3
    with pytest.raises(ValueError):
        closing_path(1)

    # And the claim itself, against a real street tree.
    config = StreetConfig(positions=("BTN", "SB", "BB"), pot=9.0, stack_bb=97.0,
                          raise_sizes=(0.75,), max_raises=2)
    node = build_street_tree(config)
    for _ in range(2):
        action = next(a for a in node.legal_actions if a.kind == "call_or_check")
        node = node.children[action]
    assert isinstance(node, DecisionNode), (
        "two checks closed a three-handed street — then the old harness "
        "was right and this module is solving a problem that does not exist"
    )
    action = next(a for a in node.legal_actions if a.kind == "call_or_check")
    assert isinstance(node.children[action], TerminalNode), (
        "three checks did not close a three-handed street"
    )
