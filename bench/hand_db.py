"""A local, append-only store of REAL played hands, for measurement.

**An instrument, never an ingredient** (CLAUDE.md's stone law): nothing
under `poker_solver/` or `api/` may import this or read its data. It exists
to tell us which spots real players meet and how real opponents act.

Layout (under `data/hands/`, git-ignored)::

    raw/<anything>/...   hand histories exactly as downloaded, kept so the
                         store can be rebuilt or re-derived later
    hands.sqlite         the store: one row per hand, derived columns
                         indexed for fast filtering

**Appending is the design.** Every hand is keyed by a hash of its content,
so ingesting a file twice, or two files that overlap, adds nothing twice.
Every file is recorded with its own hash, so re-running an ingest over a
whole directory skips what is already in and costs seconds. To add hands:

    python -m bench.hand_db ingest data/hands/raw/<new-folder> --source <name>

Input is the PHH format (https://github.com/uoftcprg/phh-std): one hand
per `.phh` file, or many per `.phhs` file under `[n]` headers. Both are
TOML, so the standard library parses them.

Reading::

    from bench.hand_db import connect, query
    with connect() as db:
        for hand in query(db, "n_players = 2 AND eff_stack_bb >= 90"):
            ...                       # hand.streets(), hand.positions, ...

`Hand.streets()` rebuilds every action with its amount in big blinds and
the pot before it, which is what a replay through `/advise` needs.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import math
import pathlib
import sqlite3
import tomllib
from dataclasses import dataclass, field

ROOT = pathlib.Path(__file__).resolve().parent.parent
DEFAULT_DIR = ROOT / "data" / "hands"
DEFAULT_DB = DEFAULT_DIR / "hands.sqlite"

#: Bump when a derived column's meaning changes; `rederive` rebuilds rows
#: whose version is older, from the raw actions stored with them.
SCHEMA_VERSION = 2

STREETS = ("preflop", "flop", "turn", "river")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS hands (
    id               TEXT PRIMARY KEY,
    source           TEXT NOT NULL,
    venue            TEXT,
    hand_no          TEXT,
    played_on        TEXT,
    variant          TEXT,
    seat_count       INTEGER,
    n_players        INTEGER NOT NULL,
    sb               REAL,
    bb               REAL NOT NULL,
    ante             REAL,
    eff_stack_bb     REAL,
    min_stack_bb     REAL,
    max_stack_bb     REAL,
    preflop_raises   INTEGER,
    pot_type         TEXT,
    players_to_flop  INTEGER,
    players_to_turn  INTEGER,
    players_to_river INTEGER,
    last_street      INTEGER,
    showdown         INTEGER,
    known_cards      INTEGER,
    all_cards_known  INTEGER,
    pot_at_flop_bb   REAL,
    flop_eff_stack_bb REAL,
    board            TEXT,
    positions        TEXT NOT NULL,
    stacks           TEXT NOT NULL,
    actions          TEXT NOT NULL,
    hole_cards       TEXT NOT NULL,
    players          TEXT,
    result           TEXT,
    derived_version  INTEGER NOT NULL,
    file_id          INTEGER,
    clean            INTEGER
);
CREATE INDEX IF NOT EXISTS hands_source ON hands(source);
CREATE INDEX IF NOT EXISTS hands_shape ON hands(n_players, eff_stack_bb);
CREATE INDEX IF NOT EXISTS hands_flop ON hands(players_to_flop, pot_type);
CREATE TABLE IF NOT EXISTS files (
    id          INTEGER PRIMARY KEY,
    path        TEXT NOT NULL,
    sha256      TEXT NOT NULL UNIQUE,
    source      TEXT NOT NULL,
    hands_seen  INTEGER NOT NULL,
    hands_added INTEGER NOT NULL,
    ingested_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""


#: Columns added after a store may already exist, so `connect` adds them.
_ADDED_COLUMNS = (("clean", "INTEGER"),)


def connect(path: str | pathlib.Path = DEFAULT_DB) -> sqlite3.Connection:
    """Open (creating if needed) the store. Usable as a context manager."""
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.executescript(_SCHEMA)
    have = {r[1] for r in db.execute("PRAGMA table_info(hands)")}
    for column, kind in _ADDED_COLUMNS:
        if column not in have:
            db.execute("ALTER TABLE hands ADD COLUMN %s %s" % (column, kind))
    db.execute("INSERT OR IGNORE INTO meta VALUES ('schema_version', ?)",
               (str(SCHEMA_VERSION),))
    return db


# -- positions ---------------------------------------------------------------

_BEFORE_BUTTON = ("CO", "HJ", "LJ", "MP1", "MP", "UTG2", "UTG1")


def position_names(n: int) -> list:
    """Seat names in PHH player order (p1 = small blind).

    PHH lists players from the small blind round to the button. HEADS-UP
    IS REVERSED (pokerkit's convention, checked against real hands: p2
    acts first preflop and p1 first after the flop): p1 is the big blind
    and p2 the button, who posts the small blind. `blinds_or_straddles`
    stays written as [small, big] there, so `player_blinds` swaps it.
    Seats between the big blind and the cut-off are UTG, UTG1, ...
    """
    if n < 2:
        raise ValueError("a hand needs at least two players")
    if n == 2:
        return ["BB", "BTN"]
    names = ["SB", "BB"]
    middle = n - 3                       # seats between BB and BTN
    late = list(_BEFORE_BUTTON[:max(0, min(middle, 3))])[::-1]  # ..., HJ, CO
    early_count = middle - len(late)
    early = ["UTG"] + ["UTG%d" % i for i in range(1, early_count)] if early_count else []
    return names + early + late + ["BTN"]


def player_blinds(blinds: list) -> list:
    """What each player (PHH order) actually posted.

    The spec: "in heads-up situations, the effective blind amounts are the
    reversed version of the array field value" - antes too.
    """
    blinds = list(blinds)
    return blinds[::-1] if len(blinds) == 2 else blinds


# -- parsing -----------------------------------------------------------------

def _hand_blocks(text: str, suffix: str) -> list:
    data = tomllib.loads(text)
    if suffix == ".phhs":
        return [data[k] for k in sorted(data, key=lambda k: int(k))]
    return [data]


def _content_id(source_key: str, block: dict) -> str:
    canon = json.dumps({
        "k": source_key,
        "venue": block.get("venue"), "hand": block.get("hand"),
        "table": block.get("table"),
        "stacks": block.get("starting_stacks"),
        "actions": block.get("actions"),
    }, sort_keys=True, default=str)
    return hashlib.sha1(canon.encode("utf-8")).hexdigest()


@dataclass
class Action:
    street: str
    player: int              # 0-based index into PHH player order
    position: str
    kind: str                # fold / check / call / bet / raise / show
    to_bb: float | None      # street total after the action, in bb
    pot_before_bb: float     # pot before this action, all streets
    facing_bb: float         # what the actor had to call, in bb


@dataclass
class Hand:
    """A decoded row. `streets()` replays the actions with amounts."""
    id: str
    source: str
    n_players: int
    bb: float
    positions: list
    stacks_bb: list
    actions: list
    hole_cards: dict
    board: str
    antes_bb: list = field(default_factory=list)
    blinds_bb: list = field(default_factory=list)
    row: dict = field(default_factory=dict)

    def streets(self) -> list:
        return replay(self.actions, self.n_players, self.bb,
                      self.blinds_bb, self.antes_bb, self.positions)


def replay(actions, n, bb, blinds_bb, antes_bb, positions) -> list:
    """Every player action with its street, size and the pot before it."""
    street_i = 0
    committed = [0.0] * n                     # this street
    pot = sum(antes_bb)                       # from earlier streets
    for i, b in enumerate(blinds_bb):
        committed[i] = b
    out = []
    for text in actions:
        parts = text.split()
        if parts[0] == "d":
            if parts[1] == "db":
                pot += sum(committed)
                committed = [0.0] * n
                street_i += 1
            continue
        if not parts[0].startswith("p"):
            continue
        who = int(parts[0][1:]) - 1
        op = parts[1]
        current = max(committed)
        facing = current - committed[who]
        pot_before = pot + sum(committed)
        street = STREETS[min(street_i, 3)]
        if op == "f":
            kind, to = "fold", None
        elif op == "cc":
            kind = "call" if facing > 1e-9 else "check"
            committed[who] = current
            to = current
        elif op == "cbr":
            amount = float(parts[2]) / bb
            # Preflop the blinds are the bet, so any cbr there raises.
            kind = "raise" if (street_i == 0 or current > 1e-9) else "bet"
            committed[who] = amount
            to = amount
        elif op == "sm":
            kind, to = "show", None
        else:
            continue
        out.append(Action(street, who, positions[who], kind,
                          None if to is None else round(to, 6),
                          round(pot_before, 6), round(max(facing, 0.0), 6)))
    return out


def derive(block: dict, source: str) -> dict:
    """The row for one PHH hand."""
    stacks = [float(s) for s in block["starting_stacks"]]
    n = len(stacks)
    blinds = [float(b) for b in block.get("blinds_or_straddles", [0] * n)]
    antes = [float(a) for a in block.get("antes", [0] * n)]
    bb = max(blinds) if any(blinds) else float(block.get("min_bet", 1))
    positions = position_names(n)
    actions = list(block.get("actions", []))

    hole, board = {}, ""
    for text in actions:
        parts = text.split()
        if parts[:2] == ["d", "db"]:
            board += parts[2]
        elif len(parts) >= 4 and parts[0] == "d" and parts[1] == "dh":
            if "?" not in parts[3]:
                hole[int(parts[2][1:]) - 1] = parts[3]
        elif len(parts) >= 3 and parts[1] == "sm" and "?" not in parts[2]:
            hole[int(parts[0][1:]) - 1] = parts[2]

    blinds_bb = [b / bb for b in player_blinds(blinds)]
    antes_bb = [a / bb for a in player_blinds(antes)]
    # The spec allows `inf` for an unknown stack, and requires forced bets
    # to be non-negative; some logs break the second. Neither kind of hand
    # is dropped - its actions are still real - but `clean` says whether
    # stack-derived columns and pot accounting can be trusted.
    finite = all(math.isfinite(x) for x in stacks)
    clean = int(finite and all(b >= 0 for b in blinds) and all(a >= 0 for a in antes))
    acts = replay(actions, n, bb, blinds_bb, antes_bb, positions)
    last = max((STREETS.index(a.street) for a in acts), default=0)
    streets_dealt = sum(1 for t in actions if t.startswith("d db"))
    last = max(last, streets_dealt)
    live_after = [p for p in range(n)
                  if p not in {a.player for a in acts if a.kind == "fold"}]

    def reached(k):
        if streets_dealt < k:
            return 0
        folded_before = {a.player for a in acts
                         if a.kind == "fold" and STREETS.index(a.street) < k}
        return n - len(folded_before)

    raises = sum(1 for a in acts if a.street == "preflop" and a.kind == "raise")
    to_flop = reached(1)
    if to_flop == 0:
        pot_type = "no_flop"
    else:
        pot_type = {0: "limped", 1: "single_raised", 2: "three_bet"}.get(raises, "four_bet_plus")
    pre = [a for a in acts if a.street == "preflop"]
    flop_first = next((a for a in acts if a.street == "flop"), None)
    pot_at_flop = flop_first.pot_before_bb if flop_first else None
    flop_players = [p for p in range(n) if p not in
                    {a.player for a in pre if a.kind == "fold"}] if to_flop else []
    stacks_bb = [s / bb for s in stacks]
    ordered = sorted(stacks_bb, reverse=True)
    eff = ordered[1] if len(ordered) > 1 else ordered[0]
    flop_eff = None
    if not finite:
        eff = None
    elif len(flop_players) >= 2:
        fs = sorted((stacks_bb[p] for p in flop_players), reverse=True)
        flop_eff = fs[1]

    result = block.get("winnings") or block.get("finishing_stacks")
    played_on = None
    if block.get("year"):
        try:
            played_on = _dt.date(int(block["year"]), int(block["month"]),
                                 int(block["day"])).isoformat()
        except (KeyError, ValueError):
            played_on = None
    return {
        "source": source, "venue": block.get("venue"),
        "hand_no": None if block.get("hand") is None else str(block.get("hand")),
        "played_on": played_on, "variant": block.get("variant"),
        "seat_count": block.get("seat_count", n), "n_players": n,
        "sb": min((b for b in blinds if b > 0), default=0.0), "bb": bb,
        "ante": max(antes) if antes else 0.0,
        "eff_stack_bb": None if eff is None else round(eff, 4),
        "min_stack_bb": round(min(stacks_bb), 4) if finite else None,
        "max_stack_bb": round(max(stacks_bb), 4) if finite else None,
        "preflop_raises": raises, "pot_type": pot_type,
        "players_to_flop": to_flop, "players_to_turn": reached(2),
        "players_to_river": reached(3), "last_street": min(last, 3),
        "showdown": int(any(t.split()[1] == "sm" for t in actions
                            if t.startswith("p")) or (streets_dealt == 3 and len(live_after) >= 2)),
        "known_cards": len(hole), "all_cards_known": int(len(hole) == n),
        "pot_at_flop_bb": None if pot_at_flop is None else round(pot_at_flop, 4),
        "flop_eff_stack_bb": None if flop_eff is None else round(flop_eff, 4),
        "board": board, "positions": json.dumps(positions),
        "stacks": json.dumps({"chips": stacks, "blinds": blinds, "antes": antes}),
        "actions": json.dumps(actions),
        "hole_cards": json.dumps({str(k): v for k, v in sorted(hole.items())}),
        "players": json.dumps(block.get("players")),
        "result": json.dumps(result),
        "derived_version": SCHEMA_VERSION,
        "clean": clean,
    }


# -- ingest ------------------------------------------------------------------

_COLUMNS = ("id", "source", "venue", "hand_no", "played_on", "variant",
            "seat_count", "n_players", "sb", "bb", "ante", "eff_stack_bb",
            "min_stack_bb", "max_stack_bb", "preflop_raises", "pot_type",
            "players_to_flop", "players_to_turn", "players_to_river",
            "last_street", "showdown", "known_cards", "all_cards_known",
            "pot_at_flop_bb", "flop_eff_stack_bb", "board", "positions",
            "stacks", "actions", "hole_cards", "players", "result",
            "derived_version", "file_id", "clean")


def ingest_file(db, path, source: str, variants=("NT",)) -> tuple:
    """Add one .phh/.phhs file. Returns (hands_seen, hands_added).

    A file already ingested (same bytes) is skipped outright. Hands are
    keyed by content, so an overlapping file adds only what is new.
    Variants other than `variants` (no-limit hold'em by default) are
    counted as seen and not stored.
    """
    path = pathlib.Path(path)
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if db.execute("SELECT 1 FROM files WHERE sha256 = ?", (digest,)).fetchone():
        return 0, 0
    blocks = _hand_blocks(raw.decode("utf-8"), path.suffix.lower())
    try:
        rel = str(path.resolve().relative_to(ROOT))
    except ValueError:
        rel = str(path.resolve())
    cur = db.execute(
        "INSERT INTO files (path, sha256, source, hands_seen, hands_added, ingested_at)"
        " VALUES (?, ?, ?, 0, 0, ?)",
        (rel, digest, source, _dt.datetime.now().isoformat(timespec="seconds")))
    file_id = cur.lastrowid
    rows, seen = [], 0
    for block in blocks:
        seen += 1
        if variants and block.get("variant") not in variants:
            continue
        row = derive(block, source)
        row["id"] = _content_id(source, block)
        row["file_id"] = file_id
        rows.append(tuple(row[c] for c in _COLUMNS))
    before = db.total_changes
    db.executemany(
        "INSERT OR IGNORE INTO hands (%s) VALUES (%s)"
        % (",".join(_COLUMNS), ",".join("?" * len(_COLUMNS))), rows)
    added = db.total_changes - before
    db.execute("UPDATE files SET hands_seen = ?, hands_added = ? WHERE id = ?",
               (seen, added, file_id))
    return seen, added


def ingest_path(db, root, source: str, verbose: bool = False) -> dict:
    """Ingest every .phh/.phhs under `root` (or one file). Commits once."""
    root = pathlib.Path(root)
    files = ([root] if root.is_file() else
             sorted(p for p in root.rglob("*") if p.suffix.lower() in (".phh", ".phhs")))
    totals = {"files": 0, "skipped_files": 0, "hands_seen": 0, "hands_added": 0}
    with db:
        for i, path in enumerate(files):
            seen, added = ingest_file(db, path, source)
            if seen == 0:
                totals["skipped_files"] += 1
            else:
                totals["files"] += 1
            totals["hands_seen"] += seen
            totals["hands_added"] += added
            if verbose and i % 500 == 0:
                print(json.dumps({"progress": i, "of": len(files), **totals}), flush=True)
    return totals


def rederive(db) -> int:
    """Rebuild the derived columns of rows older than SCHEMA_VERSION.

    Everything a row is derived from is stored with it (stacks, blinds,
    antes, actions, meta), so a changed derivation never needs the raw
    files. Returns how many rows were rebuilt.
    """
    stale = db.execute("SELECT * FROM hands WHERE derived_version < ?",
                       (SCHEMA_VERSION,)).fetchall()
    with db:
        for row in stale:
            stacks = json.loads(row["stacks"])
            block = {"starting_stacks": stacks["chips"],
                     "blinds_or_straddles": stacks["blinds"],
                     "antes": stacks["antes"],
                     "actions": json.loads(row["actions"]),
                     "variant": row["variant"], "venue": row["venue"],
                     "hand": row["hand_no"], "seat_count": row["seat_count"],
                     "players": json.loads(row["players"] or "null"),
                     "winnings": json.loads(row["result"] or "null")}
            fresh = derive(block, row["source"])
            fresh["played_on"] = row["played_on"]
            cols = [c for c in _COLUMNS if c not in ("id", "file_id")]
            db.execute("UPDATE hands SET %s WHERE id = ?"
                       % ",".join("%s = ?" % c for c in cols),
                       [fresh[c] for c in cols] + [row["id"]])
    return len(stale)


# -- reading -----------------------------------------------------------------

def _to_hand(row) -> Hand:
    d = dict(row)
    stacks = json.loads(d["stacks"])
    bb = d["bb"]
    return Hand(id=d["id"], source=d["source"], n_players=d["n_players"], bb=bb,
                positions=json.loads(d["positions"]),
                stacks_bb=[s / bb for s in stacks["chips"]],
                actions=json.loads(d["actions"]),
                hole_cards={int(k): v for k, v in json.loads(d["hole_cards"]).items()},
                board=d["board"] or "",
                antes_bb=[a / bb for a in stacks["antes"]],
                blinds_bb=[b / bb for b in player_blinds(stacks["blinds"])],
                row=d)


def query(db, where: str = "1", params: tuple = (), limit: int | None = None):
    """Yield `Hand`s matching a SQL WHERE clause over the derived columns."""
    sql = "SELECT * FROM hands WHERE %s" % where
    if limit is not None:
        sql += " LIMIT %d" % int(limit)
    for row in db.execute(sql, params):
        yield _to_hand(row)


def stats(db) -> dict:
    one = lambda sql: db.execute(sql).fetchall()
    return {
        "hands": db.execute("SELECT COUNT(*) FROM hands").fetchone()[0],
        "files": db.execute("SELECT COUNT(*) FROM files").fetchone()[0],
        "by_source": {r[0]: r[1] for r in one(
            "SELECT source, COUNT(*) FROM hands GROUP BY source")},
        "by_players": {r[0]: r[1] for r in one(
            "SELECT n_players, COUNT(*) FROM hands GROUP BY n_players")},
        "by_pot_type": {r[0]: r[1] for r in one(
            "SELECT pot_type, COUNT(*) FROM hands GROUP BY pot_type")},
        "players_to_flop": {r[0]: r[1] for r in one(
            "SELECT players_to_flop, COUNT(*) FROM hands GROUP BY players_to_flop")},
        "all_cards_known": db.execute(
            "SELECT COUNT(*) FROM hands WHERE all_cards_known = 1").fetchone()[0],
        "clean": db.execute(
            "SELECT COUNT(*) FROM hands WHERE clean = 1").fetchone()[0],
        "stale_rows": db.execute(
            "SELECT COUNT(*) FROM hands WHERE derived_version < ?",
            (SCHEMA_VERSION,)).fetchone()[0],
    }


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m bench.hand_db")
    ap.add_argument("--db", default=str(DEFAULT_DB))
    sub = ap.add_subparsers(dest="cmd", required=True)
    ing = sub.add_parser("ingest", help="add .phh/.phhs files (idempotent)")
    ing.add_argument("path")
    ing.add_argument("--source", required=True,
                     help="a short name for where these hands came from")
    sub.add_parser("stats", help="counts by source, table size, pot type")
    sub.add_parser("rederive", help="rebuild rows derived by an older version")
    args = ap.parse_args(argv)
    db = connect(args.db)
    if args.cmd == "ingest":
        print(json.dumps(ingest_path(db, args.path, args.source, verbose=True)))
    elif args.cmd == "rederive":
        print(json.dumps({"rebuilt": rederive(db)}))
    print(json.dumps(stats(db), indent=1))


if __name__ == "__main__":
    main()
