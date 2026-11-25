# RelayForge Studio 3.0.1 — اصلاح پیش‌نیاز تست Async

این نسخه یک اصلاح محدود برای ابزار ساخت ویندوز است. در نسخه 3.0.0 فایل `requirements-dev.txt` افزونه `pytest-asyncio` را نداشت. به همین دلیل ۸ تست `async def` با پیام «async def functions are not natively supported» شکست می‌خوردند؛ این شکست‌ها هنوز نشان‌دهنده نقص در منطق ارسال نیستند.

## راه‌حل فوری روی پوشه نسخه قبلی

در PowerShell، در پوشه پروژه، اجرا کنید:

```powershell
.\.venv\Scripts\python.exe -m pip install "pytest-asyncio>=1.0,<2"
.\.venv\Scripts\python.exe -m pytest -q tests
```

اگر همه تست‌ها موفق شدند، دوباره `BUILD_EXE_WINDOWS.bat` را اجرا کنید. اگر تست دیگری شکست خورد، خروجی آن باید جداگانه بررسی شود.

## استفاده از ZIP اصلاح‌شده

بسته SOURCE را استخراج کنید و `BUILD_EXE_WINDOWS.bat` داخل آن را اجرا کنید؛ وابستگی جدید به‌صورت خودکار نصب می‌شود. بسته WINDOWS_BUILD_KIT برای کپی ابزارهای ساخت روی پوشه سورس است و اکنون `requirements-dev.txt` اصلاح‌شده را هم دارد.

*توجه:* فایل `RelayForge.exe` از قبل در این بسته‌ها وجود ندارد. کامپایل Nuitka باید در ویندوز انجام شود. این نسخه در محیط فعلی فقط از نظر فایل‌ها و Syntax بررسی شده است؛ اجرای کامل تست‌ها و build ویندوز باید روی رایانه شما تأیید شود.
