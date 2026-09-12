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


#: Rank order, low to high, for naming a class the way a params file does.
RANKS = "23456789TJQKA"


def class_label(combo):
    """The label a solver params file uses for this combo: AA, AKs, 72o.

    Takes the TEXT of a combo, because a dump names its rows that way and
    `Walk.villain` carries those names rather than engine objects.
    """
    text = str(combo)
    (rank_a, suit_a), (rank_b, suit_b) = text[0:2], text[2:4]
    if RANKS.index(rank_a) < RANKS.index(rank_b):
        rank_a, rank_b, suit_a, suit_b = rank_b, rank_a, suit_b, suit_a
    if rank_a == rank_b:
        return rank_a + rank_b
    return rank_a + rank_b + ("s" if suit_a == suit_b else "o")


def parse_params_ranges(text):
    """`{'oop': {label: weight}, 'ip': {...}}` from a solver params file.

    The dump does not carry the ranges - its top level is only actions,
    childrens, node_type, player and strategy - so the weights the solve
    was given have to come from the params written beside it. Without
    them a walk silently prices against a uniform range (F59).
    """
    out = {}
    for line in text.splitlines():
        for key, name in (("set_range_oop", "oop"), ("set_range_ip", "ip")):
            if line.startswith(key):
                weights = {}
                for item in line[len(key):].strip().split(","):
                    item = item.strip()
                    if not item:
                        continue
                    label, _, weight = item.partition(":")
                    weights[label.strip()] = float(weight) if weight else 1.0
                out[name] = weights
    return out


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

    def initial_reach(self, board, weights=None):
        """Villain's reach at the root: the RANGE, with dead combos out.

        **`weights=None` means a uniform range, and that is almost never
        the game the reference solved (F59, M257).** The solver is handed
        a weighted range - a real one reads
        `JJ:0.6592, AA:0.2375, ... 88:0.000908`, spanning **700x** - and
        its strategy is an equilibrium against THOSE weights. Scoring it
        against a flat range prices every decision against an opponent it
        never played, and the damage is invisible to a conservation
        control, because `EV(h|v) + EV(v|h) == pot` holds for any reach
        weights so long as both sides use the same ones. M219's dead
        guard again: an assertion that cannot distinguish the two things
        it compares.

        Measured: the reference's own rows scored **2.1-5.0 bb of
        apparent per-hand slack on a 33bb pot** against a uniform range,
        where a solve converged to 0.19-0.49% of pot should show ~0.1 bb.
        Pass `combo_weights(...)` built from the params file.
        """
        dead = {str(c) for c in board}
        dead |= {self.hero_key[0:2], self.hero_key[2:4]}
        reach = np.ones(len(self.villain), dtype=np.float64)
        for i, villain in enumerate(self.villain):
            text = str(villain)
            if {text[0:2], text[2:4]} & dead:
                reach[i] = 0.0
            elif weights is not None:
                reach[i] = weights.get(class_label(villain), 0.0)
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

    def descend(self, node, board, reach, path):
        """Follow `path` from `node`, carrying villain's reach with it.

        Returns `(node, board, reach, street_in, total_in)` at the end of
        the path, or None if it does not exist in this tree.

        **The reach is the point.** Pricing a turn decision against a
        UNIFORM villain range prices it against an opponent who never
        made the flop bets that got here - a different game, and one that
        flatters or damns our row depending on the line. Each villain
        action multiplies their reach by the frequency they take it; each
        runout zeroes the combos it blocks.
        """
        street_in, total_in = (0.0, 0.0), (0.0, 0.0)
        for step in path:
            kind = node.get("node_type")
            if kind == CHANCE:
                kids = children_of(node)
                if step not in kids:
                    return None
                reach = reach.copy()
                reach[self._blocks(step)] = 0.0
                board = tuple(board) + (step,)
                node = kids[step]
                street_in, total_in = (0.0, 0.0), total_in
                continue
            if kind != ACTION:
                return None
            kids = children_of(node)
            if step not in kids:
                return None
            player = node.get("player")
            kind_a, amount = parse_action(step)
            if player != self.hero_index:
                rows = strategy_at(node)
                weights = np.array(
                    [(rows.get(c) or {}).get(step, 0.0) for c in self.villain],
                    dtype=np.float64)
                reach = reach * weights
            new_street = list(street_in)
            if kind_a == CALL:
                new_street[player] = max(street_in)
            elif kind_a in ("BET", "RAISE"):
                new_street[player] = float(amount)
            child = kids[step]
            if child.get("node_type") == CHANCE:
                total_in = tuple(total_in[i] + new_street[i] for i in (0, 1))
                street_in = (0.0, 0.0)
            else:
                street_in = tuple(new_street)
            node = child
        return node, board, reach, street_in, total_in

    def action_values(self, node, board, reach, street_in=(0.0, 0.0),
                      total_in=(0.0, 0.0)):
        """Hero's EV for EACH action here, the reference's line below.

        The quantity `price_row` needs and could not see. A row's EV is
        the average of these under that row's weights, so the best of
        them bounds every row at this node - including the reference's
        own.
        """
        out = {}
        for label in node.get("actions") or []:
            got = self._child_value(node, label, board, reach, street_in,
                                    total_in)
            if got is not None:
                out[label] = got
        return out

    def regret_of_row(self, node, board, reach, row, street_in=(0.0, 0.0),
                      total_in=(0.0, 0.0)):
        """`(regret, reference_slack, best_label)` - the honest version.

        **This exists because `price_row` measured two things at once and
        M257 caught it from an impossible number.** Scoring our row as
        `EV(reference row) - EV(our row)` assumes the reference's row is
        the best available answer at this node. Over a whole range it
        very nearly is - the solves here converge to 0.19-0.49% of pot -
        but that is an AVERAGE, and one hand's row can sit much further
        from its own best response. Measured: a four-bet pot priced
        `8d8c` at **-2.36 bb with 0.2% of mass remapped**, i.e. our row
        beating the reference's inside the reference's own game, which a
        converged row makes impossible. What it means is that the
        reference had not converged FOR THAT HAND.

        So the difference of two rows is our error MINUS the reference's
        own per-hand slack, and a negative result says nothing about us.

        `regret = max_a Q(a) - EV(our row)` is **non-negative by
        construction** and depends on the reference only through the
        game it defines, not through how well it solved one hand.
        `reference_slack` is the same quantity for the reference's own
        row: it measures the instrument, and a row whose slack is the
        size of our regret is a row this dump cannot resolve.
        """
        values = self.action_values(node, board, reach, street_in, total_in)
        if not values:
            return None
        best_label = max(values, key=values.get)
        best = values[best_label]
        ours = self.price_row(node, board, reach, row, street_in, total_in)
        if ours is None:
            return None
        reference = strategy_at(node).get(self.hero_key)
        slack = None
        if reference:
            ref_ev = self.price_row(node, board, reach, reference, street_in,
                                    total_in)
            if ref_ev is not None:
                slack = best - ref_ev
        return best - ours, slack, best_label

    def price_row(self, node, board, reach, row, street_in=(0.0, 0.0),
                  total_in=(0.0, 0.0)):
        """Hero's EV playing `row` HERE, and the reference's line below.

        This is the measurement M237 and M244 could not make. Both tried
        to price a disagreement using the model suspected of causing it,
        and a model always prefers its own answer - M244 measured that
        directly at corr -0.745, with a control where the two already
        agreed costing a median of exactly zero. Substituting only the
        row under test, and taking the tree, the opponent and every
        continuation from the reference, removes it: the two arms differ
        in exactly one decision.
        """
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


def map_row(our_row, dump_actions, facing):
    """Our engine's strategy row, expressed in the dump's action labels.

    The two trees offer different bet sizes, so a row cannot be compared
    action-for-action; it has to be remapped, and how much mass had to
    move is part of the result rather than an implementation detail
    (M209's convention). A size maps to the closest one the reference
    offers; fold and the passive action map by kind.

    Returns `(row, remapped_mass)` where `remapped_mass` is the share of
    our row that landed on an action whose size differs from ours.
    """
    sized = []
    passive = None
    fold = None
    for label in dump_actions:
        kind, amount = parse_action(label)
        if kind == FOLD:
            fold = label
        elif kind in (CHECK, CALL):
            passive = label
        else:
            sized.append((label, amount))

    out, moved = {}, 0.0
    for action, weight in our_row.items():
        weight = float(weight)
        if weight <= 0:
            continue
        name = str(action)
        if name == "fold":
            target = fold if fold is not None else passive
        elif name.startswith("all_in") or name.startswith("raise"):
            if not sized:
                target = passive
            else:
                want = float(name.split(":")[1]) if ":" in name else None
                if want is None:
                    target = sized[0][0]
                else:
                    target = min(sized, key=lambda pair: abs(pair[1] - want))[0]
                    if abs(dict(sized)[target] - want) > 1e-9:
                        moved += weight
        else:
            target = passive if passive is not None else (
                fold if facing else None)
        if target is None:
            continue
        out[target] = out.get(target, 0.0) + weight
    total = sum(out.values())
    if total > 0:
        out = {k: v / total for k, v in out.items()}
    return out, (moved / total if total else 0.0)


def realisation(ev, equity, pot0):
    """How much of `equity * pot0` the hand actually collected."""
    benchmark = equity * pot0
    if not benchmark or math.isclose(benchmark, 0.0):
        return None
    return ev / benchmark
