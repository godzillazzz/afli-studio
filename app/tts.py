"""Thai voice-over.

Engines (Settings → เสียงพากย์):
  gemini - Google Gemini TTS. Reads the WHOLE script in one go with a
           "friendly TikTok reviewer" style prompt → natural, not robotic.
           Needs the Gemini API key. ~0.3 baht per clip.
  edge   - Microsoft Edge neural voices (free). Line by line.
  gTTS   - last-resort free fallback.

speak_script() returns (audio_path, [(start, end) per line], total_seconds).
"""
import asyncio
import base64
import os
import re
import subprocess
import time

import edge_tts
import httpx

EDGE_VOICES = ("th-TH-PremwadeeNeural", "th-TH-NiwatNeural")
GEMINI = "https://generativelanguage.googleapis.com/v1beta"
DEFAULT_STYLE = ("Read this Thai script out loud like a cheerful Thai TikTok seller chatting with a friend: "
                 "natural, warm, lively and conversational, NOT like reading. Vary the intonation, "
                 "emphasise the key words, and leave a short natural pause between lines. "
                 "Only speak the Thai script itself:")
last_engine = {"name": ""}


def clean(text: str) -> str:
    text = re.sub(r"[^฀-๿a-zA-Z0-9\s.,!?%฿\-]", " ", text)
    text = text.replace("฿", " บาท ")
    return re.sub(r"\s+", " ", text).strip()


def run(cmd):
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(p.stderr[-500:])
    return p


def duration(path: str) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                          "-of", "default=nw=1:nk=1", path], capture_output=True, text=True)
    try:
        return float(out.stdout.strip() or 0)
    except ValueError:
        return 0.0


# ---------------- whole-script engines ----------------
def _gemini(lines, s, wav_path):
    key = s.get("gemini_api_key")
    model = s.get("gemini_tts_model") or "gemini-3.8-flash-tts"
    style = (s.get("tts_style") or "").strip() or DEFAULT_STYLE
    text = style + "\n\n" + "\n".join(lines)
    body = {"contents": [{"parts": [{"text": text}]}],
            "generationConfig": {"responseModalities": ["AUDIO"],
                                 "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {
                                     "voiceName": s.get("gemini_voice") or "Leda"}}}}}
    for attempt in range(3):  # Gemini sometimes answers 503 "high demand" for a moment
        r = httpx.post(f"{GEMINI}/models/{model}:generateContent", json=body, timeout=180,
                       headers={"x-goog-api-key": key, "Content-Type": "application/json"})
        if r.status_code not in (429, 500, 503):
            break
        time.sleep(5 * (attempt + 1))
    if r.status_code >= 300:
        raise RuntimeError(f"Gemini TTS {r.status_code}: {r.text[:300]}")
    parts = r.json()["candidates"][0]["content"]["parts"]
    part = next((p.get("inlineData") or p.get("inline_data") for p in parts
                 if p.get("inlineData") or p.get("inline_data")), None)
    if not part:
        raise RuntimeError("Gemini TTS ไม่ส่งเสียงกลับมา")
    pcm = wav_path + ".pcm"
    with open(pcm, "wb") as f:
        f.write(base64.b64decode(part["data"]))
    rate = re.search(r"rate=(\d+)", part.get("mimeType", "")) or None
    run(["ffmpeg", "-y", "-f", "s16le", "-ar", rate.group(1) if rate else "24000", "-ac", "1", "-i", pcm, wav_path])
    os.remove(pcm)
    return wav_path


def _silences(path, noise="-32dB", min_d=0.15):
    p = subprocess.run(["ffmpeg", "-i", path, "-af", f"silencedetect=noise={noise}:d={min_d}", "-f", "null", "-"],
                       capture_output=True, text=True)
    starts = [float(x) for x in re.findall(r"silence_start: ([\d.]+)", p.stderr)]
    ends = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", p.stderr)]
    return list(zip(starts, ends))


def align(lines, path):
    """Estimate when each line is spoken by matching the pauses in the audio
    to where each line should end (by character count). Dynamic programming
    picks the best increasing set of pauses; longer pauses are preferred."""
    total = duration(path)
    sil = _silences(path)
    speech_start = sil[0][1] if sil and sil[0][0] < 0.05 else 0.0
    speech_end = sil[-1][0] if sil and sil[-1][1] >= total - 0.05 else total
    inner = [x for x in sil if x[0] > speech_start + 0.1 and x[1] < speech_end - 0.1]
    n = len(lines)
    w = [max(len(l), 1) for l in lines]
    span = speech_end - speech_start
    targets, acc = [], 0
    for x in w[:-1]:
        acc += x
        targets.append(speech_start + span * acc / sum(w))
    k, m = len(targets), len(inner)
    bounds = []
    if k and m >= k:
        INF = float("inf")
        cost = lambda t, sl: abs((sl[0] + sl[1]) / 2 - t) - 0.8 * (sl[1] - sl[0])
        dp = [[INF] * m for _ in range(k)]
        back = [[-1] * m for _ in range(k)]
        for j2 in range(m):
            dp[0][j2] = cost(targets[0], inner[j2])
        for i2 in range(1, k):
            best, arg = INF, -1
            for j2 in range(m):
                if j2 >= 1 and dp[i2 - 1][j2 - 1] < best:
                    best, arg = dp[i2 - 1][j2 - 1], j2 - 1
                if arg >= 0:
                    dp[i2][j2] = best + cost(targets[i2], inner[j2])
                    back[i2][j2] = arg
        j2 = min(range(m), key=lambda x: dp[k - 1][x])
        picks = []
        for i2 in range(k - 1, -1, -1):
            picks.append(j2)
            j2 = back[i2][j2]
        bounds = [inner[x] for x in reversed(picks)]
    elif k:
        bounds = [(t, t) for t in targets]
        for sl in inner:  # snap the nearest estimate to each real pause
            idx = min(range(k), key=lambda x: abs(targets[x] - (sl[0] + sl[1]) / 2))
            bounds[idx] = sl
        bounds.sort()
    timings, start = [], speech_start
    for a, b in bounds:
        end = max(a, start + 0.3)
        timings.append((start, end))
        start = max(b, end)
    timings.append((start, max(speech_end, start + 0.3)))
    return timings, total


# ---------------- line-by-line engines ----------------
async def _edge(text, voice, rate, path):
    await edge_tts.Communicate(text, voice, rate=rate).save(path)


def speak(text, voice, rate, path):
    text = clean(text) or "สินค้าดี"
    if voice not in EDGE_VOICES:
        voice = EDGE_VOICES[0]
    errors = []
    other = EDGE_VOICES[1] if voice == EDGE_VOICES[0] else EDGE_VOICES[0]
    for v, r in ((voice, rate), (voice, "+0%"), (other, "+0%")):
        try:
            asyncio.run(_edge(text, v, r, path))
            if duration(path) > 0.2:
                last_engine["name"] = f"Edge {v}"
                return path
        except Exception as e:
            errors.append(f"{v}/{r}: {e}")
    try:
        from gtts import gTTS
        gTTS(text, lang="th").save(path)
        last_engine["name"] = "Google แปลภาษา (สำรอง)"
        return path
    except Exception as e:
        errors.append(f"google: {e}")
    raise RuntimeError("สร้างเสียงพากย์ไม่สำเร็จทุกช่องทาง: " + " | ".join(errors)[:600])


def speak_script(lines, s, workdir, build_voice):
    """Returns (voice_m4a, timings, total). build_voice = video.build_voice for the per-line path."""
    lines = [clean(l) or "..." for l in lines]
    voice = os.path.join(workdir, "voice.m4a")
    if s.get("tts_engine") == "gemini" and s.get("gemini_api_key"):
        try:
            wav = _gemini(lines, s, os.path.join(workdir, "voice_raw.wav"))
            timings, total = align(lines, wav)
            run(["ffmpeg", "-y", "-i", wav, "-af", "apad=pad_dur=0.6", "-c:a", "aac", "-b:a", "160k", voice])
            last_engine["name"] = f"Gemini {s.get('gemini_voice') or 'Leda'} (อ่านต่อเนื่องทั้งบท)"
            return voice, timings, total + 0.6
        except Exception as e:
            last_engine["fallback_reason"] = str(e)[:200]
    files = [speak(l, s.get("tts_voice"), s.get("tts_rate") or "+0%", os.path.join(workdir, f"{i:02d}.mp3"))
             for i, l in enumerate(lines)]
    timings, total = build_voice(files, voice)
    return voice, timings, total
