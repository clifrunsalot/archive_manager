"""Create event manifests before files are ingested by the watcher."""

import argparse
import os
import re
import sys
import uuid
from pathlib import Path

from archive_manager.core.event_manifests import load_manifests, save_manifests
from archive_manager.core.event_model import EventManifest, PageMetadata, SUPPORTED_EVENT_TYPES
from archive_manager.paths import PROJECT_ROOT

MANIFEST_PATH = Path(os.environ.get("EVENT_MANIFEST_PATH", PROJECT_ROOT / "data" / "events.json"))


def create_manifest(
    event_id: str,
    event_type: str,
    filenames: list[str],
    subject_ref: str | None = None,
    metadata: dict | None = None,
) -> EventManifest:
    """Create an ordered manifest for a batch of files."""
    pages = [
        PageMetadata(source_filename=filename, page_number=index, page_count=len(filenames))
        for index, filename in enumerate(filenames, start=1)
    ]
    return EventManifest(
        event_id=event_id,
        event_type=event_type,
        subject_ref=subject_ref,
        pages=pages,
        metadata=metadata or {},
    )


SUPPORTED_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg"}


def natural_sort_key(path: Path):
    """Sort filenames so numeric page components are ordered numerically."""
    import re

    return [int(part) if part.isdigit() else part.casefold() for part in re.split(r"(\d+)", path.name)]


def collect_input_files(input_path: str, pattern: str | None = None) -> list[Path]:
    """Collect one file or supported files from one directory."""
    path = Path(input_path).expanduser().resolve()
    if not path.exists():
        raise ValueError(f"Path does not exist: {input_path}")
    if path.is_file():
        candidates = [path]
    elif path.is_dir():
        candidates = [child for child in path.iterdir() if child.is_file()]
    else:
        raise ValueError(f"Path is not a file or directory: {input_path}")

    if pattern:
        if len(pattern) > 200:
            raise ValueError("Filename pattern must be 200 characters or fewer")
        try:
            filename_pattern = re.compile(pattern, re.IGNORECASE)
        except re.error as exc:
            raise ValueError(f"Invalid filename pattern: {exc}") from exc
        candidates = [candidate for candidate in candidates if filename_pattern.search(candidate.name)]

    files = sorted(
        [candidate for candidate in candidates if candidate.suffix.lower() in SUPPORTED_EXTENSIONS],
        key=natural_sort_key,
    )
    if not files:
        raise ValueError("No supported PDF or image files were found")
    return files


def prompt_event_type() -> str:
    """Prompt for a supported event type and return its value."""
    event_types = sorted(SUPPORTED_EVENT_TYPES)
    print("Event types:")
    for index, event_type in enumerate(event_types, start=1):
        print(f"  {index}. {event_type}")
    while True:
        choice = input("Event type [1]: ").strip() or "1"
        if choice.isdigit() and 1 <= int(choice) <= len(event_types):
            return event_types[int(choice) - 1]
        if choice in SUPPORTED_EVENT_TYPES:
            return choice
        print("Enter a listed number or event type.")


def interactive_add(input_path: str | None = None, pattern: str | None = None) -> EventManifest:
    """Interactively collect and confirm one single-file or page-set event."""
    while True:
        selected_path = input_path or input("File or directory path: ").strip()
        try:
            selected_pattern = pattern or input("Filename regex [all supported files]: ").strip() or None
            files = collect_input_files(selected_path, selected_pattern)
            break
        except ValueError as exc:
            if input_path:
                raise
            print(f"Unable to add files: {exc}")

    print(f"Detected {len(files)} supported file(s):")
    for index, path in enumerate(files, start=1):
        print(f"  {index}. {path.name}")

    while True:
        accepted = input("Accept this page order? [Y/n]: ").strip().lower()
        if accepted in {"", "y", "yes"}:
            break
        if accepted in {"n", "no"}:
            raise SystemExit("Intake cancelled.")
        print("Enter Y or N.")

    event_type = prompt_event_type()
    event_id = input("Event ID [generated]: ").strip() or f"evt_{uuid.uuid4().hex}"
    subject_ref = input("Opaque subject reference [optional]: ").strip() or None
    manifest = create_manifest(
        event_id,
        event_type,
        [path.name for path in files],
        subject_ref=subject_ref,
    )
    manifests = load_manifests(MANIFEST_PATH)
    if event_id in manifests:
        raise ValueError(f"Event ID already exists: {event_id}")
    save_manifests(MANIFEST_PATH, {**manifests, event_id: manifest})
    print(f"Created event manifest {event_id} with {len(files)} page(s) at {MANIFEST_PATH}")
    return manifest


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "add":
        parser = argparse.ArgumentParser(description="Interactively add one archive event")
        parser.add_argument("command", choices=["add"])
        parser.add_argument("path_positional", nargs="?", help="File or directory; omit to be prompted")
        parser.add_argument("--path", dest="path_named", help="File or directory; omit to be prompted")
        parser.add_argument("--pattern", help="Regex matched against filenames")
        args = parser.parse_args()
        if args.path_positional and args.path_named:
            parser.error("provide the path either positionally or with --path, not both")
        interactive_add(args.path_named or args.path_positional, args.pattern)
        return

    parser = argparse.ArgumentParser(
        description="Create an archive event manifest. Use 'intake.py add' for interactive intake."
    )
    parser.add_argument("files", nargs="*", help="Files belonging to one event")
    parser.add_argument("--file", dest="named_files", action="append", default=[], help="Page file; repeat for multiple files")
    parser.add_argument("--directory", type=Path, help="Directory containing event pages")
    parser.add_argument("--pattern", help="Regex matched against filenames")
    parser.add_argument("--event-id", default=None)
    parser.add_argument("--event-type", choices=sorted(SUPPORTED_EVENT_TYPES), default="general_document")
    parser.add_argument("--subject-ref", default=None)
    args = parser.parse_args()

    if args.directory and (args.files or args.named_files):
        parser.error("use --directory separately from file arguments")
    if args.directory:
        try:
            filenames = [path.name for path in collect_input_files(str(args.directory), args.pattern)]
        except ValueError as exc:
            parser.error(str(exc))
    else:
        filenames = [Path(filename).name for filename in [*args.files, *args.named_files]]
    if not filenames:
        parser.error("provide files, --file PATH, --directory DIR, or use 'add'")

    event_id = args.event_id or f"evt_{uuid.uuid4().hex}"
    manifest = create_manifest(event_id, args.event_type, filenames, args.subject_ref)
    manifests = load_manifests(MANIFEST_PATH)
    if event_id in manifests:
        raise SystemExit(f"Event ID already exists: {event_id}")
    manifests[event_id] = manifest
    save_manifests(MANIFEST_PATH, manifests)
    print(f"Created event manifest {event_id} with {len(filenames)} pages at {MANIFEST_PATH}")


if __name__ == "__main__":
    main()