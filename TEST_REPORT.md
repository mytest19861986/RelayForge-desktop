# Test report 4.3.7
- Base: delivered 4.3.6 archive. Existing 203 tests retained; expected version assertions updated.
- 217 passed (14 additional cases) on local Linux/Python; `python -m pytest -q`.
- `node --check web/main.js` passed.
- Real local HTTP smoke: `python smoke_runtime.py python main.py` passed; verifies version, assets, authentication and offline AI/rich-text previews.
- New tests: sampling/deduplication, source reference validation, local metrics and 90/30 partition, profile file persistence/edit, invalid path, consent, bounded mocked AI input, authenticated routes.
- Not certified: live Telegram history (Telethon unavailable here), live Gemini, full browser click-through, Windows build, source-side media licenses. No actual message sent.
