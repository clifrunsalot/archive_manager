"""Watch the archive directory and ingest newly added or updated documents.

This script monitors the project archive folder for file creation and modification
activity. It waits for a file to stabilize, deduplicates repeated filesystem
notifications, and submits valid document files to the ingestion pipeline.
"""

import argparse
import select
import sys
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

try:
    import termios
    import tty
except ImportError:  # pragma: no cover - Windows does not provide these modules
    termios = None
    tty = None

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

from archive_manager.ingestion.ingest import ingest_pdf
from archive_manager.paths import PROJECT_ROOT


WATCH_DIR = Path(os.environ.get("ARCHIVE_DIR", PROJECT_ROOT / "ARCHIVE"))
POLL_STABLE_SECONDS = 1.5
SUPPORTED_EXT = {".pdf", ".png", ".jpg", ".jpeg"}
MAX_WORKERS = int(os.environ.get("ARCHIVE_INGEST_WORKERS", "1"))
EVENT_DEDUPE_SECONDS = 10


def wait_for_file_stable(path: Path, timeout_seconds: int = 60):
    """Block until a file stops changing or until the timeout is reached.

    This prevents the ingestion worker from reading incomplete uploads while the
    file is still being written by another process.
    """
    deadline = time.monotonic() + timeout_seconds
    last_size = -1
    stable_for = 0.0
    last_mtime_ns = None

    while time.monotonic() < deadline:
        if not path.exists():
            time.sleep(0.2)
            continue

        stat = path.stat()
        size = stat.st_size
        mtime_ns = stat.st_mtime_ns

        if size == last_size and mtime_ns == last_mtime_ns and size > 0:
            stable_for += 0.2
            if stable_for >= POLL_STABLE_SECONDS:
                return
        else:
            stable_for = 0.0

        last_size = size
        last_mtime_ns = mtime_ns
        time.sleep(0.2)

    raise TimeoutError(f"File did not stabilize: {path}")


class Handler(FileSystemEventHandler):
    """Filesystem event handler that schedules archive ingestion jobs."""

    def __init__(self, max_workers: int = MAX_WORKERS):
        """Create the event handler and the thread pool used for ingestion."""
        self._lock = threading.Lock()
        self._inflight = set()
        self._recent = {}
        self._paused = False
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="archive_ingest")

    def set_paused(self, paused: bool):
        """Pause or resume scheduling new ingestion jobs."""
        with self._lock:
            self._paused = paused

    def toggle_paused(self) -> bool:
        """Toggle scheduling and return the new paused state."""
        with self._lock:
            self._paused = not self._paused
            return self._paused

    def _dedupe_key(self, path: Path):
        """Return a stable signature for a file event used to suppress duplicates."""
        try:
            stat = path.stat()
        except FileNotFoundError:
            return None
        return (str(path.resolve()), stat.st_size, stat.st_mtime_ns)

    def _should_process(self, path: Path):
        """Return True when a file should be scheduled for ingestion."""
        key = self._dedupe_key(path)
        if key is None:
            return False

        now = time.monotonic()
        with self._lock:
            if key in self._inflight:
                return False
            prev = self._recent.get(key)
            if prev is not None and now - prev < EVENT_DEDUPE_SECONDS:
                return False
            self._recent[key] = now
            self._inflight.add(key)
        return True

    def _finish(self, path: Path):
        """Clear the in-flight tracking entry after ingestion completes."""
        key = self._dedupe_key(path)
        if key is not None:
            with self._lock:
                self._inflight.discard(key)

    def _process_file(self, path: Path):
        """Wait for file stability and ingest a single archive document."""
        try:
            wait_for_file_stable(path)
            ingest_pdf(path, source_filename=path.name)
        except Exception as exc:
            print(f"[watch_archive] Error ingesting {path}: {exc}", file=sys.stderr)
        finally:
            self._finish(path)

    def on_any_event(self, event):
        """Process relevant filesystem events for archive documents."""
        if event.is_directory:
            return

        with self._lock:
            if self._paused:
                return

        src_path = event.src_path
        if isinstance(src_path, bytes):
            src_path = src_path.decode("utf-8", errors="surrogateescape")
        dest_path = getattr(event, "dest_path", "")
        if isinstance(dest_path, bytes):
            dest_path = dest_path.decode("utf-8", errors="surrogateescape")

        p = Path(str(src_path or dest_path or ""))
        if not p or p.suffix.lower() not in SUPPORTED_EXT:
            return

        if not p.exists():
            return

        if not self._should_process(p):
            return

        self._executor.submit(self._process_file, p)


def main():
    """Start the archive watcher and block until the process is interrupted."""
    parser = argparse.ArgumentParser(description="Watch a directory and ingest new archive files.")
    parser.add_argument(
        "--watch-dir",
        type=Path,
        default=WATCH_DIR,
        help=f"Directory to monitor (default: {WATCH_DIR})",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=MAX_WORKERS,
        help=f"Concurrent ingestion workers (default: {MAX_WORKERS})",
    )
    args = parser.parse_args()
    if args.workers < 1:
        parser.error("--workers must be at least 1")

    args.watch_dir.mkdir(parents=True, exist_ok=True)
    obs = Observer()
    handler = Handler(max_workers=args.workers)
    obs.schedule(handler, str(args.watch_dir), recursive=False)
    obs.start()
    print(f"Watching: {args.watch_dir}")
    terminal_module = termios
    tty_module = tty
    interactive_input = False
    original_terminal = None
    if sys.stdin.isatty() and terminal_module is not None and tty_module is not None:
        interactive_input = True
        original_terminal = terminal_module.tcgetattr(sys.stdin.fileno())
        tty_module.setcbreak(sys.stdin.fileno())
        print("Keyboard: press p or Space to pause/resume; Ctrl+C to stop.")
    try:
        while True:
            if interactive_input:
                readable, _, _ = select.select([sys.stdin], [], [], 1.0)
                if readable:
                    key = sys.stdin.read(1).lower()
                    if key in {"p", " "}:
                        paused = handler.toggle_paused()
                        print("Watcher paused." if paused else "Watcher resumed.")
            else:
                time.sleep(1)
    except KeyboardInterrupt:
        obs.stop()
    finally:
        if original_terminal is not None and terminal_module is not None:
            terminal_module.tcsetattr(sys.stdin.fileno(), terminal_module.TCSADRAIN, original_terminal)
    obs.join()


if __name__ == "__main__":
    main()