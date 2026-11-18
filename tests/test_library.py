"""Product keys, sanitization and clip library storage/CRUD."""
import os

import pytest

from app import db, library


@pytest.fixture()
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(db, "DB_PATH", os.path.join(str(tmp_path), "afli.db"))
    db.init()
    return str(tmp_path)


def make_video(path):
    with open(path, "wb") as f:
        f.write(b"\x00" * 64)
    return path


# ---------------- product key ----------------
def test_key_from_shopee_url_patterns():
    assert library.product_key("https://shopee.co.th/Some-Name-i.12345.67890") == "shopee-12345-67890"
    assert library.product_key("https://shopee.co.th/product/111/222?smtt=0") == "shopee-111-222"
    # ids win over the name
    assert library.product_key("https://shopee.co.th/x-i.9.8", "ชื่ออื่น") == "shopee-9-8"


def test_key_falls_back_to_normalized_name():
    assert library.product_key("", "พัดลมพกพา ชาร์จ USB") == "พัดลมพกพา-ชาร์จ-usb"
    assert library.product_key("https://example.com/no-ids", "  Test  Product!! ") == "test-product"
    assert library.product_key("", "") is None


def test_key_sanitizes_weird_names():
    key = library.product_key("", "../../etc/passwd ../x")
    assert key is not None
    assert "/" not in key and ".." not in key


# ---------------- sanitization ----------------
@pytest.mark.parametrize("bad", ["", "..", "../x", "a/b", "a\\b", ".hidden..x", "x" * 200])
def test_safe_key_rejects_bad_keys(bad):
    with pytest.raises(ValueError):
        library.safe_key(bad)


def test_safe_key_accepts_generated_keys():
    assert library.safe_key("shopee-12345-67890") == "shopee-12345-67890"
    assert library.safe_key("พัดลม-พกพา") == "พัดลม-พกพา"


def test_safe_filename_strips_paths_and_requires_video_ext():
    assert library.safe_filename("../../evil.mp4") == "evil.mp4"
    assert library.safe_filename("C:\\clips\\clip one.MOV") == "clip-one.mov"
    with pytest.raises(ValueError):
        library.safe_filename("notes.txt")
    with pytest.raises(ValueError):
        library.safe_filename("noext")


def test_product_dir_stays_inside_library(data_dir):
    d = library.product_dir("shopee-1-2")
    assert os.path.dirname(d) == os.path.join(data_dir, "library")


# ---------------- CRUD ----------------
def test_save_list_and_delete_clip(data_dir, tmp_path):
    src = make_video(str(tmp_path / "flow.mp4"))
    row = library.save_clip("shopee-1-2", "พัดลม", "flow.mp4", src)
    assert row["filename"] == "flow.mp4"
    assert os.path.exists(os.path.join(data_dir, "library", "shopee-1-2", "flow.mp4"))

    paths = library.clip_paths("shopee-1-2")
    assert paths == [os.path.join(data_dir, "library", "shopee-1-2", "flow.mp4")]
    assert library.has_clips("shopee-1-2") is True
    assert library.has_clips("shopee-9-9") is False

    products = db.library_products()
    assert products[0]["product_key"] == "shopee-1-2"
    assert products[0]["clips"] == 1
    assert products[0]["product_name"] == "พัดลม"

    assert library.delete_clip("shopee-1-2", row["id"]) is True
    assert db.library_clips("shopee-1-2") == []
    assert not os.path.exists(paths[0])


def test_save_same_filename_twice_keeps_both(data_dir, tmp_path):
    src = make_video(str(tmp_path / "flow.mp4"))
    library.save_clip("k1", "n", "flow.mp4", src)
    row2 = library.save_clip("k1", "n", "flow.mp4", src)
    assert row2["filename"] == "flow-2.mp4"
    assert len(library.clip_paths("k1")) == 2


def test_delete_clip_wrong_key_does_not_touch_other_product(data_dir, tmp_path):
    src = make_video(str(tmp_path / "a.mp4"))
    row = library.save_clip("k1", "n", "a.mp4", src)
    assert library.delete_clip("k2", row["id"]) is False
    assert len(db.library_clips("k1")) == 1


def test_clip_paths_skips_missing_files(data_dir, tmp_path):
    src = make_video(str(tmp_path / "a.mp4"))
    row = library.save_clip("k1", "n", "a.mp4", src)
    os.remove(os.path.join(data_dir, "library", "k1", row["filename"]))
    assert library.clip_paths("k1") == []
