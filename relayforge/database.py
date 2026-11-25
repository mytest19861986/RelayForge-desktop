"""Durable SQLite state with per-destination outboxes and write-ahead delivery markers."""
from __future__ import annotations
import json, os, secrets, sqlite3, threading, time, re, unicodedata, shutil, logging
from contextlib import contextmanager
from pathlib import Path
from cryptography.fernet import Fernet

PLATFORMS = ("telegram", "bale", "rubika", "eitaa", "soroush")

def now(): return time.time()

def provider(route):
    return route.split(":",1)[0]

def normalized(text):
    return unicodedata.normalize("NFKC", text).translate(str.maketrans("يكى", "یکی")).casefold().replace("\u200c", " ")

def matches_filter(body, src):
    if not src.get("filter_enabled"): return True
    text=normalized(body)
    tags=set(re.findall(r"(?<!\w)#[\w]+", normalized(body)))
    words=[normalized(x.strip()) for x in src.get("keywords",[]) if x.strip()]
    wanted=["#"+normalized(x.strip().lstrip("#")) for x in src.get("hashtags",[]) if x.strip()]
    excluded=[normalized(x.strip()) for x in src.get("exclude_words",[]) if x.strip()]
    if any(x in text for x in excluded): return False
    checks=[x in text for x in words]+[x in tags for x in wanted]
    return (all(checks) if src.get("filter_match")=="all" else any(checks)) if checks else True


DEFAULT_SOURCE = {
    "mode": "bot", "channel": "", "bot_token": "", "api_id": "", "api_hash": "",
    "phone": "", "start_id": 1, "end_id": None, "history_enabled": False,
    "realtime_enabled": True, "poll_seconds": 45, "max_media_mb": 20,
    "filter_enabled":False,"keywords":[],"hashtags":[],"exclude_words":[],"filter_match":"any",
    "prefix":"","suffix":"",
}

class Database:
    def __init__(self, path: Path):
        self.root = Path(path)
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "logs").mkdir(exist_ok=True)
        (self.root / "media").mkdir(exist_ok=True)
        self._lock = threading.RLock()
        keyfile = self.root / "secrets.key"
        if not keyfile.exists():
            with keyfile.open("xb") as f: f.write(Fernet.generate_key())
            if os.name != "nt": keyfile.chmod(0o600)
        self.cipher = Fernet(keyfile.read_bytes())
        self.db_path = self.root / "relayforge.sqlite3"
        self._init()
        self.recover()

    @contextmanager
    def connection(self):
        with self._lock:
            con = sqlite3.connect(self.db_path, timeout=20)
            con.row_factory = sqlite3.Row
            con.execute("PRAGMA foreign_keys=ON")
            con.execute("PRAGMA busy_timeout=20000")
            con.execute("PRAGMA journal_mode=WAL")
            try:
                yield con
                con.commit()
            except Exception:
                con.rollback()
                raise
            finally:
                con.close()

    def _init(self):
        with self.connection() as c:
            c.executescript('''
                CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS targets (
                    platform TEXT PRIMARY KEY,
                    token TEXT NOT NULL DEFAULT '', chat_id TEXT NOT NULL DEFAULT '',
                    enabled INTEGER NOT NULL DEFAULT 0, interval_seconds INTEGER NOT NULL DEFAULT 0,
                    next_allowed REAL NOT NULL DEFAULT 0, updated REAL NOT NULL DEFAULT 0,
                    last_error TEXT NOT NULL DEFAULT '', fail_streak INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS source_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    epoch INTEGER NOT NULL, msg_id INTEGER NOT NULL,
                    body TEXT NOT NULL DEFAULT '', media_kind TEXT NOT NULL DEFAULT 'text',
                    media_ref TEXT NOT NULL DEFAULT '', media_path TEXT NOT NULL DEFAULT '',
                    media_name TEXT NOT NULL DEFAULT '', protected INTEGER NOT NULL DEFAULT 0,
                    live INTEGER NOT NULL DEFAULT 0,
                    received_at REAL NOT NULL, UNIQUE(epoch,msg_id)
                );
                CREATE TABLE IF NOT EXISTS outbox (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    message_id INTEGER NOT NULL REFERENCES source_messages(id),
                    platform TEXT NOT NULL REFERENCES targets(platform),
                    status TEXT NOT NULL DEFAULT 'queued', remote_id TEXT NOT NULL DEFAULT '',
                    detail TEXT NOT NULL DEFAULT '', tries INTEGER NOT NULL DEFAULT 0,
                    updated REAL NOT NULL,
                    UNIQUE(message_id,platform)
                );
                CREATE INDEX IF NOT EXISTS idx_outbox_ready ON outbox(platform,status,id);
                CREATE TABLE IF NOT EXISTS activity (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, created REAL NOT NULL,
                    level TEXT NOT NULL, platform TEXT NOT NULL, message TEXT NOT NULL
                );
            ''')
            # Additive schema upgrade for v3 installs created before the
            # per-target circuit breaker; never discard delivery history.
            columns={row["name"] for row in c.execute("PRAGMA table_info(targets)")}
            if "fail_streak" not in columns:
                c.execute("ALTER TABLE targets ADD COLUMN fail_streak INTEGER NOT NULL DEFAULT 0")
            if "archived" not in columns:
                c.execute("ALTER TABLE targets ADD COLUMN archived INTEGER NOT NULL DEFAULT 0")
            c.execute("CREATE INDEX IF NOT EXISTS idx_activity_route ON activity(platform,id)")
            outcols={row["name"] for row in c.execute("PRAGMA table_info(outbox)")}
            if "target_chat" not in outcols: c.execute("ALTER TABLE outbox ADD COLUMN target_chat TEXT NOT NULL DEFAULT ''")
            msgcols={row["name"] for row in c.execute("PRAGMA table_info(source_messages)")}
            if "eligible" not in msgcols: c.execute("ALTER TABLE source_messages ADD COLUMN eligible INTEGER NOT NULL DEFAULT 1")
            for p in PLATFORMS:
                c.execute("INSERT OR IGNORE INTO targets(platform) VALUES (?)", (p,))
            for name, value in (
                ("source", DEFAULT_SOURCE), ("epoch", 1), ("bot_offset", 0), ("history_cursor", 0), ("run_enabled", False), ("live_cursor", None), ("source_peer", None)
            ):
                c.execute("INSERT OR IGNORE INTO settings(key,value) VALUES (?,?)", (name, json.dumps(value, ensure_ascii=False)))

    def secret(self, value: str) -> str:
        return "enc:" + self.cipher.encrypt(value.encode()).decode() if value else ""

    def reveal(self, value: str) -> str:
        if not value: return ""
        if not value.startswith("enc:"):
            raise ValueError("Secret must be encrypted")
        return self.cipher.decrypt(value[4:].encode()).decode()

    def get_setting(self, key):
        with self.connection() as c:
            row = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return json.loads(row["value"]) if row else None

    def set_setting(self, key, value):
        with self.connection() as c:
            c.execute("INSERT INTO settings(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, json.dumps(value, ensure_ascii=False)))

    def get_source(self, public=False):
        src = {**DEFAULT_SOURCE, **self.get_setting("source")}
        for field in ("bot_token", "api_hash", "phone"):
            src[field] = bool(src[field]) if public else self.reveal(src[field])
        src["epoch"] = self.get_setting("epoch")
        return src

    def update_source(self, raw):
        old = self.get_source()
        new = {k: old[k] for k in DEFAULT_SOURCE}
        for k in ("mode", "channel", "api_id"):
            if k in raw: new[k] = str(raw[k]).strip()
        for k in ("bot_token", "api_hash", "phone"):
            if k in raw and str(raw[k]).strip(): new[k] = str(raw[k]).strip()
        for k in ("history_enabled", "realtime_enabled", "filter_enabled"):
            if k in raw: new[k] = bool(raw[k])
        for k in ("start_id", "poll_seconds", "max_media_mb"):
            if k in raw: new[k] = int(raw[k])
        if "end_id" in raw:
            new["end_id"] = int(raw["end_id"]) if str(raw["end_id"]).strip() not in ("", "None") and raw["end_id"] is not None else None
        if new["mode"] not in ("bot", "user"): raise ValueError("روش دریافت باید BotFather یا API ID/Hash باشد")
        if not new["channel"]: raise ValueError("شناسه کانال مبدأ الزامی است")
        if new["mode"] == "bot" and not new["bot_token"]: raise ValueError("توکن BotFather مبدأ لازم است")
        if new["mode"] == "user" and (not new["api_id"].isdigit() or int(new["api_id"]) < 1 or not new["api_hash"]):
            raise ValueError("API ID و API Hash معتبر لازم است")
        for k in ("keywords","hashtags","exclude_words"):
            if k in raw:
                if not isinstance(raw[k],list) or len(raw[k])>100 or any(not isinstance(x,str) or len(x)>200 for x in raw[k]): raise ValueError("فیلتر نامعتبر")
                new[k]=raw[k]
        for k in ("prefix","suffix","filter_match"):
            if k in raw: new[k]=str(raw[k])
        if new["filter_match"] not in ("any","all") or any(len(new[k])>500 for k in ("prefix","suffix")): raise ValueError("تنظیم فیلتر نامعتبر")
        if new["mode"]=="bot" and new["history_enabled"]:
            if new["end_id"] is None: raise ValueError("برای آرشیو BotFather شماره پایان الزامی است")
            if new["filter_enabled"] or new["prefix"] or new["suffix"]: raise ValueError("آرشیو BotFather متن قابل خواندن ندارد؛ برای فیلتر یا ویرایش از API Hash استفاده کنید")
        if new["bot_token"] and not re.fullmatch(r"[A-Za-z0-9:_-]{5,250}",new["bot_token"]): raise ValueError("قالب توکن نامعتبر است")
        if not 1 <= new["start_id"] <= 2_000_000_000: raise ValueError("شناسه شروع نامعتبر است")
        if new["end_id"] is not None and new["end_id"] < new["start_id"]: raise ValueError("شماره پایان باید بزرگ‌تر از شروع باشد")
        if not 10 <= new["poll_seconds"] <= 3600: raise ValueError("فاصله بررسی ۱۰ تا ۳۶۰۰ ثانیه")
        if not 1 <= new["max_media_mb"] <= 100: raise ValueError("حجم رسانه ۱ تا ۱۰۰ مگابایت")
        if new["mode"] == "user" and len(new["api_hash"]) < 16: raise ValueError("API Hash ناقص به نظر می‌رسد")
        identity = any(new[k] != old[k] for k in ("mode", "channel", "bot_token", "api_id", "api_hash", "phone"))
        with self.connection() as c:
            exists = c.execute("SELECT COUNT(*) n FROM source_messages WHERE epoch=?", (old["epoch"],)).fetchone()["n"]
        upgrade = bool(raw.get("upgrade_archive")) and old["mode"] in ("bot","user") and new["mode"]=="user" and old["channel"]==new["channel"] and bool(self.get_setting("source_peer"))
        if identity and exists and not upgrade:
            raise ValueError("مبدأ قبلاً پیام ثبت کرده است؛ برای تغییر حساب یا کانال ابتدا پروژه تازه و پشتیبان مستقل ایجاد کنید")
        enc = dict(new)
        for k in ("bot_token", "api_hash", "phone"): enc[k] = self.secret(new[k])
        self.set_setting("source", enc)
        if not exists:
            self.set_setting("history_cursor", new["start_id"] - 1)
            if identity: self.set_setting("bot_offset",0); self.set_setting("live_cursor",None); self.set_setting("source_peer",None)
        if upgrade:
            self.set_setting("history_cursor",new["start_id"]-1)
            self.set_setting("live_cursor",None)
            self.event("INFO","source","بازخوانی آرشیو با حساب کاربری؛ شناسه واقعی کانال باید با مبدأ قبلی برابر باشد؛ سوابق ارسال حفظ شد")
        self.event("INFO", "source", "تنظیمات مبدأ ذخیره شد")

    def target(self, platform, public=False):
        if provider(platform) not in PLATFORMS: raise ValueError("پیام‌رسان ناشناخته")
        with self.connection() as c:
            row = c.execute("SELECT * FROM targets WHERE platform=?", (platform,)).fetchone()
        if row is None: raise ValueError("مقصد یافت نشد")
        d = dict(row)
        d["provider"]=provider(platform)
        if public:
            with self.connection() as c:
                d["counts"]={r["status"]:r["n"] for r in c.execute("SELECT status,COUNT(*) n FROM outbox WHERE platform=? GROUP BY status",(platform,))}
                d["sent_count"]=d["counts"].get("sent",0)
                d["archive_refs"]=c.execute("SELECT COUNT(*) FROM source_messages WHERE epoch=? AND media_kind='copy'",(self.get_setting("epoch"),)).fetchone()[0] if d["provider"]!="telegram" else 0
        d["token"] = bool(d["token"]) if public else self.reveal(d["token"])
        return d

    def targets(self, public=True):
        with self.connection() as c: ids=[r[0] for r in c.execute("SELECT platform FROM targets WHERE archived=0 ORDER BY rowid")]
        return [self.target(p, public) for p in ids]

    def add_target(self, platform):
        if platform not in PLATFORMS: raise ValueError("پیام‌رسان ناشناخته")
        with self.connection() as c:
            if c.execute("SELECT COUNT(*) FROM targets WHERE archived=0").fetchone()[0]>=50: raise ValueError("سقف ۵۰ مقصد")
            route=platform+":"+secrets.token_hex(4)
            c.execute("INSERT INTO targets(platform) VALUES(?)",(route,))
        return route

    def update_target(self, platform, raw):
        old = self.target(platform)
        if old["archived"]: raise ValueError("مقصد بایگانی شده است")
        token = str(raw.get("token", "")).strip() or old["token"]
        chat_id = str(raw.get("chat_id", old["chat_id"])).strip()
        enabled = int(bool(raw.get("enabled", old["enabled"])))
        interval = int(raw.get("interval_seconds", old["interval_seconds"]))
        if interval < 0 or interval > 604800: raise ValueError("فاصله ارسال باید از صفر تا ۷ روز باشد")
        if token and not re.fullmatch(r"[A-Za-z0-9:_-]{5,250}",token): raise ValueError("قالب توکن نامعتبر است")
        if enabled and (not token or not chat_id): raise ValueError("قبل از فعال‌سازی، توکن و شناسه کانال مقصد را وارد کنید")
        with self.connection() as c:
            epoch=self.get_setting("epoch")
            existing = c.execute("SELECT COUNT(*) n FROM outbox o JOIN source_messages m ON m.id=o.message_id WHERE o.platform=? AND m.epoch=?", (platform,epoch)).fetchone()["n"]
            inflight=c.execute("SELECT COUNT(*) FROM outbox WHERE platform=? AND status='sending'",(platform,)).fetchone()[0]
            if inflight and (token!=old["token"] or chat_id!=old["chat_id"]): raise ValueError("ارسال در جریان است؛ ابتدا توقف")
            if existing and chat_id != old["chat_id"]:
                raise ValueError("برای تغییر کانال مقصد دارای صف، مقصد جدید بسازید یا عملیات را بازنشانی کنید؛ توکن همان مقصد قابل تغییر است")
            if enabled and provider(platform)=="telegram":
                src=self.get_source()
                if normalized(chat_id)==normalized(src["channel"]): raise ValueError("مبدأ و مقصد تلگرام نباید یکسان باشند")
                if c.execute("SELECT 1 FROM targets WHERE platform<>? AND (platform='telegram' OR platform LIKE 'telegram:%') AND enabled=1 AND lower(chat_id)=lower(?)",(platform,chat_id)).fetchone(): raise ValueError("کانال مقصد تلگرام تکراری است")
            if enabled and c.execute("SELECT 1 FROM targets WHERE platform<>? AND archived=0 AND enabled=1 AND (platform=? OR platform LIKE ?) AND lower(chat_id)=lower(?)",(platform,provider(platform),provider(platform)+":%",chat_id)).fetchone(): raise ValueError("کانال مقصد تکراری است")
            c.execute("UPDATE targets SET token=?,chat_id=?,enabled=?,interval_seconds=?,updated=?,fail_streak=?,next_allowed=CASE WHEN ?=1 THEN 0 ELSE next_allowed END WHERE platform=?",
                      (self.secret(token), chat_id, enabled, interval, now(), 0 if enabled and (not old["enabled"] or token!=old["token"]) else old["fail_streak"], int(enabled and (not old["enabled"] or token!=old["token"])),platform))
            if enabled and raw.get("include_backlog", False):
                c.execute("""INSERT OR IGNORE INTO outbox(message_id,platform,status,updated,target_chat)
                    SELECT m.id, ?, 'queued', ?, ? FROM source_messages m WHERE m.protected=0 AND m.eligible=1 AND m.epoch=? AND (m.media_kind<>'copy' OR ?='telegram')
                    AND NOT EXISTS (SELECT 1 FROM outbox o JOIN targets t ON t.platform=o.platform WHERE o.message_id=m.id AND o.status IN ('sent','uncertain','sending') AND (t.platform=? OR t.platform LIKE ?) AND lower(COALESCE(NULLIF(o.target_chat,''),t.chat_id))=lower(?))""", (platform, now(),chat_id,epoch,provider(platform),provider(platform),provider(platform)+":%",chat_id))
        self.event("INFO", platform, "مقصد %s شد" % ("فعال" if enabled else "غیرفعال"))
        if enabled and provider(platform)!="telegram" and self.get_source()["mode"]=="bot" and self.get_source()["history_enabled"]:
            self.event("WARN",platform,"آرشیو BotFather فقط به تلگرام می‌رود؛ برای آرشیو این مقصد روش API ID/Hash را انتخاب کنید")
        if old["chat_id"] and old["chat_id"]!=chat_id: self.event("WARN",platform,"شناسه کانال مقصد عوض شد؛ سوابق قبلی متعلق به "+old["chat_id"][:150])

    def ingest(self, msg_id, body="", media_kind="text", media_ref="", media_path="", media_name="", protected=False, epoch=None, live=False):
        epoch = epoch if epoch is not None else self.get_setting("epoch")
        src = self.get_source()
        if not live and (msg_id < src["start_id"] or (src["end_id"] is not None and msg_id > src["end_id"])):
            return False
        if epoch != src["epoch"]: return False
        if live and not src["realtime_enabled"]: return False
        eligible=matches_filter(body,src) if media_kind!="copy" else not src["filter_enabled"]
        if eligible and media_kind!="copy": body=(src["prefix"]+body+src["suffix"])
        with self.connection() as c:
            cur = c.execute("INSERT OR IGNORE INTO source_messages(epoch,msg_id,body,media_kind,media_ref,media_path,media_name,protected,live,received_at,eligible) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                            (epoch,int(msg_id),body[:15000],media_kind,media_ref,media_path,media_name,int(protected),int(live),now(),int(eligible)))
            if not cur.rowcount:
                old=c.execute("SELECT id,media_kind FROM source_messages WHERE epoch=? AND msg_id=?",(epoch,msg_id)).fetchone()
                if old["media_kind"]!="copy" or media_kind=="copy": return False
                mid=old["id"]
                c.execute("UPDATE source_messages SET body=?,media_kind=?,media_ref=?,media_path=?,media_name=?,protected=?,live=?,eligible=? WHERE id=?",(body[:15000],media_kind,media_ref,media_path,media_name,int(protected),int(live),int(eligible),mid))
                if protected or not eligible: c.execute("UPDATE outbox SET status='skipped',detail='محتوا محافظت‌شده یا فیلتر شده' WHERE message_id=? AND status='queued'",(mid,))
            else: mid=cur.lastrowid
            if not protected and eligible:
                c.execute("""INSERT OR IGNORE INTO outbox(message_id,platform,status,updated,target_chat) SELECT ?, platform, 'queued', ?,chat_id FROM targets WHERE enabled=1 AND archived=0 AND (?<>'copy' OR platform='telegram' OR platform LIKE 'telegram:%')""", (mid, now(),media_kind))
        self.event("WARN" if protected or not eligible else "INFO", "source", f"پست {msg_id} " + ("محافظت‌شده/غیرقابل‌انتشار" if protected else "فیلتر شد" if not eligible else "ثبت شد"))
        for target in self.targets():
            if target["enabled"]:
                reason="محتوا محافظت‌شده/غیرقابل‌انتشار" if protected else "عدم انطباق با فیلتر" if not eligible else "آرشیو BotFather محتوا ندارد؛ API ID/Hash لازم است" if media_kind=="copy" and target["provider"]!="telegram" else "وارد صف شد یا قبلاً ثبت شده"
                self.event("WARN" if protected or not eligible or (media_kind=="copy" and target["provider"]!="telegram") else "INFO",target["platform"],f"مبدأ #{msg_id}: {reason}")
        return True

    def set_media_path(self, message_id, relative):
        with self.connection() as c:
            c.execute("UPDATE source_messages SET media_path=? WHERE id=?", (str(relative), message_id))

    def media_path_for(self, message_id):
        with self.connection() as c:
            result=c.execute("SELECT media_path FROM source_messages WHERE id=?", (message_id,)).fetchone()
        return result["media_path"] if result else ""

    def next_delivery(self, platform):
        with self.connection() as c:
            row = c.execute('''SELECT o.*,m.msg_id,m.body,m.media_kind,m.media_ref,m.media_path,m.media_name,m.protected,m.live,
                      t.next_allowed,t.interval_seconds,t.token,t.chat_id,t.enabled
                      FROM outbox o JOIN source_messages m ON m.id=o.message_id
                      JOIN targets t ON t.platform=o.platform
                      WHERE o.platform=? AND o.status='queued' AND t.enabled=1 AND t.archived=0 AND m.epoch=?
                      ORDER BY (CASE WHEN t.interval_seconds=0 AND m.live=1 THEN 0 ELSE 1 END),m.msg_id ASC LIMIT 1''', (platform,self.get_setting("epoch"))).fetchone()
            if not row: return None
            d=dict(row)
            d["token"] = self.reveal(d["token"])
            if d["next_allowed"] > now(): return None
            if c.execute("SELECT 1 FROM outbox WHERE platform=? AND status IN ('sending','uncertain')",(platform,)).fetchone(): return None
            c.execute("UPDATE outbox SET status='sending',tries=tries+1,updated=? WHERE id=? AND status='queued'", (now(), d["id"]))
            return d

    def finish_delivery(self, did, platform, status, remote_id="", detail="", interval=0):
        if status not in ("sent", "failed", "uncertain", "skipped"):
            raise ValueError("وضعیت نامعتبر")
        with self.connection() as c:
            c.execute("UPDATE outbox SET status=?,remote_id=?,detail=?,updated=? WHERE id=? AND platform=?", (status, remote_id, detail[:500], now(), did, platform))
            previous=c.execute("SELECT fail_streak FROM targets WHERE platform=?",(platform,)).fetchone()["fail_streak"]
            streak=0 if status=='sent' else previous+1 if status in ('failed','uncertain') else previous
            disabled=int(streak>=3 and status in ('failed','uncertain'))
            c.execute("UPDATE targets SET next_allowed=?,last_error=?,fail_streak=?,enabled=CASE WHEN ?=1 THEN 0 ELSE enabled END WHERE platform=?",
                      (now()+max(0,interval), detail[:500] if status!='sent' else '', streak, disabled, platform))
        self.event("INFO" if status=="sent" else "ERROR", platform, f"ارسال #{did}: {status} | شناسه مقصد: {remote_id or '—'} | {detail or 'ok'}")
        if disabled:self.event("WARN",platform,"پس از سه خطای متوالی، فقط همین مقصد خودکار غیرفعال شد؛ بقیه فعال می‌مانند")

    def retry_or_resolve(self, did, action):
        with self.connection() as c:
            row = c.execute("SELECT o.status,o.platform,m.epoch,t.archived FROM outbox o JOIN source_messages m ON m.id=o.message_id JOIN targets t ON t.platform=o.platform WHERE o.id=?", (did,)).fetchone()
            if not row: raise ValueError("ارسال یافت نشد")
            if row["archived"]: raise ValueError("مقصد بایگانی شده؛ تاریخچه فقط خواندنی است")
            if row["epoch"] != self.get_setting("epoch"): raise ValueError("ارسال متعلق به عملیات بایگانی شده است")
            if row["status"] not in ("failed", "uncertain"): raise ValueError("فقط خطاهای ثبت‌شده قابل تعیین تکلیف‌اند")
            if action == "retry": status="queued"
            elif action == "mark_sent": status="sent"
            elif action == "skip": status="skipped"
            else: raise ValueError("عملیات نامعتبر")
            c.execute("UPDATE outbox SET status=?,detail=?,updated=? WHERE id=?", (status, "تعیین تکلیف دستی: "+action, now(), did))
        self.event("WARN", row["platform"], f"ارسال شماره {did} به‌صورت دستی {action} شد")

    def recover(self):
        with self.connection() as c:
            n = c.execute("UPDATE outbox SET status='uncertain', detail='برنامه در هنگام ارسال متوقف شد؛ وضعیت واقعی بررسی شود', updated=? WHERE status='sending'", (now(),)).rowcount
        if n: self.event("WARN", "system", f"{n} ارسال نامطمئن پس از راه‌اندازی مجدد شناسایی شد")

    def event(self, level, platform, message):
        # Never pass raw HTTP bodies or credentials. Gateway errors are sanitized.
        message=re.sub(r"https?://[^\s]+","[URL]",str(message))
        message=re.sub(r"\b\d{5,}:[A-Za-z0-9_-]{5,}","[TOKEN]",message)[:1200]
        with self.connection() as c:
            c.execute("INSERT INTO activity(created,level,platform,message) VALUES (?,?,?,?)", (now(), level, platform, message))
        logging.getLogger("relayforge.activity").log(getattr(logging,level if level!="WARN" else "WARNING",logging.INFO),"[%s] %s",platform,message)

    def backup(self):
        dest=self.root/"backups"/(time.strftime("%Y%m%d-%H%M%S")+"-"+secrets.token_hex(3))
        dest.mkdir(parents=True,exist_ok=False)
        with self.connection() as c:
            with sqlite3.connect(dest/"relayforge.sqlite3") as target: c.backup(target)
        shutil.copy2(self.root/"secrets.key",dest/"secrets.key")
        if os.name!="nt": dest.chmod(0o700)
        self.event("INFO","system","پشتیبان پایگاه داده و کلید ساخته شد؛ رسانه و session جداگانه نگهداری شوند")
        return str(dest)

    def reset_operation(self):
        # Preserve old delivery records; do not delete the database or encryption key.
        epoch=self.get_setting("epoch")
        with self.connection() as c:
            if c.execute("SELECT 1 FROM outbox WHERE status IN ('sending','uncertain')").fetchone(): raise ValueError("ارسال نامطمئن را ابتدا تعیین تکلیف کنید")
            c.execute("UPDATE outbox SET status='skipped',detail='عملیات بایگانی شد' WHERE status='queued'")
            c.execute("UPDATE targets SET enabled=0,next_allowed=0,fail_streak=0,last_error=''")
            for k,v in (("epoch",epoch+1),("bot_offset",0),("history_cursor",self.get_source()["start_id"]-1),("live_cursor",None),("source_peer",None),("run_enabled",False)):
                c.execute("UPDATE settings SET value=? WHERE key=?",(json.dumps(v),k))
        self.event("WARN","system","عملیات جدید؛ سابقه قبلی حفظ شد و مقصدها غیرفعال شدند")

    def snapshot(self):
        with self.connection() as c:
            stats = {r["status"]:r["n"] for r in c.execute("SELECT status,COUNT(*) n FROM outbox GROUP BY status")}
            total = c.execute("SELECT COUNT(*) n FROM source_messages").fetchone()["n"]
            recent = [dict(r) for r in c.execute('''SELECT o.id,o.platform,o.status,o.remote_id,o.detail,o.updated,m.msg_id
                FROM outbox o JOIN source_messages m ON m.id=o.message_id ORDER BY o.updated DESC LIMIT 35''')]
            events = [dict(r) for r in c.execute("SELECT created,level,platform,message FROM activity ORDER BY id DESC LIMIT 45")]
            return {"source":self.get_source(public=True),"targets":self.targets(public=True),"stats":stats,"total_posts":total,
                    "recent":recent,"events":events,"cursor":self.get_setting("history_cursor"),"server_time":now()}

    def defer_delivery(self, did, platform, delay, reason):
        with self.connection() as c:
            c.execute("UPDATE outbox SET status='queued',detail=?,updated=? WHERE id=? AND platform=?", (reason[:500], now(), did, platform))
            c.execute("UPDATE targets SET next_allowed=?,last_error=? WHERE platform=?", (now()+delay, reason[:500], platform))
        self.event("WARN", platform, f"ارسال #{did}: {reason}؛ تلاش مجدد پس از {delay} ثانیه")

    def remove_targets(self, routes):
        routes=list(dict.fromkeys(routes))
        if not routes or len(routes)>50: raise ValueError("حداقل یک مقصد انتخاب کنید")
        with self.connection() as c:
            for route in routes:
                t=c.execute("SELECT archived FROM targets WHERE platform=?",(route,)).fetchone()
                if not t or t["archived"]: raise ValueError("مقصد فعال یافت نشد")
                if c.execute("SELECT 1 FROM outbox WHERE platform=? AND status IN ('sending','uncertain')",(route,)).fetchone(): raise ValueError("ابتدا ارسال نامطمئن یا در جریان این مقصد را تعیین تکلیف کنید")
            for route in routes:
                c.execute("UPDATE outbox SET status='skipped',detail='مقصد بایگانی شد',updated=? WHERE platform=? AND status='queued'",(now(),route))
                c.execute("UPDATE targets SET archived=1,enabled=0,token='',updated=? WHERE platform=?",(now(),route))
        for route in routes: self.event("WARN",route,"مقصد حذف شد (بایگانی)؛ تاریخچه حفظ شد")
        return len(routes)

    def clear_queue(self, route):
        with self.connection() as c:
            t=c.execute("SELECT archived FROM targets WHERE platform=?",(route,)).fetchone()
            if not t or t["archived"]: raise ValueError("مقصد یافت نشد")
            n=c.execute("UPDATE outbox SET status='skipped',detail='صف با تأیید کاربر پاک شد',updated=? WHERE platform=? AND status='queued'",(now(),route)).rowcount
        self.event("WARN",route,f"{n} پیام صف به صورت ردشده ثبت شد؛ سوابق حفظ شد")
        return n

    def pause_target(self, route, enabled):
        t=self.target(route)
        if t["archived"] or (enabled and (not t["token"] or not t["chat_id"])): raise ValueError("مقصد قابل فعال‌سازی نیست")
        with self.connection() as c:
            c.execute("UPDATE targets SET enabled=?,fail_streak=0 WHERE platform=?",(int(enabled),route))
        self.event("INFO",route,"مقصد از سر گرفته شد" if enabled else "مقصد متوقف شد؛ ارسال در جریان ممکن است تکمیل شود")

    def report_routes(self):
        with self.connection() as c:
            return [dict(r) for r in c.execute("SELECT platform,chat_id,archived FROM targets ORDER BY rowid")]

    def report(self, kind, route="", query="", limit=100, offset=0):
        if kind=="events":
            sql="SELECT a.id,a.created,a.level,a.platform,a.message,t.chat_id FROM activity a LEFT JOIN targets t ON a.platform=t.platform"
            where=" WHERE 1=1"; args=[]
            if route: where+=" AND a.platform=?";args.append(route)
            if query: where+=" AND (instr(lower(a.message),lower(?))>0 OR instr(lower(a.level),lower(?))>0 OR instr(lower(coalesce(t.chat_id,'')),lower(?))>0)";args += [query]*3
            order=" ORDER BY a.id DESC"
        elif kind=="deliveries":
            sql="SELECT o.id,o.platform,o.status,o.remote_id,o.detail,o.updated,o.tries,m.msg_id,COALESCE(NULLIF(o.target_chat,''),t.chat_id) chat_id,t.archived FROM outbox o JOIN source_messages m ON m.id=o.message_id JOIN targets t ON t.platform=o.platform"
            where=" WHERE 1=1";args=[]
            if route: where+=" AND o.platform=?";args.append(route)
            if query: where+=" AND (instr(lower(o.detail),lower(?))>0 OR instr(lower(o.status),lower(?))>0 OR instr(CAST(m.msg_id AS TEXT),?)>0 OR instr(lower(COALESCE(NULLIF(o.target_chat,''),t.chat_id)),lower(?))>0)";args += [query]*4
            order=" ORDER BY o.updated DESC,o.id DESC"
        else: raise ValueError("نوع گزارش نامعتبر")
        with self.connection() as c:
            total=c.execute("SELECT COUNT(*) FROM ("+sql+where+")",args).fetchone()[0]
            rows=[dict(r) for r in c.execute(sql+where+order+" LIMIT ? OFFSET ?",args+[limit,offset])]
        return {"total":total,"rows":rows}
