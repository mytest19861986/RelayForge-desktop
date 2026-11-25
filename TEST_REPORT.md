# RelayForge 4.0.0 — Evidence / Test report

Baseline: `RelayForge_v3.0.2_PY312_BUILD_FIXED_SOURCE.zip` extracted and upgraded, retaining additive SQLite schema and prior architecture. No modification of the user's live data, VPS, or GitHub.

- Environment: Linux, CPython 3.13.5; FastAPI 0.128.2, Starlette 0.50.0, Pydantic 2.13.5, Uvicorn 0.48.0, HTTPX 0.28.1, cryptography 46.0.4, pytest 9.0.2, pytest-asyncio 1.3.0.
- Unit and API test suite: **75 passed**, including 50 baseline tests and 25 additional v4 tests. Two baseline test expectations intentionally changed: BotFather historical copy now accepts a bounded archive (instead of always rejecting it), and backfill explicitly opts in. Existing security, queue isolation, restart and migration tests retained.
- Actual local Python runtime HTTP smoke: PASS. Started subprocess, checked health, HTML and JS/CSS, 401 on unauthorized request, authenticated status with idle engine; stopped disposable test process. No external posts sent.
- JS syntax (`node --check web/main.js`): PASS. Python self-test: PASS. Offline screenshots rendered actual HTML/CSS/JS with synthetic status and no live API. Direct Chromium localhost navigation was blocked by environment policy, so complete live browser interactions not verified.
- `Telethon` is not installed in this Linux environment; tests for Telethon actual account login, media download, and reconnect are **not performed**. Provider HTTP tests use mocks. No real provider credentials used.
- Windows compile, EXE smoke, scheduler, service registration, Windows 10/11 and Server 2019 deployment: **UNVERIFIED**. Windows workflow and scripts included, not run here. No Windows binary included. Nuitka source protection is not absolute.

## Key regression scenarios
1. Archive end 200 does not drop live 201; disabling live prevents ingestion.
2. Exact hashtag and keyword any/all matching, exclusions, prefix/suffix.
3. Multiple independent destination IDs; default no backfill; opt-in backfill; token update preserves history.
4. Uncertain delivery blocks only own destination; connect error and rate limit deferred; read timeout remains uncertain.
5. Bot archive references only Telegram; historical reference upgraded if real live content becomes available; cursor persists; filter/archive incompatible in bot mode.
6. Reset archives previous operations; backup and no reset with uncertain delivery; API one-time ticket; validation errors redact input.
7. Provider result missing message ID rejected; Telegram copy protection and same-chat safeguard; publishing rights mock check.

## Release gates still open
Real Telegram BotFather and Telethon account tests, Eitaa/Bale/Rubika/Soroush text and media sends, Windows build and service lifecycle, full browser workflow, media album handling and automatic safe cache pruning, single-panel multi-project management, exact daily clock schedule. Status: release candidate / NOT production accepted.
