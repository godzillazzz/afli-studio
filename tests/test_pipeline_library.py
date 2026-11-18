"""Pipeline reuses library clips for the same product and skips paid AI video."""
import json
import os

import pytest

from app import db, library, pipeline


@pytest.fixture()
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(db, "DB_PATH", os.path.join(str(tmp_path), "afli.db"))
    db.init()
    return str(tmp_path)


def _dummy(path):
    with open(path, "wb") as f:
        f.write(b"\x00" * 32)
    return path


def _fake_job(data_dir, url="", name="พัดลมพกพา", mode="ai"):
    return db.create_job(url=url, mode=mode, status="running",
                         product=json.dumps({"name": name, "price": "199", "images": [], "description": ""},
                                            ensure_ascii=False),
                         script=json.dumps({"lines": ["สวัสดี", "ทดสอบคลิป"]}, ensure_ascii=False),
                         reuse_script=1, review=0, post_fb=0, post_tiktok=0)


def _patch_ffmpeg(monkeypatch):
    calls = {"video": [], "image": []}

    def video_segment(src, dur, out):
        calls["video"].append((src, dur))
        return _dummy(out)

    def image_segment(img, dur, out, idx=0):
        calls["image"].append((img, dur))
        return _dummy(out)

    monkeypatch.setattr(pipeline.video, "video_segment", video_segment)
    monkeypatch.setattr(pipeline.video, "image_segment", image_segment)
    monkeypatch.setattr(pipeline.video, "concat", lambda parts, out: _dummy(out))
    monkeypatch.setattr(pipeline.video, "write_ass", lambda *a, **k: os.path.join(a[-1] if a else "", "subs.ass"))
    monkeypatch.setattr(pipeline.video, "finalize",
                        lambda silent, voice, ass, out, music=None, vol=0.12: _dummy(out))
    monkeypatch.setattr(pipeline.video, "thumbnail", lambda v, out: _dummy(out))
    monkeypatch.setattr(pipeline.video, "build_voice", lambda files, out, gap=0.18: ([], 0.0))
    monkeypatch.setattr(pipeline.tts, "duration", lambda p: 5.0)
    monkeypatch.setattr(pipeline, "make_voice",
                        lambda job_id, lines, s, vdir: (_dummy(os.path.join(vdir, "voice.m4a")),
                                                        [(0.0, 2.0), (2.2, 4.0)], 4.0, "fake"))
    monkeypatch.setattr(pipeline, "load_preview", lambda *a: None)
    monkeypatch.setattr(pipeline.shopee, "fetch_product",
                        lambda url: {"name": "พัดลมพกพา", "price": None, "images": [], "description": ""})

    def boom(*a, **k):
        raise AssertionError("paid AI video must not be called when library clips exist")

    monkeypatch.setattr(pipeline.aivideo, "image_to_video", boom)
    return calls


def test_pipeline_uses_library_clips(data_dir, monkeypatch):
    src = _dummy(os.path.join(data_dir, "flow.mp4"))
    key = "shopee-12345-67890"
    library.save_clip(key, "พัดลม", "flow.mp4", src)
    job_id = _fake_job(data_dir, url=f"https://shopee.co.th/พัดลม-i.12345.67890", mode="ai")
    calls = _patch_ffmpeg(monkeypatch)

    pipeline.process(job_id)

    job = db.get_job(job_id)
    assert job["status"] == "done"
    assert job["product_key"] == key
    assert f"ใช้คลิปจากคลังสินค้า: 1 คลิป" in (job["log"] or "")
    lib_path = os.path.join(data_dir, "library", key, "flow.mp4")
    assert calls["video"] and calls["video"][0][0] == lib_path
    assert calls["video"][0][1] <= 10.0  # clips are used up to 10 s
    assert job["cost"] < 9  # no AI video cost (only small text/tts allowance)


def test_pipeline_own_uploads_win_over_library(data_dir, monkeypatch):
    lib_src = _dummy(os.path.join(data_dir, "flow.mp4"))
    library.save_clip("shopee-111-222", "พัดลม", "flow.mp4", lib_src)
    job_id = _fake_job(data_dir, url="https://shopee.co.th/x-i.111.222", mode="ai")
    up = os.path.join(pipeline.job_dir(job_id), "uploads")
    os.makedirs(up, exist_ok=True)
    own = _dummy(os.path.join(up, "mine.mp4"))
    calls = _patch_ffmpeg(monkeypatch)

    pipeline.process(job_id)

    assert db.get_job(job_id)["status"] == "done"
    assert calls["video"][0][0] == own
    assert "คลังสินค้า" not in (db.get_job(job_id)["log"] or "")


def test_pipeline_empty_library_requires_images(data_dir, monkeypatch):
    job_id = _fake_job(data_dir, url="https://shopee.co.th/x-i.333.444", mode="slideshow")
    _patch_ffmpeg(monkeypatch)
    with pytest.raises(RuntimeError):
        pipeline.process(job_id)
