"""
This module provides a service for running heavy Python work and
discovering Python interpreters in a controlled way.

It centralizes synchronous and asynchronous background execution plus
subprocess-based code running and interpreter scanning.

It defines typed dictionaries for run results, interpreter descriptions,
and cached scan metadata.

It exposes a PythonRunnerService class that owns a thread pool executor,
a lock, and a cache of discovered interpreters.

The run_heavy and run_heavy_async methods offload callables to a thread
pool with configurable timeouts, raising TimeoutError on long-running
tasks.

The handle_run method writes provided Python source to a temporary file,
runs it via a specified interpreter with a timeout, and returns stdout,
stderr, and exit code.

The scan_python_versions method discovers Python interpreters on the
system, caches the results with a TTL, and uses helper methods to build
candidate paths and query interpreter versions.

Private helpers build candidate executable paths, probe each
interpreters version via subprocess, and clone cached results to avoid
external mutation.

At module level, a singleton PythonRunnerService is created and wrapped
by convenience functions run_heavy, run_heavy_async, handle_run,
scan_python_versions, and shutdown_runner_executor.

In a broader system, this module likely acts as a backend utility for
safely running user or tool code and selecting appropriate Python
interpreters without blocking the main application thread.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from subprocess import CompletedProcess

import asyncio
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError
from typing import Callable, TypedDict, TypeVar

ReturnT = TypeVar("ReturnT")

HEAVY_TASK_TIMEOUT_SECONDS: int = 50
PYTHON_SCAN_CACHE_TTL_SECONDS: float = 30.0


class RunResult(TypedDict):
    """
    Represent the result of executing Python code in a subprocess.

    This type groups standard output, error output, and the numeric exit
    status.

    Attributes:
        stdout (str):
            Captured text written to standard output during execution.
        stderr (str):
            Captured text written to standard error during execution,
            including tracebacks or diagnostic messages.
        returncode (int):
            (int): Process exit status code indicating success or
            failure of the execution.
    """

    stdout: str
    stderr: str
    returncode: int


class PythonVersion(TypedDict):
    """
    Represent a discovered Python interpreter and its version string.

    This type captures the executable path and a human readable version.

    Attributes:
        path (str):
            Absolute or resolved path to the Python executable that was
            found on the system.
        version (str):
            Textual version information reported by the Python
            executable, such as "Python 3.12.7" or a placeholder when
            unknown.
    """

    path: str
    version: str


class PythonScanCache(TypedDict):
    """
    Represent cached results for a Python interpreter version scan.

    This type stores both the discovered interpreters and when they
    expire.

    Attributes:
        deadline (float):
            Monotonic timestamp, in seconds, after which the cached scan
            should be treated as stale and recomputed.
        result (list[PythonVersion]):
            (list[PythonVersion]): List of discovered Python interpreter
            entries that were captured during the most recent scan.
    """

    deadline: float
    result: list[PythonVersion]


class PythonRunnerService:
    """
    Represent a service that executes Python code and scans
    interpreters.

    This class manages background execution, subprocess invocation, and
    caching of discovered Python interpreter versions.

    # Methods:

        run_heavy(
            func: Callable[..., ReturnT],
            *args: object, timeout: int,
            **kwargs: object,
        ) -> ReturnT:
            Run a callable in a background worker thread with a timeout
            and return its result, raising a timeout error if it takes
            too long.

        run_heavy_async(
            func: Callable[..., ReturnT],
            *args: object,
            timeout: int,
            **kwargs: object,
        ) -> ReturnT:
            Await the result of a callable executed in a background
            thread, enforcing an asynchronous timeout on the operation.

        handle_run(
            code: str,
            python_path: str,
        ) -> RunResult:
            Execute Python source code in a separate interpreter process
            and return a structured summary of its output and exit
            status.

        scan_python_versions(
            *,
            force: bool,
            ttl_seconds: float,
        ) -> list[PythonVersion]:
            Discover available Python interpreters on the system and
            return a possibly cached list of their paths and version
            strings.
    """

    RUN_SCRIPT_TIMEOUT_SECONDS: int = 10
    PYTHON_SCAN_VERSION_TIMEOUT_SECONDS: int = 2
    PYTHON_SCAN_MIN_TTL_SECONDS: float = 1.0
    PYTHON_VERSION_CANDIDATE_MIN: int = 8
    PYTHON_VERSION_CANDIDATE_MAX: int = 20

    def __init__(self) -> None:
        self._executor = ThreadPoolExecutor(
            max_workers=3, thread_name_prefix="ai_"
        )
        self._scan_lock = threading.Lock()
        self._scan_cache: PythonScanCache = {"deadline": 0.0, "result": []}

    def run_heavy(
        self,
        func: Callable[..., ReturnT],
        *args: object,
        timeout: int = HEAVY_TASK_TIMEOUT_SECONDS,
        **kwargs: object,
    ) -> ReturnT:
        """
        Execute a callable in a background thread with a timeout limit.

        This method delegates work to a shared thread pool and waits for
        a result up to the specified duration.

        Args:
            func (Callable[..., ReturnT]):
                The function or callable object to execute in a
                background worker thread.
            *args (object):
                Positional arguments that are forwarded directly to the
                callable when it is invoked.
            timeout (int):
                Maximum number of seconds to wait for the callable to
                finish before treating the operation as timed out.
            **kwargs (object):
                Keyword arguments that are forwarded directly to the
                callable when it is invoked.

        Returns:
            ReturnT:
                The value produced by the callable if it completes
                successfully within the configured timeout period.

        Raises:
            TimeoutError: If the callable does not complete execution
            before the timeout duration has elapsed.
        """
        future = self._executor.submit(func, *args, **kwargs)
        try:
            return future.result(timeout=timeout)
        except FuturesTimeoutError as exc:
            raise TimeoutError("The operation took too long") from exc

    async def run_heavy_async(
        self,
        func: Callable[..., ReturnT],
        *args: object,
        timeout: int = HEAVY_TASK_TIMEOUT_SECONDS,
        **kwargs: object,
    ) -> ReturnT:
        """
        Run a synchronous callable in a background thread with a
        timeout.

        This coroutine schedules blocking work on a shared executor and
        waits asynchronously for its result.

        Args:
            func (Callable[..., ReturnT]):
                The callable object to execute in a background worker
                thread.
            *args (object):
                Positional arguments forwarded directly to the callable
                when it is invoked.
            timeout (int):
                Maximum number of seconds to wait for the callable to
                complete before treating the operation as timed out.
            **kwargs (object):
                Keyword arguments forwarded directly to the callable
                when to the callable when it is invoked.

        Returns:
            ReturnT:
                The value produced by the callable if it finishes
                successfully with in the configured timeout period.

        Raises:
            TimeoutError:
                If the callable does not complete execution before the
                timeout duration has elapsed.
        """
        loop = asyncio.get_running_loop()
        future = loop.run_in_executor(
            executor=self._executor, func=lambda: func(*args, **kwargs)
        )
        try:
            return await asyncio.wait_for(fut=future, timeout=timeout)
        except asyncio.TimeoutError as exc:
            raise TimeoutError("The operation took too long") from exc

    def handle_run(self, code: str, python_path: str) -> RunResult:
        """
        Execute Python source code in a subprocess and collect its
        results.

        This method writes code to a temporary file, runs it with a
        specified interpreter, and reports the outcome in a structured
        form.

        Args:
            code (str):
                The Python source text to serialize into a temporary
                script and execute.
            python_path (str):
                Filesystem path to the Python interpreter used to run
                the temporary script.

        Returns:
            RunResult:
                A mapping that summarizes execution, including captured
                standard output, standard error, and the process return
                code. On timeout or unexpected errors, the mapping
                reflects the failure in the stderr field and uses a
                negative return code.

        Raises:
            OSError:
                If interacting with the temporary file fails in a way
                that prevents the subprocess from being started.
        """
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".py", delete=False, encoding="utf-8"
            ) as temp_file:
                temp_file.write(code)
                path: str = temp_file.name
            result: CompletedProcess[str] = subprocess.run(  # noqa: S603
                [python_path, path],
                capture_output=True,
                timeout=self.RUN_SCRIPT_TIMEOUT_SECONDS,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
            try:
                os.unlink(path)
            except OSError:
                pass
            return {
                "stdout": result.stdout,
                "stderr": result.stderr,
                "returncode": result.returncode,
            }
        except subprocess.TimeoutExpired:
            return {
                "stdout": "",
                "stderr": f"Execution time limit exceeded ({self.RUN_SCRIPT_TIMEOUT_SECONDS} s)",
                "returncode": -1,
            }
        except Exception as exc:
            return {"stdout": "", "stderr": str(exc), "returncode": -1}

    def scan_python_versions(
        self,
        *,
        force: bool = False,
        ttl_seconds: float = PYTHON_SCAN_CACHE_TTL_SECONDS,
    ) -> list[PythonVersion]:
        """
        Scan for available Python interpreters and return cached
        results.

        This method reuses a recent scan when valid and refreshes the
        cache only when necessary or explicitly requested.

        Args:
            force (bool):
                Whether to bypass the existing cache and always perform
                a fresh scan of Python interpreter candidates.
            ttl_seconds (float):
                Time-to-live, in seconds, that determines how long a
                cached scan remains valid before it is considered stale.

        Returns:
            list[PythonVersion]:
                A list of dictionaries, each describing a discovered
                Python interpreter with its executable path and
                reported version string.
        """
        now: float = time.monotonic()
        with self._scan_lock:
            cached: list[PythonVersion] = self._scan_cache["result"]
            deadline: float = self._scan_cache["deadline"]
            if not force and cached and now < deadline:
                return self._clone_python_versions(versions=cached)
        result: list[PythonVersion] = self._scan_python_versions_uncached()
        with self._scan_lock:
            self._scan_cache["result"] = self._clone_python_versions(
                versions=result
            )
            self._scan_cache["deadline"] = now + max(
                self.PYTHON_SCAN_MIN_TTL_SECONDS, ttl_seconds
            )
        return result

    def shutdown(self) -> None:
        """
        Shut down the internal thread pool executor used for heavy
        tasks.

        This method stops accepting new work and initiates executor
        teardown without waiting for in-flight tasks to finish.
        """
        self._executor.shutdown(wait=False)

    def _scan_python_versions_uncached(self) -> list[PythonVersion]:
        """
        Discover Python interpreter versions without using the cache.

        This method inspects candidate executable paths and records each
        unique interpreter along with its reported version string.

        Returns:
            list[PythonVersion]:
                A list of dictionaries, where each entry contains the
                executable path of a discovered Python interpreter and
                its associated version string.
        """
        discovered_versions: list[PythonVersion] = []
        seen_executables: set[str] = set()
        candidate_paths: list[str | None] = (
            self._build_python_candidate_paths()
        )
        for executable_path in candidate_paths:
            if not executable_path or executable_path in seen_executables:
                continue
            seen_executables.add(executable_path)
            discovered_versions.append(
                {
                    "path": executable_path,
                    "version": self._detect_python_version(executable_path),
                }
            )
        return discovered_versions

    def _build_python_candidate_paths(self) -> list[str | None]:
        """
        Build an ordered list of candidate Python interpreter paths.

        This helper gathers likely executable locations for different
        Python versions to use during interpreter discovery.

        Returns:
            list[str | None]:
                A list of potential Python executable paths, including
                the current interpreter, generic "python" launchers,
                and several version-specific candidates. Entries that
                cannot be resolved may be None and should be filtered by
                the caller.
        """
        return [
            sys.executable,
            shutil.which("python3"),
            shutil.which("python"),
            *[
                shutil.which(f"python3.{minor_version}")
                for minor_version in range(
                    self.PYTHON_VERSION_CANDIDATE_MIN,
                    self.PYTHON_VERSION_CANDIDATE_MAX + 1,
                )
            ],
        ]

    def _detect_python_version(self, executable_path: str) -> str:
        """
        Determine the human-readable version string for a Python
        executable.

        This helper invokes the target interpreter with --version and
        normalizes the reported output into a single text value.

        Args:
            executable_path (str):
                Filesystem path to the Python interpreter whose version
                should be queried.

        Returns:
            str:
                A trimmed version string reported by the interpreter, or
                "?" when the version cannot be determined.
        """
        try:
            version_result: CompletedProcess[str] = (
                subprocess.run(  # noqa: S603
                    [executable_path, "--version"],
                    capture_output=True,
                    timeout=self.PYTHON_SCAN_VERSION_TIMEOUT_SECONDS,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    check=False,
                )
            )
            return (
                version_result.stdout or version_result.stderr or ""
            ).strip() or "?"
        except Exception:
            return "?"

    @staticmethod
    def _clone_python_versions(
        versions: list[PythonVersion],
    ) -> list[PythonVersion]:
        """
        Create a shallow, normalized copy of discovered Python versions.

        This helper duplicates each interpreter entry so callers cannot
        mutate the internal cache directly.

        Args:
            versions (list[PythonVersion]):
                The list of Python interpreter records to replicate into
                a new list structure.

        Returns:
            list[PythonVersion]:
                A new list containing copies of each input record,
                preserving their executable paths and version strings
                while decoupling them from the original container.
        """
        return [
            {"path": item["path"], "version": item["version"]}
            for item in versions
        ]


_RUNNER_SERVICE: PythonRunnerService = PythonRunnerService()


def run_heavy(
    func: Callable[..., ReturnT],
    *args: object,
    timeout: int = HEAVY_TASK_TIMEOUT_SECONDS,
    **kwargs: object,
) -> ReturnT:
    """
    Run a synchronous function in a background thread with a timeout.

    This helper offloads heavy work to a shared thread pool executor.

    Args:
        func (Callable[..., ReturnT]):
            The callable object to execute in the background.
        *args (object):
            Positional arguments to pass to the callable.
        timeout (int):
            Maximum number of seconds to wait for completion before
            raising a timeout.
        **kwargs (object):
            Keyword arguments to pass to the callable.

    Returns:
        ReturnT
            The value returned by the callable when it completes
            successfully within the timeout period.

    Raises:
        TimeoutError
            If the callable does not finish execution before the timeout
            elapses.
    """
    return _RUNNER_SERVICE.run_heavy(func, *args, timeout=timeout, **kwargs)


async def run_heavy_async(
    func: Callable[..., ReturnT],
    *args: object,
    timeout: int = HEAVY_TASK_TIMEOUT_SECONDS,
    **kwargs: object,
) -> ReturnT:
    """
    Run a synchronous function in a background thread with a timeout.

    This helper offloads heavy work to a shared thread pool executor.

    Args:
        func (Callable[..., ReturnT]):
            The callable object to execute in the background.
        *args (object):
            Positional arguments to pass to the callable.
        timeout (int):
            Maximum number of seconds to wait for completion before
            raising a timeout.
        **kwargs (object):
            Keyword arguments to pass to the callable.

    Returns:
        ReturnT
            The value returned by the callable when it completes
            successfully within the timeout period.

    Raises:
        TimeoutError
            If the callable does not finish execution before the timeout
            elapses.
    """
    return await _RUNNER_SERVICE.run_heavy_async(
        func, *args, timeout=timeout, **kwargs
    )


def handle_run(code: str, python_path: str) -> RunResult:
    """
    Execute Python source code in a temporary file and capture output.

    This helper runs the code with a given interpreter and reports
    results.

    Args:
        code (str):
            Python source code to be written to a temporary script and
            executed.
        python_path (str):
            Filesystem path to the Python interpreter that should be
            used to run the temporary script.

    Returns:
        RunResult:
            A dictionary containing "stdout", "stderr", and "returncode"
            keys that describe the execution outcome.

    Raises:
        OSError:
            If creating or deleting the temporary file fails in a way
            that prevents execution.
    """
    return _RUNNER_SERVICE.handle_run(code, python_path)


def scan_python_versions(
    *, force: bool = False, ttl_seconds: float = PYTHON_SCAN_CACHE_TTL_SECONDS
) -> list[PythonVersion]:
    """
    Retrieve a cached list of Python interpreters, refreshing as needed.

    This function wraps an uncached system scan with simple time-based
    caching.

    Args:
        force (bool):
            Whether to ignore any existing cache and force a fresh scan
            of available Python interpreters.
        ttl_seconds (float):
            Number of seconds a cached scan result remains valid before
            a new scan is triggered.

    Returns:
        list[PythonVersion]:
            A list of dictionaries describing each discovered Python
            interpreter, including its executable path and version
            string.
    """
    return _RUNNER_SERVICE.scan_python_versions(
        force=force, ttl_seconds=ttl_seconds
    )


def shutdown_runner_executor() -> None:
    """
    Shut down the shared thread pool executor for heavy tasks.

    This function stops accepting new work and begins executor teardown.
    """
    _RUNNER_SERVICE.shutdown()
