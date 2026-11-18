"""HTTP API of the clip library (cookie auth, upload, delete, save from job)."""
import os

import pytest
from fastapi.testclient import TestClient

from app import db, library


@pytest.fixture(scope="module")
def client():
    from app.main import app
    with TestClient(app) as c:
        r = c.post("/api/login", json={"password": "testpw"})
        assert r.status_code == 200
        yield c


def _mp4(name="flow.mp4"):
    return ("files", (name, b"\x00" * 128, "video/mp4"))


def test_library_requires_login():
    from app.main import app
    with TestClient(app) as anon:
        assert anon.get("/api/library").status_code == 401


def test_library_upload_list_and_delete(client):
    key = "shopee-777-888"
    r = client.post(f"/api/library/{key}/clips", files=[_mp4()], data={"name": "พัดลมทดสอบ"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["count"] == 1 and len(body["saved"]) == 1
    clip = body["saved"][0]
    assert clip["product_key"] == key and clip["filename"] == "flow.mp4"

    r = client.get("/api/library")
    assert r.status_code == 200
    entry = [p for p in r.json() if p["product_key"] == key][0]
    assert entry["count"] == 1
    assert entry["product_name"] == "พัดลมทดสอบ"

    r = client.get(f"/api/library/{key}")
    assert r.json()["clips"][0]["filename"] == "flow.mp4"

    r = client.delete(f"/api/library/{key}/clips/{clip['id']}")
    assert r.status_code == 200 and r.json()["count"] == 0
    assert client.delete(f"/api/library/{key}/clips/{clip['id']}").status_code == 404


def test_library_rejects_non_video_and_bad_key(client):
    r = client.post("/api/library/shopee-1-1/clips",
                    files=[("files", ("notes.txt", b"hi", "text/plain"))], data={"name": "x"})
    assert r.status_code == 400
    assert "วิดีโอ" in r.text
    assert client.get("/api/library/..%2F..%2Fetc").status_code in (400, 404)


def test_save_job_clips_to_library(client):
    r = client.post("/api/jobs", data={"url": "", "name": "สินค้าทดสอบคลัง", "mode": "slideshow",
                                       "review": "0"},
                    files=[("files", ("own.mp4", b"\x00" * 256, "video/mp4"))])
    assert r.status_code == 200, r.text
    job_id = r.json()["id"]

    r = client.post(f"/api/jobs/{job_id}/save-to-library")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["count"] == 1
    assert body["product_key"] == library.slug("สินค้าทดสอบคลัง")
    assert db.get_job(job_id)["product_key"] == body["product_key"]

    r = client.get(f"/api/library/{body['product_key']}")
    assert r.json()["count"] == 1
    # cleanup
    for c in r.json()["clips"]:
        client.delete(f"/api/library/{body['product_key']}/clips/{c['id']}")
    client.delete(f"/api/jobs/{job_id}")
