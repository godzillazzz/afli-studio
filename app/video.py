"""FFmpeg video assembly: 1080x1920 vertical, blurred background,
gentle zoom on product photos, burned-in Thai subtitles, voice + music."""
import glob
import os
import random
import subprocess

from .tts import duration

W, H, FPS = 1080, 1920, 30
FONT = os.environ.get("SUB_FONT", "Noto Sans Thai")
ENC = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "22", "-pix_fmt", "yuv420p", "-r", str(FPS)]


def run(cmd):
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError("ffmpeg error: " + p.stderr[-800:])
    return p


def _fit_filter(dur, zoom=True, direction=1):
    # background: cover + blur ; foreground: fit inside width, slow zoom
    z = f"(1+0.10*t/{dur:.3f})" if direction > 0 else f"(1.10-0.10*t/{dur:.3f})"
    fg_scale = (f",scale=w='trunc(iw*{z}/2)*2':h=-2:eval=frame" if zoom else "")
    return (f"[0:v]split[a][b];"
            f"[a]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},boxblur=25:3,eq=brightness=-0.08[bg];"
            f"[b]scale={W - 60}:{int(H * 0.62)}:force_original_aspect_ratio=decrease{fg_scale}[fg];"
            f"[bg][fg]overlay=(W-w)/2:(H-h)/2+20:shortest=1,fps={FPS},format=yuv420p[v]")


def image_segment(img, dur, out, idx=0):
    run(["ffmpeg", "-y", "-loop", "1", "-t", f"{dur:.3f}", "-i", img,
         "-filter_complex", _fit_filter(dur, True, 1 if idx % 2 == 0 else -1),
         "-map", "[v]", "-t", f"{dur:.3f}", *ENC, "-an", out])
    return out


def video_segment(src, dur, out):
    run(["ffmpeg", "-y", "-stream_loop", "-1", "-i", src,
         "-filter_complex", _fit_filter(dur, zoom=False),
         "-map", "[v]", "-t", f"{dur:.3f}", *ENC, "-an", out])
    return out


def concat(parts, out):
    lst = out + ".txt"
    with open(lst, "w") as f:
        for p in parts:
            f.write(f"file '{os.path.abspath(p)}'\n")
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", lst, "-c", "copy", out])
    return out


def build_voice(line_files, out, gap=0.18):
    """Join line audio with short pauses; return list of (start, end) per line."""
    timings, t, inputs, filt = [], 0.25, [], []
    for i, f in enumerate(line_files):
        d = duration(f)
        timings.append((t, t + d))
        t += d + gap
        inputs += ["-i", f]
        filt.append(f"[{i}:a]aresample=44100,apad=pad_dur={gap}[a{i}]")
    total = t + 0.6
    chain = ";".join(filt) + ";" + "".join(f"[a{i}]" for i in range(len(line_files)))
    chain += f"concat=n={len(line_files)}:v=0:a=1,adelay=250|250,apad=whole_dur={total:.2f}[out]"
    run(["ffmpeg", "-y", *inputs, "-filter_complex", chain, "-map", "[out]", "-c:a", "aac", "-b:a", "160k", out])
    return timings, total


def _ass_time(s):
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{sec:05.2f}"


def _esc(t):
    return t.replace("\\", "").replace("{", "(").replace("}", ")").replace("\n", " ")


def write_ass(lines, timings, total, title, price, path):
    head = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Sub,{FONT},92,&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,6,2,2,60,60,430,222
Style: Hi,{FONT},92,&H0000E5FF,&H0000E5FF,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,6,2,2,60,60,430,222
Style: Title,{FONT},66,&H00FFFFFF,&H00FFFFFF,&H002D4BEE,&H002D4BEE,-1,0,0,0,100,100,0,0,3,18,0,8,60,60,170,222
Style: Price,{FONT},76,&H00000000,&H00000000,&H0000D7FF,&H0000D7FF,-1,0,0,0,100,100,0,0,3,16,0,8,60,60,300,222

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    ev = []
    if title:
        ev.append(f"Dialogue: 1,{_ass_time(0)},{_ass_time(total)},Title,,0,0,0,,{_esc(title)}")
    if price:
        ev.append(f"Dialogue: 1,{_ass_time(0)},{_ass_time(total)},Price,,0,0,0,,{_esc('฿' + str(price))}")
    for i, (line, (s, e)) in enumerate(zip(lines, timings)):
        style = "Hi" if i == 0 or i == len(lines) - 1 else "Sub"
        pop = r"{\fad(80,40)\t(0,120,\fscx108\fscy108)\t(120,220,\fscx100\fscy100)}"
        ev.append(f"Dialogue: 0,{_ass_time(s)},{_ass_time(e + 0.12)},{style},,0,0,0,,{pop}{_esc(line)}")
    with open(path, "w", encoding="utf-8") as f:
        f.write(head + "\n".join(ev) + "\n")
    return path


def pick_music(data_dir):
    files = glob.glob(os.path.join(data_dir, "music", "*.mp3")) + glob.glob(os.path.join(data_dir, "music", "*.m4a"))
    return random.choice(files) if files else None


def finalize(video, voice, ass, out, music=None, music_vol=0.12):
    ass_arg = ass.replace(":", r"\:").replace("'", r"\'")
    vf = f"subtitles='{ass_arg}'"
    if music:
        cmd = ["ffmpeg", "-y", "-i", video, "-i", voice, "-stream_loop", "-1", "-i", music,
               "-filter_complex",
               f"[0:v]{vf}[v];[2:a]volume={music_vol}[m];[1:a][m]amix=inputs=2:duration=first:dropout_transition=0[a]",
               "-map", "[v]", "-map", "[a]"]
    else:
        cmd = ["ffmpeg", "-y", "-i", video, "-i", voice, "-vf", vf, "-map", "0:v", "-map", "1:a"]
    run(cmd + [*ENC, "-c:a", "aac", "-b:a", "160k", "-shortest", "-movflags", "+faststart", out])
    return out


def thumbnail(video, out):
    run(["ffmpeg", "-y", "-ss", "1.0", "-i", video, "-frames:v", "1", "-vf", "scale=360:-2", out])
    return out
