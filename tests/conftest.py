import io
import json

import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

from app import config, db, extractor


@pytest.fixture(autouse=True)
def tmp_data(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "UPLOAD_DIR", tmp_path / "uploads")
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "ipc.db")
    db.init_db()
    yield tmp_path


def make_jpeg(text="IPC-WC", size=(800, 1100)) -> bytes:
    img = Image.new("RGB", size, "white")
    ImageDraw.Draw(img).text((40, 40), text, fill="black")
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


def cell(value, confidence="high"):
    return {"value": value, "confidence": confidence}


SAMPLE_WC = {
    "header": {
        "product_name": cell("Sữa rửa mặt 50g"),
        "batch_no": cell("L240915"),
        "line": cell("Line 2"),
        "check_date": cell("15/09/2024"),
        "nominal_weight": cell("50"),
        "inspector": cell("Nguyễn A", "medium"),
        "remarks": cell(""),
    },
    "rows": [
        {"time": cell("8:00"), "sample_no": cell("1"), "net_weight": cell("50,2"), "result": cell("Đ")},
        {"time": cell("8:00"), "sample_no": cell("2"), "net_weight": cell("48,7", "low"), "result": cell("Đ")},
    ],
    "notes": "",
}


class FakeClaude:
    """Stands in for extractor._call: returns canned JSON and records the requests."""

    def __init__(self, data=None, form_code="IPC-WC"):
        self.data = data or SAMPLE_WC
        self.form_code = form_code
        self.calls = []

    def __call__(self, content, schema, max_tokens):
        self.calls.append(schema)
        if "form_code" in schema["properties"]:
            return json.dumps({"form_code": self.form_code})
        return json.dumps(self.data, ensure_ascii=False)


@pytest.fixture
def fake_claude(monkeypatch):
    fake = FakeClaude()
    monkeypatch.setattr(extractor, "_call", fake)
    return fake


@pytest.fixture
def client(fake_claude):
    from app.main import app

    db.create_user("ipc1", "Nguyễn Văn A", "1234")
    with TestClient(app) as c:
        r = c.post("/api/login", json={"name": "ipc1", "pin": "1234"})
        assert r.status_code == 200
        yield c
