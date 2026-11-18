"""AI image-to-video. Two providers, chosen in Settings:

  fal  - Kling 2.1 Standard via fal.ai: ~$0.25 per 5-second clip
  veo  - Google video via Gemini API. Veo 3.1 previews were shut down 2026-10-22; Google's
         replacement is Gemini Omni Flash (interactions endpoint). Setting name kept as "veo".

image_to_video() returns the cost in USD so the job can show it.
"""
import base64
import mimetypes
import time

import httpx

FAL_PRICE_PER_CLIP = 0.25
GEMINI = "https://generativelanguage.googleapis.com/v1beta"
NEG = "blur, distort, low quality, text, watermark, extra hands"


def _b64(path):
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode()


def _mime(path):
    return mimetypes.guess_type(path)[0] or "image/jpeg"


def image_to_video(image_path, prompt, out_path, settings):
    prompt = prompt or "slow cinematic push-in on the product, soft studio lighting"
    if settings.get("ai_provider") == "veo":
        return _veo(image_path, prompt, out_path, settings)
    return _fal(image_path, prompt, out_path, settings)


# ---------------- fal.ai (Kling) ----------------
def _fal(image_path, prompt, out_path, settings, timeout=600):
    key = settings.get("fal_key")
    if not key:
        raise RuntimeError("ยังไม่ได้ใส่ fal.ai API key ในหน้าตั้งค่า (จำเป็นสำหรับโหมดวิดีโอ AI)")
    model = settings.get("fal_model") or "fal-ai/kling-video/v2.1/standard/image-to-video"
    headers = {"Authorization": f"Key {key}", "Content-Type": "application/json"}
    body = {"prompt": prompt, "image_url": f"data:{_mime(image_path)};base64,{_b64(image_path)}",
            "duration": "5", "aspect_ratio": "9:16", "negative_prompt": NEG}
    r = httpx.post(f"https://queue.fal.run/{model}", headers=headers, json=body, timeout=60)
    if r.status_code >= 300:
        raise RuntimeError(f"fal.ai ไม่รับงาน ({r.status_code}): {r.text[:300]}")
    sub = r.json()
    start = time.time()
    while time.time() - start < timeout:
        time.sleep(6)
        st = httpx.get(sub["status_url"], headers=headers, timeout=30).json()
        if st.get("status") == "COMPLETED":
            break
        if st.get("status") in ("FAILED", "ERROR"):
            raise RuntimeError(f"fal.ai สร้างวิดีโอไม่สำเร็จ: {st}")
    else:
        raise RuntimeError("fal.ai ใช้เวลานานเกินไป")
    res = httpx.get(sub["response_url"], headers=headers, timeout=60).json()
    url = (res.get("video") or {}).get("url")
    if not url:
        raise RuntimeError(f"fal.ai ไม่ส่งวิดีโอกลับมา: {str(res)[:300]}")
    _save(httpx.get(url, timeout=180, follow_redirects=True), out_path)
    return FAL_PRICE_PER_CLIP


# ---------------- Google Gemini Omni Flash (Gemini API, replaced Veo 3.1 in Oct 2026) ----------------
def _find_video(obj):
    """Walk the interaction response and return the first video content item."""
    if isinstance(obj, dict):
        if obj.get("type") == "video" and (obj.get("data") or obj.get("uri")):
            return obj
        for v in obj.values():
            found = _find_video(v)
            if found:
                return found
    elif isinstance(obj, list):
        for v in obj:
            found = _find_video(v)
            if found:
                return found
    return None


def _veo(image_path, prompt, out_path, settings, timeout=900):
    key = settings.get("gemini_api_key")
    if not key:
        raise RuntimeError("ยังไม่ได้ใส่ Gemini API key ในหน้าตั้งค่า (จำเป็นสำหรับวิดีโอ Gemini)")
    model = settings.get("veo_model") or "gemini-omni-1.1-flash"
    headers = {"x-goog-api-key": key, "Content-Type": "application/json"}
    body = {"model": model,
            "input": [{"type": "image", "data": _b64(image_path), "mime_type": _mime(image_path)},
                      {"type": "text", "text": prompt + " Vertical 9:16. No dialogue, no speech, no text on screen."}],
            "generation_config": {"video_config": {"task": "image_to_video"}},
            "response_format": {"type": "video", "aspect_ratio": "9:16", "resolution": "720p"}}
    r = httpx.post(f"{GEMINI}/interactions", headers=headers, json=body, timeout=timeout)
    if r.status_code >= 300:
        raise RuntimeError(f"Gemini วิดีโอไม่รับงาน ({r.status_code}): {r.text[:400]}")
    res = r.json()
    start = time.time()
    while not _find_video(res):
        status = str(res.get("status", "")).lower()
        if status in ("failed", "cancelled", "canceled") or res.get("error"):
            raise RuntimeError(f"Gemini สร้างวิดีโอไม่สำเร็จ: {str(res.get('error') or res)[:300]}")
        if not res.get("id") or status in ("completed", "done") or time.time() - start > timeout:
            raise RuntimeError(f"Gemini ไม่ส่งวิดีโอกลับมา (อาจโดนตัวกรองเนื้อหาของ Google): {str(res)[:300]}")
        time.sleep(10)
        res = httpx.get(f"{GEMINI}/interactions/{res['id']}", headers=headers, timeout=120).json()
    vid = _find_video(res)
    if vid.get("data"):
        with open(out_path, "wb") as f:
            f.write(base64.b64decode(vid["data"]))
    else:
        file_id = vid["uri"].rstrip("/").split("/")[-1].split(":")[0]
        _save(httpx.get(f"{GEMINI}/files/{file_id}:download", params={"alt": "media"},
                        headers={"x-goog-api-key": key}, timeout=300, follow_redirects=True), out_path)
    return 0.0  # Google has not published a per-second price we can rely on; see AI Studio billing


def _save(resp, path):
    resp.raise_for_status()
    with open(path, "wb") as f:
        f.write(resp.content)
