"""Job pipeline: product -> script -> voice -> visuals -> video -> post."""
import glob
import os
import shutil
import threading
import time
import traceback

from . import aivideo, db, posting, scriptgen, shopee, tts, video

VIDEO_EXT = (".mp4", ".mov", ".m4v", ".webm")
IMG_EXT = (".jpg", ".jpeg", ".png", ".webp")
USD_THB = 36


def job_dir(job_id):
    d = os.path.join(db.DATA_DIR, "jobs", str(job_id))
    os.makedirs(d, exist_ok=True)
    return d


def build_caption(script, product, url, for_fb=True):
    tags = " ".join(script.get("hashtags") or [])
    cap = (script.get("caption") or product.get("name") or "").strip()
    if for_fb and url:
        cap += f"\n\n🛒 สั่งซื้อที่นี่: {url}"
    return (cap + "\n\n" + tags).strip()


def step(job_id, text, pct):
    db.update_job(job_id, step=text, progress=pct)
    db.append_log(job_id, text)


def process(job_id):
    job = db.get_job(job_id)
    s = db.get_settings()
    d = job_dir(job_id)
    up_dir = os.path.join(d, "uploads")
    os.makedirs(up_dir, exist_ok=True)
    cost = 0.0

    # 1) product info ---------------------------------------------------
    product = job.get("product") or {}
    if not product.get("name") or (not product.get("images") and not _uploads(up_dir)):
        step(job_id, "กำลังดึงข้อมูลสินค้าจาก Shopee", 5)
        fetched = shopee.fetch_product(job["url"])
        fetched.update({k: v for k, v in product.items() if v})  # user-typed values win
        product = fetched
    db.update_job(job_id, product=product)

    media = []
    img_dir = os.path.join(d, "img")
    os.makedirs(img_dir, exist_ok=True)
    for i, u in enumerate(product.get("images") or []):
        p = os.path.join(img_dir, f"p{i}.jpg")
        try:
            if not os.path.exists(p):
                shopee.download(u, p)
            media.append(p)
        except Exception as e:
            db.append_log(job_id, f"โหลดรูปไม่ได้: {e}")
    uploads = _uploads(up_dir)
    user_images = [f for f in uploads if f.lower().endswith(IMG_EXT)]
    user_videos = [f for f in uploads if f.lower().endswith(VIDEO_EXT)]
    images = user_images + media
    if not images and not user_videos:
        raise RuntimeError("ไม่มีรูปหรือคลิปสินค้าเลย กรุณาอัปโหลดรูปสินค้าอย่างน้อย 1 รูป")

    # 2) script ----------------------------------------------------------
    script = job.get("script") if job.get("reuse_script") else None
    if not script or not script.get("lines"):
        step(job_id, "AI กำลังเขียนสคริปต์", 15)
        script = scriptgen.generate(product, s)
        cost += 0.003
    caption = build_caption(script, product, job["url"])
    db.update_job(job_id, script=script, caption=caption)

    # 3) voice -----------------------------------------------------------
    step(job_id, "กำลังสร้างเสียงพากย์", 25)
    vdir = os.path.join(d, "voice")
    shutil.rmtree(vdir, ignore_errors=True)
    os.makedirs(vdir)
    line_files = []
    for i, line in enumerate(script["lines"]):
        line_files.append(tts.speak(line, s["tts_voice"], s["tts_rate"], os.path.join(vdir, f"{i:02d}.mp3")))
    voice = os.path.join(d, "voice.m4a")
    timings, total = video.build_voice(line_files, voice)

    # 4) visuals ---------------------------------------------------------
    seg_dir = os.path.join(d, "seg")
    shutil.rmtree(seg_dir, ignore_errors=True)
    os.makedirs(seg_dir)
    clips = []  # (path, kind)
    mode = job["mode"]
    n_ai = 0
    if mode == "ai":
        n_ai = max(1, int(s.get("ai_clip_count") or 2))
    elif mode == "mix":
        n_ai = 1
    for i, img in enumerate(images[:n_ai]):
        step(job_id, f"AI กำลังสร้างวิดีโอ {i + 1}/{min(n_ai, len(images))} (ใช้เวลา 1-5 นาที)", 35 + i * 10)
        out = os.path.join(seg_dir, f"ai{i}.mp4")
        cost += aivideo.image_to_video(img, script.get("motion_prompt"), out, s)
        clips.append((out, "video"))
    clips += [(v, "video") for v in user_videos]
    still = images[n_ai:] or images

    step(job_id, "กำลังตัดต่อวิดีโอ", 60)
    plan, t, k = [], 0.0, 0
    for path, _ in clips:
        dur = min(max(tts.duration(path), 2.0), 6.0, total - t)
        if dur < 1.0:
            break
        plan.append(("video", path, dur))
        t += dur
    while t < total - 0.05 and still:
        dur = min(3.0, total - t)
        if dur < 1.2 and plan:  # stretch previous segment instead of a tiny one
            kind, p, dd = plan[-1]
            plan[-1] = (kind, p, dd + dur)
            break
        plan.append(("image", still[k % len(still)], dur))
        t += dur
        k += 1
    if not plan:
        raise RuntimeError("ไม่มีภาพสำหรับตัดต่อ")
    parts = []
    for i, (kind, p, dur) in enumerate(plan):
        out = os.path.join(seg_dir, f"s{i:02d}.mp4")
        (video.image_segment(p, dur, out, i) if kind == "image" else video.video_segment(p, dur, out))
        parts.append(out)
        db.update_job(job_id, progress=60 + int(25 * (i + 1) / len(plan)))
    silent = video.concat(parts, os.path.join(d, "silent.mp4"))

    step(job_id, "กำลังใส่ซับไตเติลและเสียง", 88)
    ass = video.write_ass(script["lines"], timings, total, script.get("short_title"),
                          product.get("price"), os.path.join(d, "subs.ass"))
    final = os.path.join(d, "final.mp4")
    video.finalize(silent, voice, ass, final, video.pick_music(db.DATA_DIR), float(s.get("music_volume") or 0.12))
    thumb = video.thumbnail(final, os.path.join(d, "thumb.jpg"))
    for f in glob.glob(os.path.join(seg_dir, "*")) + [silent]:
        try:
            os.remove(f)
        except OSError:
            pass
    db.update_job(job_id, video=final, thumb=thumb, cost=round(cost * USD_THB, 2),
                  status="done", step="เสร็จแล้ว", progress=100)
    db.append_log(job_id, f"วิดีโอยาว {total:.1f} วินาที ค่าใช้จ่ายประมาณ {cost * USD_THB:.2f} บาท")

    # 5) auto post -------------------------------------------------------
    job = db.get_job(job_id)
    if job["post_fb"]:
        do_post_fb(job_id)
    if job["post_tiktok"]:
        do_post_tiktok(job_id)


def _uploads(up_dir):
    return sorted(glob.glob(os.path.join(up_dir, "*")))


def do_post_fb(job_id):
    job = db.get_job(job_id)
    try:
        db.update_job(job_id, fb_status="กำลังโพสต์...")
        vid = posting.post_facebook(job["video"], job["caption"], db.get_settings())
        db.update_job(job_id, fb_status="โพสต์แล้ว ✅")
        db.append_log(job_id, f"โพสต์ Facebook Reels แล้ว (video id {vid})")
    except Exception as e:
        db.update_job(job_id, fb_status=f"ผิดพลาด: {e}")
        db.append_log(job_id, f"Facebook ผิดพลาด: {e}")


def do_post_tiktok(job_id):
    job = db.get_job(job_id)
    try:
        db.update_job(job_id, tt_status="กำลังส่ง...")
        pid = posting.send_tiktok_inbox(job["video"], db.get_settings())
        db.update_job(job_id, tt_status="ส่งเข้าแอป TikTok แล้ว ➜ เปิดกล่องข้อความในแอป ปักตะกร้า แล้วกดโพสต์")
        db.append_log(job_id, f"ส่งเข้า TikTok inbox แล้ว ({pid})")
    except Exception as e:
        db.update_job(job_id, tt_status=f"ผิดพลาด: {e}")
        db.append_log(job_id, f"TikTok ผิดพลาด: {e}")


def worker_loop():
    while True:
        job_id = db.next_queued()
        if not job_id:
            time.sleep(2)
            continue
        try:
            process(job_id)
        except Exception as e:
            db.update_job(job_id, status="error", error=str(e), step="ผิดพลาด")
            db.append_log(job_id, "ERROR: " + str(e))
            traceback.print_exc()


def start_worker():
    threading.Thread(target=worker_loop, daemon=True).start()
