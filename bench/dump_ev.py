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


#: How a chance node combines its cards. "equal" is what every figure
#: published off a dump used; "reach" is the derivation in `Walk._chance`
#: and needs the turn bank re-run before it can be adopted (A12/M275).
DEFAULT_CHANCE_WEIGHTING = "equal"


class LeafEquity:
    """Hero's exact equity against each villain combo, cached per board.

    A two-round dump revisits the same turn card under every flop line,
    and each table is an enumeration rather than a lookup, so caching is
    what makes the walk affordable.

    **A12: the FLOP leaf used to be sampled, and it was the walk's own
    error floor.** A leaf on a three-card board - what an all-in on the
    flop reaches - fell through to `build_board_equity_table`, which
    Monte-Carlos two cards to come (M154: only flop tables are sampled).
    Turn and river leaves were exact, so the walk was exact exactly where
    it was cheap and sampled where the money was.

    It showed up in M273 as slack the solver could not explain: five
    four-bet flop spots passed `dump_control` against a 0.33% reference
    and three FAILED against a 0.042% one, because the walk's own slack
    barely moved (0.8745% -> 0.8022% of pot) while the solver's figure
    improved eightfold. The boards that failed were the ones where the
    reference actually BETS, i.e. the ones whose lines reach a flop
    all-in; boards where it never bets scored 0.000 either way.

    Enumerating both cards is 1,081 runouts against a turn's 46, and it
    is paid ONCE per (hero, board) because a flop dump has exactly one
    flop board.
    """

    def __init__(self, hero, villain_combos, equity_fn=None):
        self._hero = hero
        self._villain = list(villain_combos)
        self._cache = {}
        self._equity_fn = equity_fn
        self._arrays = None

    def _villain_arrays(self):
        if self._arrays is None:
            from poker_solver.board_equity import _SUIT_INDEX
            values = np.array([[c.card_a.value, c.card_b.value]
                               for c in self._villain], dtype=np.int64)
            suits = np.array([[_SUIT_INDEX[c.card_a.suit], _SUIT_INDEX[c.card_b.suit]]
                              for c in self._villain], dtype=np.int64)
            names = [(str(c.card_a), str(c.card_b)) for c in self._villain]
            self._arrays = (values, suits, names)
        return self._arrays

    def _hero_row(self, cards):
        """Hero's exact equity against every villain, hero's row alone.

        Handles a river (nothing to come), a turn (one card) and - since
        A12 - a FLOP (two cards, 1,081 runouts enumerated).

        M260. The general path builds the whole (N+1) x (N+1) table and
        keeps row 0, which is O(N^2) per board; a turn dump walks ~46
        river boards per leaf, and at a 140-class pool that made one turn
        row cost several minutes. Equal to the table's row exactly
        (`test_the_hero_row_equals_the_full_table`): the same deck, the
        same enumeration, the same tie convention, NaN where hands
        collide.
        """
        from poker_solver.board_equity import _SUIT_INDEX
        from poker_solver.cards import Card
        from poker_solver.hand_eval import best_hand_rank_batch

        values, suits, names = self._villain_arrays()
        hero = (self._hero.card_a, self._hero.card_b)
        hero_names = {str(c) for c in hero}
        board_names = {str(c) for c in cards}
        deck = [Card.from_str(r + s) for r in "23456789TJQKA" for s in "shdc"
                if r + s not in board_names and r + s not in hero_names]
        if len(cards) == 5:
            runouts = [()]
        elif len(cards) == 4:
            runouts = [(c,) for c in deck]
        else:
            # A12: the flop leaf, enumerated rather than sampled. See
            # `vector` for why this exists and what it was costing.
            runouts = [(deck[i], deck[j])
                       for i in range(len(deck)) for j in range(i + 1, len(deck))]
        n, k = len(self._villain), len(runouts)
        width = len(cards) + len(runouts[0])
        full = [tuple(cards) + run for run in runouts]
        b_values = np.array([[c.value for c in b] for b in full], dtype=np.int64)
        b_suits = np.array([[_SUIT_INDEX[c.suit] for c in b] for b in full],
                           dtype=np.int64)
        h_values = np.concatenate(
            [np.tile([[c.value for c in hero]], (k, 1)), b_values], axis=1)
        h_suits = np.concatenate(
            [np.tile([[_SUIT_INDEX[c.suit] for c in hero]], (k, 1)), b_suits], axis=1)
        hero_scores = best_hand_rank_batch(h_values, h_suits)             # (k,)
        v_values = np.concatenate(
            [np.repeat(values[:, None, :], k, axis=1),
             np.broadcast_to(b_values, (n, k, width))], axis=2).reshape(n * k, width + 2)
        v_suits = np.concatenate(
            [np.repeat(suits[:, None, :], k, axis=1),
             np.broadcast_to(b_suits, (n, k, width))], axis=2).reshape(n * k, width + 2)
        v_scores = best_hand_rank_batch(v_values, v_suits).reshape(n, k)
        run_names = [{str(c) for c in run} for run in runouts]
        valid = np.ones((n, k), dtype=bool)
        row = np.full(n, np.nan)
        for i, (a, b) in enumerate(names):
            if a in hero_names or b in hero_names or a in board_names or b in board_names:
                valid[i, :] = False
                continue
            for j, r in enumerate(run_names):
                if a in r or b in r:
                    valid[i, j] = False
        share = np.where(hero_scores[None, :] > v_scores, 1.0,
                         np.where(hero_scores[None, :] == v_scores, 0.5, 0.0))
        counts = valid.sum(axis=1)
        live = counts > 0
        row[live] = (share * valid).sum(axis=1)[live] / counts[live]
        return row

    def vector(self, board):
        key = tuple(str(c) for c in board)
        if key in self._cache:
            return self._cache[key]
        if self._equity_fn is not None:
            row = np.asarray(self._equity_fn(board), dtype=np.float64)
        elif len(board) >= 3:
            from poker_solver.cards import Card
            cards = tuple(Card.from_str(c) if isinstance(c, str) else c
                          for c in board)
            row = self._hero_row(cards)
        else:
            raise ValueError(
                "a leaf board must have 3, 4 or 5 cards, got %d" % len(board))
        self._cache[key] = row
        return row


class Walk:
    """One hero hand's EV over one dump."""

    def __init__(self, *, hero_index, hero_key, villain_combos, leaf, pot0,
                 chance_weighting=DEFAULT_CHANCE_WEIGHTING):
        if chance_weighting not in ("equal", "reach"):
            raise ValueError("chance_weighting must be 'equal' or 'reach', got %r"
                             % (chance_weighting,))
        self.chance_weighting = chance_weighting
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
        # A12 (M275). Cards are NOT equally likely once villain's
        # blockers are taken out. Given hero's hand and ONE villain hand
        # the next card is uniform over 45, and summing over villain
        # hands makes P(card) proportional to the villain reach that
        # SURVIVES it, since each villain hand blocks two of the 47
        # candidates: sum_c sub(c) = 45 * sum_v reach(v), so the outer
        # combination is that weighted average and not a plain mean.
        # `_villain_node` already makes this argument for actions.
        #
        # **Derived, and DEFAULT OFF.** It is M161's situation: turning
        # it on changes every figure ever taken off a dump with a chance
        # node in it - the turn bank behind M260's grades and the
        # turn-shove price - and those dumps are deleted, so re-deriving
        # them is ~40 minutes a spot. Adopting it is a measurement, not a
        # tidy-up; until that is run, the shipped default reproduces what
        # every published figure used.
        #
        # It is also NOT the four-bet control failure it was written to
        # explain: on that board the reported slack went 0.80% -> 1.08%
        # of pot, i.e. the wrong way. Conservation cannot referee it
        # either (F59's shape) - both sides use the same weights, so
        # `EV(h|v) + EV(v|h) == pot` holds under either convention.
        values, weights = [], []
        for card, child in kids.items():
            if card in seen:
                continue
            sub = reach.copy()
            sub[self._blocks(card)] = 0.0
            mass = float(sub.sum())
            if mass <= 0:
                continue
            got = self.value(child, tuple(board) + (card,), sub,
                             (0.0, 0.0), total_in)
            if got is not None:
                values.append(got)
                weights.append(mass)
        if not values:
            return None
        if self.chance_weighting == "reach":
            return float(np.average(values, weights=weights))
        return float(np.mean(values))

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
                      total_in=(0.0, 0.0), only=None):
        """Hero's EV for EACH action here, the reference's line below.

        The quantity `price_row` needs and could not see. A row's EV is
        the average of these under that row's weights, so the best of
        them bounds every row at this node - including the reference's
        own.
        """
        out = {}
        for label in node.get("actions") or []:
            if only is not None and label not in only:
                continue
            got = self._child_value(node, label, board, reach, street_in,
                                    total_in)
            if got is not None:
                out[label] = got
        return out

    def action_support(self, node):
        """How much of the range takes each action here.

        The solver trains a branch in proportion to how much reach
        arrives at it, so an action almost nobody takes is an action
        almost nothing trained. This is the weight `regret_of_row` uses
        to decide which actions are worth maximising over.
        """
        rows = strategy_at(node)
        if not rows:
            return {}
        out = {}
        for label in node.get("actions") or []:
            total = sum(float((r or {}).get(label, 0.0)) for r in rows.values())
            out[label] = total / len(rows)
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

        Returns `(regret, reference_slack, best_label, unsupported_mass)`,
        where the last is the share of our row that had to be dropped for
        the two arms to share a support. A large value means we are
        playing lines the reference does not, and the figure says more
        about that than about our accuracy.
        """
        # Evaluating a subtree is the expensive step, and the actions
        # excluded above are often the biggest ones (an all-in opens the
        # widest tree). Computing support FIRST and valuing only what
        # survives is both the correct scope and the cheap one.
        support = self.action_support(node)
        wanted = {k for k, v in support.items() if v >= MIN_ACTION_SUPPORT}
        values = self.action_values(node, board, reach, street_in, total_in,
                                    only=wanted or None)
        if not values:
            values = self.action_values(node, board, reach, street_in,
                                        total_in)
        if not values:
            return None
        # Maximise only over actions the reference ACTUALLY PLAYS. An
        # action nothing takes is an action nothing trained, and an
        # unrestricted max lands on it by construction - M258 measured a
        # walk calling a 6x-pot overbet shove the best action on 12 of 16
        # turn rows, worth 4.86 bb more than what a converged solver
        # does with pocket fives, because villain's response to a bet
        # nobody makes is the least-trained branch in the tree.
        best_label = max(values, key=values.get)
        best = values[best_label]
        # BOTH arms must sit on the same support, or the comparison is
        # incoherent: `price_row` values our row over whatever WE play,
        # and if that includes an action the max no longer covers, our EV
        # can exceed the max and regret goes negative - which it did,
        # at -0.0161 bb, on a flop cell (M258). Restricting our row to
        # the same actions restores `regret >= 0` by construction and
        # keeps the exclusion honest in both directions: we do not get
        # credit for a line valued against an untrained subtree either.
        eligible = set(values)
        kept = {k: v for k, v in row.items() if k in eligible and v > 0}
        moved = 1.0 - sum(kept.values()) / (sum(v for v in row.values()
                                                if v > 0) or 1.0)
        if not kept:
            # Our row lives entirely outside what the reference plays.
            # That is a real observation and not a regret; the caller's
            # gate should see it rather than a fabricated number.
            return None
        total = sum(kept.values())
        row = {k: v / total for k, v in kept.items()}
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
        return best - ours, slack, best_label, moved

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


#: An action taken by less than this share of the range is treated as
#: untrained and is not maximised over. Regret becomes "how far from the
#: best action the reference actually plays" rather than a true best
#: response - a deliberate trade, because an unrestricted max searches
#: for the least-trained subtree and its bias then scales with how big
#: that subtree is, which makes cells of different depths incomparable
#: (M258).
MIN_ACTION_SUPPORT = 0.01


#: Two sizes are the SAME action when they differ by less than this
#: share of the size. The solver rounds amounts to integers, so a 5%
#: band treats 37.5 and 38 as one action and 37.5 and 11 as two (F65).
SIZE_MATCH_TOLERANCE = 0.05


class BestResponseWalk(Walk):
    """Hero plays a BEST RESPONSE: the max over actions at every hero node.

    A13 (M276). The gain over hero's own row bounds every deviation,
    including the root-only one `regret_of_row` prices, so it is the
    measure of how exploitable a dumped strategy actually is - computed
    FROM the dump, and therefore not dependent on the solver's own
    reported figure being about the same object.

    That distinction is the finding. On the same four-bet flop spot:

    | solve | solver's figure | this walk | ratio |
    |---|---|---|---|
    | 100 iterations | 2.057% of pot | 1.677% | **0.82** |
    | 700 iterations | 0.054% | 1.463% | **27.3** |

    The dumped strategy barely improved while the reported number fell
    38x. On a RIVER dump - no chance node - the two agree (1.46x). So the
    reported exploitability stops describing the dumped strategy as a
    DEEP solve converges, and a control that divides by it gets stricter
    while the thing it is checking does not get better (M273's "passes
    loose, fails tight", explained).
    """

    def _hero_node(self, node, board, reach, street_in, total_in):
        best = None
        for label in node.get("actions") or []:
            got = self._child_value(node, label, board, reach, street_in, total_in)
            if got is not None and (best is None or got > best):
                best = got
        return best


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
    # A size counts as MOVED only when it differs materially. The solver
    # rounds its bet amounts to integers - a 15 bb pot offers BET 5 /
    # BET 11 / BET 38 / BET 92 where our tree names 4.95 / 11.25 / 37.5 /
    # 90 - so an exact comparison marks every action as remapped and the
    # figure measures rounding rather than any disagreement about sizing
    # (F65, M257). It read 0.82 at SPR 6.17 on menus that in fact match.

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
                    got = dict(sized)[target]
                    if abs(got - want) > SIZE_MATCH_TOLERANCE * max(want, 1e-9):
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
