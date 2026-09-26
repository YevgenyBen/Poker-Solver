"""`bench/reference_params.py` - the file every external comparison here
has needed and none of them kept (M307).

Each test below pins a mistake this project has actually made while
writing this file by hand in a scratch script: an unmatched bet menu
(F64), an unmatched re-raise one action deeper (M241), a range the dump
cannot be read back against (F59), and a pot that is the node's rather
than the street's (M177).
"""
import pytest

from bench import reference_params as params


def _kwargs(**over):
    base = dict(pot=15.0, effective_stack=92.5, board=("Kd", "7c", "2h"),
                oop={"AA": 1.0, "72o": 0.25}, ip={"KK": 0.5},
                bet_pcts=(0.33, 0.75, 2.5), dump_path="out.json")
    base.update(over)
    return base


# -- the ranges the dump cannot carry ------------------------------------

def test_a_range_is_written_heaviest_first_with_its_weights():
    line = params.range_line({"72o": 0.25, "AA": 1.0, "KK": 0.5})
    assert line == "AA:1.0000,KK:0.5000,72o:0.2500"


def test_a_zero_weight_class_is_left_out_rather_than_written_as_zero():
    """A class at 0.0 is not in the range. Writing it invites the solver
    to round it up to its own parse floor."""
    assert params.range_line({"AA": 1.0, "72o": 0.0}) == "AA:1.0000"


def test_the_weights_survive_a_round_trip_through_the_parser():
    """F59: the dump does NOT carry the ranges, so a walk over it reads
    them from the params written beside it. If these two disagree the
    walk prices against a range the solve never saw, and every one of
    M256's four controls passes while it does."""
    from bench.dump_ev import parse_params_ranges

    text = params.params_text(**_kwargs())
    back = parse_params_ranges(text)
    assert back["oop"] == {"AA": 1.0, "72o": 0.25}
    assert back["ip"] == {"KK": 0.5}


def test_a_position_with_no_range_is_refused():
    with pytest.raises(ValueError, match="both positions need a range"):
        params.params_text(**_kwargs(ip={}))


# -- the menu, at the root and one action deeper -------------------------

def test_our_opening_menu_is_written_as_percentages_of_the_pot():
    """F64/M257: the reference was once handed (33, 75) while /advise
    offered 33/75/250 plus all-in. At a shallow stack the 250 IS the
    all-in and nothing shows; deeper, our overbet was scored as a 75%
    bet."""
    text = params.params_text(**_kwargs())
    assert "set_bet_sizes oop,flop,bet,33,75,250" in text
    assert "set_bet_sizes ip,flop,bet,33,75,250" in text


def test_both_players_get_the_menu_on_every_street():
    lines = params.bet_size_lines((0.33, 0.75, 2.5))
    for player in ("oop", "ip"):
        for street in params.STREETS:
            assert f"set_bet_sizes {player},{street},bet,33,75,250" in lines
            assert f"set_bet_sizes {player},{street},allin" in lines


def test_the_re_raise_menu_is_matched_one_action_deeper():
    """M241: facing a 5bb bet the reference could raise to 20bb and we to
    9.90bb - M222's void run in miniature, two arms solving different
    games while both look right at the root."""
    text = params.params_text(**_kwargs())
    assert f"set_bet_sizes oop,flop,raise,{params.RAISE_PCT}" in text
    assert f"set_bet_sizes ip,river,raise,{params.RAISE_PCT}" in text


def test_a_menu_with_no_bet_is_refused():
    """F40: check-or-shove is not our tree, and M151 measured what that
    collapse does to a strategy."""
    with pytest.raises(ValueError, match="check-or-shove"):
        params.params_text(**_kwargs(bet_pcts=()))


def test_the_menu_is_written_for_streets_the_dump_will_not_carry():
    """The TREE is what the flop strategy is solved against, so a menu
    truncated to the dumped rounds would change the game while leaving
    the flop looking identical."""
    text = params.params_text(**_kwargs(dump_rounds=1))
    assert "set_bet_sizes oop,river,bet,33,75,250" in text
    assert "set_dump_rounds 1" in text


# -- the spot's own pot and stack ----------------------------------------

def test_the_pot_and_stack_are_written_as_given():
    text = params.params_text(**_kwargs(pot=33.0, effective_stack=47.5))
    assert "set_pot 33" in text and "set_effective_stack 47.5" in text


@pytest.mark.parametrize("bad", [{"pot": 0.0}, {"pot": -1.0}, {"effective_stack": 0.0}])
def test_a_pot_or_stack_that_cannot_be_played_is_refused(bad):
    with pytest.raises(ValueError, match="must both be positive"):
        params.params_text(**_kwargs(**bad))


def test_the_board_is_written_comma_separated():
    text = params.params_text(**_kwargs(board=("Kd", "7c", "2h", "9s")))
    assert "set_board Kd,7c,2h,9s" in text


# -- the shape of the file -----------------------------------------------

def test_the_file_builds_then_solves_then_dumps_in_that_order():
    """`build_tree` after the ranges and menu, `start_solve` after the
    settings, `dump_result` last. Out of order the solver reads a
    half-specified game and still prints a figure (M257)."""
    lines = params.params_text(**_kwargs()).splitlines()
    order = [lines.index(next(l for l in lines if l.startswith(key)))
             for key in ("set_range_oop", "set_bet_sizes", "build_tree",
                         "set_max_iteration", "start_solve", "dump_result")]
    assert order == sorted(order)


def test_writing_it_puts_the_text_on_disk(tmp_path):
    out = params.write(tmp_path / "p.txt", **_kwargs())
    assert out.read_text(encoding="utf-8") == params.params_text(**_kwargs())


def test_the_defaults_are_the_converged_ones_the_project_measured():
    """0.32-0.50% of pot is what M221/M242/M256 call converged, and
    `reference_solver` refuses anything over 5%."""
    from bench import reference_solver

    assert params.DEFAULT_ACCURACY == 0.5
    assert params.DEFAULT_ACCURACY < reference_solver.MAX_PLAUSIBLE_EXPLOITABILITY_PCT
    assert params.DEFAULT_DUMP_ROUNDS == 1, "M298: three rounds on a flop is impossible here"


def test_the_class_weights_come_from_the_scenario_the_product_solved():
    """M164: a study that rebuilds the ranges agrees with production
    until it does not, and then measures its own reconstruction."""
    class Scenario:
        ranges = {"BB": {"AA": 1.0, "KK": 0.0, "72o": 0.5}}

    assert params.class_weights(Scenario(), "BB") == {"AA": 1.0, "72o": 0.5}
