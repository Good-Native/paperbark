"""Live ticker for ``paperbark monitor``.

Renders the running-monitor status line into a :class:`rich.live.Live` panel so
the user sees a heartbeat (spinner, elapsed time, iteration count, captured
total, time-until-next-snapshot) while flyctl captures are in flight. Mirrors
the bash dispatcher's animator: ``◐ ◓ ◑ ◒`` quarter-circle spinner at 5 Hz, and
the same field set as ``reference/logs.sh``'s ``ticker_animator``.

The renderer is a pure function (:func:`render_status`) so tests can pin its
output without spinning up a real terminal. :class:`MonitorAnimator` is the
thin context-manager wrapper around :class:`rich.live.Live` + the redraw
thread; CLI code uses it, tests use ``render_status`` directly.

Why not :class:`rich.spinner.Spinner` directly: the bash animator keeps
ticking the elapsed clock and the next-snapshot countdown *between* state
publishes, so the renderer needs the current monotonic time on every frame —
not just whatever was set on the last :func:`MonitorAnimator.update` call.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from types import TracebackType
from typing import Self

from rich.console import Console
from rich.live import Live
from rich.text import Text

from paperbark.dispatcher import MonitorState
from paperbark.duration import format_elapsed

# Quarter-circle spinner — same set as the bash ticker because Braille glyphs
# render too small in some terminals (notably VS Code's integrated terminal).
SPINNER_FRAMES: tuple[str, ...] = ("◐", "◓", "◑", "◒")
DEFAULT_FPS = 5


def render_status(
    state: MonitorState | None,
    frame: int,
    *,
    elapsed_override: int | None = None,
    next_snapshot_override: int | None = None,
) -> Text:
    """Build the ticker line for ``state`` at the given spinner ``frame``.

    The override kwargs let the redraw thread tick ``elapsed`` and the
    snapshot countdown forward between :func:`MonitorAnimator.update` calls
    so the line stays alive even during a long flyctl capture. They default
    to the values on ``state``; tests can pass them explicitly to pin output.

    Returns a :class:`rich.text.Text` instance — non-styled fields go through
    plain ``append`` so ``text.plain`` round-trips for assertions.
    """
    spin = SPINNER_FRAMES[frame % len(SPINNER_FRAMES)]
    if state is None:
        # No iteration has completed yet; show a plain spinner so the user
        # sees the process is alive even before the first capture lands.
        return Text(f"   {spin} starting…", style="dim")

    elapsed = elapsed_override if elapsed_override is not None else state.elapsed_seconds
    elapsed_str = format_elapsed(elapsed)

    iter_part = str(state.iteration)
    if state.iterations_max > 0:
        iter_part = f"{state.iteration} / {state.iterations_max}"

    text = Text("   ")
    text.append(spin, style="bold cyan")
    text.append(" ")
    text.append(elapsed_str, style="bold cyan")
    text.append(" - ", style="dim")
    text.append(iter_part, style="bold cyan")
    text.append(" - ", style="dim")
    text.append(str(state.captured_total), style="bold cyan")
    text.append(" logs")

    next_snapshot = (
        next_snapshot_override
        if next_snapshot_override is not None
        else state.next_snapshot_seconds
    )
    if next_snapshot >= 0:
        text.append(" - ", style="dim")
        text.append(f"next snapshot {format_elapsed(next_snapshot)}", style="dim")

    return text


class MonitorAnimator:
    """Context-managed live ticker driven by :class:`MonitorState` updates.

    Use as ``with MonitorAnimator(console) as ticker: ...`` and pass
    ``ticker.update`` as the loop's ``on_state`` callback. The redraw thread
    keeps the spinner and elapsed clock fresh at :data:`DEFAULT_FPS` even
    during long flyctl captures; ``update`` snapshots the latest published
    :class:`MonitorState` so the next redraw picks it up.
    """

    def __init__(
        self,
        console: Console | None = None,
        *,
        fps: int = DEFAULT_FPS,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._console = console if console is not None else Console()
        self._fps = fps
        # Exposed so the CLI can render the startup banner through the same
        # Console; rich.live.Live renders ``console.print`` calls above the
        # active live region while ``transient=False``.
        self.console = self._console
        self._monotonic = monotonic
        self._state: MonitorState | None = None
        self._state_published_at: float | None = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._frame = 0
        self._thread: threading.Thread | None = None
        self._live: Live | None = None

    def __enter__(self) -> Self:
        self._stop.clear()
        self._frame = 0
        self._live = Live(
            render_status(None, 0),
            console=self._console,
            refresh_per_second=self._fps,
            transient=False,
        )
        self._live.__enter__()
        self._thread = threading.Thread(
            target=self._tick_forever, name="paperbark-animator", daemon=True
        )
        self._thread.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
        if self._live is not None:
            self._live.__exit__(exc_type, exc, tb)

    def update(self, state: MonitorState) -> None:
        """Publish a fresh :class:`MonitorState` to the redraw thread."""
        with self._lock:
            self._state = state
            self._state_published_at = self._monotonic()
        # Force one redraw immediately so the new counter values appear without
        # waiting up to ``1/fps`` seconds for the next tick.
        if self._live is not None:
            self._live.update(self._compose())

    def _tick_forever(self) -> None:
        period = 1.0 / self._fps
        while not self._stop.wait(period):
            self._frame += 1
            if self._live is not None:
                self._live.update(self._compose())

    def _compose(self) -> Text:
        with self._lock:
            state = self._state
            published_at = self._state_published_at
        if state is None or published_at is None:
            return render_status(None, self._frame)
        # Tick elapsed and the snapshot countdown forward between publishes
        # so the line stays alive during a slow capture.
        delta = max(int(self._monotonic() - published_at), 0)
        elapsed = state.elapsed_seconds + delta
        if state.next_snapshot_seconds < 0:
            next_snapshot = -1
        else:
            next_snapshot = max(state.next_snapshot_seconds - delta, 0)
        return render_status(
            state,
            self._frame,
            elapsed_override=elapsed,
            next_snapshot_override=next_snapshot,
        )
