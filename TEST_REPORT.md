# RelayForge v4.3.6 — local verification

- Baseline: delivered v4.3.5 source ZIP; 184 prior tests passed unchanged.
- Full suite: 203 passed (19 new). Python 3.13.5 / Linux; mock HTTP for external Gemini API. FFmpeg real local output in clip test.
- New tests: economy/quality order and bounds; HTTP 503/404 fallback; stop on 400/401/403/429/500; consent and daily budget; diagnostic JSON result; ambiguous timeout stop; Retry-After; lexical fact guard; podcast script; image/sticker/MP4; no external request when FFmpeg missing; project isolation and separate dashboard authentication.
- node --check web/main.js: PASS (syntax only).
- python smoke_runtime.py python main.py: PASS (real local HTTP, health/version, assets, unauthorized access, rich text and local AI preview).
- Two simultaneous local processes: PASS (different data directories and ports, different dashboard keys; cross-project authentication rejected).
- Telethon not installed in this environment; installing it failed due to DNS. No real Telegram/AI provider traffic or Windows binary compilation was performed. Full browser click test not performed. Previous version's successful live podcast is user-reported.
- FFmpeg optional, not bundled. MP4 is image-based motion, not Veo. No automatic publication.
