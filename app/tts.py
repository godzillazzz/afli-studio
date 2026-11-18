"""Thai voice-over.

1st choice: Microsoft Edge neural voices (free, natural Thai)
   th-TH-PremwadeeNeural (female), th-TH-NiwatNeural (male)
Fallback:  Google Translate voice (free, plainer) if Edge refuses.
Each script line is spoken separately so subtitles line up exactly.
"""
import asyncio
import re
import subprocess

import edge_tts

EDGE_VOICES = ("th-TH-PremwadeeNeural", "th-TH-NiwatNeural")
last_engine = {"name": ""}


def clean(text: str) -> str:
    # strip emoji / symbols Edge can choke on; keep Thai, latin, digits, basic punctuation
    text = re.sub(r"[^฀-๿a-zA-Z0-9\s.,!?%฿\-]", " ", text)
    text = text.replace("฿", " บาท ")
    return re.sub(r"\s+", " ", text).strip()


async def _edge(text, voice, rate, path):
    await edge_tts.Communicate(text, voice, rate=rate).save(path)


def _gtts(text, path):
    from gtts import gTTS
    gTTS(text, lang="th").save(path)


def speak(text: str, voice: str, rate: str, path: str):
    text = clean(text) or "สินค้าดี"
    if voice not in EDGE_VOICES:
        voice = EDGE_VOICES[0]
    errors = []
    for v, r in ((voice, rate), (voice, "+0%"), (EDGE_VOICES[1] if voice == EDGE_VOICES[0] else EDGE_VOICES[0], "+0%")):
        try:
            asyncio.run(_edge(text, v, r, path))
            if duration(path) > 0.2:
                last_engine["name"] = f"Edge {v}"
                return path
        except Exception as e:
            errors.append(f"{v}/{r}: {e}")
    try:
        _gtts(text, path)
        last_engine["name"] = "Google (สำรอง)"
        return path
    except Exception as e:
        errors.append(f"google: {e}")
    raise RuntimeError("สร้างเสียงพากย์ไม่สำเร็จทุกช่องทาง: " + " | ".join(errors)[:600])


def duration(path: str) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                          "-of", "default=nw=1:nk=1", path], capture_output=True, text=True)
    try:
        return float(out.stdout.strip() or 0)
    except ValueError:
        return 0.0
