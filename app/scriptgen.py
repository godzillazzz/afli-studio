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
- ภาษาพูดเป็นกันเอง เป็นธรรมชาติ เหมือนคนรีวิวจริง
- แต่ละบรรทัดสั้น ไม่เกิน 22 ตัวอักษร (เพราะใช้เป็นซับไตเติลบนจอมือถือ) รวม 8-14 บรรทัด
- ห้ามอ้างสรรพคุณเกินจริง ห้ามอ้างผลทางการแพทย์ ห้ามแต่งตัวเลขที่ไม่มีในข้อมูล
- บรรทัดสุดท้ายชวนกดตะกร้า/กดลิงก์
- ห้ามใช้อีโมจิในสคริปต์เสียง

ตอบเป็น JSON เท่านั้น รูปแบบ:
{{"lines": ["...", "..."],
 "caption": "แคปชันโพสต์ 1-2 ประโยค มีอีโมจิได้",
 "hashtags": ["#...", "#..."],
 "short_title": "ชื่อสินค้าแบบสั้น ไม่เกิน 18 ตัวอักษร",
 "motion_prompt": "English prompt describing a short product showcase camera motion for an image-to-video model"}}"""


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
            "short_title": short, "motion_prompt": "slow cinematic push-in on the product, soft studio lighting"}


def generate(product: dict, settings: dict) -> dict:
    key = settings.get("anthropic_api_key")
    if not key:
        return _template(product)
    prompt = PROMPT.format(name=product.get("name"), price=product.get("price") or "ไม่ระบุ",
                           description=(product.get("description") or "-")[:1500])
    r = httpx.post("https://api.anthropic.com/v1/messages", timeout=90, headers={
        "x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
        json={"model": settings.get("llm_model") or "claude-haiku-5-5", "max_tokens": 1500,
              "messages": [{"role": "user", "content": prompt}]})
    if r.status_code != 200:
        raise RuntimeError(f"AI เขียนสคริปต์ไม่สำเร็จ ({r.status_code}): {r.text[:300]}")
    text = "".join(b.get("text", "") for b in r.json().get("content", []))
    m = re.search(r"\{.*\}", text, re.S)
    data = json.loads(m.group(0))
    data["lines"] = [l.strip() for l in data.get("lines", []) if l.strip()]
    if not data["lines"]:
        raise RuntimeError("AI ส่งสคริปต์ว่างกลับมา")
    return data
