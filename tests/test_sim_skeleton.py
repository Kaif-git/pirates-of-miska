import json
import subprocess
import sys
from pathlib import Path

import pytest

from sim import __version__
from sim.config import load_config


ROOT = Path(__file__).resolve().parents[1]


def test_headless_sim_package_has_a_data_driven_default_config():
    config = load_config()

    assert __version__
    assert config["schema_version"] == 1
    assert config["time"] == {
        "hours_per_day": 24,
        "days_per_year": 365,
        "hours_per_tick": 1,
    }
    assert "WORLD_GENERATED" in config["event_rules"]["root_event_types"]


def test_config_loader_accepts_an_explicit_json_file(tmp_path):
    config_path = tmp_path / "sim.json"
    config_path.write_text(json.dumps({
        "schema_version": 1,
        "time": {"hours_per_day": 20, "days_per_year": 300, "hours_per_tick": 1},
        "event_rules": {"root_event_types": ["WORLD_GENERATED"]},
    }), encoding="utf-8")

    assert load_config(config_path)["time"]["hours_per_day"] == 20


@pytest.mark.parametrize("payload", [
    "[]",
    '{"schema_version":"1","time":{}}',
    '{"schema_version":1,"time":{"hours_per_day":24,"days_per_year":365}}',
    '{"schema_version":1,"time":{"hours_per_day":0,"days_per_year":365,"hours_per_tick":1}}',
])
def test_config_loader_rejects_invalid_shape_or_clock_values(tmp_path, payload):
    config_path = tmp_path / "invalid.json"
    config_path.write_text(payload, encoding="utf-8")

    with pytest.raises(ValueError):
        load_config(config_path)


def test_importing_sim_package_does_not_load_game_or_renderer():
    probe = subprocess.run(
        [sys.executable, "-c", "import sys, sim; "
         "assert 'pirates.game' not in sys.modules; "
         "assert 'play' not in sys.modules; "
         "assert not any(name.startswith('omnisim.') for name in sys.modules)"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert probe.returncode == 0, probe.stderr
