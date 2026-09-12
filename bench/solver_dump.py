"""Read an independent solver's dump, including the streets past the flop.

**A dump's chance nodes hide their subtree under `dealcards`, not under
`childrens`, and a walker that follows only `childrens` reports a 24 MB
dump as having 47 nodes and no turn.** That is not hypothetical: it is
the first thing that happened when M255 looked, and it is very close to
the reason a blocker stood unexamined for fourteen milestones.

M237 recorded that blocker as "a correct price needs the reference's own
EV machinery, which its dumps do not expose". Both halves are true and
the conclusion drawn from them was too strong. The dumps carry no EV —
but EV does not have to come from the solver. Hero's expected value is
determined by the tree, both players' strategies and exact showdown
evaluation, and none of those three is this engine's opinion. What was
actually missing was the strategies for the streets past the flop, and
those are one parameter away: `set_dump_rounds`.

Measured on one ordinary spot, ranges deliberately narrow so the scaling
is about ROUNDS and not width:

    rounds   dump     solve    what it contains
      1      0.10 MB   54s     the flop's betting only
      2     24.14 MB   52s     + every one of 52 turn cards, with real
                               per-combo turn strategies
      3      3.98 GB  207s     + the river

So a flop-and-turn study is affordable at ~24 MB a spot and a river one
is 165x that. Solve time barely moves — the cost is serialisation, not
solving, which is why this was never visible from the solver's own
timings.

Nothing here is shipped: this reads an instrument's output, and no
external solver's answer may reach `poker_solver/` or `api/`.
"""
from __future__ import annotations

#: Node types the dump uses.
ACTION = "action_node"
CHANCE = "chance_node"


def children_of(node: dict) -> dict:
    """Every child of a node, whichever key the dump filed it under.

    Action nodes use `childrens`; chance nodes use `dealcards`, keyed by
    the card dealt. Merging them here is the whole point of this module —
    every caller that reimplements the walk gets the chance branch wrong
    the first time.
    """
    if not isinstance(node, dict):
        return {}
    kids = {}
    for key in ("childrens", "children", "dealcards"):
        value = node.get(key)
        if isinstance(value, dict):
            for name, child in value.items():
                if isinstance(child, dict):
                    kids[name] = child
    return kids


def walk(node: dict, path: str = "root"):
    """Yield `(path, node)` for the whole tree, chance branches included."""
    if not isinstance(node, dict):
        return
    yield path, node
    for name, child in children_of(node).items():
        yield from walk(child, f"{path}/{name}")


def strategy_at(node: dict) -> dict:
    """`{combo: {action: frequency}}` at one action node, or {} if none.

    The dump stores the action list once and each combo's frequencies as
    a bare list positionally against it, so the two must be zipped rather
    than assumed to be a mapping.
    """
    block = node.get("strategy")
    if not isinstance(block, dict):
        return {}
    actions = block.get("actions") or []
    rows = block.get("strategy") or {}
    if not isinstance(rows, dict):
        return {}
    return {combo: dict(zip(actions, values))
            for combo, values in rows.items()
            if isinstance(values, (list, tuple))}


def populated_rounds(node: dict) -> int:
    """How many betting rounds the dump actually carries.

    Counts the deepest chain of POPULATED chance nodes, so it reports
    what is in the file rather than what was asked for. A dump requested
    at `set_dump_rounds 3` that ran out of disk is indistinguishable from
    a 2-round one by its filename and not by this.
    """
    def deepest(n, rounds=1):
        best = rounds
        for child in children_of(n).values():
            if n.get("node_type") == CHANCE:
                best = max(best, deepest(child, rounds + 1))
            else:
                best = max(best, deepest(child, rounds))
        return best

    return deepest(node) if isinstance(node, dict) else 0


def normalise_combo(text: str) -> str:
    """Card-order-independent key: 'Kh6h' and '6hKh' both -> '6hKh'.

    The solver writes combos in its own card order and a mismatched
    spelling silently matches nothing, which cost M174 a study.
    """
    cards = sorted(text[i:i + 2] for i in range(0, len(text), 2))
    return "".join(cards)
