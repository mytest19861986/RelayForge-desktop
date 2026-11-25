# رِله‌فورج ۳.۰ — RelayForge Studio / Windows VPS Edition

**نسخه اجرایی نرم‌افزار به زبان Python و FastAPI، داشبورد فارسی HTML/CSS/JavaScript، پایگاه داده SQLite و پنج Adapter مستقل.**

## وضعیت تحویل (شفاف)

- این ZIP شامل **سورس کامل قابل اجرا**، داشبورد، ابزار ساخت EXE با Nuitka و آزمون‌ها است.
- کد FastAPI، صف‌های ارسال، ذخیره پیشرفت، BotFather realtime polling و اتصال Telethon پیاده‌سازی شده‌اند؛ آزمون محلی بدون توکن واقعی اجرا شده است.
- **در محیط تولید فعلی که لینوکس است، فایل واقعی Windows `.exe` کامپایل نشده و نباید وانمود شود آماده است.** برای ساخت فایل EXE واقعی باید `BUILD_EXE_WINDOWS.bat` را روی Windows Server 2019/Windows 10/11 اجرا کنید یا workflow موجود در مخزن خصوصی خودتان را با Windows runner اجرا کنید. در پایان `RelayForge_v3.0.0_WINDOWS_EXE.zip` واقعاً تولید می‌شود.
- Nuitka کد پایتون را به باینری native کامپایل می‌کند و فایل‌های `.py` در ZIP اجرایی قرار نمی‌گیرند، ولی EXE هرگز غیرقابل مهندسی معکوس نیست. کد فرانت‌اند HTML/CSS/JS برای مرورگر قابل مشاهده است؛ کلیدهای API داخل آن hardcode نشده‌اند.
- **اتصال واقعی به Telegram/Bale/Rubika/Eitaa/Soroush روی حساب‌های شما هنوز تست نشده**، بنابراین برای استفاده عملی ابتدا کانال‌های آزمایشی را بررسی کنید. Endpoint سروش با توجه به منابع غیررسمی/جامعه توسعه‌دهندگان است و **تأیید رسمی end-to-end نشده**.

## ویژگی‌ها

- دو روش مستقل مبدأ تلگرام در یک فرم:
  - **BotFather**: توکن بات، ادمین کانال مبدأ، `getUpdates(channel_post)` long polling بدون نیاز به وب‌هوک یا پورت ورودی؛ **فقط پیام‌های جدید دریافتی ربات**. تلگرام ممکن است آپدیت‌های زمان قطع بودن طولانی (بیش از بازه نگهداری Bot API) را نگه ندارد.
  - **API ID / API Hash** از my.telegram.org به همراه ورود اختیاری کاربر با کد/۲FA: دریافت تاریخچه مجاز با Telethon و دریافت رویداد `NewMessage`. برای دسترسی به آرشیو قدیمی بهتر است این روش انتخاب شود. هیچ حفاظت `Restrict Saving Content` دور زده نمی‌شود.
- شروع و پایان شماره پیام مادر، فعال/غیرفعال کردن realtime و history (فقط حساب کاربری).
- مقصدهای **Telegram، Bale، Rubika، Eitaa، Soroush+**: فعال/غیرفعال کردن مستقل، API Token، کانال مقصد و زمان‌بندی مستقل. صفر ثانیه=فوری؛ ۱۰۸۰۰ ثانیه=هر ۳ ساعت.
- برای مقصدهای «فوری»، پست جدید (live) در اولویت قرار می‌گیرد؛ برای زمان‌بندی بلندمدت، ترتیب آرشیو حفظ می‌شود.
- ذخیره تمام پست‌های دریافتی در SQLite؛ صف هر پیام‌رسان، ثبت outcome جداگانه؛ خطای یک مقصد بقیه را متوقف نمی‌کند.
- بعد از سه خطای متوالی در یک مقصد، تنها همان مسیر خودکار غیرفعال می‌شود و چهار مقصد دیگر فعال می‌مانند؛ پس از اصلاح اتصال می‌توان آن را در داشبورد مجدداً فعال کرد. مهاجرت افزودنی پایگاه داده، تاریخچه ارسال نسخه ۳ اولیه را حفظ می‌کند.
- حفظ `update_id` بات، `history_cursor` حساب و وضعیت ارسال‌ها پس از خاموش‌شدن. پیام `sending` هنگام restart به `uncertain` تبدیل می‌شود تا تکرار کور اتفاق نیفتد؛ تعیین تکلیف دستی در پنل.
- دانلود رسانه‌های قابل‌دسترس با محدودیت حجم و کش محلی؛ ارسال مجدد بدون Forward banner. **آلبوم‌ها، قالب‌بندی‌های غنی و برخی رسانه‌های خاص ممکن است به‌صورت کامل حفظ نشوند.**
- داشبورد راست‌به‌چپ، بدون CDN، مخصوص VPS، API دسترسی فقط localhost، کلید داشبورد تصادفی، محدودیت Origin برای عملیات، پاسخ با توکن‌های مخفی، رمزنگاری توکن‌ها در SQLite و قفل فرایند برای جلوگیری از اجرای همزمان.
- فایل لاگ گردشی در `data\logs\relayforge.log` و گزارش قابل مشاهده در داشبورد.

## نصب سورس روی Windows Server 2019

۱. Python 3.12 x64 را نصب کنید و هنگام نصب `Add python.exe to PATH` را فعال کنید. پوشه را در مسیر قابل‌نوشتن مانند `C:\RelayForgeSrc` استخراج کنید.

۲. `RUN_FROM_SOURCE_WINDOWS.bat` را اجرا کنید (در صورت نیاز کتابخانه‌ها دانلود می‌شوند). یا در PowerShell:

```powershell
cd C:\RelayForgeSrc
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe main.py
```

۳. داشبورد در `http://127.0.0.1:8765` باز می‌شود. رمز نخستین اجرا را از `data\dashboard-key.txt` بردارید. آن را به هیچ‌کس نفرستید.

۴. تب «مبدأ تلگرام» را باز کنید، یکی از روش‌ها را انتخاب و کانال را معرفی کنید. در روش کاربری، پس از ذخیره API ID/Hash/phone دکمه ارسال کد و تأیید ورود را بزنید. در روش BotFather ربات باید به کانال مبدأ دسترسی ادمین داشته باشد. دکمه «تست اتصال مبدأ» را بزنید.

۵. تب «مقصدها و APIها»: برای هرکدام توکن، شناسه کانال، فعال‌سازی و فاصله را تنظیم کنید. «تست API» فقط توکن را بررسی می‌کند، نه حق ارسال به کانال. **ابتدا با کانال آزمایشی یک پیام متن و یک فایل امتحان کنید.**

۶. تاب «لاگ و وضعیت» را ببینید. حالت `uncertain` یعنی نتیجه ارسال احتمالاً نامعلوم است؛ قبل از Retry کانال مقصد را بررسی کنید. اگر `failed` قطعی بود می‌توان همان پیام را دستی دوباره فرستاد.

## دو راه ساخت EXE مستقل بدون انتشار سورس

**روش الف: روی خود VPS یا ویندوز دیگر**

- از پوشه سورس، `BUILD_EXE_WINDOWS.bat` را با دسترسی معمولی اجرا کنید.
- اسکریپت Python venv/Requirements را نصب، ۵۰ آزمون پایه را اجرا، Nuitka را با `--mode=onefile` به‌کار می‌گیرد و در صورت موفقیت فایل `build_output\RelayForge.exe` و ZIP `RelayForge_v3.0.0_WINDOWS_EXE.zip` را می‌سازد.
- **تنها ZIP اجرایی ساخته‌شده** را به شخص ثالث بدهید. پوشه سورس و فایل‌های `data` را منتشر نکنید.

**روش ب: با GitHub Actions در مخزن PRIVATE**

- محتوای سورس را در یک Repository خصوصی در GitHub قرار دهید و Actions را فعال کنید.
- workflow `.github/workflows/build-windows.yml` با Windows Server runner اجرا و فایل ZIP اجرایی را در Artifacts قرار می‌دهد.
- هیچ توکن تلگرام یا داده جلسه کاربری داخل مخزن قرار ندهید. سورس فقط در مخزن خصوصی است؛ انتشار نهایی ZIP اجرایی فاقد فایل Python است.

**دقت**: build با Nuitka روی Windows به ابزار C و برخی دانلودها نیاز دارد؛ ساخت و رفتار اجرایی آن در این محیط لینوکس قابل تضمین نیست تا build واقعی روی ویندوز پاس شود.

## نصب دائمی روی VPS بدون Remote Desktop

۱. ZIP اجرایی واقعی را در مسیر پایدار مثل `C:\RelayForge` استخراج کنید. سپس EXE را یک بار دستی اجرا و تنظیمات را ذخیره کنید.

۲. PowerShell را در Windows Server با دسترسی Administrator باز کنید:

```powershell
cd C:\RelayForge
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\INSTALL_AUTO_START.ps1
```

۳. حساب Windows دارای دسترسی خواندن/نوشتن به پوشه برنامه (ترجیحاً یک حساب غیرادمین اختصاصی) را معرفی کنید. Task Scheduler در حالت `At startup` و `Run whether user is logged on or not` تنظیم می‌شود. هنگام اجرا با تسک، آرگومان `--no-browser` استفاده می‌شود.

۴. قبل از شروع تسک، پنجره EXE دستی را ببندید و تسک را یک‌بار Run کنید. گزارش `data\logs\relayforge.log` را بررسی کنید. دو نمونه همزمان روی همان پوشه به کمک قفل OS محدود شده‌اند.

**پوشه data را هرگز به‌صورت تصادفی حذف نکنید.** Backup امن از تمام فایل‌ها: `relayforge.sqlite3`، `secrets.key`، `dashboard-key.txt`، `telegram_user.session`، `media/` و logها. دسترسی NTFS را برای حساب اجرا محدود کنید.

## محدودیت APIها و واقعیت‌های مهم

| سرویس | ارسال متن | ارسال فایل/تصویر | توضیح |
|---|---|---|---|
| تلگرام | Bot API | SendPhoto/Video/Document/Audio/Voice | تست واقعی لازم است؛ BotFather به تاریخچه دسترسی ندارد |
| بله | Bot API رسمی | API فایل و مدیا | محدودیت حجم/نرخ و مجوز کانال مطابق حساب |
| روبیکا | Bot API رسمی | `requestSendFile → upload → sendFile` | `upload_url` باید روی دامنه HTTPS معتبر روبیکا باشد |
| ایتا | API ایتایار | `sendFile` | توکن و شناسه کانال پنل ایتایار؛ امکان هزینه/سهمیه |
| سروش‌پلاس | endpoint Bot API بر اساس کتابخانه‌های جامعه | روش‌های استاندارد Bot API | **نیازمند تأیید عملی با حساب آزمایشی** |

**اطمینان تحویل واقعی**: تأیید `ok=true` صرفاً پاسخ API است؛ ممکن است پیام با تأخیر نمایش داده شود. exactly-once بین شبکه‌های مستقل بدون پشتیبانی idempotency سمت ارائه‌دهنده تضمین نمی‌شود. به همین علت برای خطاهای شبکه پس از ارسال، وضعیت «نامطمئن» ذخیره می‌شود و Retry خودکار انجام نمی‌شود.

## منابع رسمی/اصلی مستندات پیاده‌سازی

- Telegram Bot API: https://core.telegram.org/bots/api
- Telethon docs: https://docs.telethon.dev/
- Bale official Bot API: https://docs.bale.ai/
- Rubika official Bot API: https://rubika.ir/botapi
- EitaaYar API PDF: https://eitaayar.ir/assets/download/API_eitaayar.ir.pdf
- Soroush+ community API reference (not verified end-to-end): https://pypi.org/project/attapy/
- Nuitka compiler: https://nuitka.net/user-documentation/user-manual.html

## نکات توسعه آینده

`relayforge/database.py` ذخیره‌سازی و State؛ `relayforge/source.py` گردآوری Bot/User؛ `relayforge/gateways.py` اتصال‌دهنده هر پیام‌رسان؛ `relayforge/engine.py` صف/worker مستقل؛ `main.py` FastAPI/احراز هویت/مسیرهای پنل؛ `web/` رابط کاربری مستقل. برای اضافه‌کردن سرویس ششم، نام آن را در PLATFORMS ثبت و Adapter و تست مستقل برای API آن اضافه کنید. برای چند کانال مادر همزمان، در نسخه بعد Schema باید به source-specific workspace گسترش یابد؛ این نسخه یک کانال مادر با پنج مقصد دارد.
