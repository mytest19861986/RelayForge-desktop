# RelayForge 4.3.0 — گزارش کنترل کیفیت

وضعیت: Release Candidate؛ پذیرش تولید ویندوز/سرویس زنده باز است.

- منبع پایه: RelayForge_v4.2.0_FULL.zip؛ سورس اصلی تغییری نکرده و در پوشه جدا استخراج شده است.
- Python محلی 3.13.5 / Linux؛ FastAPI موجود 0.128.2. Telethon در محیط موجود نیست؛ fixture و mock استفاده شده‌اند. هیچ توکن زنده یا ارسال واقعی استفاده نشده است.
- `python -m pytest -q tests`: 134 passed (111 قبلی بدون تغییر + 23 جدید). ابتدا یک fixture API Hash کوتاه خطا داد؛ fixture معتبر شد و تست‌ها مجدداً اجرا شدند.
- `node --check web/main.js`: pass. `python -m compileall -q main.py relayforge`: pass.
- `python smoke_runtime.py <python> main.py`: pass؛ راه‌اندازی HTTP روی loopback و پاسخ واقعی API rich text و پیش‌نمایش محلی AI با کلید داشبورد و بدون ارسال واقعی.
- آزمون Chromium E2E: مسدود به علت `net::ERR_BLOCKED_BY_ADMINISTRATOR` هنگام localhost؛ کلیک‌ها و رندر زنده تأیید نشده‌اند.
- Windows EXE / Windows Service / Task Scheduler: از نسخه قبلی حفظ شده؛ ساخت واقعی در این محیط ممکن نیست. ابزار build تست سورس و smoke باینری را الزامی می‌کند.
- شبکه و سرویس AI: mock برای شکل درخواست و پاسخ؛ هیچ درخواست پولی ارسال نشده. تست سقف ۱۰، رضایت، غیرفعال بودن پیش‌فرض، رد تغییر قیمت و قفل نام دستی، عدم انتشار. قفل واژگانی تضمین معنا یا صحت تمام حقایق نیست.
- Rich Text: تبدیل UTF-16، کپشن Bot API، fixture Telethon، جابه‌جایی prefix، migration ستون جدید، بازخوانی بدون requeue ارسال موفق، serialization multipart، رندر بله، fallback و حالت‌های اختیاری ایتا/روبیکا.
- گیت AT-01 دیداری ۵ پیام‌رسان و مجموعه ۵۰ نمونه کامل نشده؛ AT-16 Windows smoke واقعی باز است. قابلیت‌های فاز D44/C50 جزو این نسخه نیستند.
