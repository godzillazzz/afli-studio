"""Write a short Thai selling script with Claude (cheap Haiku model).

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


def _gemini(prompt, settings):
    model = settings.get("gemini_text_model") or "gemini-2.5-flash"
    r = httpx.post(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent", timeout=90,
                   headers={"x-goog-api-key": settings["gemini_api_key"], "Content-Type": "application/json"},
                   json={"contents": [{"parts": [{"text": prompt}]}],
                         "generationConfig": {"responseMimeType": "application/json", "temperature": 0.9}})
    if r.status_code != 200:
        raise RuntimeError(f"Gemini เขียนสคริปต์ไม่สำเร็จ ({r.status_code}): {r.text[:300]}")
    parts = r.json()["candidates"][0]["content"]["parts"]
    return _parse("".join(p.get("text", "") for p in parts))


def generate(product: dict, settings: dict) -> dict:
    key = settings.get("anthropic_api_key")
    prompt = PROMPT.format(name=product.get("name"), price=product.get("price") or "ไม่ระบุ",
                           description=(product.get("description") or "-")[:1500])
    if not key and settings.get("gemini_api_key"):
        return _gemini(prompt, settings)
    if not key:
        return _template(product)
    r = httpx.post("https://api.anthropic.com/v1/messages", timeout=90, headers={
        "x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
        json={"model": settings.get("llm_model") or "claude-haiku-5-5", "max_tokens": 1500,
              "messages": [{"role": "user", "content": prompt}]})
    if r.status_code != 200:
        raise RuntimeError(f"AI เขียนสคริปต์ไม่สำเร็จ ({r.status_code}): {r.text[:300]}")
    return _parse("".join(b.get("text", "") for b in r.json().get("content", [])))
