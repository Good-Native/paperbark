"""PyPI version check + opt-in self-upgrade for the paperbark CLI.

The CLI calls :func:`maybe_run` once after argparse and before subcommand
dispatch. Behaviour is governed by :class:`paperbark.config.AutoupdateConfig`
and a small set of environment / flag overrides:

- ``PAPERBARK_NO_AUTO_UPDATE=1`` or ``--no-auto-update`` skips the check.
- ``--yes`` / ``-y`` auto-accepts a prompt without asking.
- The ``init`` and ``--version`` paths short-circuit before this runs.

Network and subprocess errors are swallowed; the auto-update path is best-
effort and must never break the user's actual command. PyPI is only contacted
once per ``check_interval_hours`` (default 24), with the result cached at
``~/.cache/paperbark/last_check.json``. The cache also tracks declined
versions so we don't re-prompt for a release the user already said no to.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any
from urllib.error import URLError
from urllib.request import Request, urlopen

from paperbark import __version__

PYPI_URL = "https://pypi.org/pypi/paperbark/json"
PYPI_TIMEOUT_SECONDS = 2.0
PROMPT_TIMEOUT_SECONDS = 10.0
UPGRADE_TIMEOUT_SECONDS = 60.0


@dataclass(frozen=True, slots=True)
class _Cache:
    """On-disk record of the last PyPI lookup.

    ``last_check`` is a Unix timestamp; ``latest_version`` is whatever PyPI
    reported at that time (may equal the local version). ``declined_version``
    records a release the user declined at the prompt so we don't ask again
    for the same one — a newer release clears the suppression.
    """

    last_check: float
    latest_version: str
    declined_version: str = ""


def maybe_run(
    *,
    enabled: bool,
    mode: str,
    check_interval_hours: int,
    assume_yes: bool = False,
    stdin: IO[str] | None = None,
    stdout: IO[str] | None = None,
    stderr: IO[str] | None = None,
    argv: list[str] | None = None,
) -> None:
    """Entry point invoked from the CLI.

    All knobs are passed in so this stays trivially testable. The function
    returns ``None`` regardless of outcome; on a successful synchronous
    upgrade it replaces the current process via :func:`os.execv` and never
    returns.
    """
    if not enabled or mode == "off":
        return
    if os.environ.get("PAPERBARK_NO_AUTO_UPDATE"):
        return
    if _is_editable_install():
        return
    # Skip the PyPI lookup entirely for installs we can't safely upgrade
    # (system Python, Homebrew). _detect_upgrade_command returns None for
    # those, and we'd otherwise burn a network round-trip just to refuse.
    if _detect_upgrade_command() is None:
        return

    out = stdout if stdout is not None else sys.stdout
    err = stderr if stderr is not None else sys.stderr
    in_ = stdin if stdin is not None else sys.stdin

    cache_path = _cache_path()
    cache = _load_cache(cache_path)
    interval_seconds = max(0, check_interval_hours) * 3600

    latest = _get_latest_version(cache, interval_seconds, cache_path)
    if latest is None:
        return
    if not _is_newer(latest, __version__):
        return

    effective_mode = mode
    is_tty = _both_are_tty(in_, out)
    if effective_mode == "prompt" and not is_tty:
        # Falling back to notify keeps non-interactive contexts (CI, pipes)
        # quiet but still informative.
        effective_mode = "notify"

    if effective_mode == "notify":
        _print_notify(latest, err)
        return

    if effective_mode == "prompt":
        if cache is not None and cache.declined_version == latest:
            return
        accepted = assume_yes or _ask(latest, in_, out)
        if not accepted:
            _record_decline(cache_path, latest)
            return
    elif effective_mode == "auto":
        # Falls through to the upgrade path without asking.
        pass
    else:
        return

    _run_upgrade_and_relaunch(out, err, argv)


def _print_notify(latest: str, stream: IO[str]) -> None:
    stream.write(
        f"paperbark {latest} available (you have {__version__}). "
        f"Run 'pipx upgrade paperbark' (or 'pip install -U paperbark').\n"
    )
    stream.flush()


def _ask(latest: str, stdin: IO[str], stdout: IO[str]) -> bool:
    """Prompt with a short timeout. Treat Ctrl+C / timeout as decline."""
    prompt = f"paperbark {latest} available (you have {__version__}). Upgrade now? [Y/n] "
    stdout.write(prompt)
    stdout.flush()
    try:
        line = _readline_with_timeout(stdin, PROMPT_TIMEOUT_SECONDS)
    except (KeyboardInterrupt, EOFError):
        stdout.write("\n")
        return False
    if line is None:
        stdout.write("\n(no response — skipping upgrade)\n")
        stdout.flush()
        return False
    answer = line.strip().lower()
    return answer in ("", "y", "yes")


def _readline_with_timeout(stdin: IO[str], timeout: float) -> str | None:
    """Read one line from ``stdin`` with a timeout, returning ``None`` on expiry.

    Uses :func:`select.select` on the underlying file descriptor; falls back
    to a plain blocking read when ``stdin`` is not a real fd-backed stream
    (e.g. an in-memory buffer in tests). Windows ``select`` does not accept
    file objects, so we also fall back there.
    """
    import select

    try:
        fd = stdin.fileno()
    except (AttributeError, OSError, ValueError):
        return stdin.readline()

    if sys.platform.startswith("win"):
        return stdin.readline()

    ready, _, _ = select.select([fd], [], [], timeout)
    if not ready:
        return None
    return stdin.readline()


def _run_upgrade_and_relaunch(stdout: IO[str], stderr: IO[str], argv: list[str] | None) -> None:
    cmd = _detect_upgrade_command()
    if cmd is None:
        stderr.write(
            "could not detect installer (pipx/pip); run 'pipx upgrade paperbark' manually.\n"
        )
        return
    stdout.write(f"running: {' '.join(cmd)}\n")
    stdout.flush()
    try:
        proc = subprocess.run(  # noqa: S603 - args are a fixed allowlist
            cmd,
            check=False,
            timeout=UPGRADE_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        stderr.write(
            f"upgrade timed out after {UPGRADE_TIMEOUT_SECONDS:.0f}s; "
            f"run '{' '.join(cmd)}' manually.\n"
        )
        return
    except (OSError, subprocess.SubprocessError) as exc:
        stderr.write(f"upgrade failed: {exc}\n")
        return
    if proc.returncode != 0:
        stderr.write(f"upgrade exited with status {proc.returncode}\n")
        return

    # Replace the current process with the (now upgraded) entry point so the
    # user's command runs against the new version. Skipped on Windows where
    # the semantics differ enough to be risky in a fire-and-forget path.
    if sys.platform.startswith("win"):
        stdout.write("upgrade complete — please re-run the command.\n")
        return

    new_argv = argv if argv is not None else sys.argv
    binary = shutil.which(new_argv[0]) or new_argv[0]
    try:
        os.execv(binary, [binary, *new_argv[1:]])  # noqa: S606 - intentional process replacement
    except OSError as exc:
        stderr.write(f"relaunch failed ({exc}); please re-run the command.\n")


def _detect_upgrade_command() -> list[str] | None:
    """Pick the right upgrade command for how paperbark was installed.

    pipx wins if the running interpreter sits inside a pipx venvs tree
    (the common case for this project); otherwise we fall back to
    ``python -m pip install -U`` against the running interpreter, which
    works for plain venvs and ``pip --user`` installs alike. System /
    Homebrew installs return ``None`` so we don't try to mutate a
    package-manager-owned tree.
    """
    exec_path = Path(sys.executable).resolve()
    if "pipx" in exec_path.parts and "venvs" in exec_path.parts:
        pipx = shutil.which("pipx")
        if pipx is not None:
            return [pipx, "upgrade", "paperbark"]
    if _looks_like_system_python(exec_path):
        return None
    return [sys.executable, "-m", "pip", "install", "--upgrade", "paperbark"]


def _looks_like_system_python(exec_path: Path) -> bool:
    """Heuristic: paths under /usr or Homebrew's Cellar are package-manager owned."""
    parts = exec_path.parts
    if parts[:2] == ("/", "usr") and "local" not in parts[:3]:
        return True
    return "Cellar" in parts


def _is_editable_install() -> bool:
    """Skip auto-update when running from a source checkout.

    Editable installs have ``__init__.py`` under a path that doesn't include
    ``site-packages``. ``importlib.metadata.version`` already returns the
    pinned ``0.0.0+unknown`` sentinel in that case, but checking the path is
    cheaper than parsing the version string.
    """
    try:
        from paperbark import __file__ as init_file
    except ImportError:
        return True
    return "site-packages" not in Path(init_file).parts


def _both_are_tty(stdin: IO[str], stdout: IO[str]) -> bool:
    return _is_tty(stdin) and _is_tty(stdout)


def _is_tty(stream: IO[str]) -> bool:
    isatty = getattr(stream, "isatty", None)
    if isatty is None:
        return False
    try:
        return bool(isatty())
    except (OSError, ValueError):
        return False


def _cache_path() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "paperbark" / "last_check.json"


def _load_cache(path: Path) -> _Cache | None:
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    last_check = data.get("last_check")
    latest_version = data.get("latest_version")
    declined_version = data.get("declined_version", "")
    if not isinstance(last_check, int | float):
        return None
    if not isinstance(latest_version, str):
        return None
    if not isinstance(declined_version, str):
        declined_version = ""
    return _Cache(
        last_check=float(last_check),
        latest_version=latest_version,
        declined_version=declined_version,
    )


def _write_cache(path: Path, cache: _Cache) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as f:
            json.dump(
                {
                    "last_check": cache.last_check,
                    "latest_version": cache.latest_version,
                    "declined_version": cache.declined_version,
                },
                f,
            )
    except OSError:
        return


def _record_decline(path: Path, version: str) -> None:
    cache = _load_cache(path)
    if cache is None:
        cache = _Cache(last_check=time.time(), latest_version=version)
    _write_cache(
        path,
        _Cache(
            last_check=cache.last_check,
            latest_version=cache.latest_version,
            declined_version=version,
        ),
    )


def _get_latest_version(
    cache: _Cache | None, interval_seconds: int, cache_path: Path
) -> str | None:
    """Return the latest PyPI version, hitting the network at most once per interval."""
    now = time.time()
    if cache is not None and (now - cache.last_check) < interval_seconds:
        return cache.latest_version

    latest = _fetch_pypi_version()
    if latest is None:
        # On network failure, fall back to whatever we cached previously.
        return cache.latest_version if cache is not None else None

    declined = cache.declined_version if cache is not None else ""
    # Drop a stale decline once a newer release supersedes it.
    if declined and _is_newer(latest, declined):
        declined = ""
    _write_cache(
        cache_path,
        _Cache(last_check=now, latest_version=latest, declined_version=declined),
    )
    return latest


def _fetch_pypi_version() -> str | None:
    request = Request(PYPI_URL, headers={"Accept": "application/json"})  # noqa: S310 - https-only constant
    try:
        with urlopen(request, timeout=PYPI_TIMEOUT_SECONDS) as response:  # noqa: S310
            payload: Any = json.load(response)
    except (URLError, TimeoutError, OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    info = payload.get("info")
    if not isinstance(info, dict):
        return None
    version_raw = info.get("version")
    if not isinstance(version_raw, str) or not version_raw:
        return None
    return version_raw


def _is_newer(candidate: str, current: str) -> bool:
    """Return ``True`` if ``candidate`` is a strictly newer release than ``current``.

    Uses :class:`packaging.version.Version` when available; falls back to a
    tuple-of-ints comparison so the auto-update path doesn't introduce a
    hard dependency on ``packaging``. The fallback is conservative: if either
    string can't be parsed as a dotted-int triple it returns ``False``.
    """
    try:
        from packaging.version import InvalidVersion, Version
    except ImportError:  # pragma: no cover — packaging ships with pip in practice.
        return _is_newer_fallback(candidate, current)
    try:
        return Version(candidate) > Version(current)
    except InvalidVersion:
        return _is_newer_fallback(candidate, current)


def _is_newer_fallback(candidate: str, current: str) -> bool:
    def _parts(value: str) -> tuple[int, ...] | None:
        try:
            return tuple(int(p) for p in value.split("+", 1)[0].split(".") if p)
        except ValueError:
            return None

    cand = _parts(candidate)
    curr = _parts(current)
    if cand is None or curr is None:
        return False
    return cand > curr
