"""FastAPI app: upload scans -> AI extraction -> IPC review/correct -> SQLite."""
from __future__ import annotations

import logging
import secrets
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import BackgroundTasks, Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.middleware.sessions import SessionMiddleware

from app import config, db, exporter, extractor
from app.pdf_utils import UnsupportedFile, split_upload
from app.templates_loader import Template, evaluate, load_templates

log = logging.getLogger("ipc")
STATIC_DIR = Path(__file__).parent / "static"
MAX_UPLOAD_BYTES = 50 * 1024 * 1024
CONFIDENCE_VALUES = set(extractor.CONFIDENCE) | {"verified"}


@asynccontextmanager
async def lifespan(_app: FastAPI):
    db.init_db()
    yield


app = FastAPI(title="IPC Scan", lifespan=lifespan)
app.add_middleware(SessionMiddleware, secret_key=config.SECRET_KEY, max_age=12 * 3600)


# ---------- auth ----------

def current_user(request: Request) -> dict:
    user = request.session.get("user")
    if not user:
        raise HTTPException(401, "Chưa đăng nhập")
    return user


class LoginIn(BaseModel):
    name: str
    pin: str


@app.post("/api/login")
def login(body: LoginIn, request: Request) -> dict:
    user = db.verify_user(body.name.strip(), body.pin)
    if not user:
        raise HTTPException(401, "Sai tên đăng nhập hoặc PIN")
    request.session["user"] = user
    return user


@app.post("/api/logout")
def logout(request: Request) -> dict:
    request.session.clear()
    return {"ok": True}


@app.get("/api/me")
def me(user: dict = Depends(current_user)) -> dict:
    return user


# ---------- templates ----------

def get_templates() -> dict[str, Template]:
    # Đọc lại mỗi lần để thêm/sửa file YAML không cần khởi động lại app.
    return load_templates()


def get_template(code: str | None) -> Template:
    templates = get_templates()
    if not code or code not in templates:
        raise HTTPException(400, f"Không có biểu mẫu '{code}'")
    return templates[code]


@app.get("/api/templates")
def list_templates(user: dict = Depends(current_user)) -> list[dict]:
    return [t.to_dict() for t in get_templates().values()]


# ---------- upload & processing ----------

def process_document(doc_id: int) -> None:
    doc = db.get_document(doc_id)
    try:
        image = Path(doc["image_path"]).read_bytes()
        templates = get_templates()
        code = doc["template_code"]
        if not code:
            code = extractor.classify(templates, image)
        tpl = templates[code]
        data = extractor.extract(tpl, image)
        db.save_extraction(doc_id, code, config.CLAUDE_MODEL, data, evaluate(tpl, data))
    except extractor.ExtractionError as e:
        db.save_error(doc_id, str(e))
    except Exception as e:  # keep the record visible with an error instead of stuck in 'processing'
        log.exception("Extraction failed for document %s", doc_id)
        db.save_error(doc_id, f"Lỗi hệ thống: {e}")


@app.post("/api/upload")
async def upload(
    background: BackgroundTasks,
    files: list[UploadFile] = File(...),
    template_code: str = Form("auto"),
    user: dict = Depends(current_user),
) -> dict:
    code = None if template_code == "auto" else get_template(template_code).code
    batch = datetime.now().strftime("%Y%m%d-%H%M%S-") + secrets.token_hex(3)
    batch_dir = config.UPLOAD_DIR / batch
    batch_dir.mkdir(parents=True, exist_ok=True)
    doc_ids, errors = [], []
    for i, f in enumerate(files):
        content = await f.read()
        name = f.filename or f"file{i}"
        if len(content) > MAX_UPLOAD_BYTES:
            errors.append(f"{name}: file quá lớn (>50MB)")
            continue
        try:
            pages = split_upload(name, content)
        except UnsupportedFile as e:
            errors.append(f"{name}: {e}")
            continue
        safe_name = f"{i:02d}_{Path(name).name}"
        original = batch_dir / safe_name
        original.write_bytes(content)
        for page_no, jpeg in enumerate(pages, start=1):
            image_path = batch_dir / f"{i:02d}_p{page_no:03d}.jpg"
            image_path.write_bytes(jpeg)
            doc_id = db.create_document(
                batch_upload=batch, original_name=name, original_path=str(original),
                page_no=page_no, image_path=str(image_path), template_code=code,
                uploaded_by=user["name"],
            )
            doc_ids.append(doc_id)
            background.add_task(process_document, doc_id)
    if not doc_ids and errors:
        raise HTTPException(400, "; ".join(errors))
    return {"batch": batch, "document_ids": doc_ids, "errors": errors}


# ---------- documents ----------

def _doc_or_404(doc_id: int) -> dict:
    doc = db.get_document(doc_id)
    if not doc:
        raise HTTPException(404, "Không tìm thấy bản ghi")
    return doc


def _summary(d: dict) -> dict:
    header = (d.get("data") or {}).get("header", {})
    return {
        "id": d["id"], "status": d["status"], "template_code": d["template_code"],
        "original_name": d["original_name"], "page_no": d["page_no"],
        "uploaded_by": d["uploaded_by"], "uploaded_at": d["uploaded_at"],
        "overall": d["overall"], "error": d["error"],
        "product_name": (header.get("product_name") or {}).get("value", ""),
        "batch_no": (header.get("batch_no") or {}).get("value", ""),
        "low_confidence": _count_low(d.get("data")),
    }


def _count_low(data: dict | None) -> int:
    if not data:
        return 0
    cells = list(data.get("header", {}).values())
    for row in data.get("rows", []):
        cells += list(row.values())
    return sum(1 for c in cells if isinstance(c, dict) and c.get("confidence") in ("low", "medium"))


@app.get("/api/documents")
def documents(status: str | None = None, template_code: str | None = None, q: str | None = None,
              date_from: str | None = None, date_to: str | None = None,
              user: dict = Depends(current_user)) -> list[dict]:
    docs = db.list_documents(status or None, template_code or None, q or None,
                             date_from or None, date_to or None)
    return [_summary(d) for d in docs]


@app.get("/api/documents/{doc_id}")
def document(doc_id: int, user: dict = Depends(current_user)) -> dict:
    doc = _doc_or_404(doc_id)
    templates = get_templates()
    tpl = templates.get(doc["template_code"]) if doc["template_code"] else None
    return {
        **{k: v for k, v in doc.items() if k not in ("image_path", "original_path")},
        "template": tpl.to_dict() if tpl else None,
        "history": db.get_history(doc_id),
    }


@app.get("/api/documents/{doc_id}/image")
def document_image(doc_id: int, user: dict = Depends(current_user)) -> FileResponse:
    return FileResponse(_doc_or_404(doc_id)["image_path"], media_type="image/jpeg")


@app.get("/api/documents/{doc_id}/original")
def document_original(doc_id: int, user: dict = Depends(current_user)) -> FileResponse:
    doc = _doc_or_404(doc_id)
    return FileResponse(doc["original_path"], filename=doc["original_name"])


def sanitize_data(tpl: Template, data: dict) -> dict:
    """Keep only the template's fields, force every value to a string."""
    def cell(raw: Any) -> dict:
        raw = raw if isinstance(raw, dict) else {"value": raw}
        conf = raw.get("confidence")
        return {
            "value": "" if raw.get("value") is None else str(raw.get("value")).strip(),
            "confidence": conf if conf in CONFIDENCE_VALUES else "verified",
        }

    header = data.get("header") or {}
    rows = data.get("rows") or []
    if not isinstance(header, dict) or not isinstance(rows, list):
        raise HTTPException(400, "Dữ liệu không đúng cấu trúc")
    return {
        "header": {f.key: cell(header.get(f.key)) for f in tpl.header_fields},
        "rows": [{c.key: cell((r or {}).get(c.key)) for c in tpl.columns} for r in rows
                 if isinstance(r, dict)],
        "notes": str(data.get("notes") or ""),
    }


class DataIn(BaseModel):
    data: dict
    reason: str | None = None


@app.put("/api/documents/{doc_id}/data")
def save_data(doc_id: int, body: DataIn, user: dict = Depends(current_user)) -> dict:
    doc = _doc_or_404(doc_id)
    tpl = get_template(doc["template_code"])
    data = sanitize_data(tpl, body.data)
    try:
        changed = db.update_data(doc_id, data, evaluate(tpl, data), user["name"], body.reason)
    except db.ReviewError as e:
        raise HTTPException(400, str(e))
    return {"changed": changed, "evaluation": evaluate(tpl, data)}


class ReviewIn(BaseModel):
    note: str | None = None


@app.post("/api/documents/{doc_id}/approve")
def approve(doc_id: int, body: ReviewIn, user: dict = Depends(current_user)) -> dict:
    _doc_or_404(doc_id)
    try:
        db.set_review(doc_id, "approved", user["name"], body.note)
    except db.ReviewError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


@app.post("/api/documents/{doc_id}/reject")
def reject(doc_id: int, body: ReviewIn, user: dict = Depends(current_user)) -> dict:
    _doc_or_404(doc_id)
    try:
        db.set_review(doc_id, "rejected", user["name"], body.note)
    except db.ReviewError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


class RetryIn(BaseModel):
    template_code: str | None = None


@app.post("/api/documents/{doc_id}/retry")
def retry(doc_id: int, body: RetryIn, background: BackgroundTasks,
          user: dict = Depends(current_user)) -> dict:
    """Re-run AI extraction (e.g. after an error or when the wrong form type was detected)."""
    doc = _doc_or_404(doc_id)
    if doc["status"] not in ("error", "pending_review"):
        raise HTTPException(400, "Chỉ chạy lại được bản ghi lỗi hoặc đang chờ duyệt")
    if doc["status"] == "pending_review" and db.get_history(doc_id)["edits"]:
        raise HTTPException(400, "Bản ghi đã có chỉnh sửa thủ công, không chạy lại AI để tránh mất dữ liệu")
    code = body.template_code or doc["template_code"]
    if code:
        get_template(code)
    db.reset_for_retry(doc_id, code)
    background.add_task(process_document, doc_id)
    return {"ok": True}


# ---------- export ----------

@app.get("/api/export")
def export(template_code: str, format: str = "xlsx", status: str = "approved",
           date_from: str | None = None, date_to: str | None = None,
           user: dict = Depends(current_user)) -> Response:
    tpl = get_template(template_code)
    docs = list(reversed(db.list_documents(status or None, tpl.code, None, date_from or None,
                                           date_to or None, limit=100_000)))
    docs = [d for d in docs if d.get("data")]
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    if format == "csv":
        return Response(exporter.to_csv(tpl, docs), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="{tpl.code}_{stamp}.csv"'})
    if format == "xlsx":
        return Response(
            exporter.to_xlsx(tpl, docs),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="{tpl.code}_{stamp}.xlsx"'},
        )
    raise HTTPException(400, "format phải là csv hoặc xlsx")


# ---------- pages ----------

@app.get("/")
def root() -> RedirectResponse:
    return RedirectResponse("/static/index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
