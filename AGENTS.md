# AGENTS.md — Afli Studio (handoff context)

Read this first. It is the full context of the project so far (built with Claude, Nov 2026).

## Owner & how to work with him
- Owner: GitHub `godzillazzz`. Thai speaker — **always reply in Thai**, short, step by step.
- Says he is **not strong with computers**. Give copy-paste commands, one at a time, and say what he should see. He sends phone screenshots of errors.
- He uses PuTTY (Windows) to reach the server as `root` (no `sudo` installed). He monitors the app from an iPhone browser.
- Does Shopee affiliate marketing; wants cheap, easy, mostly automatic video production.

## What the system does
Phone web app: paste a Shopee (affiliate) link → fetch product → AI writes Thai selling script → Thai voice-over → subtitles burned in → 1080x1920 video → post to Facebook Page Reels and/or send to TikTok inbox.

## Deployment (production)
- Server: Proxmox **LXC container** `sms-mick`, LAN IP `192.168.23.94`, Debian 13 (trixie). Logged in as root.
- **The same container also runs his production system SMS-v3 (PostgreSQL etc.). Do not touch it.** Never stop/modify other services, Postgres, or firewall rules beyond what Docker adds. After infra changes ask him to confirm SMS-v3 still works.
- App lives in `/root/afli-studio` (git clone of this repo), runs in Docker: container `afli-studio`, port **8088** → `http://192.168.23.94:8088`.
- Data (SQLite DB, job files, `music/`) in `./data` (git-ignored). Login password in `./.env` (`APP_PASSWORD`, `PORT`) — never commit `.env`.
- Update/deploy: `cd ~/afli-studio && git pull && bash install.sh` (install.sh is idempotent: installs Docker if missing, keeps .env, `docker compose up -d --build`).
- Logs: `docker logs afli-studio --tail 100`.
- Remote access outside LAN: not set up yet (suggested Tailscale; Cloudflare Tunnel needed for TikTok OAuth https redirect).

## Code map
- `app/main.py` — FastAPI: cookie login, jobs API, settings API, TikTok OAuth, serves `web/`.
- `app/pipeline.py` — background worker thread; `process(job_id)` = product → script → voice → visuals → ffmpeg → optional auto-post.
- `app/shopee.py` — resolve short link, try `api/v4/pdp/get_pc`, then HTML og/ld+json. **Shopee often returns 403**; user can type name/price + upload screenshots instead (UI form "ใส่ชื่อ/ราคา/รูปเอง แล้วลองใหม่" → `POST /api/jobs/{id}/info`).
- `app/scriptgen.py` — script AI chosen by setting `llm_provider` (auto/claude/openai/gemini/opencode). Claude Messages API, OpenAI chat completions (`gpt-5-mini`, json_object), Gemini generateContent, OpenCode Zen (OpenAI-compatible `https://opencode.ai/zen/v1`, model `deepseek-v4-flash`). Auto tries the others if one fails; result has `_by`. Template fallback when no key.
- Review step (setting `review_first`, job column `review`): pipeline stops after the script with status `review`. Job page: edit lines, `POST /rewrite {provider}`, `POST /voice` (voice preview, cached in `jobs/<id>/preview`, reused at render if lines+voice settings unchanged), `POST /approve {lines, mode}` renders.
- `app/tts.py` — `speak_script()`: engine `gemini` (Gemini TTS, whole script in one call with a conversational style prompt; subtitle timing found by matching audio pauses to lines via DP in `align()`), else edge-tts per line (`th-TH-PremwadeeNeural`/`NiwatNeural`) with retries, then gTTS fallback.
- One Gemini API key powers: Gemini TTS, script writing when no Anthropic key (`gemini_text_model`), and Veo.
- Owner can write his own Thai voice-over at creation (`reuse_script=2`: keep his lines, AI writes caption only). Uploaded clips (e.g. from Google Flow) replace paid AI clips and are used up to 10 s; their audio is always dropped. Job page has a Flow helper: AI-written Flow prompt + upload clips → re-render.
- `app/video.py` — ffmpeg: blurred-bg + zooming foreground per image, video segments, concat, ASS subtitles (font "Noto Sans Thai"), optional music from `data/music/*.mp3`.
- `app/aivideo.py` — AI image-to-video, provider setting `ai_provider`: `fal` (Kling 2.1 Standard, ~$0.25/5s) or `veo` (Gemini API `predictLongRunning`, 9:16, 720p, 6 s; Lite/Fast/Standard). Returns cost USD.
- `app/posting.py` — Facebook Reels (start → rupload → finish, Graph v25.0); TikTok Content Posting API **upload-to-inbox** (`/v2/post/publish/inbox/video/init/`).
- `app/db.py` — SQLite tables `settings` (all keys editable in the web Settings page) and `jobs`.
- `web/index.html` — single-file mobile UI (Thai), tabs: สร้างคลิป / งานทั้งหมด / ตั้งค่า. PWA manifest.

## Decisions already made (keep unless owner changes them)
- **TikTok basket (ปักตะกร้า) is NOT automated.** TikTok's API cannot attach products; browser bots risk bans. Flow: video goes to TikTok inbox → owner pins product in app → posts. Manual fallback: download button + copy caption.
- **Do not download/reuse other creators' videos** (copyright + reach penalty). Use shop images, owner's own clips, or AI clips.
- **Google Flow has no official API.** Owner has a Flow subscription; use it by making clips in Flow manually and uploading them. Do not use unofficial Flow wrappers (account risk). Veo via Gemini API is billed separately from Flow.
- Settings live in the DB (edited from phone), not in `.env`, so the owner never edits files.
- Secrets are masked in the settings API (`••••1234`) and never logged.

## Status (2026-11-18)
- Commit after 5da2418: Google retired gemini-2.5-flash (new users), gemini-2.5-flash-preview-tts (2026-11-17) and Veo 3.1 previews (2026-10-22). Defaults now gemini-3.8-flash, gemini-3.8-flash-tts, video = Gemini Omni Flash (`gemini-omni-1.1-flash`, `POST /v1beta/interactions`, setting names `ai_provider=veo`/`veo_model` kept). OpenCode default deepseek-v4.1-flash. `db.RETIRED` maps old saved ids. Omni and 3.8 TTS untested against real API. OpenAI key had no credits.
- Added multi-AI script writing + review/voice-preview/one-button render (commit 5da2418). Owner has ChatGPT, Gemini, Claude, OpenCode accounts — API keys still needed (subscriptions are not API keys). Not yet verified with real APIs.
- Owner feedback: Edge voice sounds robotic/"like reciting" → added Gemini TTS + conversational script prompt (commit after d64be5e). Not yet verified on the server.
- Installed and running on the server; web app opens on his phone.
- Job #1: Shopee 403 → expected; fixed UX with manual-info retry form.
- Job #2: edge-tts "No audio was received" → fixed with clean text + retries + gTTS fallback (commit 7fe0b2e). **Not yet verified on the server** — ask him to `git pull && bash install.sh`, retry job #2, and check the log line `เสียงพากย์: ...` (Edge vs Google).
- Facebook/TikTok/fal/Veo/Anthropic keys: not configured yet as far as we know.

## Next tasks (owner agreed in principle, after voice works)
1. **Clip library per product** — upload Flow (or own) clips once, auto-reused for every video of that product.
2. **Multi-version button** — one set of AI/Flow clips → 3–5 videos with different hook/script/voice/shot order (cuts average AI cost per video).
3. **Cheaper AI options** — clip length selector (Veo Lite supports 4/6/8 s), more fal models (e.g. MiniMax/Wan), "one AI clip split into several shots" (crop/zoom/flip/speed).
4. Optional: better paid Thai TTS if edge-tts keeps failing; remote access (Tailscale); Facebook token setup help.

## Testing tips
- Local run: `DATA_DIR=/tmp/afli APP_PASSWORD=t SUB_FONT=Garuda uvicorn app.main:app --port 8099` (needs ffmpeg + a Thai font).
- Pipeline can be tested offline by monkeypatching `tts.speak` and putting images in `data/jobs/<id>/uploads`.
- Commit messages in English; push to `main`; then tell the owner the one-line update command.
