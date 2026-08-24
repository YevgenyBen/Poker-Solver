# Maximal benchmarks and a live-play GTO comparison — 2026-08-24 (M110)

Two deliverables. First, a full performance matrix: every `/advise` cell
cold and warm, every engine primitive, and the four scaling curves that
govern this project's budget decisions. Second, a random-deal advisory
simulation compared against GTO.

Every timing is reported in **reference units** alongside seconds,
against a fixed workload measured in the same run (0.277s here). CLAUDE.md
records that this machine drifts ~1.7x between sessions, so bare seconds
are not comparable to anything recorded earlier — the units are.

---

## 1. Every `/advise` cell, cold and warm

| cell | cold | units | warm | speedup |
|---|---|---|---|---|
| 2max preflop | 2.64s | 9.5 | 2.04ms | 1,293x |
| 2max flop | 8.47s | 30.6 | 4.75ms | 1,783x |
| 2max flop mid-street | 7.08s | 25.6 | 3.49ms | 2,030x |
| 2max turn | 18.32s | 66.2 | 2.86ms | 6,415x |
| 2max river | 12.29s | 44.4 | 9.74ms | 1,262x |
| 3max preflop | 26.30s | 95.1 | 1.66ms | 15,866x |
| 3max flop | 30.30s | 109.5 | 4.60ms | 6,585x |
| 6max preflop | 65.51s | 236.7 | 2.12ms | 30,867x |
| 6max flop | 70.63s | 255.2 | 6.49ms | 10,883x |
| 9max preflop | 98.16s | 354.7 | 1.86ms | 52,730x |
| 9max flop | 104.10s | 376.2 | 7.29ms | 14,270x |

**Warm is flat.** Every cell serves in 1.7-9.7ms whatever the table size —
9-max preflop is *faster* warm (1.86ms) than heads-up flop (4.75ms),
because warm cost is response shaping, not solving.

**Cold scales with seats, not streets.** 9-max preflop costs 37x heads-up
preflop; heads-up river costs 4.7x heads-up preflop. Player count
dominates.

**Mid-street flop is cheaper than the street's opening decision** (7.08s
vs 8.47s), confirming M88's note that making both flop decisions share
the canonical library's tree was the cheaper option as well as the
consistent one.

## 2. Engine primitives

| primitive | seconds | units |
|---|---|---|
| `get_equity_table` (disk-cached) | ~0.00 | 0.0 |
| `solve_flop` (flop only) | 0.195 | 0.7 |
| `solve_flop_turn` (+turn) | 10.87 | 38.0 |
| `solve_flop_to_river` (+turn+river) | **765.60** | **2,680** |
| `build_equity_table` 24 hands x 200 samples | 9.53 | 33.4 |

Each chance node multiplies cost by roughly 50-70x: flop-only to +turn is
56x, +turn to +river another 70x, for **3,926x end to end**.

The number that matters operationally: the raw primitive costs 765s at 19
combos while the `2max river` *endpoint* serves cold in 12.29s. The combo
caps (`RIVER_PATH_QUERY_MAX_COMBOS_PER_SIDE = 9`) are not tuning — they
are the only reason the river is reachable at all.

## 3. Scaling curves

| axis | measurements | shape |
|---|---|---|
| iterations (HU preflop) | 200 -> 0.391s, 1k -> 1.875s, 4k -> 7.488s | linear |
| players (300 iters, 40 hands) | 3max 0.607s, 6max 1.857s, 9max 2.664s | sub-linear |
| equity samples (6max) | 50 -> 1.862s, 200 -> 7.764s, 400 -> 14.059s | linear |
| combo pool (6max) | 20 -> 0.701s, 40 -> 1.951s, 80 -> 4.392s | **super-linear** |

Equity samples scaling linearly is why M99's equal-cost experiment had to
hold wall clock fixed rather than assume `samples x iterations` was the
budget — holding the product constant does not hold cost constant.

Combo pool being super-linear (2.8x and 2.25x per doubling) is why the
path-query class caps exist, and matches M35's finding that pool size is
the dominant multiway cost driver.

## 4. Throughput

| threads | requests/sec |
|---|---|
| 1 | 209.5 |
| 2 | 252.1 |
| 4 | 238.7 |
| 8 | 228.1 |

Warm throughput is **flat at ~210-250 rps regardless of thread count** —
a warm request is GIL-bound, so concurrency buys ~20% at two threads and
then costs a little. Scaling this service means processes, not threads.

---

## 5. Live-play simulation vs GTO

40 random deals: random table size, random point in the preflop action so
the acting seat varies, random hole cards. The solver returns a MIXED
strategy, and those frequencies are reported as the confidence figures —
a pure 100% recommendation and a 55/45 split are different advice, and
flattening them would discard the part a player most needs.

Sample of what a player sees:

```
2max BTN  72s  -> fold           92.8%   (fold  92.8%)
2max BTN  AQs  -> call_or_check  60.2%   (fold   0.0%)
6max UTG  92o  -> fold          100.0%   (fold 100.0%)
6max UTG  KJo  -> raise:2.50     55.5%   (fold   0.0%)
6max CO   93s  -> fold           99.6%   (fold  99.6%)
2max BB   32o  -> call_or_check  52.8%   (fold  47.2%)
```

**Categorical GTO checks: 0 violations in 40 deals.** No premium hand
folded; no trash played from early position. Individual hands are
classified sensibly.

### The range comparison, which is where it breaks

Anecdotes about single hands cannot settle "is this GTO". The measurable
version is the implied opening range per seat — combo-weighted, because
there are 6 combos of a suited hand and 4 of an offsuit one, and
published frequencies are combo-weighted too.

| seat | shipped (3,000 iters) | 12,000 iters | GTO, approx |
|---|---|---|---|
| 2max BTN | **0.871** ✓ | — | 0.70-0.95 |
| 6max UTG | 0.281 ✗ | **0.176** ✓ | 0.15-0.18 |
| 6max MP | 0.319 | 0.171 | ~0.19 |
| 6max CO | 0.316 ✓ | 0.174 | ~0.26 |
| 6max BTN | 0.384 ✓ | **0.159** | **~0.45** |
| 6max SB | 0.498 | 0.806 | ~0.80-0.87 (see below) |

**Correction to this table (M111).** The SB row originally carried a GTO
reference of ~0.45 and was reported as "wildly loose". That was wrong,
and wrong the same way M106's equity references were: when it folds to
the small blind, SB is **heads-up against BB**, so the right comparison
is the heads-up opening frequency (~0.80-0.87), not a generic 6-max SB
number. 0.806 is close to correct, not a defect. The reference was
remembered rather than derived, which is exactly the failure M106
recorded.

Heads-up is genuinely good. 6-max is not, in two ways:

1. **At the shipped budget the gradient is compressed** — six points of
   widening across four seats, where GTO spans about thirty.
2. **At 12,000 iterations the BUTTON OPENS TIGHTER THAN UNDER THE GUN**
   (0.159 vs 0.176), on **both** seeds tested.

**Sharpened in M111, and point 2 above was over-read.** Breaking the
strategy down by action mass at each seat (12,000 iterations, seed 1):

| seat | live | trained | fold | raise | call | all-in |
|---|---|---|---|---|---|---|
| UTG | 6 | 1.0 | 0.824 | 0.100 | 0.064 | 0.012 |
| MP | 5 | 1.0 | 0.829 | 0.040 | 0.099 | 0.032 |
| CO | 4 | 1.0 | 0.826 | 0.078 | 0.069 | 0.027 |
| BTN | 3 | 1.0 | 0.841 | 0.050 | 0.057 | 0.052 |
| SB | 2 | 1.0 | 0.194 | 0.420 | 0.362 | 0.025 |

`trained_share` is **1.0 at every seat**, so under-training is refuted —
the button is not starved of visits. And the fold mass is **flat at
0.82-0.84 across all four non-blind seats**.

So "the button opens tighter" is the wrong description: the 1.7pp gap is
smaller than the 2.8pp CO alone varies between seeds. The correct and
stronger statement is that **position is not learned at all** among
non-blind seats. SB differs only because it is heads-up against BB by
then, and it behaves correctly there.

This also connects the defect to a known cause rather than leaving it
free-floating. M98 established that terminals are priced at raw showdown
equity, so *playing* is uniformly underpriced; if the value of playing is
understated equally at every seat, the fold/play boundary cannot move
with position. **One root cause, two symptoms** — and it means this needs
the same architectural fix (solved postflop continuation values), not a
larger budget.

The inversion is the strongest result here because it **needs no
published chart to condemn**: later position must open wider. It is also
consistent across seeds, unlike the ordering wobble seen at 3,000
iterations, which moved between seats and is convergence noise.

### What this changed in the product

`SIZING_CAVEAT_REASON` told users: *"Trust the fold-vs-play call."* That
was written when only the sizing axis had been measured against a
reference, and it is too strong. It now says the fold-vs-play call is
sounder but not fully GTO — individual hands are classified sensibly,
while the opening range does not widen with position the way GTO does,
and at 6-max the button has measured tighter than under the gun.

This is a **new** defect class. M98 established that the SIZING axis is
structurally broken and treated fold-vs-play as the reliable half. The
reliable half has its own positional defect, and users were being told to
trust it.
