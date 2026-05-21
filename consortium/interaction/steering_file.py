"""File-based steering queue — cross-node analogue of http_steering.

The HTTP steering server only works for foreground runs (it binds to
127.0.0.1, so the VSCode extension on a login node cannot reach a runner
on a compute node). This module gives the runner a way to consume steering
instructions written by the extension/CLI to a JSONL file on shared
storage, no networking required.

Wire-format (inbox.jsonl, append-only): one JSON object per line. Required
field ``kind`` selects the action; remaining fields depend on the kind.

    {"id": "...", "ts": "...", "kind": "interrupt"}
    {"id": "...", "ts": "...", "kind": "instruction",
     "text": "focus on linear case only", "type": "m"}

The poller writes per-instruction acks to outbox.jsonl. A cursor file
(``<inbox>.cursor``) tracks the last-seen byte offset so the runner
doesn't reprocess instructions on resume.
"""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from queue import Queue
from typing import Optional


_POLL_INTERVAL_SEC = 2.0


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _append_outbox(outbox: Path, record: dict) -> None:
    try:
        outbox.parent.mkdir(parents=True, exist_ok=True)
        with outbox.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, sort_keys=True) + "\n")
    except OSError as exc:
        print(f"[steering_file] failed to write ack to {outbox}: {exc}")


def _enqueue_instruction(queue: Queue, text: str, choice: str) -> None:
    """Push lines onto the queue the same way a human typist would.

    Matches the semantics of ``http_steering.py`` so the runner's input
    interpreter doesn't need to know which channel an instruction came in
    on.
    """
    for line in text.splitlines():
        queue.put(line)
    queue.put("")  # first Enter
    queue.put("")  # second Enter — double-Enter terminates the message
    queue.put(choice)


def _process_record(queue: Queue, record: dict, outbox: Path) -> None:
    instr_id = str(record.get("id") or _now_iso())
    kind = str(record.get("kind") or "").lower()

    if kind == "interrupt":
        queue.put("interrupt")
        _append_outbox(outbox, {"id": instr_id, "kind": kind, "status": "queued", "ts": _now_iso()})
        return

    if kind == "instruction":
        text = str(record.get("text") or "").strip()
        choice = str(record.get("type") or "m").strip().lower()
        if not text or choice not in ("m", "n"):
            _append_outbox(outbox, {
                "id": instr_id, "kind": kind, "status": "rejected",
                "reason": "invalid text or type", "ts": _now_iso(),
            })
            return
        _enqueue_instruction(queue, text, choice)
        _append_outbox(outbox, {"id": instr_id, "kind": kind, "status": "queued", "ts": _now_iso()})
        return

    _append_outbox(outbox, {
        "id": instr_id, "kind": kind or "unknown", "status": "rejected",
        "reason": f"unknown kind: {kind!r}", "ts": _now_iso(),
    })


def _read_cursor(cursor_path: Path) -> int:
    try:
        return int(cursor_path.read_text().strip() or 0)
    except (OSError, ValueError):
        return 0


def _write_cursor(cursor_path: Path, offset: int) -> None:
    try:
        cursor_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = cursor_path.with_suffix(cursor_path.suffix + ".tmp")
        tmp.write_text(str(offset))
        os.replace(tmp, cursor_path)
    except OSError as exc:
        print(f"[steering_file] failed to update cursor {cursor_path}: {exc}")


def poll_inbox_jsonl(queue: Queue, inbox: str, outbox: str, *, interval: float = _POLL_INTERVAL_SEC) -> None:
    """Blocking loop. Reads new lines from inbox, drains into queue.

    Should be run inside a daemon thread. Survives the inbox not yet
    existing (it's created lazily by the first writer). Tolerates partial
    last lines by tracking the byte offset of the last complete newline.
    """
    inbox_path = Path(inbox)
    outbox_path = Path(outbox)
    cursor_path = Path(str(inbox_path) + ".cursor")
    offset = _read_cursor(cursor_path)

    print(f"[steering_file] watching {inbox_path} (cursor offset {offset})")

    while True:
        try:
            if inbox_path.exists():
                size = inbox_path.stat().st_size
                if size < offset:
                    # File was rotated/truncated; restart from the top.
                    offset = 0
                if size > offset:
                    with inbox_path.open("r", encoding="utf-8") as handle:
                        handle.seek(offset)
                        remaining = handle.read()
                    # Only consume up to the last newline; keep partial trailing line.
                    if "\n" in remaining:
                        complete, _, partial = remaining.rpartition("\n")
                        consumed = len(complete) + 1
                        for line in complete.splitlines():
                            line = line.strip()
                            if not line:
                                continue
                            try:
                                record = json.loads(line)
                            except json.JSONDecodeError as exc:
                                _append_outbox(outbox_path, {
                                    "status": "rejected", "reason": f"bad json: {exc}",
                                    "ts": _now_iso(),
                                })
                                continue
                            _process_record(queue, record, outbox_path)
                        offset += consumed
                        _write_cursor(cursor_path, offset)
        except Exception as exc:
            # Never crash the poller thread; surface and keep going.
            print(f"[steering_file] poll error: {exc}")
        time.sleep(interval)


def start_inbox_poller(queue: Queue, inbox: str, outbox: str) -> Optional[threading.Thread]:
    """Start ``poll_inbox_jsonl`` in a daemon thread. Returns the thread or None."""
    if not inbox or not outbox:
        return None
    thread = threading.Thread(
        target=poll_inbox_jsonl,
        args=(queue, inbox, outbox),
        name="steering-file-poller",
        daemon=True,
    )
    thread.start()
    print(f"[Info] File-based steering poller active: {inbox} -> {outbox}")
    return thread


def write_inbox_record(inbox: str, *, kind: str, **fields) -> str:
    """Helper used by the CLI to append a record. Returns the assigned id."""
    inbox_path = Path(inbox)
    inbox_path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "id": fields.pop("id", None) or f"steer-{int(time.time() * 1000)}",
        "ts": _now_iso(),
        "kind": kind,
        **fields,
    }
    with inbox_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")
    return str(record["id"])
