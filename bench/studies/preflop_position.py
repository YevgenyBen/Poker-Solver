"""Does the multiway preflop opening range widen with position, and does
the sizing split still move with the seed, AT THE SHIPPED CONFIGURATION?
(the 2026-09-20 audit's R3, M305)

`SIZING_CAVEAT_REASON` carries two figures from M110/M111, and both were
measured at **12,000 iterations with one seed**. Six-handed ships 3,000
iterations with a **four-seed ensemble** (M290), and M290's whole point
was that averaging seeds steadies this solve. So the copy quotes a
spread and a flatness from an arm the product does not run - and M110's
own table already shows the 3,000-iteration arm carrying a gradient
(0.281 under the gun rising to 0.384 on the button) where the 12,000 one
is flat. A warning may not misdescribe its own defect in either
direction: overstating it teaches a player to discard advice that is
right (M232's rule, read the other way round).

**What is re-measured here, and what is not.**

| clause | source | this study |
|---|---|---|
| the sizing split moves with the seed | M73/M110 @ 12,000 | arm A |
| the opening range does not widen with position | M111 @ 12,000 | arm A |
| the fold call is sounder first in | M289 singles | read off M290's committed rows |
| hands are classified sensibly at 3+ live | M282/M287 | unchanged, already at this configuration |

**Arms.** Six-handed, 100bb, the shipped seat list and iteration budget,
equity held fixed so only the traversal seed moves:
- **A (shipped):** ensemble of `ENSEMBLE` at base seeds `SEEDS`.
- **B (control):** one seed, the SAME iteration budget. M197's confound,
  controlled rather than argued - without it a change here could be the
  budget rather than the ensemble.

Per solve, each non-blind seat's FIRST-IN node (everyone before has
folded) gives a combo-weighted `open_frequency` over all 169 classes,
and the under-the-gun node gives each class's `all_in_share` of its own
non-fold mass - M98's axis, the one the caveat calls unreliable.

**PRE-REGISTERED READING RULE (fixed before any seed was solved):**

1. `open_frequency` is the combo-weighted mass on non-fold actions.
   `all_in_share` is all-in mass over non-fold mass.
2. G = the mean over seeds of (button open - under-the-gun open), arm A.
3. **The yardstick is the solver's own movement**, which is M111's own
   criterion for calling the gradient absent: N = the mean over the four
   non-blind seats of that seat's own spread in `open_frequency` across
   the seeds.
4. "The opening range does not widen with position at all" SURVIVES iff
   |G| <= N. It is REFUTED iff G > N **and** every seed agrees in sign.
   Anything else - G over N with the seeds disagreeing, or a negative
   gradient past N - is its own result and the copy states it.
5. The copy quotes the measured gradient against the ~30 points GTO
   spans. The word "flat" may be written only if rule 4 SURVIVES.
6. P = the combo-weighted mean, over classes holding at least
   `MIN_NON_FOLD` non-fold mass, of each class's spread in
   `all_in_share` across the seeds. "The split among the non-fold
   actions moves with the random seed" SURVIVES iff P >= `SPLIT_LEVEL`.
7. The copy quotes AA's own measured span at this configuration. If AA's
   span misses `SPLIT_LEVEL` while P clears it, the copy names the
   POPULATION figure instead of AA - a claim is carried by the
   population its gate fires on, not by the hand that first found it
   (M282).
8. Arm B is reported and decides nothing. It attributes, it does not
   grade.
9. A null is a result: if every clause survives, only the provenance
   changes.

    python -m bench.studies.preflop_position out.json
"""
from __future__ import annotations

import json
import sys

SIZE = 6
STACK = 100.0
SEEDS = (1, 51, 61, 71)        # 1 is the shipped seed; the rest are fresh
ENSEMBLE = 4                   # M290's K, read back from the config at run time
SPLIT_LEVEL = 0.10             # rule 6, M289's level on the fold axis
MIN_NON_FOLD = 0.01            # rule 6, a class with no play has no split
GTO_SPAN = 0.30                # ~15% under the gun to ~45% on the button
FOLD_MIN_GAP = 0.05            # M289's own MIN_GAP, reused for its own clause
NON_BLIND = ("UTG", "MP", "CO", "BTN")


# -- the axes ------------------------------------------------------------

def open_frequency(rows: dict, weights: dict) -> float:
    """Combo-weighted mass on the non-fold actions - M110's own measure.

    `rows` maps a class name to its action->probability row; `weights`
    maps the same names to combo counts.
    """
    total = sum(weights[name] for name in rows)
    if not total:
        return 0.0
    return sum(weights[name] * (1.0 - row.get("fold", 0.0))
               for name, row in rows.items()) / total


def all_in_share(row: dict) -> tuple:
    """(share of non-fold mass that is all-in, the non-fold mass itself).

    Returns (None, mass) when nothing but folding is played, because a
    hand that never continues has no split to be unstable about.
    """
    non_fold = sum(p for k, p in row.items() if k != "fold")
    if non_fold <= 0.0:
        return None, non_fold
    jam = sum(p for k, p in row.items() if k.startswith("all_in"))
    return jam / non_fold, non_fold


def _spread(values) -> float:
    kept = [v for v in values if v is not None]
    return (max(kept) - min(kept)) if len(kept) >= 2 else 0.0


# -- rules 2-4: the gradient ---------------------------------------------

def gradient(by_seed: dict, low: str = NON_BLIND[0], high: str = NON_BLIND[-1]) -> dict:
    """G, its per-seed values and whether every seed agrees in sign.

    `by_seed` maps a seed to {seat: open_frequency}.
    """
    per_seed = [by_seed[s][high] - by_seed[s][low] for s in sorted(by_seed)]
    if not per_seed:
        return {"n": 0}
    mean = sum(per_seed) / len(per_seed)
    return {"n": len(per_seed), "mean": mean, "per_seed": per_seed,
            "signs_agree": all(v > 0 for v in per_seed) or all(v < 0 for v in per_seed)}


def seed_noise(by_seed: dict, seats=NON_BLIND) -> dict:
    """N: the mean over seats of that seat's own spread across seeds.

    This is the yardstick M111 used to call a 1.7-point gradient absent,
    and reusing it is what makes this a re-measurement of the same claim
    rather than a new one under a friendlier bar.
    """
    present = [s for s in seats if all(s in by_seed[k] for k in by_seed)]
    if not present or len(by_seed) < 2:
        return {"n": 0}
    per_seat = {s: _spread([by_seed[k][s] for k in by_seed]) for s in present}
    return {"n": len(present), "mean": sum(per_seat.values()) / len(per_seat),
            "per_seat": per_seat}


def position_verdict(by_seed: dict) -> dict:
    """Rule 4, both directions, with the null spelt out rather than
    inferred from a missing key."""
    grad, noise = gradient(by_seed), seed_noise(by_seed)
    if not grad.get("n") or not noise.get("n"):
        return {"measured": False}
    over = abs(grad["mean"]) > noise["mean"]
    return {"measured": True, "gradient": grad, "noise": noise,
            "flat": not over,
            "widens": bool(over and grad["mean"] > 0 and grad["signs_agree"]),
            "share_of_gto_span": grad["mean"] / GTO_SPAN}


# -- rules 6-7: the sizing split ----------------------------------------

def split_move(shares_by_seed: dict, weights: dict, min_non_fold: float = MIN_NON_FOLD) -> dict:
    """P, plus how many classes carried enough non-fold mass to count.

    `shares_by_seed` maps a seed to {class: (share, non_fold_mass)}.
    """
    seeds = sorted(shares_by_seed)
    if len(seeds) < 2:
        return {"n": 0}
    names = [n for n in shares_by_seed[seeds[0]]
             if all(shares_by_seed[s].get(n, (None, 0.0))[1] >= min_non_fold for s in seeds)]
    if not names:
        return {"n": 0}
    spreads = {n: _spread([shares_by_seed[s][n][0] for s in seeds]) for n in names}
    total = sum(weights[n] for n in names)
    return {"n": len(names),
            "mean": sum(weights[n] * spreads[n] for n in names) / total,
            "worst": max(spreads.values()),
            "spreads": spreads}


def hand_span(shares_by_seed: dict, name: str) -> dict:
    """Rule 7: one named class's own min and max at this configuration."""
    values = [shares_by_seed[s].get(name, (None, 0.0))[0] for s in sorted(shares_by_seed)]
    kept = [v for v in values if v is not None]
    if not kept:
        return {"n": 0}
    return {"n": len(kept), "low": min(kept), "high": max(kept),
            "span": max(kept) - min(kept), "per_seed": values}


# -- rule 9's neighbour: the clause that needs no new solving ------------

def fold_axis(nodes: list, arm: str = "ensemble_move") -> dict:
    """"The fold call is sounder when you are first in" - re-read off
    M290's committed rows on whichever arm is asked for.

    M289 measured this on SINGLE seeds and six-handed now ships an
    ensemble, so the clause has to be re-read on the arm that ships. It
    needs no new solving at all: the rows are already committed, and the
    expensive half of a study is the solving (M300).

    **This clause's bar could not be pre-registered, and that is worth
    saying rather than hiding.** The rows existed before the question was
    asked, so the honest substitute is a bar chosen by somebody else:
    `FOLD_MIN_GAP` is M289's own `MIN_GAP`, fixed before any of this was
    measured. An INHERITED bar is the only kind that can be trusted once
    the data is already on disk.
    """
    out = {}
    for name, facing in (("facing", True), ("first_in", False)):
        mine = [n for n in nodes if n["facing"] is facing]
        weight = sum(n["count"] for n in mine)
        out[name] = (sum(n[arm] * n["count"] for n in mine) / weight) if weight else None
        out[name + "_weight"] = weight
    if out["facing"] is not None and out["first_in"] is not None:
        out["gap"] = out["facing"] - out["first_in"]
        out["first_in_is_sounder"] = out["gap"] >= FOLD_MIN_GAP
    return out


# -- the whole reading ---------------------------------------------------

def summarise(record: dict) -> dict:
    """Both arms read under the rule. `record` is what `main` writes."""
    weights = record["weights"]
    out = {"arms": {}}
    for arm, data in record["arms"].items():
        by_seed = {int(k): v for k, v in data["open"].items()}
        shares = {int(k): {n: tuple(v) for n, v in cls.items()}
                  for k, cls in data["all_in"].items()}
        out["arms"][arm] = {
            "position": position_verdict(by_seed),
            "split": split_move(shares, weights),
            "aa": hand_span(shares, "AA"),
            "open": by_seed,
        }
    return out


def verdict(summary: dict, arm: str = "ensemble") -> dict:
    """Rules 4, 6 and 7 on the SHIPPED arm. The control grades nothing."""
    entry = summary["arms"].get(arm)
    if entry is None or not entry["position"].get("measured"):
        return {"measured": False}
    split, aa = entry["split"], entry["aa"]
    moves = bool(split.get("n") and split["mean"] >= SPLIT_LEVEL)
    return {
        "measured": True,
        "position_is_flat": entry["position"]["flat"],
        "position_widens": entry["position"]["widens"],
        "split_moves_with_seed": moves,
        "copy_may_say_flat": entry["position"]["flat"],
        # Rule 7: AA is quotable only if it carries the claim by itself.
        "copy_may_quote_aa": bool(aa.get("n") and aa["span"] >= SPLIT_LEVEL),
    }


def main(argv=None) -> int:                              # pragma: no cover
    from api import config as cfg, solving
    from poker_solver.game_tree import DecisionNode, GameConfig
    from poker_solver.solver import solve_preflop

    out_path = (argv if argv is not None else sys.argv[1:])[0]
    table = cfg.MULTIWAY_TABLE_CONFIGS[SIZE]
    seats = list(table["positions"])
    config = GameConfig(positions=seats, stack_bb=STACK)
    equity = solving._get_multiway_equity_cache(cfg.MULTIWAY_PREFLOP_HANDS)
    shipped_runs = int(table.get("ensemble", 1))
    weights = {str(h): h.combo_count for h in cfg.MULTIWAY_PREFLOP_HANDS}

    def first_in(result, seat):
        """The node where everyone before `seat` has folded."""
        node = result.root
        for _ in range(seats.index(seat)):
            action = next((a for a in node.legal_actions if a.kind == "fold"), None)
            if action is None:
                return None
            node = node.children[action]
        return node if isinstance(node, DecisionNode) else None

    record = {"size": SIZE, "stack": STACK, "seeds": list(SEEDS), "weights": weights,
              "iterations": table["iterations"], "shipped_ensemble": shipped_runs,
              "arms": {}}
    for arm, runs in (("ensemble", shipped_runs), ("single", 1)):
        record["arms"][arm] = {"runs": runs, "open": {}, "all_in": {}}
        for seed in SEEDS:
            result = solve_preflop(config=config, hands=cfg.MULTIWAY_PREFLOP_HANDS,
                                   equity_cache=equity, iterations=table["iterations"],
                                   seed=seed, floor_regret=table.get("floor_regret"),
                                   ensemble=runs)
            opens = {}
            for seat in seats:
                node = first_in(result, seat)
                if node is None:
                    continue
                opens[seat] = open_frequency(result.strategy_at(node), weights)
            record["arms"][arm]["open"][str(seed)] = opens
            utg = first_in(result, seats[0])
            rows = result.strategy_at(utg)
            record["arms"][arm]["all_in"][str(seed)] = {
                name: list(all_in_share(row)) for name, row in rows.items()}
            print(f"{arm} x{runs} seed {seed}: " + " ".join(
                f"{s} {opens[s]:.3f}" for s in seats if s in opens), flush=True)
        with open(out_path, "w", encoding="utf-8") as handle:
            json.dump(record, handle)

    summary = summarise(record)
    for arm, entry in summary["arms"].items():
        print(arm, json.dumps({
            "position": entry["position"],
            "split": {k: v for k, v in entry["split"].items() if k != "spreads"},
            "aa": entry["aa"]}, indent=1, default=str), flush=True)
    print(json.dumps(verdict(summary), indent=1))
    return 0


if __name__ == "__main__":                               # pragma: no cover
    raise SystemExit(main())
