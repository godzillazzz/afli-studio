"""Pull product name, price and images from a Shopee (or affiliate short) link.

Shopee blocks most bots, so we try several ways and the user can always
type the name / upload photos themselves in the web form instead.
"""
import html
import json
import re

import httpx

UA_BROWSER = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
              "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1")
UA_BOT = "facebookexternalhit/1.1 (+http://www.facebook.com/externalhit_uatext.php)"
IMG_CDN = "https://down-th.img.susercontent.com/file/"


def resolve(url: str) -> str:
    try:
        r = httpx.get(url, headers={"User-Agent": UA_BROWSER}, follow_redirects=True, timeout=20)
        return str(r.url)
    except Exception:
        return url


def parse_ids(url: str):
    m = re.search(r"-i\.(\d+)\.(\d+)", url) or re.search(r"/product/(\d+)/(\d+)", url)
    return (m.group(1), m.group(2)) if m else (None, None)


def _from_api(shop_id, item_id):
    url = f"https://shopee.co.th/api/v4/pdp/get_pc?shop_id={shop_id}&item_id={item_id}"
    r = httpx.get(url, headers={"User-Agent": UA_BROWSER, "Referer": "https://shopee.co.th/",
                                "x-api-source": "pc", "Accept": "application/json"}, timeout=20)
    data = r.json()
    item = (data.get("data") or {}).get("item") or {}
    if not item.get("title") and not item.get("name"):
        return None
    price = item.get("price") or item.get("price_min") or 0
    return {
        "name": item.get("title") or item.get("name"),
        "price": round(price / 100000) if price else None,
        "description": (item.get("description") or "")[:1500],
        "images": [IMG_CDN + i for i in (item.get("images") or [])][:8],
    }


def _from_html(url):
    r = httpx.get(url, headers={"User-Agent": UA_BOT}, follow_redirects=True, timeout=20)
    text = r.text

    def meta(prop):
        m = re.search(rf'<meta[^>]+(?:property|name)="{prop}"[^>]+content="([^"]*)"', text)
        return html.unescape(m.group(1)) if m else None

    name = meta("og:title")
    images = []
    for m in re.finditer(r'<script type="application/ld\+json"[^>]*>(.*?)</script>', text, re.S):
        try:
            ld = json.loads(m.group(1))
        except Exception:
            continue
        if isinstance(ld, dict) and ld.get("@type") == "Product":
            name = name or ld.get("name")
            img = ld.get("image")
            images += img if isinstance(img, list) else [img] if img else []
            offers = ld.get("offers") or {}
            price = offers.get("price") or offers.get("lowPrice")
            if price:
                return {"name": name, "price": price, "description": ld.get("description", "")[:1500],
                        "images": images[:8] or ([meta("og:image")] if meta("og:image") else [])}
    if meta("og:image"):
        images.append(meta("og:image"))
    if not name or "Shopee" == name.strip():
        return None
    name = re.sub(r"\s*\|\s*Shopee.*$", "", name)
    return {"name": name, "price": None, "description": meta("og:description") or "", "images": images[:8]}


def fetch_product(url: str) -> dict:
    final = resolve(url)
    shop_id, item_id = parse_ids(final)
    errors = []
    if shop_id:
        try:
            p = _from_api(shop_id, item_id)
            if p:
                return p
        except Exception as e:
            errors.append(f"api: {e}")
    try:
        p = _from_html(final)
        if p:
            return p
    except Exception as e:
        errors.append(f"html: {e}")
    raise RuntimeError("ดึงข้อมูลสินค้าจาก Shopee ไม่ได้ (Shopee บล็อกบอท) — "
                       "กรุณาพิมพ์ชื่อสินค้าและอัปโหลดรูปสินค้าในหน้าสร้างคลิปแทน " + "; ".join(errors))


def download(url: str, path: str):
    r = httpx.get(url, headers={"User-Agent": UA_BROWSER}, follow_redirects=True, timeout=60)
    r.raise_for_status()
    with open(path, "wb") as f:
        f.write(r.content)
    return path
