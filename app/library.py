"""Clip library: clips the owner made once (e.g. in Google Flow) per product.

Files live in DATA_DIR/library/<product_key>/ and are reused for every new job
of the same product, so no paid AI video is needed again.
"""
import os
import re
import shutil
import unicodedata

from . import db, shopee

VIDEO_EXT = (".mp4", ".mov", ".m4v", ".webm")

# letters used in generated keys: latin, digits, Thai, dot/underscore/dash
_KEY_CHARS = r"0-9A-Za-zก-๙._-"
_KEY_RE = re.compile(rf"^[{_KEY_CHARS}]{{1,120}}$")
_KEY_BAD = re.compile(rf"[^{_KEY_CHARS}]+")


def slug(name: str) -> str:
    """Normalized product name -> directory/key-safe slug (keeps Thai letters)."""
    t = unicodedata.normalize("NFC", (name or "")).strip().lower()
    t = _KEY_BAD.sub("-", t).strip("-._")
    t = re.sub(r"\.{1,}", "-", t)
    t = re.sub(r"-{2,}", "-", t)
    return t[:60].strip("-._")


def product_key(url: str, name: str = "") -> str | None:
    """Stable key for a product: Shopee shop_id+item_id, else the product name."""
    shop_id, item_id = shopee.parse_ids(url or "")
    if shop_id and item_id:
        return f"shopee-{shop_id}-{item_id}"
    return slug(name) or None


def safe_key(key: str) -> str:
    """Reject path traversal / junk before touching the filesystem."""
    k = (key or "").strip()
    if not k or not _KEY_RE.match(k) or ".." in k:
        raise ValueError("รหัสสินค้าไม่ถูกต้อง")
    return k


def safe_filename(name: str) -> str:
    """Keep only the last path piece and a video extension."""
    base = os.path.basename((name or "").replace("\\", "/")).strip()
    ext = os.path.splitext(base)[1].lower()
    if ext not in VIDEO_EXT:
        raise ValueError("รับเฉพาะไฟล์วิดีโอ (.mp4 .mov .m4v .webm)")
    stem = _KEY_BAD.sub("-", os.path.splitext(base)[0]).strip("-._") or "clip"
    return (stem[:60] + ext)


def root() -> str:
    return os.path.join(db.DATA_DIR, "library")


def product_dir(key: str) -> str:
    d = os.path.join(root(), safe_key(key))
    if os.path.commonpath([os.path.abspath(root()), os.path.abspath(d)]) != os.path.abspath(root()):
        raise ValueError("รหัสสินค้าไม่ถูกต้อง")
    os.makedirs(d, exist_ok=True)
    return d


def clip_path(key: str, filename: str) -> str:
    return os.path.join(product_dir(key), safe_filename(filename))


def clip_paths(key: str) -> list[str]:
    """Existing files of a product, oldest first (only real video files)."""
    if not key:
        return []
    try:
        d = product_dir(key)
    except ValueError:
        return []
    rows = [db.library_clip(c["id"]) for c in db.library_clips(key)]
    out = []
    for r in rows:
        if not r:
            continue
        p = os.path.join(d, r["filename"])
        if os.path.exists(p) and os.path.getsize(p) > 0:
            out.append(p)
    return out


def has_clips(key: str) -> bool:
    return bool(clip_paths(key))


def save_clip(key: str, product_name: str, filename: str, source: str) -> dict:
    """Copy an existing file into the library and record it. Returns the DB row."""
    k = safe_key(key)
    fname = safe_filename(filename)
    dest = os.path.join(product_dir(k), fname)
    if os.path.exists(dest):  # same name again: keep both
        stem, ext = os.path.splitext(fname)
        n = 2
        while os.path.exists(dest):
            dest = os.path.join(product_dir(k), f"{stem}-{n}{ext}")
            n += 1
        fname = os.path.basename(dest)
    if os.path.abspath(source) != os.path.abspath(dest):
        shutil.copyfile(source, dest)
    clip_id = db.library_add(k, product_name or k, fname)
    return {"id": clip_id, "product_key": k, "product_name": product_name or k, "filename": fname}


def delete_clip(key: str, clip_id: int) -> bool:
    k = safe_key(key)
    row = db.library_clip(clip_id)
    if not row or row["product_key"] != k:
        return False
    p = os.path.join(product_dir(k), safe_filename(row["filename"]))
    try:
        os.remove(p)
    except OSError:
        pass
    db.library_delete(clip_id)
    return True
