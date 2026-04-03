from __future__ import annotations

import sys
import time

import pytest

from viewer_app.runtime import python_runner

_CODE_PRINTS_FORTY_TWO: str = "print(40+2)"
_RUN_HEAVY_SHORT_TIMEOUT_S: int = 1
_RUN_HEAVY_DEFAULT_TIMEOUT_S: int = 5
_SCAN_VERSIONS_TTL_S: float = 1.0
_SLOW_CALLABLE_SLEEP_S: float = 60.0
_EXPECTED_SYNC_HEAVY_VALUE: int = 7
_EXPECTED_ASYNC_HEAVY_VALUE: int = 99


def test_handle_run_reports_success_and_captured_stdout() -> None:
    result = python_runner.handle_run(_CODE_PRINTS_FORTY_TWO, sys.executable)

    assert result["returncode"] == 0  # noqa: S101
    assert "42" in result["stdout"]  # noqa: S101


def test_run_heavy_returns_callable_result_before_timeout() -> None:
    returned = python_runner.run_heavy(
        lambda: _EXPECTED_SYNC_HEAVY_VALUE,
        timeout=_RUN_HEAVY_DEFAULT_TIMEOUT_S,
    )

    assert returned == _EXPECTED_SYNC_HEAVY_VALUE  # noqa: S101


def test_run_heavy_raises_timeout_when_callable_exceeds_limit() -> None:
    def blocks_longer_than_timeout() -> int:
        time.sleep(_SLOW_CALLABLE_SLEEP_S)
        return 1

    with pytest.raises(TimeoutError):
        python_runner.run_heavy(
            blocks_longer_than_timeout, timeout=_RUN_HEAVY_SHORT_TIMEOUT_S
        )


@pytest.mark.asyncio
async def test_run_heavy_async_returns_awaited_result() -> None:
    returned = await python_runner.run_heavy_async(
        lambda: _EXPECTED_ASYNC_HEAVY_VALUE,
        timeout=_RUN_HEAVY_DEFAULT_TIMEOUT_S,
    )

    assert returned == _EXPECTED_ASYNC_HEAVY_VALUE  # noqa: S101


def test_scan_python_versions_lists_current_interpreter() -> None:
    discovered = python_runner.scan_python_versions(
        force=True,
        ttl_seconds=_SCAN_VERSIONS_TTL_S,
    )

    assert isinstance(discovered, list)  # noqa: S101
    interpreter_paths = {entry["path"] for entry in discovered}
    assert sys.executable in interpreter_paths  # noqa: S101
