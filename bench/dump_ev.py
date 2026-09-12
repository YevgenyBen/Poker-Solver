"""Hero's expected value under a reference solver's OWN strategy pair.

This is the machinery M237 concluded was unavailable. It is not: EV does
not have to come from the solver. The tree, both players' strategies and
exact showdown evaluation determine it, and **none of those three is
this engine's opinion** — the argument M202 used when it priced street
isolation, applied one layer up.

What it exists for: **equity realisation**. A hand with raw equity `e`
entering a pot of `P` collects `e * P` only if the money goes straight
to showdown. What it actually collects, playing streets out of position,
is different — and how different is the missing input that left M253's
R2 undecided, with the verdict flipping at a realisation factor around
0.87 and plausible values at 0.80-0.90.

    realisation = EV(hand, under the reference's equilibrium) / (e * P)

**The dump's conventions, pinned by arithmetic rather than assumed.**
On a pot of 10 with a 95 stack and bet sizes 33%/75%:

    root actions   CHECK, BET 3, BET 8, BET 95
                   33% of 10 = 3.3, 75% = 7.5, all-in = the whole stack
    facing BET 3   CALL, RAISE 13, RAISE 95
                   a 60% raise is 3 + 0.6 * (10 + 3 + 3) = 12.6
    after CALL     pot 16; the turn offers BET 5, BET 12, BET 92
                   33% of 16 = 5.3, 75% = 12, stack left 95 - 3 = 92

So an amount is what that player is committed to **for this street** — a
"to" total, not an increment; CALL matches the opponent's street total;
all-in is the remaining stack; FOLD has no child and is terminal.

**Every leaf lands on a four-card board**, because a two-round dump
populates every turn card, so the walk always reaches the river's chance
node with the turn already dealt. One card to come is enumerated EXACTLY
(M154) — there is no Monte Carlo anywhere in this walk, which is why the
passive control below can be asserted to machine precision.

**Reach is villain's alone.** Hero's own reach is deliberately not
carried: the question is what THIS hand is worth, not what the range is
worth. Villain's reach is unnormalised, and every value returned is
already conditional on it, so branches combine by their share of the
mass rather than by raw sums — getting that backwards was the first
version's bug and it is the kind that returns a plausible number.
"""
from __future__ import annotations

import math

import numpy as np

from bench.solver_dump import ACTION, CHANCE, children_of, strategy_at

FOLD = "FOLD"
CHECK = "CHECK"
CALL = "CALL"


def parse_action(label: str):
    """('CHECK'|'CALL'|'FOLD'|'BET'|'RAISE', amount or None)."""
    head, _, rest = label.partition(" ")
    head = head.upper()
    if head in (CHECK, CALL, FOLD):
        return head, None
    return head, float(rest)


class LeafEquity:
    """Hero's exact equity against each villain combo, cached per board.

    A two-round dump revisits the same turn card under every flop line,
    and each table is an enumeration rather than a lookup, so caching is
    what makes the walk affordable.
    """

    def __init__(self, hero, villain_combos, equity_fn=None):
        self._hero = hero
        self._villain = list(villain_combos)
        self._cache = {}
        self._equity_fn = equity_fn

    def vector(self, board):
        key = tuple(str(c) for c in board)
        if key in self._cache:
            return self._cache[key]
        if self._equity_fn is not None:
            row = np.asarray(self._equity_fn(board), dtype=np.float64)
        else:
            from poker_solver.board_equity import build_board_equity_table
            from poker_solver.cards import Card
            # A board grows by CARD NAMES taken from `dealcards` keys and
            # starts as Card objects, so it arrives mixed. Coerce here,
            # at the one boundary that cares, rather than making every
            # caller remember which half it is holding.
            cards = tuple(Card.from_str(c) if isinstance(c, str) else c
                          for c in board)
            table = build_board_equity_table(
                cards, [self._hero] + self._villain)
            row = np.asarray(table[0, 1:], dtype=np.float64)
        self._cache[key] = row
        return row


class Walk:
    """One hero hand's EV over one dump."""

    def __init__(self, *, hero_index, hero_key, villain_combos, leaf, pot0):
        self.hero_index = hero_index
        self.hero_key = hero_key
        self.villain = list(villain_combos)
        self.leaf = leaf
        self.pot0 = float(pot0)
        self.leaves = 0
        self.truncated = 0

    def initial_reach(self, board):
        """Villain's reach at the root, with impossible combos removed.

        A villain combo sharing a card with hero or with the board cannot
        be held, and starting it at weight 1 is not harmless: it carries
        through every villain decision, inflating the branch shares that
        combine hero's values, and is only dropped at the leaf where the
        equity is NaN. The distortion then varies by how many of
        villain's combos hero happens to block, which is different for
        every hand - so it moves each hand's EV by a different amount and
        looks like a finding.

        Measured before this existed: the walk manufactured **8.8% of the
        pot** on a symmetric control where the two players must split it
        exactly.
        """
        dead = {str(c) for c in board}
        dead |= {self.hero_key[0:2], self.hero_key[2:4]}
        reach = np.ones(len(self.villain), dtype=np.float64)
        for i, villain in enumerate(self.villain):
            text = str(villain)
            if {text[0:2], text[2:4]} & dead:
                reach[i] = 0.0
        return reach

    # -- terminals ------------------------------------------------------
    def _showdown(self, board, reach, total_in):
        pot = self.pot0 + total_in[0] + total_in[1]
        equity = self.leaf.vector(board)
        live = np.isfinite(equity) & (reach > 0)
        if not live.any():
            return None
        w = reach[live]
        share = float((w * equity[live]).sum() / w.sum())
        self.leaves += 1
        return share * pot - total_in[self.hero_index]

    def _fold(self, folder, street_in, total_in):
        """What a fold is worth to hero, counting THIS street's chips.

        A villain who bets and is then raised off the hand leaves that
        bet behind, so the folded money has to include the current
        street - reading only the carried-over total silently hands hero
        less than they won, and only in the lines where someone folds
        after committing, which is most of them.
        """
        committed = [total_in[i] + street_in[i] for i in (0, 1)]
        if folder == self.hero_index:
            return -committed[self.hero_index]
        return self.pot0 + committed[1 - self.hero_index]

    # -- the walk -------------------------------------------------------
    def value(self, node, board, reach, street_in=(0.0, 0.0),
              total_in=(0.0, 0.0)):
        kind = node.get("node_type")
        if kind == CHANCE:
            return self._chance(node, board, reach, total_in)
        if kind != ACTION:
            return None
        if node.get("player") == self.hero_index:
            return self._hero_node(node, board, reach, street_in, total_in)
        return self._villain_node(node, board, reach, street_in, total_in)

    def _chance(self, node, board, reach, total_in):
        kids = children_of(node)
        if not kids:
            # The dump stops here; the rest is decided by cards alone.
            self.truncated += 1
            return self._showdown(board, reach, total_in)
        # A runout cannot be a card already on the board OR in hero's
        # hand. Villain's blockers are handled by zeroing their reach
        # below, but hero's are not represented in the reach vector at
        # all, so leaving them in averages over cards that cannot come.
        #
        # It is invisible to a marginal check and fatal to an exact one:
        # hero and villain block different cards, so the two sides of a
        # conservation identity end up conditioned on different decks.
        # Measured before this line existed: 0.80% of the pot.
        seen = {str(c) for c in board} | {self.hero_key[0:2],
                                          self.hero_key[2:4]}
        values = []
        for card, child in kids.items():
            if card in seen:
                continue
            sub = reach.copy()
            sub[self._blocks(card)] = 0.0
            if sub.sum() <= 0:
                continue
            got = self.value(child, tuple(board) + (card,), sub,
                             (0.0, 0.0), total_in)
            if got is not None:
                values.append(got)
        return float(np.mean(values)) if values else None

    def _child_value(self, node, label, board, reach, street_in, total_in):
        """The value behind one action, whoever took it."""
        kind_a, amount = parse_action(label)
        player = node.get("player")
        if kind_a == FOLD:
            return self._fold(player, street_in, total_in)

        new_street = list(street_in)
        if kind_a == CALL:
            new_street[player] = max(street_in)
        elif kind_a in ("BET", "RAISE"):
            new_street[player] = float(amount)

        child = children_of(node).get(label)
        if child is None:
            # Nothing but FOLD should be childless; treat anything else as
            # a street that closed and let the cards decide, counting it so
            # a caller can see it happened.
            self.truncated += 1
            nt = tuple(total_in[i] + new_street[i] for i in (0, 1))
            return self._showdown(board, reach, nt)
        if child.get("node_type") == CHANCE:
            nt = tuple(total_in[i] + new_street[i] for i in (0, 1))
            return self.value(child, board, reach, (0.0, 0.0), nt)
        return self.value(child, board, reach, tuple(new_street), total_in)

    def _hero_node(self, node, board, reach, street_in, total_in):
        rows = strategy_at(node)
        row = rows.get(self.hero_key)
        if not row:
            return None
        total, mass = 0.0, 0.0
        for label, probability in row.items():
            if probability <= 0:
                continue
            got = self._child_value(node, label, board, reach, street_in,
                                    total_in)
            if got is None:
                continue
            total += probability * got
            mass += probability
        return total / mass if mass > 0 else None

    def _villain_node(self, node, board, reach, street_in, total_in):
        """Villain's branches combine by their SHARE of the reach mass.

        Every value returned below is already conditional on the reach it
        was computed with, so weighting by a raw sum double-counts. This
        is where the first version was wrong.
        """
        rows = strategy_at(node)
        parent = float(reach.sum())
        if parent <= 0:
            return None
        total, mass = 0.0, 0.0
        for label in (node.get("actions") or []):
            weights = np.array(
                [(rows.get(c) or {}).get(label, 0.0) for c in self.villain],
                dtype=np.float64)
            branch = reach * weights
            share = float(branch.sum())
            if share <= 0:
                continue
            got = self._child_value(node, label, board, branch, street_in,
                                    total_in)
            if got is None:
                continue
            total += share * got
            mass += share
        return total / mass if mass > 0 else None

    def _blocks(self, card):
        return np.array([card in str(c) for c in self.villain], dtype=bool)


def realisation(ev, equity, pot0):
    """How much of `equity * pot0` the hand actually collected."""
    benchmark = equity * pot0
    if not benchmark or math.isclose(benchmark, 0.0):
        return None
    return ev / benchmark
