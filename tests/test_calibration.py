import json
import sys

import pytest

from hoi4_harness.adapters.input_driver import InputDriverAdapter
from hoi4_harness.calibration import (
    SCHEMA_VERSION,
    Calibration,
    capture_calibration,
    load_calibration,
    mouse_position,
    save_calibration,
    screen_size,
)

POINT = {"production": (1234, 567)}


def write_calibration(tmp_path, data):
    path = tmp_path / "calibration.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_saving_and_loading_round_trips(tmp_path):
    path = save_calibration(Calibration(screen=(1920, 1080), coordinates=POINT), tmp_path / "c.json")
    loaded = load_calibration(path, current_resolution=(1920, 1080))
    assert loaded.screen == (1920, 1080)
    assert loaded.coordinates == POINT


def test_the_file_records_the_resolution_and_the_coordinates(tmp_path):
    path = save_calibration(Calibration(screen=(1920, 1080), coordinates=POINT), tmp_path / "c.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["screen"] == [1920, 1080]
    assert data["coordinates"]["production"] == [1234, 567]


def test_saving_creates_the_run_directory(tmp_path):
    path = save_calibration(Calibration(screen=(1, 1), coordinates={}), tmp_path / "runs" / "c.json")
    assert path.exists()


def test_a_calibration_from_another_resolution_is_refused_and_names_both(tmp_path):
    path = save_calibration(Calibration(screen=(1920, 1080), coordinates=POINT), tmp_path / "c.json")
    with pytest.raises(RuntimeError) as refused:
        load_calibration(path, current_resolution=(2560, 1440))
    assert "1920x1080" in str(refused.value)
    assert "2560x1440" in str(refused.value)
    assert "calibrate" in str(refused.value)


def test_a_missing_calibration_file_is_a_clear_error(tmp_path):
    with pytest.raises(FileNotFoundError, match="calibrate"):
        load_calibration(tmp_path / "nowhere.json")


def test_a_file_from_another_schema_version_is_refused(tmp_path):
    path = write_calibration(
        tmp_path, {"version": SCHEMA_VERSION + 1, "screen": [1920, 1080], "coordinates": {}}
    )
    with pytest.raises(ValueError, match="schema version"):
        load_calibration(path)


def test_a_corrupt_file_is_reported_against_the_file_not_the_json_library(tmp_path):
    path = tmp_path / "calibration.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(RuntimeError, match=r"calibration\.json is not valid JSON"):
        load_calibration(path)


def test_a_malformed_coordinate_names_its_target(tmp_path):
    path = write_calibration(
        tmp_path, {"version": SCHEMA_VERSION, "screen": [1920, 1080],
                   "coordinates": {"production": [1, 2, 3]}}
    )
    with pytest.raises(ValueError, match="production"):
        load_calibration(path)


def test_the_walk_records_one_coordinate_per_target():
    prompts: list[str] = []
    aims = iter([(10, 20), (30, 40), (50, 60)])

    def fake_input(prompt: str) -> str:
        prompts.append(prompt)
        return ""

    calibration = capture_calibration(
        ("a", "b", "c"),
        input_fn=fake_input,
        screen_size_fn=lambda: (1920, 1080),
        mouse_position_fn=lambda: next(aims),
    )
    assert calibration.screen == (1920, 1080)
    assert calibration.coordinates == {"a": (10, 20), "b": (30, 40), "c": (50, 60)}
    assert len(prompts) == 3 and "'a'" in prompts[0]


def test_the_adapter_loader_populates_coordinates(tmp_path):
    path = save_calibration(Calibration(screen=(1920, 1080), coordinates=POINT), tmp_path / "c.json")
    adapter = InputDriverAdapter(dry_run=True)
    adapter.load_calibration(path, current_resolution=(1920, 1080))
    adapter.click("production")
    assert adapter.log == ["click production at (1234, 567)"]


def test_a_live_adapter_refuses_a_stale_calibration_and_loads_nothing(tmp_path):
    path = save_calibration(Calibration(screen=(1920, 1080), coordinates=POINT), tmp_path / "c.json")
    adapter = InputDriverAdapter(dry_run=False)
    with pytest.raises(RuntimeError, match="2560x1440"):
        adapter.load_calibration(path, current_resolution=(2560, 1440))
    assert adapter.config.coordinates == {}


def test_a_dry_run_loads_without_asking_for_a_screen(tmp_path):
    # No pyautogui here, and a dry run sends no clicks, so it must load anyway.
    path = save_calibration(Calibration(screen=(1920, 1080), coordinates=POINT), tmp_path / "c.json")
    adapter = InputDriverAdapter(dry_run=True)
    adapter.load_calibration(path)
    assert adapter.config.coordinates == POINT


@pytest.mark.parametrize("probe", [screen_size, mouse_position])
def test_screen_probes_name_the_missing_extra(monkeypatch, probe):
    monkeypatch.setitem(sys.modules, "pyautogui", None)
    with pytest.raises(RuntimeError, match=r"hoi4-agent-harness\[input\]"):
        probe()
