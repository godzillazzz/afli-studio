"""Thai voice-over. Uses Microsoft Edge neural voices (free, natural Thai).

Voices: th-TH-PremwadeeNeural (female), th-TH-NiwatNeural (male),
        th-TH-AcharaNeural (female).
Each script line is spoken separately so subtitles line up exactly.
"""
import asyncio
import subprocess

import edge_tts


async def _say(text, voice, rate, path):
    await edge_tts.Communicate(text, voice, rate=rate).save(path)


def speak(text: str, voice: str, rate: str, path: str, retries=3):
    last = None
    for _ in range(retries):
        try:
            asyncio.run(_say(text, voice, rate, path))
            return path
        except Exception as e:  # network hiccup: try again
            last = e
    raise RuntimeError(f"สร้างเสียงพากย์ไม่สำเร็จ: {last}")


def duration(path: str) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                          "-of", "default=nw=1:nk=1", path], capture_output=True, text=True)
    return float(out.stdout.strip() or 0)
