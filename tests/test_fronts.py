"""Front summarisation (#12): a wartime brief has to stay small however big the war."""

from hoi4_harness.observation import render_delta, render_full
from hoi4_harness.observation.fronts import front_changes, summarise_fronts
from hoi4_harness.types import Front, GameState, War


def _twelve_front_war() -> GameState:
    fronts = [
        Front(name=f"sector_{i}", enemy="SOV", divisions_friendly=6, divisions_enemy=7)
        for i in range(9)
    ]
    fronts += [
        Front(name="karelia", enemy="SOV", divisions_friendly=12, divisions_enemy=50,
              pressure="losing_ground", supply=0.35),
        Front(name="salla", enemy="SOV", divisions_friendly=4, divisions_enemy=9,
              pocket_divisions=3),
        Front(name="petsamo", enemy="SOV", divisions_friendly=2, divisions_enemy=3,
              stance="offensive", pressure="advancing"),
    ]
    return GameState(
        country="FIN", country_name="Finland", date="1940-01-10",
        wars=[War(against="SOV", since="1939-11-30")], fronts=fronts,
    )


def test_twelve_fronts_collapse_to_the_three_that_matter_plus_a_fold():
    lines = summarise_fronts(_twelve_front_war().fronts)
    assert len(lines) == 4
    assert lines[0].startswith("Front karelia")          # losing ground ranks first
    assert lines[1].startswith("Front salla")            # then a pocket forming
    assert lines[2].startswith("Front petsamo")          # then an attack
    assert lines[3] == "+ 9 quiet sectors (54v63)"


def test_a_fold_that_hides_trouble_says_so():
    fronts = [
        Front(name=f"f{i}", enemy="GER", divisions_friendly=5, divisions_enemy=20,
              pressure="losing_ground")
        for i in range(5)
    ]
    lines = summarise_fronts(fronts, limit=3)
    assert len(lines) == 4
    assert "2 of them NOT QUIET" in lines[-1]


def test_few_fronts_are_all_shown_and_nothing_is_folded():
    fronts = [Front(name="west", enemy="FRA"), Front(name="east", enemy="POL")]
    lines = summarise_fronts(fronts)
    assert len(lines) == 2
    assert not any(line.startswith("+") for line in lines)


def test_a_front_line_carries_the_flags_that_need_a_decision():
    line = summarise_fronts([
        Front(name="karelia", enemy="SOV", divisions_friendly=12, divisions_enemy=50,
              pressure="losing_ground", supply=0.35, pocket_divisions=4,
              threatens_capital=True),
    ])[0]
    assert "LOSING GROUND" in line
    assert "SUPPLY 35%" in line
    assert "POCKET FORMING (4 divs at risk)" in line
    assert "CAPITAL THREATENED" in line


def test_unobserved_supply_is_not_printed_as_fine():
    line = summarise_fronts([Front(name="west", enemy="FRA")])[0]
    assert "supply" not in line.lower()


def test_a_twelve_front_wartime_brief_stays_within_a_few_hundred_tokens():
    brief = render_full(_twelve_front_war())
    # Four characters a token is the usual rough rule for English-and-numbers
    # text; the acceptance line is "a few hundred tokens".
    assert len(brief) / 4 < 300
    assert sum(1 for line in brief.splitlines() if "ront" in line or "sector" in line) <= 4


def test_the_delta_reports_a_front_changing_pressure():
    before = _twelve_front_war()
    after = _twelve_front_war()
    karelia = next(f for f in after.fronts if f.name == "karelia")
    karelia.pressure = "stable"
    text = render_delta(before, after)
    assert "Front karelia (12v50): losing_ground -> stable" in text


def test_the_delta_reports_supply_collapse_a_pocket_and_a_closed_front():
    before = [Front(name="west", enemy="FRA", supply=0.9), Front(name="east", enemy="POL")]
    after = [Front(name="west", enemy="FRA", supply=0.3, pocket_divisions=5)]
    lines = front_changes(before, after)
    assert any("supply 90% -> 30%" in line and "POCKET FORMING" in line for line in lines)
    assert "Front east: closed" in lines


def test_the_delta_does_not_report_supply_wobbling_inside_one_band():
    before = [Front(name="west", enemy="FRA", supply=0.9)]
    after = [Front(name="west", enemy="FRA", supply=0.8)]
    assert front_changes(before, after) == []


def test_front_changes_are_capped():
    before = [Front(name=f"f{i}", enemy="GER") for i in range(10)]
    after = [Front(name=f"f{i}", enemy="GER", pressure="losing_ground") for i in range(10)]
    lines = front_changes(before, after, limit=4)
    assert len(lines) == 5
    assert lines[-1] == "+ 6 more front change(s)"
