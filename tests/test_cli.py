"""The play command's output contract: JSON on stdout, progress on stderr."""

import json

from hoi4_harness.cli import main


def play_args(tmp_path, *extra):
    return ["play", "--turns", "2", "--adapter", "mock", "--provider", "scripted",
            "--run-dir", str(tmp_path / "run"), *extra]


def test_play_prints_progress_to_stderr_and_json_to_stdout(tmp_path, capsys):
    code = main(play_args(tmp_path))
    out, err = capsys.readouterr()
    assert code == 0
    assert json.loads(out)["turns"] == 2          # stdout stays machine-readable
    assert len(err.strip().splitlines()) == 2     # one line per turn
    assert "wake:" in err


def test_play_quiet_leaves_stderr_empty(tmp_path, capsys):
    code = main(play_args(tmp_path, "--quiet"))
    out, err = capsys.readouterr()
    assert code == 0
    assert err == ""
    assert json.loads(out)["turns"] == 2
