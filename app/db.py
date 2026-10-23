"""SQLite storage: users, documents (one row per scanned page) and the field-edit audit trail."""
from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator

from app import config

STATUSES = ("processing", "pending_review", "approved", "rejected", "error")

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    full_name   TEXT NOT NULL,
    role        TEXT NOT NULL DEFAULT 'ipc',   -- ipc | qa
    pin_hash    TEXT NOT NULL,
    created_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS documents (
    id             INTEGER PRIMARY KEY,
    batch_upload   TEXT NOT NULL,       -- groups pages of one upload
    original_name  TEXT NOT NULL,
    original_path  TEXT NOT NULL,       -- file gốc, không bao giờ xóa
    page_no        INTEGER NOT NULL,
    image_path     TEXT NOT NULL,       -- ảnh trang đã chuẩn hóa
    template_code  TEXT,
    status         TEXT NOT NULL,
    error          TEXT,
    model          TEXT,
    ai_data        TEXT,                -- JSON AI đọc được (giữ nguyên, không sửa)
    data           TEXT,                -- JSON hiện hành (sau khi IPC sửa)
    evaluation     TEXT,                -- JSON kết quả so với tiêu chuẩn
    overall        TEXT,                -- pass | fail | n/a
    uploaded_by    TEXT NOT NULL,
    uploaded_at    TEXT NOT NULL,
    reviewed_by    TEXT,
    reviewed_at    TEXT,
    review_note    TEXT,
    lark_record_id TEXT,                -- giai đoạn 2: đồng bộ Lark Base
    synced_at      TEXT
);
CREATE INDEX IF NOT EXISTS idx_documents_status ON documents(status);
CREATE TABLE IF NOT EXISTS field_edits (
    id           INTEGER PRIMARY KEY,
    document_id  INTEGER NOT NULL REFERENCES documents(id),
    field_path   TEXT NOT NULL,         -- header.batch_no | rows.2.net_weight | notes
    old_value    TEXT,
    new_value    TEXT,
    edited_by    TEXT NOT NULL,
    edited_at    TEXT NOT NULL,
    reason       TEXT
);
CREATE TABLE IF NOT EXISTS events (
    id           INTEGER PRIMARY KEY,
    document_id  INTEGER NOT NULL REFERENCES documents(id),
    action       TEXT NOT NULL,         -- uploaded | extracted | edited | approved | rejected | reopened
    actor        TEXT NOT NULL,
    at           TEXT NOT NULL,
    detail       TEXT
);
"""


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    config.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    with connect() as conn:
        conn.executescript(SCHEMA)


# ---------- users ----------

def _hash_pin(pin: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", pin.encode(), salt.encode(), 200_000).hex()


def create_user(name: str, full_name: str, pin: str, role: str = "ipc") -> None:
    if role not in ("ipc", "qa"):
        raise ValueError("role phải là 'ipc' hoặc 'qa'")
    salt = secrets.token_hex(8)
    with connect() as conn:
        conn.execute(
            "INSERT INTO users (name, full_name, role, pin_hash, created_at) VALUES (?, ?, ?, ?, ?)",
            (name, full_name, role, f"{salt}${_hash_pin(pin, salt)}", now()),
        )


def verify_user(name: str, pin: str) -> dict | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE name = ?", (name,)).fetchone()
    if not row:
        return None
    salt, digest = row["pin_hash"].split("$", 1)
    if not secrets.compare_digest(_hash_pin(pin, salt), digest):
        return None
    return {"name": row["name"], "full_name": row["full_name"], "role": row["role"]}


def count_users() -> int:
    with connect() as conn:
        return conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]


# ---------- documents ----------

JSON_COLUMNS = ("ai_data", "data", "evaluation")


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    for col in JSON_COLUMNS:
        if d.get(col):
            d[col] = json.loads(d[col])
    return d


def log_event(conn: sqlite3.Connection, doc_id: int, action: str, actor: str, detail: str = "",
              at: str | None = None) -> None:
    conn.execute(
        "INSERT INTO events (document_id, action, actor, at, detail) VALUES (?, ?, ?, ?, ?)",
        (doc_id, action, actor, at or now(), detail),
    )


def create_document(*, batch_upload: str, original_name: str, original_path: str, page_no: int,
                    image_path: str, template_code: str | None, uploaded_by: str) -> int:
    with connect() as conn:
        cur = conn.execute(
            """INSERT INTO documents (batch_upload, original_name, original_path, page_no, image_path,
                   template_code, status, uploaded_by, uploaded_at)
               VALUES (?, ?, ?, ?, ?, ?, 'processing', ?, ?)""",
            (batch_upload, original_name, original_path, page_no, image_path, template_code,
             uploaded_by, now()),
        )
        log_event(conn, cur.lastrowid, "uploaded", uploaded_by, f"{original_name} trang {page_no}")
        return cur.lastrowid


def get_document(doc_id: int) -> dict | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()
    return _row_to_dict(row) if row else None


def list_documents(status: str | None = None, template_code: str | None = None,
                   q: str | None = None, date_from: str | None = None,
                   date_to: str | None = None, limit: int = 200) -> list[dict]:
    sql = "SELECT * FROM documents WHERE 1=1"
    args: list[Any] = []
    if status:
        sql += " AND status = ?"
        args.append(status)
    if template_code:
        sql += " AND template_code = ?"
        args.append(template_code)
    if q:
        sql += " AND (data LIKE ? OR original_name LIKE ?)"
        args += [f"%{q}%", f"%{q}%"]
    if date_from:
        sql += " AND uploaded_at >= ?"
        args.append(date_from)
    if date_to:
        sql += " AND substr(uploaded_at, 1, 10) <= ?"
        args.append(date_to)
    sql += " ORDER BY id DESC LIMIT ?"
    args.append(limit)
    with connect() as conn:
        return [_row_to_dict(r) for r in conn.execute(sql, args).fetchall()]


def save_extraction(doc_id: int, template_code: str, model: str, ai_data: dict,
                    evaluation: dict) -> None:
    payload = json.dumps(ai_data, ensure_ascii=False)
    with connect() as conn:
        conn.execute(
            """UPDATE documents SET template_code = ?, model = ?, ai_data = ?, data = ?, evaluation = ?,
                   overall = ?, status = 'pending_review', error = NULL WHERE id = ?""",
            (template_code, model, payload, payload, json.dumps(evaluation), evaluation["overall"],
             doc_id),
        )
        log_event(conn, doc_id, "extracted", "AI", model)


def save_error(doc_id: int, message: str) -> None:
    with connect() as conn:
        conn.execute("UPDATE documents SET status = 'error', error = ? WHERE id = ?", (message, doc_id))


def reset_for_retry(doc_id: int, template_code: str | None) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE documents SET status = 'processing', error = NULL, template_code = ? WHERE id = ?",
            (template_code, doc_id),
        )


def _cell_value(cell: Any) -> str:
    if isinstance(cell, dict):
        return str(cell.get("value", ""))
    return "" if cell is None else str(cell)


def diff_data(old: dict, new: dict) -> list[tuple[str, str, str]]:
    """List (field_path, old_value, new_value) for every changed value."""
    changes = []
    old_h, new_h = old.get("header", {}), new.get("header", {})
    for key in sorted(set(old_h) | set(new_h)):
        a, b = _cell_value(old_h.get(key)), _cell_value(new_h.get(key))
        if a != b:
            changes.append((f"header.{key}", a, b))
    old_rows, new_rows = old.get("rows", []), new.get("rows", [])
    for i in range(max(len(old_rows), len(new_rows))):
        ro = old_rows[i] if i < len(old_rows) else None
        rn = new_rows[i] if i < len(new_rows) else None
        if ro is None:
            changes.append((f"rows.{i + 1}", "", "(thêm dòng)"))
            continue
        if rn is None:
            changes.append((f"rows.{i + 1}", "(xóa dòng)", ""))
            continue
        for key in sorted(set(ro) | set(rn)):
            a, b = _cell_value(ro.get(key)), _cell_value(rn.get(key))
            if a != b:
                changes.append((f"rows.{i + 1}.{key}", a, b))
    if (old.get("notes") or "") != (new.get("notes") or ""):
        changes.append(("notes", old.get("notes") or "", new.get("notes") or ""))
    return changes


class ReviewError(Exception):
    pass


def update_data(doc_id: int, new_data: dict, evaluation: dict, user: str,
                reason: str | None = None) -> int:
    """Save IPC corrections. Every changed value goes to field_edits.

    Editing an approved record requires a reason and sends it back to pending_review.
    Returns the number of changed fields.
    """
    doc = get_document(doc_id)
    if not doc:
        raise ReviewError("Không tìm thấy bản ghi.")
    if doc["status"] not in ("pending_review", "approved"):
        raise ReviewError("Bản ghi không ở trạng thái cho phép sửa.")
    reopen = doc["status"] == "approved"
    if reopen and not (reason and reason.strip()):
        raise ReviewError("Bản ghi đã duyệt: bắt buộc nhập lý do khi sửa.")
    changes = diff_data(doc["data"] or {}, new_data)
    if not changes:
        return 0
    ts = now()
    with connect() as conn:
        conn.executemany(
            """INSERT INTO field_edits (document_id, field_path, old_value, new_value, edited_by,
                   edited_at, reason) VALUES (?, ?, ?, ?, ?, ?, ?)""",
            [(doc_id, p, a, b, user, ts, reason) for p, a, b in changes],
        )
        conn.execute(
            "UPDATE documents SET data = ?, evaluation = ?, overall = ?, status = 'pending_review' WHERE id = ?",
            (json.dumps(new_data, ensure_ascii=False), json.dumps(evaluation), evaluation["overall"],
             doc_id),
        )
        log_event(conn, doc_id, "reopened" if reopen else "edited", user,
                  f"{len(changes)} trường" + (f" – {reason}" if reason else ""), at=ts)
    return len(changes)


def set_review(doc_id: int, status: str, user: str, note: str | None = None) -> None:
    if status not in ("approved", "rejected"):
        raise ValueError(status)
    doc = get_document(doc_id)
    if not doc:
        raise ReviewError("Không tìm thấy bản ghi.")
    if doc["status"] != "pending_review":
        raise ReviewError("Chỉ duyệt/từ chối được bản ghi đang chờ duyệt.")
    if status == "rejected" and not (note and note.strip()):
        raise ReviewError("Từ chối cần ghi lý do (ví dụ: ảnh mờ, chụp lại).")
    with connect() as conn:
        conn.execute(
            "UPDATE documents SET status = ?, reviewed_by = ?, reviewed_at = ?, review_note = ? WHERE id = ?",
            (status, user, now(), note, doc_id),
        )
        log_event(conn, doc_id, status, user, note or "")


def get_history(doc_id: int) -> dict[str, list[dict]]:
    with connect() as conn:
        edits = conn.execute(
            "SELECT * FROM field_edits WHERE document_id = ? ORDER BY id", (doc_id,)
        ).fetchall()
        events = conn.execute(
            "SELECT * FROM events WHERE document_id = ? ORDER BY id", (doc_id,)
        ).fetchall()
    return {"edits": [dict(r) for r in edits], "events": [dict(r) for r in events]}
