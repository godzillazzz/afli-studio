"""AI image-to-video. Two providers, chosen in Settings:

  fal  - Kling 2.1 Standard via fal.ai: ~$0.25 per 5-second clip
  veo  - Google Veo 3.1 via Gemini API (6-second 720p clip):
         Lite ~$0.05/s, Fast ~$0.10/s, Standard ~$0.40/s

image_to_video() returns the cost in USD so the job can show it.
"""
import base64
import mimetypes
import time

import httpx

FAL_PRICE_PER_CLIP = 0.25
VEO_SECONDS = 6
VEO_PRICE_PER_SEC = {"lite": 0.05, "fast": 0.10}  # anything else = standard 0.40
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


# ---------------- Google Veo (Gemini API) ----------------
def _veo(image_path, prompt, out_path, settings, timeout=900):
    key = settings.get("gemini_api_key")
    if not key:
        raise RuntimeError("ยังไม่ได้ใส่ Gemini API key ในหน้าตั้งค่า (จำเป็นสำหรับ Veo)")
    model = settings.get("veo_model") or "veo-3.1-fast-generate-preview"
    headers = {"x-goog-api-key": key, "Content-Type": "application/json"}
    params = {"aspectRatio": "9:16", "durationSeconds": VEO_SECONDS, "resolution": "720p",
              "negativePrompt": NEG}
    img = _b64(image_path)
    # The REST image field has had two shapes; try the documented one, then the other.
    shapes = [{"bytesBase64Encoded": img, "mimeType": _mime(image_path)},
              {"inlineData": {"mimeType": _mime(image_path), "data": img}}]
    r = None
    for shape in shapes:
        body = {"instances": [{"prompt": prompt, "image": shape}], "parameters": params}
        r = httpx.post(f"{GEMINI}/models/{model}:predictLongRunning", headers=headers, json=body, timeout=90)
        if r.status_code < 300 or r.status_code != 400:
            break
    if r.status_code >= 300:
        raise RuntimeError(f"Veo ไม่รับงาน ({r.status_code}): {r.text[:400]}")
    op_name = r.json()["name"]

    start = time.time()
    while time.time() - start < timeout:
        time.sleep(10)
        op = httpx.get(f"{GEMINI}/{op_name}", headers={"x-goog-api-key": key}, timeout=30).json()
        if op.get("error"):
            raise RuntimeError(f"Veo สร้างวิดีโอไม่สำเร็จ: {op['error'].get('message', op['error'])}")
        if op.get("done"):
            break
    else:
        raise RuntimeError("Veo ใช้เวลานานเกินไป")

    resp = (op.get("response") or {}).get("generateVideoResponse") or {}
    samples = resp.get("generatedSamples") or []
    if not samples:
        why = resp.get("raiMediaFilteredReasons") or op.get("response")
        raise RuntimeError(f"Veo ไม่ส่งวิดีโอกลับมา (อาจโดนตัวกรองเนื้อหาของ Google): {str(why)[:300]}")
    uri = samples[0]["video"]["uri"]
    _save(httpx.get(uri, headers={"x-goog-api-key": key}, timeout=180, follow_redirects=True), out_path)

    tier = next((t for t in VEO_PRICE_PER_SEC if t in model), None)
    return VEO_SECONDS * VEO_PRICE_PER_SEC.get(tier, 0.40)


def _save(resp, path):
    resp.raise_for_status()
    with open(path, "wb") as f:
        f.write(resp.content)
