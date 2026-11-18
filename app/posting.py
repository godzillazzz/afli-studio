"""Publishing.

Facebook: official Reels API on a Facebook Page (fully automatic).
TikTok:   official Content Posting API, *Upload to inbox* mode. The video
          lands in your TikTok app inbox as a draft; tap it, pin the
          product basket (ตะกร้า), and post. TikTok does not allow apps to
          pin the basket, and this keeps your account safe.
"""
import os
import time
import urllib.parse

import httpx

from . import db

GRAPH = "https://graph.facebook.com/v25.0"
TT = "https://open.tiktokapis.com"


# ---------------- Facebook ----------------
def post_facebook(video_path: str, caption: str, s: dict) -> str:
    page, token = s.get("fb_page_id"), s.get("fb_page_token")
    if not page or not token:
        raise RuntimeError("ยังไม่ได้ตั้งค่า Facebook Page ID / Page Token")
    r = httpx.post(f"{GRAPH}/{page}/video_reels", data={"upload_phase": "start", "access_token": token}, timeout=60)
    if r.status_code >= 300:
        raise RuntimeError(f"Facebook start: {r.text[:300]}")
    vid = r.json()["video_id"]
    size = os.path.getsize(video_path)
    with open(video_path, "rb") as f:
        up = httpx.post(f"https://rupload.facebook.com/video-upload/v25.0/{vid}", content=f.read(), timeout=600,
                        headers={"Authorization": f"OAuth {token}", "offset": "0", "file_size": str(size)})
    if up.status_code >= 300:
        raise RuntimeError(f"Facebook upload: {up.text[:300]}")
    fin = httpx.post(f"{GRAPH}/{page}/video_reels", timeout=60, data={
        "access_token": token, "video_id": vid, "upload_phase": "finish",
        "video_state": "PUBLISHED", "description": caption})
    if fin.status_code >= 300:
        raise RuntimeError(f"Facebook publish: {fin.text[:300]}")
    return vid


# ---------------- TikTok ----------------
def tiktok_auth_url(s: dict, state: str) -> str:
    q = {"client_key": s["tiktok_client_key"], "scope": "user.info.basic,video.upload",
         "response_type": "code", "redirect_uri": s["public_url"].rstrip("/") + "/api/tiktok/callback",
         "state": state}
    return "https://www.tiktok.com/v2/auth/authorize/?" + urllib.parse.urlencode(q)


def _save_tokens(data):
    db.set_settings({"tiktok_access_token": data["access_token"],
                     "tiktok_refresh_token": data.get("refresh_token", ""),
                     "tiktok_token_expires": str(int(time.time() + int(data.get("expires_in", 86400)) - 300))})


def tiktok_exchange(code: str, s: dict):
    r = httpx.post(f"{TT}/v2/oauth/token/", timeout=30, data={
        "client_key": s["tiktok_client_key"], "client_secret": s["tiktok_client_secret"], "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": s["public_url"].rstrip("/") + "/api/tiktok/callback"})
    data = r.json()
    if "access_token" not in data:
        raise RuntimeError(f"TikTok login ไม่สำเร็จ: {data}")
    _save_tokens(data)


def _tiktok_token(s):
    if float(s.get("tiktok_token_expires") or 0) > time.time():
        return s["tiktok_access_token"]
    if not s.get("tiktok_refresh_token"):
        raise RuntimeError("ยังไม่ได้เชื่อมบัญชี TikTok (กดปุ่ม 'เชื่อม TikTok' ในหน้าตั้งค่า)")
    r = httpx.post(f"{TT}/v2/oauth/token/", timeout=30, data={
        "client_key": s["tiktok_client_key"], "client_secret": s["tiktok_client_secret"],
        "grant_type": "refresh_token", "refresh_token": s["tiktok_refresh_token"]})
    data = r.json()
    if "access_token" not in data:
        raise RuntimeError(f"TikTok token หมดอายุ กรุณาเชื่อมบัญชีใหม่: {data}")
    _save_tokens(data)
    return data["access_token"]


def send_tiktok_inbox(video_path: str, s: dict) -> str:
    token = _tiktok_token(s)
    size = os.path.getsize(video_path)
    r = httpx.post(f"{TT}/v2/post/publish/inbox/video/init/", timeout=60,
                   headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=UTF-8"},
                   json={"source_info": {"source": "FILE_UPLOAD", "video_size": size,
                                         "chunk_size": size, "total_chunk_count": 1}})
    data = r.json()
    if (data.get("error") or {}).get("code") not in (None, "ok"):
        raise RuntimeError(f"TikTok init: {data['error']}")
    up_url, publish_id = data["data"]["upload_url"], data["data"]["publish_id"]
    with open(video_path, "rb") as f:
        up = httpx.put(up_url, content=f.read(), timeout=600, headers={
            "Content-Type": "video/mp4", "Content-Length": str(size),
            "Content-Range": f"bytes 0-{size - 1}/{size}"})
    if up.status_code >= 300:
        raise RuntimeError(f"TikTok upload: {up.status_code} {up.text[:300]}")
    return publish_id
