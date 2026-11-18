"""Write a short Thai selling script with Claude, ChatGPT, Gemini or OpenCode.

Falls back to a simple template when no API key is set, so the system
still works on day one.
"""
import json
import re

import httpx

PROMPT = """คุณคือนักเขียนสคริปต์คลิปขายของสั้นบน TikTok และ Facebook Reels ภาษาไทย
สินค้า:
ชื่อ: {name}
ราคา: {price}
รายละเอียด: {description}

เขียนสคริปต์เสียงพากย์ยาว 25-40 วินาที:
- บรรทัดแรกเป็น hook ดึงความสนใจใน 2 วินาทีแรก
- เขียนเป็น "ภาษาพูด" ไม่ใช่ภาษาเขียน เหมือนแม่ค้าออนไลน์คุยกับเพื่อน ใช้คำลงท้ายและคำเชื่อมแบบคนพูดจริง เช่น นะ เลย อะ แบบว่า คือ จริงๆ นะ บอกเลย
- เล่าเป็นเรื่องต่อเนื่อง (ปัญหา → ของตัวนี้ช่วยยังไง → ใช้แล้วเป็นไง → ราคา → ชวนซื้อ) ไม่ใช่ลิสต์สรรพคุณทีละข้อ
- เลี่ยงคำโฆษณาแข็งๆ เช่น "คุณภาพเกินราคา" "ดีไซน์สวย ใช้งานง่าย" ให้พูดถึงรายละเอียดจริงของสินค้าตัวนี้แทน
- แบ่งเป็นบรรทัดสั้น ไม่เกิน 22 ตัวอักษร (ใช้เป็นซับไตเติล) รวม 8-14 บรรทัด แต่เมื่ออ่านต่อกันต้องเป็นประโยคพูดที่ลื่นไหล
- ห้ามอ้างสรรพคุณเกินจริง ห้ามอ้างผลทางการแพทย์ ห้ามแต่งตัวเลขที่ไม่มีในข้อมูล
- บรรทัดสุดท้ายชวนกดตะกร้า/กดลิงก์
- ห้ามใช้อีโมจิในสคริปต์เสียง

ตอบเป็น JSON เท่านั้น รูปแบบ:
{{"lines": ["...", "..."],
 "caption": "แคปชันโพสต์ 1-2 ประโยค มีอีโมจิได้",
 "hashtags": ["#...", "#..."],
 "short_title": "ชื่อสินค้าแบบสั้น ไม่เกิน 18 ตัวอักษร",
 "motion_prompt": "English prompt (2-3 sentences) for an image-to-video model like Google Veo/Flow: describe THIS product from the photo being showcased in a vertical 9:16 ad shot, camera motion, lighting, setting that fits the product, hands using it if natural; no text, no logos, no dialogue, no speech, no one talking to camera (Thai voice-over is added later)"}}"""


def _template(product):
    name = product.get("name") or "สินค้าตัวนี้"
    short = name[:18]
    price = product.get("price")
    lines = [
        "ใครกำลังหาของดีอยู่ ดูก่อน!",
        f"วันนี้มารีวิว {short}",
        "ดีไซน์สวย ใช้งานง่าย",
        "คุณภาพเกินราคามาก",
    ]
    if price:
        lines.append(f"ราคาแค่ {price} บาทเท่านั้น")
    lines += ["ของมีจำนวนจำกัดนะ", "สนใจกดตะกร้าได้เลย!"]
    return {"lines": lines, "caption": f"{short} ของมันต้องมี ✨", "hashtags": ["#ของดีบอกต่อ", "#shopee", "#รีวิว"],
            "short_title": short, "motion_prompt": f"Vertical 9:16 product ad shot of {name}. Slow cinematic camera push-in and gentle orbit around the product on a clean modern table, soft studio lighting, shallow depth of field. No text, no logos, no people talking."}


def _parse(text):
    data = json.loads(re.search(r"\{.*\}", text, re.S).group(0))
    data["lines"] = [l.strip() for l in data.get("lines", []) if l.strip()]
    if not data["lines"]:
        raise RuntimeError("AI ส่งสคริปต์ว่างกลับมา")
    return data


PROVIDERS = {  # id: (label, settings key)
    "claude": ("Claude", "anthropic_api_key"),
    "openai": ("ChatGPT", "openai_api_key"),
    "gemini": ("Gemini", "gemini_api_key"),
    "opencode": ("OpenCode", "opencode_api_key"),
}


def available(settings):
    """Providers that have a key, in the order 'auto' tries them."""
    return [p for p, (_, k) in PROVIDERS.items() if settings.get(k)]


def _err(name, r):
    try:
        msg = r.json()["error"]["message"]
    except Exception:
        msg = r.text[:200]
    hint = ""
    if r.status_code == 429 or "credit" in msg.lower() or "quota" in msg.lower():
        hint = " ➜ เครดิตหมด/ยังไม่ได้เติมเงินในหน้า API"
    elif r.status_code in (401, 403):
        hint = " ➜ คีย์ไม่ถูกต้อง"
    elif r.status_code == 404 or "unavailable" in msg.lower() or "no longer" in msg.lower():
        hint = " ➜ ชื่อรุ่น (โมเดล) ใช้ไม่ได้แล้ว เปลี่ยนในหน้าตั้งค่า"
    return RuntimeError(f"{name}{hint} ({r.status_code}: {msg[:160]})")


def _claude(prompt, settings):
    r = httpx.post("https://api.anthropic.com/v1/messages", timeout=90, headers={
        "x-api-key": settings["anthropic_api_key"], "anthropic-version": "2023-06-01",
        "content-type": "application/json"},
        json={"model": settings.get("llm_model") or "claude-haiku-5-5", "max_tokens": 1500,
              "messages": [{"role": "user", "content": prompt}]})
    if r.status_code != 200:
        raise _err("Claude", r)
    return _parse("".join(b.get("text", "") for b in r.json().get("content", [])))


def _gemini(prompt, settings):
    model = settings.get("gemini_text_model") or "gemini-3.8-flash"
    r = httpx.post(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent", timeout=90,
                   headers={"x-goog-api-key": settings["gemini_api_key"], "Content-Type": "application/json"},
                   json={"contents": [{"parts": [{"text": prompt}]}],
                         "generationConfig": {"responseMimeType": "application/json", "temperature": 0.9}})
    if r.status_code != 200:
        raise _err("Gemini", r)
    parts = r.json()["candidates"][0]["content"]["parts"]
    return _parse("".join(p.get("text", "") for p in parts))


def _chat(name, base, key, model, prompt, json_mode):
    """OpenAI-style /chat/completions (OpenAI, OpenCode Zen and other compatible gateways)."""
    body = {"model": model, "messages": [{"role": "user", "content": prompt}]}
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    r = httpx.post(base.rstrip("/") + "/chat/completions", timeout=120, json=body,
                   headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    if r.status_code != 200:
        raise _err(name, r)
    msg = r.json()["choices"][0]["message"]
    content = msg.get("content")
    if isinstance(content, list):
        content = "".join(c.get("text", "") for c in content)
    return _parse(content or "")


def _openai(prompt, settings):
    return _chat("ChatGPT", "https://api.openai.com/v1", settings["openai_api_key"],
                 settings.get("openai_model") or "gpt-5-mini", prompt, True)


def _opencode(prompt, settings):
    return _chat("OpenCode", "https://opencode.ai/zen/v1", settings["opencode_api_key"],
                 settings.get("opencode_model") or "deepseek-v4.1-flash", prompt, False)


_CALL = {"claude": _claude, "openai": _openai, "gemini": _gemini, "opencode": _opencode}


def generate(product: dict, settings: dict, provider: str | None = None) -> dict:
    """provider: claude/openai/gemini/opencode, or None/'auto' = setting, then first one with a key.
    Result has '_by' = which AI wrote it. If the chosen AI fails, the next one with a key is tried."""
    prompt = PROMPT.format(name=product.get("name"), price=product.get("price") or "ไม่ระบุ",
                           description=(product.get("description") or "-")[:1500])
    have = available(settings)
    want = provider if provider and provider != "auto" else settings.get("llm_provider") or "auto"
    if want != "auto" and want not in have:
        raise RuntimeError(f"ยังไม่ได้ใส่ API key ของ {PROVIDERS.get(want, (want,))[0]} ในหน้าตั้งค่า")
    order = ([want] if want != "auto" else []) + [p for p in have if p != want]
    if not order:
        data = _template(product)
        data["_by"] = "แม่แบบ (ยังไม่มีคีย์ AI)"
        return data
    errors = []
    for p in order:
        try:
            data = _CALL[p](prompt, settings)
            data["_by"] = PROVIDERS[p][0]
            if errors:
                data["_note"] = " / ".join(errors)
            return data
        except Exception as e:
            errors.append(str(e)[:200])
            if provider and provider != "auto":
                break  # owner picked this AI on purpose: report its error instead of silently switching
    raise RuntimeError("AI เขียนบทไม่สำเร็จ:\n• " + "\n• ".join(errors))
