#!/usr/bin/python3
"""
Extract selected events from agent JSONL logs.

Modes:
- exec:     toolCall name == "exec"         -> arguments.command
- thinking: content item type == "thinking" -> item.thinking
- web:      toolCall name == "web_search"   -> arguments.query
- fetch:    toolCall name == "web_fetch"    -> arguments.url
- file:     toolCall name in {"read","write","edit"} -> file path only
- all:      exec + thinking + web + fetch + file

Output:
<timestamp>\t<event_type>\t<payload>

Notes:
- event_type is colorized (TTY only)
- for file events, event_type is the specific action: read/write/edit
- thinking output is kept to one line by default (newlines escaped). Use --keep-newlines to preserve.
- --last N prints only the last N matched output records.
- If no input file paths are provided, autodetects the latest session file from:
    ~/.openclaw/agents/main/sessions/sessions.json
  by reading key "agent:main:main" and its "sessionFile".
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict, Iterable, Optional, Set, Tuple, List


# ---------- ANSI colors ----------
USE_COLOR = sys.stdout.isatty()

RESET = "\033[0m"
TS_COLOR = "\033[36m"       # cyan timestamp

# Event label colors
LABEL_COLORS = {
    "exec": "\033[33m",      # yellow
    "thinking": "\033[32m",  # green
    "web": "\033[35m",       # magenta
    "fetch": "\033[34m",     # blue

    # file sub-events
    "read": "\033[94m",      # bright blue
    "write": "\033[91m",     # bright red
    "edit": "\033[93m",      # bright yellow
}


def _c(text: str, color: str) -> str:
    if not USE_COLOR:
        return text
    return f"{color}{text}{RESET}"


def _c_ts(text: str) -> str:
    return _c(text, TS_COLOR)


def _c_label(label: str) -> str:
    return _c(label, LABEL_COLORS.get(label, "\033[37m"))  # default gray
# --------------------------------


def _safe_get(d: Any, *keys: str) -> Any:
    cur = d
    for k in keys:
        if not isinstance(cur, dict) or k not in cur:
            return None
        cur = cur[k]
    return cur


def _iter_lines(paths: list[str]) -> Iterable[tuple[str, str]]:
    for p in paths:
        if p == "-":
            for line in sys.stdin:
                yield ("<stdin>", line)
        else:
            with open(p, "r", encoding="utf-8", errors="replace") as f:
                print(f"[info] Loaded session file: {p}\n")
                for line in f:
                    yield (p, line)


def _get_timestamp(obj: Dict[str, Any]) -> str:
    ts: Optional[str] = obj.get("timestamp")
    if not ts:
        ts_val = _safe_get(obj, "message", "timestamp")
        if ts_val is not None:
            ts = str(ts_val)
    return ts or "UNKNOWN_TIME"


def _message_content(obj: Dict[str, Any]) -> Optional[list]:
    content = _safe_get(obj, "message", "content")
    return content if isinstance(content, list) else None


def _autodetect_session_file() -> str:
    """
    Reads ~/.openclaw/agents/main/sessions/sessions.json, finds key "agent:main:main",
    returns its "sessionFile".
    """
    sessions_path = os.path.expanduser("~/.openclaw/agents/main/sessions/sessions.json")
    try:
        with open(sessions_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        raise RuntimeError(f"Autodetect failed: sessions file not found: {sessions_path}")
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Autodetect failed: invalid JSON in {sessions_path}: {e}")

    main = data.get("agent:main:main")
    if not isinstance(main, dict):
        raise RuntimeError('Autodetect failed: missing key "agent:main:main" in sessions.json')

    session_file = main.get("sessionFile")
    if not isinstance(session_file, str) or not session_file.strip():
        raise RuntimeError('Autodetect failed: "agent:main:main.sessionFile" missing/empty')

    return session_file.strip()


def _extract_execs(obj: Dict[str, Any]) -> List[Tuple[str, str]]:
    out: List[Tuple[str, str]] = []
    if obj.get("type") != "message":
        return out

    ts = _get_timestamp(obj)
    content = _message_content(obj)
    if content is None:
        return out

    for item in content:
        if not isinstance(item, dict):
            continue
        if item.get("type") != "toolCall":
            continue
        if item.get("name") != "exec":
            continue

        cmd = _safe_get(item, "arguments", "command")
        if isinstance(cmd, str) and cmd.strip():
            out.append((ts, cmd.strip()))

    return out


def _extract_thinking(obj: Dict[str, Any]) -> List[Tuple[str, str]]:
    out: List[Tuple[str, str]] = []
    if obj.get("type") != "message":
        return out

    ts = _get_timestamp(obj)
    content = _message_content(obj)
    if content is None:
        return out

    for item in content:
        if not isinstance(item, dict):
            continue
        if item.get("type") != "thinking":
            continue

        thinking = item.get("thinking")
        if isinstance(thinking, str) and thinking.strip():
            out.append((ts, thinking.strip()))

    return out


def _extract_web_searches(obj: Dict[str, Any]) -> List[Tuple[str, str]]:
    out: List[Tuple[str, str]] = []
    if obj.get("type") != "message":
        return out

    ts = _get_timestamp(obj)
    content = _message_content(obj)
    if content is None:
        return out

    for item in content:
        if not isinstance(item, dict):
            continue
        if item.get("type") != "toolCall":
            continue
        if item.get("name") != "web_search":
            continue

        query = _safe_get(item, "arguments", "query")
        if isinstance(query, str) and query.strip():
            out.append((ts, query.strip()))

    return out


def _extract_web_fetches(obj: Dict[str, Any]) -> List[Tuple[str, str]]:
    out: List[Tuple[str, str]] = []
    if obj.get("type") != "message":
        return out

    ts = _get_timestamp(obj)
    content = _message_content(obj)
    if content is None:
        return out

    for item in content:
        if not isinstance(item, dict):
            continue
        if item.get("type") != "toolCall":
            continue
        if item.get("name") != "web_fetch":
            continue

        url = _safe_get(item, "arguments", "url")
        if isinstance(url, str) and url.strip():
            out.append((ts, url.strip()))

    return out


def _extract_file_events(obj: Dict[str, Any]) -> List[Tuple[str, str, str]]:
    """
    Returns list of (timestamp, action, path) where action in {"read","write","edit"}.
    We only print filenames/paths, not content.
    """
    out: List[Tuple[str, str, str]] = []
    if obj.get("type") != "message":
        return out

    ts = _get_timestamp(obj)
    content = _message_content(obj)
    if content is None:
        return out

    for item in content:
        if not isinstance(item, dict):
            continue
        if item.get("type") != "toolCall":
            continue

        action = item.get("name")
        if action not in {"read", "write", "edit"}:
            continue

        args = item.get("arguments") if isinstance(item.get("arguments"), dict) else {}
        path = (
            args.get("path")
            or args.get("file_path")
            or args.get("filepath")
            or args.get("filename")
            or args.get("target")
        )

        if isinstance(path, str) and path.strip():
            out.append((ts, action, path.strip()))
        else:
            out.append((ts, action, "UNKNOWN_PATH"))

    return out


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Extract exec/thinking/web/fetch/file events from JSONL logs."
    )
    ap.add_argument(
        "modes",
        nargs="+",
        help="One or more modes: exec, thinking, web, fetch, file, all",
    )
    ap.add_argument(
        "paths",
        nargs="*",
        help="JSONL file(s) to parse, '-' for stdin. If omitted, autodetect from sessions.json",
    )
    ap.add_argument(
        "--errors",
        choices=["ignore", "stderr"],
        default="stderr",
        help="What to do with malformed JSON lines (default: stderr)",
    )
    ap.add_argument(
        "--no-color",
        action="store_true",
        help="Disable colored output even if stdout is a terminal.",
    )
    ap.add_argument(
        "--keep-newlines",
        action="store_true",
        help="Print thinking blocks with real newlines (default: escape to keep one log line per event).",
    )
    ap.add_argument(
        "--last",
        type=int,
        default=0,
        metavar="N",
        help="Only print the last N matching records (default: print all).",
    )

    args = ap.parse_args()

    modes: Set[str] = set(args.modes)
    valid = {"exec", "thinking", "web", "fetch", "file", "all"}
    unknown = sorted(modes - valid)
    if unknown:
        print(
            f"[error] Unknown mode(s): {', '.join(unknown)}. "
            f"Valid: exec, thinking, web, fetch, file, all",
            file=sys.stderr,
        )
        return 2

    # Expand "all"
    if "all" in modes:
        modes.update({"exec", "thinking", "web", "fetch", "file"})
        modes.discard("all")

    global USE_COLOR
    if args.no_color:
        USE_COLOR = False

    # Autodetect input file if none supplied
    paths = list(args.paths)
    if not paths:
        try:
            paths = [_autodetect_session_file()]
        except RuntimeError as e:
            print(f"[error] {e}", file=sys.stderr)
            return 2

    output_lines: List[str] = []

    for src, line in _iter_lines(paths):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as e:
            if args.errors == "stderr":
                print(f"[warn] {src}: invalid JSON: {e}", file=sys.stderr)
            continue

        if not isinstance(obj, dict):
            continue

        if "exec" in modes:
            for ts, cmd in _extract_execs(obj):
                output_lines.append(f"{_c_ts(ts)}\t{_c_label('exec')}\t{cmd}")

        if "thinking" in modes:
            for ts, t in _extract_thinking(obj):
                payload = t if args.keep_newlines else t.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\\n")
                output_lines.append(f"{_c_ts(ts)}\t{_c_label('thinking')}\t{payload}")

        if "web" in modes:
            for ts, q in _extract_web_searches(obj):
                output_lines.append(f"{_c_ts(ts)}\t{_c_label('web')}\t{q}")

        if "fetch" in modes:
            for ts, u in _extract_web_fetches(obj):
                output_lines.append(f"{_c_ts(ts)}\t{_c_label('fetch')}\t{u}")

        if "file" in modes:
            for ts, action, path in _extract_file_events(obj):
                output_lines.append(f"{_c_ts(ts)}\t{_c_label(action)}\t{path}")

    if args.last and args.last > 0:
        output_lines = output_lines[-args.last:]

    for out in output_lines:
        print(out)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
