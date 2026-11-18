"""Web server: mobile web app + API."""
import hashlib
import json
import hmac
import os
import secrets
import shutil
import threading

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from . import db, pipeline, posting, scriptgen

APP_PASSWORD = os.environ.get("APP_PASSWORD", "admin")
WEB_DIR = os.path.join(os.path.dirname(__file__), "..", "web")

db.init()
_secret_file = os.path.join(db.DATA_DIR, ".secret")
if not os.path.exists(_secret_file):
    with open(_secret_file, "w") as f:
        f.write(secrets.token_hex(32))
SECRET = open(_secret_file).read().strip()
TOKEN = hmac.new(SECRET.encode(), APP_PASSWORD.encode(), hashlib.sha256).hexdigest()

app = FastAPI(title="Afli Studio")
pipeline.start_worker()


@app.middleware("http")
async def auth(request: Request, call_next):
    p = request.url.path
    if p.startswith("/api/") and p not in ("/api/login",) and request.cookies.get("afli") != TOKEN:
        return JSONResponse({"detail": "login required"}, status_code=401)
    return await call_next(request)


@app.post("/api/login")
async def login(request: Request):
    body = await request.json()
    if not hmac.compare_digest(str(body.get("password", "")), APP_PASSWORD):
        raise HTTPException(401, "รหัสผ่านไม่ถูกต้อง")
    r = JSONResponse({"ok": True})
    r.set_cookie("afli", TOKEN, max_age=60 * 60 * 24 * 365, httponly=True, samesite="lax")
    return r


@app.get("/api/me")
def me():
    return {"ok": True}


# ---------------- jobs ----------------
def _public(job):
    j = {k: v for k, v in job.items() if k not in ("video", "thumb")}
    j["has_video"] = bool(job.get("video") and os.path.exists(job["video"]))
    j["has_voice"] = os.path.exists(os.path.join(db.DATA_DIR, "jobs", str(job["id"]), "preview", "voice.m4a"))
    return j


@app.get("/api/jobs")
def jobs():
    return [_public(j) for j in db.list_jobs()]


@app.get("/api/jobs/{job_id}")
def job(job_id: int):
    j = db.get_job(job_id)
    if not j:
        raise HTTPException(404)
    return _public(j)


@app.post("/api/jobs")
async def create(url: str = Form(""), mode: str = Form("slideshow"), name: str = Form(""), price: str = Form(""),
                 post_fb: str = Form("0"), post_tiktok: str = Form("0"), script: str = Form(""),
                 review: str = Form(""), files: list[UploadFile] = File(default=[])):
    url = url.strip()
    if not url and not name:
        raise HTTPException(400, "กรุณาใส่ลิงก์สินค้า หรือชื่อสินค้า")
    product = {"name": name.strip() or None, "price": price.strip() or None, "images": [], "description": ""}
    lines = [l.strip() for l in script.splitlines() if l.strip()]
    job_id = db.create_job(url=url, mode=mode, product=json.dumps(product, ensure_ascii=False),
                           post_fb=int(post_fb == "1"), post_tiktok=int(post_tiktok == "1"), status="uploading",
                           script=json.dumps({"lines": lines}, ensure_ascii=False) if lines else None,
                           reuse_script=2 if lines else 0,
                           review=int((review or db.get_settings().get("review_first")) == "1"))
    up = os.path.join(pipeline.job_dir(job_id), "uploads")
    os.makedirs(up, exist_ok=True)
    for i, f in enumerate(files):
        if not f.filename:
            continue
        ext = os.path.splitext(f.filename)[1].lower() or ".jpg"
        with open(os.path.join(up, f"u{i:02d}{ext}"), "wb") as out:
            shutil.copyfileobj(f.file, out)
    db.update_job(job_id, status="queued")
    return {"id": job_id}


@app.post("/api/jobs/{job_id}/rerender")
async def rerender(job_id: int, request: Request):
    body = await request.json()
    j = db.get_job(job_id)
    script = j.get("script") or {}
    if body.get("lines"):
        script["lines"] = [l.strip() for l in body["lines"] if l.strip()]
    if "caption" in body:
        script["caption"] = body["caption"]
    db.update_job(job_id, script=script, reuse_script=int(bool(body.get("lines"))), status="queued",
                  step="รอคิว", progress=0, error=None)
    return {"ok": True}


@app.post("/api/jobs/{job_id}/info")
async def fix_info(job_id: int, name: str = Form(""), price: str = Form(""),
                   files: list[UploadFile] = File(default=[])):
    j = db.get_job(job_id)
    if not j:
        raise HTTPException(404)
    product = j.get("product") or {}
    if name.strip():
        product["name"] = name.strip()
    if price.strip():
        product["price"] = price.strip()
    up = os.path.join(pipeline.job_dir(job_id), "uploads")
    os.makedirs(up, exist_ok=True)
    start = len(os.listdir(up))
    for i, f in enumerate(files):
        if f.filename:
            ext = os.path.splitext(f.filename)[1].lower() or ".jpg"
            with open(os.path.join(up, f"u{start + i:02d}{ext}"), "wb") as out:
                shutil.copyfileobj(f.file, out)
    keep = int(bool((j.get("script") or {}).get("lines")))  # adding Flow clips keeps the same script
    db.update_job(job_id, product=product, status="queued", step="รอคิว", progress=0, error=None, reuse_script=keep)
    return {"ok": True}


def _lines(body):
    return [str(l).strip() for l in body.get("lines") or [] if str(l).strip()]


@app.get("/api/providers")
def providers():
    s = db.get_settings()
    return {"default": s.get("llm_provider") or "auto",
            "list": [{"id": p, "label": scriptgen.PROVIDERS[p][0]} for p in scriptgen.available(s)]}


@app.post("/api/jobs/{job_id}/rewrite")
def rewrite(job_id: int, body: dict):
    """Ask an AI (owner's choice) for a new script, without touching the video."""
    j = db.get_job(job_id)
    if not j:
        raise HTTPException(404)
    try:
        script = scriptgen.generate(j.get("product") or {}, db.get_settings(), body.get("provider"))
    except Exception as e:
        raise HTTPException(400, str(e))
    db.update_job(job_id, script=script, caption=pipeline.build_caption(script, j.get("product") or {}, j["url"]))
    db.append_log(job_id, f"เขียนบทใหม่โดย: {script.get('_by', '')}")
    return {"ok": True, "script": script}


@app.post("/api/jobs/{job_id}/voice")
def make_voice_preview(job_id: int, body: dict):
    j = db.get_job(job_id)
    lines = _lines(body)
    if not j or not lines:
        raise HTTPException(400, "ยังไม่มีบทพูด")
    script = j.get("script") or {}
    script["lines"] = lines
    db.update_job(job_id, script=script)
    try:
        engine = pipeline.voice_preview(job_id, lines)
    except Exception as e:
        raise HTTPException(400, f"สร้างเสียงไม่สำเร็จ: {e}")
    return {"ok": True, "engine": engine}


@app.get("/api/jobs/{job_id}/voice")
def get_voice_preview(job_id: int):
    p = os.path.join(pipeline.job_dir(job_id), "preview", "voice.m4a")
    if not os.path.exists(p):
        raise HTTPException(404)
    return FileResponse(p, media_type="audio/mp4")


@app.post("/api/jobs/{job_id}/approve")
def approve(job_id: int, body: dict):
    """Owner checked the script: render the video with exactly these lines."""
    j = db.get_job(job_id)
    lines = _lines(body)
    if not j or not lines:
        raise HTTPException(400, "ยังไม่มีบทพูด")
    script = j.get("script") or {}
    script["lines"] = lines
    fields = dict(script=script, reuse_script=1, review=0, status="queued", step="รอคิว", progress=0, error=None)
    if body.get("mode") in ("slideshow", "mix", "ai"):
        fields["mode"] = body["mode"]
    db.update_job(job_id, **fields)
    return {"ok": True}


@app.post("/api/jobs/{job_id}/retry")
def retry(job_id: int):
    review = int(db.get_settings().get("review_first") == "1")
    db.update_job(job_id, status="queued", step="รอคิว", progress=0, error=None, reuse_script=0, review=review)
    return {"ok": True}


@app.post("/api/jobs/{job_id}/caption")
async def save_caption(job_id: int, request: Request):
    db.update_job(job_id, caption=(await request.json()).get("caption", ""))
    return {"ok": True}


@app.post("/api/jobs/{job_id}/post/{target}")
def post(job_id: int, target: str):
    fn = {"facebook": pipeline.do_post_fb, "tiktok": pipeline.do_post_tiktok}.get(target)
    if not fn:
        raise HTTPException(404)
    threading.Thread(target=fn, args=(job_id,), daemon=True).start()
    return {"ok": True}


@app.delete("/api/jobs/{job_id}")
def delete(job_id: int):
    shutil.rmtree(pipeline.job_dir(job_id), ignore_errors=True)
    db.delete_job(job_id)
    return {"ok": True}


@app.get("/api/jobs/{job_id}/video")
def get_video(job_id: int, dl: int = 0):
    j = db.get_job(job_id)
    if not j or not j.get("video"):
        raise HTTPException(404)
    return FileResponse(j["video"], media_type="video/mp4",
                        filename=f"afli-{job_id}.mp4" if dl else None)


@app.get("/api/jobs/{job_id}/thumb")
def get_thumb(job_id: int):
    j = db.get_job(job_id)
    if not j or not j.get("thumb") or not os.path.exists(j["thumb"]):
        raise HTTPException(404)
    return FileResponse(j["thumb"], media_type="image/jpeg")


# ---------------- settings ----------------
def _mask(v):
    return ("••••" + v[-4:]) if v else ""


@app.get("/api/settings")
def get_settings():
    s = db.get_settings()
    out = {k: (_mask(v) if k in db.SECRET_KEYS else v) for k, v in s.items()}
    out["tiktok_connected"] = bool(s.get("tiktok_refresh_token"))
    return out


@app.post("/api/settings")
async def save_settings(request: Request):
    body = await request.json()
    clean = {k: str(v).strip() for k, v in body.items()
             if k in db.DEFAULT_SETTINGS and not str(v).startswith("••••")}
    db.set_settings(clean)
    return {"ok": True}


_tt_state = {"v": ""}


@app.get("/api/tiktok/login")
def tiktok_login():
    s = db.get_settings()
    if not (s["tiktok_client_key"] and s["tiktok_client_secret"] and s["public_url"]):
        raise HTTPException(400, "กรอก TikTok Client Key, Client Secret และ Public URL ก่อน")
    _tt_state["v"] = secrets.token_urlsafe(16)
    return RedirectResponse(posting.tiktok_auth_url(s, _tt_state["v"]))


@app.get("/api/tiktok/callback")
def tiktok_callback(code: str = "", state: str = "", error: str = ""):
    if error or not code or state != _tt_state["v"]:
        return RedirectResponse("/#settings?tt=fail")
    posting.tiktok_exchange(code, db.get_settings())
    return RedirectResponse("/#settings?tt=ok")


app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
