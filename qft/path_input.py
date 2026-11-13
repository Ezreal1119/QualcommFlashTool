"""Folder-only completion for the interactive terminal prompt."""

import os
import shlex


def folder_completions(value):
    def matches(prefix):
        parent, name = os.path.split(prefix)
        try:
            with os.scandir(os.path.expanduser(parent or ".")) as entries:
                return sorted(
                    os.path.join(parent, entry.name) + "/"
                    for entry in entries
                    if entry.name.startswith(name)
                    and (not entry.name.startswith(".") or name.startswith("."))
                    and entry.is_dir()
                    and not any(ord(c) < 32 or ord(c) == 127 for c in entry.name)
                )
        except OSError:
            return []

    # Prefer literal paths, just as folder_path does. Finder may instead insert
    # shell escapes or quotes; accept those, including an unfinished quote.
    result = matches(value)
    if result:
        return result
    try:
        try:
            values = shlex.split(value)
        except ValueError:
            values = shlex.split(value + value[0]) if value[:1] in ("'", '"') else []
        return matches(values[0]) if len(values) == 1 else []
    except ValueError:
        return []


def read_folder():
    try:
        import readline
    except ImportError:
        return input("Firmware folder: ")

    previous = readline.get_completer()
    delimiters = readline.get_completer_delims()
    candidates = []

    def complete(value, state):
        nonlocal candidates
        if state == 0:
            candidates = folder_completions(value)
        return candidates[state] if state < len(candidates) else None

    try:
        # The complete line is a single path, including spaces and punctuation.
        readline.set_completer_delims("")
        readline.set_completer(complete)
        readline.parse_and_bind(
            "bind ^I rl_complete"
            if "libedit" in (readline.__doc__ or "")
            else "tab: complete"
        )
        return input("Firmware folder: ")
    finally:
        readline.set_completer(previous)
        readline.set_completer_delims(delimiters)
