import runpy
from unittest.mock import patch

from pathlib import Path

_REPOSITORY_ROOT_PARENT_LEVEL: int = 1


def _repository_root() -> Path:
    return Path(__file__).resolve().parents[_REPOSITORY_ROOT_PARENT_LEVEL]


def test_study_md_desk_entry_script_calls_viewer_main_when_run_as_main() -> (
    None
):
    entry_script = _repository_root() / "study_md_desk.py"

    with patch("viewer_app.app.main.main") as mock_main:
        runpy.run_path(str(entry_script), run_name="__main__", init_globals={})

    mock_main.assert_called_once()
