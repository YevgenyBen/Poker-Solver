"""Preflop all-in equity between starting-hand classes.

Equity between two StartingHand classes is estimated via Monte Carlo
simulation: deal concrete (mutually distinct) hole cards for each class,
sample random 5-card boards from the rest of the deck, and score each
showdown with hand_eval.best_hand_rank.

This intentionally does not weight by exact combo-vs-combo blocker
effects between the two players' hands — each class is treated as a
single unit with one representative combo per matchup. That's a known,
documented approximation for a preflop-only solver (see the project plan).

The full 169x169 table is expensive to build from scratch (tens of
thousands of matchups); build_equity_table/get_equity_table are meant to
be run once and cached to disk, not recomputed on every solve.
"""

import hashlib
import os
import random
import threading
from collections import Counter
from pathlib import Path

import numpy as np

from .cards import SUITS, Card
from .hand_eval import best_hand_rank, best_hand_rank_batch
from .hand_utils import RANK_ORDER
from .starting_hands import StartingHand, all_starting_hands

DEFAULT_SAMPLES = 200
DEFAULT_SEED = 42
DEFAULT_CACHE_PATH = Path(__file__).parent / "data" / "preflop_equity.npy"

_ALL_HANDS = all_starting_hands()


def _suit_pairs_for(hand: StartingHand) -> list:
    """Candidate (suit_for_high, suit_for_low) pairs respecting `hand`.

    Pairs and offsuit hands need two different suits; suited hands need
    the same suit twice.
    """
    if hand.suited and not hand.is_pair:
        return [(suit, suit) for suit in SUITS]
    return [(s1, s2) for s1 in SUITS for s2 in SUITS if s1 != s2]


def deal_two_hands(hand_a: StartingHand, hand_b: StartingHand):
    """Pick 4 concrete, mutually-distinct cards for hand_a and hand_b.

    Two different starting-hand classes can share a rank (e.g. AKs vs
    AQo both contain an ace), so their representative cards must be
    chosen together to guarantee no physical card is used twice.
    """
    for suit_a in _suit_pairs_for(hand_a):
        card_a1 = Card(hand_a.high_rank, suit_a[0])
        card_a2 = Card(hand_a.low_rank, suit_a[1])
        for suit_b in _suit_pairs_for(hand_b):
            card_b1 = Card(hand_b.high_rank, suit_b[0])
            card_b2 = Card(hand_b.low_rank, suit_b[1])
            dealt = {card_a1, card_a2, card_b1, card_b2}
            if len(dealt) == 4:
                return (card_a1, card_a2), (card_b1, card_b2)
    raise RuntimeError(f"Could not deal distinct cards for {hand_a} vs {hand_b}")


def _remaining_deck(used: list) -> list:
    used_set = set(used)
    return [
        Card(rank, suit)
        for rank in RANK_ORDER
        for suit in SUITS
        if Card(rank, suit) not in used_set
    ]


def monte_carlo_equity(
    hand_a: StartingHand,
    hand_b: StartingHand,
    samples: int = DEFAULT_SAMPLES,
    rng: random.Random | None = None,
) -> float:
    """hand_a's all-in equity against hand_b, via random board runouts.

    Ties split the pot (0.5 each). Deterministic for a given `rng` seed.
    """
    rng = rng if rng is not None else random.Random(DEFAULT_SEED)
    (card_a1, card_a2), (card_b1, card_b2) = deal_two_hands(hand_a, hand_b)
    deck = _remaining_deck([card_a1, card_a2, card_b1, card_b2])

    wins = 0.0
    for _ in range(samples):
        board = rng.sample(deck, 5)
        rank_a = best_hand_rank([card_a1, card_a2, *board])
        rank_b = best_hand_rank([card_b1, card_b2, *board])
        if rank_a > rank_b:
            wins += 1.0
        elif rank_a == rank_b:
            wins += 0.5
    return wins / samples


def build_equity_table(
    hands: list | None = None,
    samples: int = DEFAULT_SAMPLES,
    seed: int = DEFAULT_SEED,
) -> np.ndarray:
    """Build an NxN equity table (hands[i]'s equity against hands[j]).

    Only the upper triangle is actually simulated — the lower triangle is
    filled in as 1 - equity by construction, so the result is exactly
    symmetric (equity(i, j) == 1 - equity(j, i)) and the diagonal is
    exactly 0.5.
    """
    hands = hands if hands is not None else _ALL_HANDS
    n = len(hands)
    table = np.full((n, n), 0.5, dtype=float)
    rng = random.Random(seed)
    for i in range(n):
        for j in range(i + 1, n):
            equity_ij = monte_carlo_equity(hands[i], hands[j], samples=samples, rng=rng)
            table[i, j] = equity_ij
            table[j, i] = 1.0 - equity_ij
    return table


_equity_table_cache_lock = threading.Lock()  # see get_equity_table's own docstring


def get_equity_table(
    cache_path: Path | str | None = None,
    hands: list | None = None,
    samples: int = DEFAULT_SAMPLES,
    seed: int = DEFAULT_SEED,
    force_rebuild: bool = False,
) -> np.ndarray:
    """Load the equity table from disk, building and caching it if needed.

    Building the full 169x169 table from scratch takes real time (tens of
    thousands of Monte Carlo matchups) — this is meant to be paid once,
    with subsequent calls served from the cached .npy file.

    Thread-safe against a real, already-live race (docs/full-table-
    diagnostic-2026-08.md's §3.10): `api/main.py` runs a background
    pre-warm thread that can call this concurrently with a live request
    (CLAUDE.md's M14 entry already measured real contention between the
    two) — on a cold cache (the file doesn't exist yet), both could
    previously reach `path.exists() == False`, both build the table, and
    both `np.save` to the *same* path, risking a caller reading a
    partially-written file if the two writes interleaved. Fixed two ways,
    doing different jobs: (1) `_equity_table_cache_lock` (module-level,
    process-wide) avoids the *redundant* rebuild — a second thread
    re-checks `path.exists()` after acquiring the lock, so only the
    thread that actually loses the race pays the (expensive) rebuild
    cost. (2) The write itself goes to a per-thread/per-process temp file
    in the same directory, then `os.replace`s it into place — atomic on
    both POSIX and Windows — so even a caller that reaches this function
    through some path that doesn't hold the lock (e.g. a future multi-
    *process* deployment; `threading.Lock` only protects threads within
    one process, a scope limitation stated plainly, not glossed over)
    can never observe a partially-written file: a reader either sees the
    old file or the new one, complete, never a mix. `force_rebuild=True`
    intentionally still takes the lock too, so a forced rebuild can't
    race a concurrent normal load either.
    """
    path = Path(cache_path) if cache_path is not None else DEFAULT_CACHE_PATH
    if not force_rebuild and path.exists():
        return np.load(path)
    with _equity_table_cache_lock:
        # Re-check now that we hold the lock — another thread may have
        # already built and written the table while we were waiting.
        if not force_rebuild and path.exists():
            return np.load(path)
        table = build_equity_table(hands=hands, samples=samples, seed=seed)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.parent / f"{path.stem}.tmp-{os.getpid()}-{threading.get_ident()}.npy"
        np.save(tmp_path, table)
        os.replace(tmp_path, path)
    return table


# ---------------------------------------------------------------------------
# N-way (multiway) equity.
#
# A full N-way equity table is never viable to precompute eagerly — the
# unique-matchup count explodes with N (169-choose-3-with-repeats alone
# is ~819k, versus ~14k for pairs), so multiway equity is instead
# computed lazily and cached per opponent-hand-tuple the first time a
# solve actually needs it — see MultiwayEquityCache below. This is also
# the mechanism a much larger N (6/9-max) will need regardless, since a
# full table is never an option there at all.
# ---------------------------------------------------------------------------

MULTIWAY_DEFAULT_SAMPLES = 200  # measured during M8: at 50 samples, equity
# noise for a matchup that should be an exact coinflip (e.g. a pocket
# pair against the identical pocket pair held by an opponent) got
# amplified by the all-in pot size into a large enough value error to
# visibly distort MCCFR's learned strategy — not just "a bit less
# precise". 200 (matching the pairwise DEFAULT_SAMPLES) fixed it in
# testing without meaningfully hurting solve speed, since this is a
# lazy per-matchup cost paid once per unique opponent combination
# actually encountered, not an eager one-time build.


def _provably_infeasible(hands: list, avoiding: frozenset) -> bool:
    """A cheap, O(N) *necessary* feasibility check for deal_n_hands, run
    before paying for the exponential backtracking search below.

    Only 4 physical cards of any given rank exist. A pair StartingHand
    demands 2 cards of one rank; a non-pair demands 1 card of each of
    two different ranks (suited vs. offsuit only changes which *suits*
    are compatible with each other, not how many cards of each rank are
    needed). If the total demand for some rank, summed across every
    hand, exceeds what's left of that rank once `avoiding` is
    subtracted from the usual 4, no assignment can possibly exist — by
    the pigeonhole principle alone, independent of any suit — so this
    is airtight: it can never call a truly-dealable (`hands`, `avoiding`)
    pair infeasible. Confirmed two ways during M27's design, not just
    assumed: the pigeonhole argument above, and an empirical 23,000-trial
    sweep (weighted-random hands, the same way MCCFR actually samples
    them) that produced zero false positives.

    This is necessary, not sufficient, by design: a (hands, avoiding)
    pair can pass this check and still be undealable for suit-only
    reasons — e.g. `avoiding` happens to strip every suit that a
    rank-available card would need to match (a concrete counterexample:
    a single suited A-K hand, with avoiding = {every ace but the spade,
    plus the spade king} — per-rank counts are fine, but the only
    remaining ace can't be paired with a same-suited king). That case
    is still caught correctly — it just falls through to the
    backtracking search below at full cost, exactly as if this check
    didn't exist, which is why that search stays in place rather than
    being replaced by this one.
    """
    demand: Counter = Counter()
    for hand in hands:
        if hand.is_pair:
            demand[hand.high_rank] += 2
        else:
            demand[hand.high_rank] += 1
            demand[hand.low_rank] += 1
    used_by_rank = Counter(card.rank for card in avoiding)
    return any(count > 4 - used_by_rank.get(rank, 0) for rank, count in demand.items())


def deal_n_hands(hands: list, avoiding: frozenset = frozenset(), rng: random.Random | None = None) -> list:
    """Pick concrete, mutually-distinct cards for N StartingHand classes,
    additionally avoiding any card already in `avoiding`.

    Generalizes deal_two_hands via backtracking (assign one hand's cards,
    filter the next hand's candidates against what's already used,
    recurse, backtrack on dead ends) — this stays correct, not just
    "usually works", as N grows toward 6/9-max later.

    `avoiding` lets a caller deal a *subset* of hands (e.g. just one
    candidate hand) around cards already committed elsewhere (e.g. a
    fixed set of opponents dealt separately) — see
    MultiwayEquityCache.traverser_equity_vector, which deals its fixed
    opponents once and reuses that instead of re-dealing them from
    scratch for every candidate hand it evaluates.

    `rng` (default `None`, every pre-existing call site unaffected —
    docs/full-table-diagnostic-2026-08.md's §3.5): when omitted, each
    hand's candidate suit-pairs are tried in `_suit_pairs_for`'s own
    fixed order, so a suited hand's first available suit is always the
    same one (confirmed: dealing several suited hands simultaneously
    with no `rng` deals every one of them the identical suit) —
    harmless for a feasibility check (the specific cards are discarded),
    but a real, systematic bias for a multiway equity computation that
    actually uses the dealt cards (distorts flush/chop frequency at
    7-9 handed, where several suited opponents are common). When `rng`
    is supplied, each hand's own suit-pair candidates are shuffled
    (a fresh copy, not `_suit_pairs_for`'s cached list) before the
    search tries them, so which suit "wins" varies — deterministically,
    given `rng`'s own seed — instead of collapsing to one suit every
    time. Callers that only care whether a deal exists at all (e.g.
    cfr.py's own opponent-feasibility checks) can safely omit `rng`;
    callers that use the actual dealt cards for equity (MultiwayEquity
    Cache) should supply their own already-seeded `rng`.

    Before searching, `_provably_infeasible` short-circuits the common
    case where some rank is simply overcommitted — see its own
    docstring. Measured during M27: this turns a search that can take
    up to ~63 seconds on a genuinely infeasible 8-hand input into a
    ~0.02ms immediate raise, without changing the result for any input.
    """
    if _provably_infeasible(hands, avoiding):
        raise RuntimeError(f"Could not deal distinct cards for {hands}")

    assignment = [None] * len(hands)

    def backtrack(index: int, used: set) -> bool:
        if index == len(hands):
            return True
        candidates = _suit_pairs_for(hands[index])
        if rng is not None:
            candidates = list(candidates)
            rng.shuffle(candidates)
        for suit_a, suit_b in candidates:
            card_a = Card(hands[index].high_rank, suit_a)
            card_b = Card(hands[index].low_rank, suit_b)
            if card_a == card_b or card_a in used or card_b in used:
                continue
            assignment[index] = (card_a, card_b)
            used.add(card_a)
            used.add(card_b)
            if backtrack(index + 1, used):
                return True
            used.discard(card_a)
            used.discard(card_b)
            assignment[index] = None
        return False

    if not backtrack(0, set(avoiding)):
        raise RuntimeError(f"Could not deal distinct cards for {hands}")
    return assignment


_SUIT_INDEX = {suit: i for i, suit in enumerate(SUITS)}


def _simulate_equity(dealt: list, samples: int, rng: random.Random) -> list:
    """Each already-dealt hand's all-in win-share (a k-way tie splits
    1/k), via random shared-board runouts. `dealt` is a list of
    (card_a, card_b) tuples, already guaranteed mutually distinct (see
    deal_n_hands) — this is the reusable "simulate" half of
    monte_carlo_equity_n, split out so a caller that deals hands its own
    way (see MultiwayEquityCache.traverser_equity_vector) doesn't have to
    re-deal everything through deal_n_hands to use it.

    The *ranking* of each sampled board is vectorized
    (hand_eval.best_hand_rank_batch) instead of calling the scalar
    best_hand_rank once per (hand, sample) pair — see hand_eval.py's
    module docstring for why: at N>=6 that per-call Python overhead was
    the dominant cost of solving at all.
    """
    used_cards = [card for pair in dealt for card in pair]
    deck = _remaining_deck(used_cards)

    num_hands = len(dealt)
    hole_values = np.array([[card_a.value, card_b.value] for card_a, card_b in dealt])
    hole_suits = np.array([[_SUIT_INDEX[card_a.suit], _SUIT_INDEX[card_b.suit]] for card_a, card_b in dealt])

    values = np.empty((samples, num_hands, 7), dtype=np.int64)
    suits = np.empty((samples, num_hands, 7), dtype=np.int64)
    values[:, :, :2] = hole_values[None, :, :]
    suits[:, :, :2] = hole_suits[None, :, :]

    for sample_idx in range(samples):
        board = rng.sample(deck, 5)
        values[sample_idx, :, 2:] = [card.value for card in board]
        suits[sample_idx, :, 2:] = [_SUIT_INDEX[card.suit] for card in board]

    scores = best_hand_rank_batch(
        values.reshape(samples * num_hands, 7), suits.reshape(samples * num_hands, 7)
    ).reshape(samples, num_hands)

    best = scores.max(axis=1, keepdims=True)
    is_winner = scores == best
    shares = is_winner / is_winner.sum(axis=1, keepdims=True)
    return (shares.sum(axis=0) / samples).tolist()


def monte_carlo_equity_n(
    hands: list,
    samples: int = MULTIWAY_DEFAULT_SAMPLES,
    rng: random.Random | None = None,
) -> list:
    """Each hand's all-in win-share (a k-way tie splits 1/k) against all
    the others simultaneously, via random shared-board runouts.

    Returned list is the same length and order as `hands`. Deterministic
    for a given `rng` seed — including which concrete suit each hand
    gets dealt (§3.5's fix: `rng` is threaded into `deal_n_hands` itself,
    not just used for the runout afterward, so several simultaneously-
    suited hands don't all collapse onto the same suit).
    """
    rng = rng if rng is not None else random.Random(DEFAULT_SEED)
    dealt = deal_n_hands(hands, rng=rng)
    return _simulate_equity(dealt, samples, rng)


def _stable_seed(*parts) -> int:
    """A seed derived from `parts` that's stable across process restarts
    — unlike Python's built-in hash() for strings/StartingHand, which is
    randomized per-process by default, this must reproduce the same
    value today, tomorrow, and in CI, for "same seed -> same result" to
    actually hold across separate runs, not just within one process.
    """
    joined = "|".join(str(part) for part in parts)
    digest = hashlib.sha256(joined.encode("utf-8")).hexdigest()
    return int(digest[:16], 16)


FALLBACK_PAIRWISE_SAMPLES = 50  # deliberately smaller than the usual
# MULTIWAY_DEFAULT_SAMPLES (200) — this only runs for the residual cases
# where no single concrete deal can seat a candidate and every opponent
# at once (see traverser_equity_vector's own docstring for why those are
# rare, not eliminated, after M27). A less precise fallback is an
# acceptable tradeoff for keeping an already-rare path cheap.


def _pairwise_fallback_equity(hand: StartingHand, opponent_hands: tuple, rng: random.Random) -> float:
    """`hand`'s mean pairwise Monte Carlo equity against each of
    `opponent_hands`, individually — used by traverser_equity_vector
    whenever no single deal can seat `hand` and every opponent at once.

    Ignores cross-*opponent* card conflicts (each pairwise matchup is
    computed independently) — the same "ignore blockers between
    players' hands" approximation this project has used since v1 (see
    this module's own docstring). The point isn't a perfectly exact
    n-way number — there isn't one; the combination this stands in for
    genuinely can't be dealt — it's a *hand-aware* placeholder instead
    of a value blind to what `hand` actually is. Replaced M27's first
    attempt at this fix (a flat 1/n_live n-way split, treating every
    candidate as equally strong) once testing showed that a strong hand
    like KK being assigned the same low placeholder as 32o was itself a
    real, measurable source of bias — see traverser_equity_vector's own
    docstring for the fuller story.
    """
    return float(np.mean([
        monte_carlo_equity(hand, opponent, samples=FALLBACK_PAIRWISE_SAMPLES, rng=rng)
        for opponent in opponent_hands
    ]))


class MultiwayEquityCache:
    """Lazy, memoized N-way equity: computes and caches a traverser's
    full equity vector (one value per candidate hand in `hands`, default
    all 169 classes) against one *fixed* tuple of opponent hands, only
    the first time that exact opponent combination is actually needed.

    Deterministic given `seed`: each cache entry's Monte Carlo run uses
    a seed derived from (`seed`, the opponent hands) via `_stable_seed`,
    not a single shared advancing RNG — so results don't depend on which
    order different opponent-hand combinations happen to be requested
    in, only on the combination itself.

    Thread-safe (docs/full-table-diagnostic-2026-08.md's §3.10):
    `self._cache`'s check-then-maybe-compute-then-write sequence in
    `traverser_equity_vector` is guarded by `self._lock`, the same
    "check under lock, compute unlocked, write under lock" pattern
    `api/main.py`'s own `_get_or_solve*` helpers already use — the
    expensive part (dealing cards, simulating runouts) never holds the
    lock, so concurrent callers for *different* keys never serialize on
    each other; two callers racing for the *same* key may both compute
    (deterministic given `seed`+the key, so either result is correct —
    the loser's own value is simply discarded rather than written over
    the winner's), never observe a torn/partial write. No current
    caller actually shares one instance across threads (every real
    construction site in this codebase builds a fresh instance per
    solve, used single-threaded within it) — this is a proactive
    hardening for a scaling move the diagnostic named as blocked, not a
    fix for an already-observed bug, and cheap enough (the locked
    sections are a single dict read or write, not the surrounding
    computation) to add now rather than defer.
    """

    def __init__(
        self,
        hands: list | None = None,
        samples: int = MULTIWAY_DEFAULT_SAMPLES,
        seed: int = DEFAULT_SEED,
    ):
        self.hands = hands if hands is not None else _ALL_HANDS
        self.samples = samples
        self.seed = seed
        self._cache: dict = {}
        self._validity: dict = {}
        self._lock = threading.Lock()

    def traverser_equity_vector(self, opponent_hands: tuple) -> np.ndarray:
        """Length-len(self.hands) array: index i is the win-share of a
        traverser holding self.hands[i], against the fixed
        `opponent_hands` (in any order — the result doesn't depend on
        which opponent holds which hand, only the traverser's own).

        Two determinism/bias fixes from docs/full-table-diagnostic-2026-
        08.md's §3.5/§3.6, both rooted in the same underlying cause
        (deal_n_hands's fixed, first-fit suit-pair order — see its own
        docstring): (1) every `deal_n_hands` call below passes this
        method's own seeded `rng`, so several simultaneously-suited
        opponents no longer collapse onto the identical suit every time
        (confirmed pre-fix: dealing 5 suited hands together always
        produced clubs for every one of them); (2) the opponents' own
        joint deal uses `key` (the already-sorted, cache-key-canonical
        tuple), not the caller's raw `opponent_hands` order, so which
        concrete cards get dealt — and therefore this method's own
        result — genuinely doesn't depend on which order the caller
        happened to list opponents in, closing the gap between this
        docstring's own claim and what §3.6 confirmed was actually true
        pre-fix (two orderings of the same opponent tuple, fresh caches,
        producing equity vectors differing by up to 0.0069).

        Deals the (fixed) opponents' concrete cards *once*, then deals
        just each candidate hand's own 2 cards around that — not once
        per candidate hand via a full monte_carlo_equity_n(hand,
        *opponents) call each time, which re-deals the same opponents'
        cards from scratch len(self.hands) times over. Measured during
        M9 at N=9 (8 opponents): re-dealing dominated this method's cost
        even after vectorizing the ranking computation itself (~60% of
        the time was deal_n_hands's backtracking, not hand evaluation).

        M27's placeholder story, kept here rather than scattered across
        inline comments, since it took three real iterations to land on
        this design: some `opponent_hands`/candidate combinations can't
        physically be dealt (see the two `except RuntimeError` branches
        below), and the value used *there* turned out to matter more
        than the diagnostic's own "small, localized" framing expected.
        A flat 0.5 (pre-M27) overstated every hand's share once N>2 —
        confirmed to bias a real 9-max solve's live output — and a flat
        1/n_live (M27's first attempt, "an equal n-way split instead of
        a 2-way coinflip") fixed *that* cleanly, but full-suite testing
        (not skipped) caught a new problem it introduced: at 6-max, a
        strong-but-not-nuts hand's fold rate grew with more iterations
        instead of stabilizing (e.g. AKs: 22.8% at 300 iters -> 94.8% at
        30,000) — because a hand like KK is nowhere near "average
        strength," so a flat 1/n_live placeholder is a *systematically
        low* value for it specifically, and CFR+'s regret flooring never
        un-learns a persistent low-side bias the way a plain average
        would. This module's `_pairwise_fallback_equity` (a hand-aware
        placeholder, reusing this project's existing "ignore blockers
        between players' hands" approximation) plus cfr.py's opponent-
        resampling plus this method's own joint-redeal (see below) each
        measurably reduced the problem — but did not eliminate it: even
        with all three in place, the same divergence still shows up at
        6-max at high iteration counts. Investigated, not left
        unexplained: the *pre-M27* code shows the same non-monotonic
        instability too (just biased toward over-jamming instead of
        over-folding), so this is a pre-existing MCCFR convergence
        sensitivity at 6-max with a small, top-heavy hand pool — not
        something this fix introduced, and not something a better
        placeholder value alone can fully resolve. See CLAUDE.md's M27
        entry for the full investigation and api/main.py's own
        MULTIWAY_TABLE_CONFIGS comment for the resulting mitigation.
        """
        key = tuple(sorted(opponent_hands, key=str))
        with self._lock:
            cached = self._cache.get(key)
        if cached is not None:
            return cached

        rng = random.Random(_stable_seed(self.seed, *key))

        try:
            opponent_dealt = deal_n_hands(list(key), rng=rng)
        except RuntimeError:
            # The opponents' own hands are mutually incompatible (e.g.
            # two opponents both holding KK exhausts all 4 kings) — no
            # concrete deal exists for this combination at all, so every
            # candidate needs the same hand-aware fallback (see this
            # method's own docstring and _pairwise_fallback_equity).
            # This is NOT a dead branch: MCCFR's opponent-hand sampling
            # (cfr.py) draws each opponent independently, and — even
            # after cfr.py's own resampling fix reduces how often this
            # specific branch fires — combinations like this remain
            # possible (measured on a real 9-max solve, pre-resampling:
            # 58 of 441 showdown equity evaluations overall, 46% of
            # those at 7 live opponents, 43% at 8).
            vector = np.array([
                _pairwise_fallback_equity(hand, opponent_hands, rng) for hand in self.hands
            ])
            # EVERY entry here is a fallback, so nothing in this vector is
            # a real simulated equity (see traverser_validity_mask).
            validity = np.zeros(len(self.hands), dtype=bool)
            with self._lock:
                self._cache[key] = vector
                self._validity[key] = validity
            return vector
        opponent_used = frozenset(card for pair in opponent_dealt for card in pair)

        values = []
        valid_flags = []
        for hand in self.hands:
            try:
                candidate_dealt = deal_n_hands([hand], avoiding=opponent_used, rng=rng)
                equity = _simulate_equity(candidate_dealt + opponent_dealt, self.samples, rng)[0]
                valid = True
            except RuntimeError:
                # `hand` conflicts with the SHARED opponent assignment
                # dealt above — but that assignment was chosen once,
                # arbitrarily (deal_n_hands's first successful backtrack),
                # and reused for every candidate purely for speed (see
                # this method's own docstring on why that reuse matters).
                # An arbitrary tie-break isn't necessarily a TRUE conflict
                # between `hand` and these opponent *classes* — a
                # different, equally valid concrete assignment of the
                # same classes might coexist with `hand` just fine (e.g.
                # opponents = KK, AKs might have been dealt as Ks-Kh /
                # As-Ah, blocking a KK candidate on spades+hearts, even
                # though Kd-Kc / Ah-Ac would have left it free). One more,
                # thorough attempt before falling back to a placeholder: a
                # fresh JOINT search over `hand` and every opponent
                # together (not reusing the fixed assignment), which finds
                # ANY mutually-compatible assignment if one exists at all.
                try:
                    joint_dealt = deal_n_hands([hand, *key], rng=rng)
                    equity = _simulate_equity(joint_dealt, self.samples, rng)[0]
                    valid = True
                except RuntimeError:
                    # A genuine structural conflict: no concrete
                    # assignment of `hand` plus these opponent classes can
                    # coexist at all (e.g. `hand` and 2+ opponents are all
                    # the same pair class, jointly demanding more cards of
                    # that rank than the 4 that exist, regardless of how
                    # suits are assigned). See this method's own docstring
                    # for why the fallback value here is hand-aware, not a
                    # flat constant, and for the honest limit of what that
                    # alone was found to fix.
                    equity = _pairwise_fallback_equity(hand, opponent_hands, rng)
                    valid = False
            values.append(equity)
            valid_flags.append(valid)

        vector = np.array(values)
        validity = np.array(valid_flags, dtype=bool)
        with self._lock:
            self._cache[key] = vector
            self._validity[key] = validity
        return vector

    def traverser_validity_mask(self, opponent_hands: tuple) -> np.ndarray:
        """Boolean companion to `traverser_equity_vector`: index i is True
        iff that vector's entry i came from a REAL simulated showdown, and
        False iff it came from `_pairwise_fallback_equity` — i.e. iff no
        concrete deal of `self.hands[i]` alongside `opponent_hands` exists
        at all (see `traverser_equity_vector`'s own docstring for the two
        distinct ways that happens, and M27's investigation of why any
        fallback *value*, hand-aware or not, is a problem for CFR+).

        Added in M66. The point is to let a caller distinguish "this hand's
        equity is a real number" from "this hand's equity is our best guess
        at a number that has no true value," which `traverser_equity_vector`
        alone cannot express — it has to return SOME float for every entry,
        since it returns a dense array. M27 established that feeding CFR+ a
        fallback value compounds destructively under its regret floor
        (regret only ratchets up, so a persistent one-sided bias never
        averages out); `cfr._mccfr_recurse` uses this mask to skip the
        regret update for such hands entirely rather than learn from a
        fabricated number.

        Computing this is free if `traverser_equity_vector` has already run
        for the same `opponent_hands` (both are filled in the same pass and
        cached under the same key); otherwise it triggers that computation.
        """
        key = tuple(sorted(opponent_hands, key=str))
        with self._lock:
            cached = self._validity.get(key)
        if cached is not None:
            return cached
        self.traverser_equity_vector(opponent_hands)  # fills self._validity[key]
        with self._lock:
            return self._validity[key]

    def __len__(self) -> int:
        """Number of distinct opponent-hand combinations cached so far."""
        return len(self._cache)
