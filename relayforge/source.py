"""Telegram source ingestion: BotFather live channel_post or Telethon account archive+live.
No scraping, bypass of protected content, or automatic OTP verification.
"""
from __future__ import annotations
import asyncio, logging, mimetypes, os, re
from pathlib import Path
from urllib.parse import quote
import httpx
from .database import Database

log = logging.getLogger("relayforge.source")

class SourceUnavailable(Exception): pass


def matches_channel(config: str, chat: dict) -> bool:
    ref = (config or "").strip().lower().rstrip("/")
    if ref.startswith("https://t.me/"):
        ref = "@" + ref.split("https://t.me/",1)[1].split("/",1)[0]
    chat_id = str(chat.get("id", ""))
    username = "@"+str(chat.get("username", "")).lower()
    return bool(ref) and (ref == chat_id or (ref.startswith("@") and ref == username))


def bot_message_payload(item: dict) -> dict:
    kind, ref, name = "text", "", ""
    if item.get("photo"):
        kind,ref = "photo", item["photo"][-1]["file_id"]
        name = "photo.jpg"
    else:
        for field in ("video","animation","audio","voice","document"):
            obj = item.get(field)
            if isinstance(obj,dict) and obj.get("file_id"):
                kind,ref = field,obj["file_id"]
                name = obj.get("file_name","") or field+{"video":".mp4","animation":".gif","audio":".mp3","voice":".ogg"}.get(field,".bin")
                break
    return {"msg_id":int(item["message_id"]),"body":item.get("text") or item.get("caption") or "",
            "media_kind":kind,"media_ref":ref,"media_name":name,
            "protected":bool(item.get("has_protected_content"))}

class Collector:
    def __init__(self, db:Database):
        self.db=db
        self.user_client=None
        self._user_signature=None
        self._entity=None
        self._handler=None
        self._auth_lock=asyncio.Lock()
        self._media_lock=asyncio.Lock()
        self._login_hash=""
        self._login_phone=""
        self.state="starting"

    async def shutdown(self):
        if self.user_client:
            try: await self.user_client.disconnect()
            finally: self.user_client=None

    async def _client(self):
        config=self.db.get_source()
        if config["mode"]!="user" or not config["api_id"] or not config["api_hash"]:
            raise SourceUnavailable("برای API ID/Hash ابتدا روش کاربری را تنظیم کنید")
        signature=(config["api_id"],config["api_hash"])
        if self.user_client and self._user_signature!=signature:
            await self.shutdown()
            self._entity=None
        if not self.user_client:
            try:
                from telethon import TelegramClient
            except ImportError as ex:
                raise SourceUnavailable("بسته telethon نصب نیست") from ex
            self.user_client = TelegramClient(str(self.db.root/"telegram_user"),int(config["api_id"]),config["api_hash"],
                                                auto_reconnect=True,connection_retries=3,request_retries=2)
            self._user_signature=signature
        if not self.user_client.is_connected(): await self.user_client.connect()
        return self.user_client

    async def authorized(self):
        if self.db.get_source()["mode"]!="user": return False
        async with self._auth_lock:
            client=await self._client()
            return bool(await client.is_user_authorized())

    async def request_code(self):
        async with self._auth_lock:
            client=await self._client()
            phone=self.db.get_source()["phone"]
            if not phone: raise SourceUnavailable("شماره تلفن با کد کشور وارد نشده است")
            if await client.is_user_authorized(): return "حساب از قبل وارد شده است"
            result=await client.send_code_request(phone)
            self._login_hash=result.phone_code_hash
            self._login_phone=phone
            return "کد ورود از طریق تلگرام درخواست شد"

    async def confirm_code(self,code,password):
        async with self._auth_lock:
            client=await self._client()
            from telethon.errors import SessionPasswordNeededError
            if await client.is_user_authorized(): return "حساب از قبل تأیید شده است"
            if not self._login_phone: raise SourceUnavailable("ابتدا درخواست دریافت کد را ثبت کنید")
            try:
                if code:
                    await client.sign_in(phone=self._login_phone,code=code,phone_code_hash=self._login_hash)
                if not await client.is_user_authorized() and password:
                    await client.sign_in(password=password)
            except SessionPasswordNeededError:
                if not password: raise SourceUnavailable("رمز دومرحله‌ای تلگرام لازم است") from None
                await client.sign_in(password=password)
            if not await client.is_user_authorized(): raise SourceUnavailable("ورود کامل نشد")
            self._login_hash=""
            self.db.event("INFO","source","ورود مجاز حساب تلگرام تأیید شد")
            return "ورود به حساب تلگرام موفق بود"

    async def check_source(self):
        cfg=self.db.get_source()
        if cfg["mode"] == "user":
            client=await self._client()
            if not await client.is_user_authorized():
                raise SourceUnavailable("حساب هنوز وارد نشده است")
            try:
                entity=await client.get_entity(cfg["channel"])
            except Exception:
                raise SourceUnavailable("کانال با حساب فعلی در دسترس نیست") from None
            return {"ok":True,"method":"API ID/Hash","channel":getattr(entity,"title",cfg["channel"]),"note":"حساب مجاز و کانال قابل خواندن است"}
        token=cfg["bot_token"]
        if not token:raise SourceUnavailable("توکن BotFather وارد نشده است")
        async with httpx.AsyncClient(timeout=20,trust_env=True) as client:
            try:
                me=await client.post(f"https://api.telegram.org/bot{token}/getMe")
                meobj=me.json()
                if meobj.get("ok") is not True:raise SourceUnavailable("توکن بات‌فادر معتبر نیست")
                chat=await client.post(f"https://api.telegram.org/bot{token}/getChat",json={"chat_id":cfg["channel"]})
                info=chat.json()
                if info.get("ok") is not True:raise SourceUnavailable("ربات کانال مبدأ را پیدا نمی‌کند")
                member=await client.post(f"https://api.telegram.org/bot{token}/getChatMember",json={"chat_id":cfg["channel"],"user_id":meobj["result"]["id"]})
                mobj=member.json()
                role=(mobj.get("result") or {}).get("status","")
            except (httpx.TransportError,httpx.TimeoutException,ValueError):
                raise SourceUnavailable("اتصال به API تلگرام موقتاً در دسترس نیست") from None
        if role not in ("administrator","creator"):
            raise SourceUnavailable("ربات در کانال مبدأ ادمین نیست؛ برای دریافت channel_post ادمین کنید")
        return {"ok":True,"method":"BotFather","channel":(info.get("result") or {}).get("title",cfg["channel"]),
                "note":"ربات به کانال دسترسی ادمین دارد؛ دریافت واقعی پست جدید پس از انتشار آزمایشی بررسی شود"}

    async def tick(self):
        cfg=self.db.get_source()
        if not cfg["channel"]: self.state="needs_config"; await asyncio.sleep(3); return
        if cfg["mode"] == "bot":
            await self._bot_tick(cfg)
        else:
            await self._user_tick(cfg)

    async def _bot_tick(self,cfg):
        if not cfg["realtime_enabled"]: self.state="paused"; await asyncio.sleep(3); return
        self.state="listening_bot"
        token=cfg["bot_token"]
        offset=self.db.get_setting("bot_offset") or 0
        payload={"offset":offset+1,"timeout":18,"allowed_updates":["channel_post"],"limit":100}
        try:
            async with httpx.AsyncClient(timeout=30,trust_env=True) as client:
                res=await client.post(f"https://api.telegram.org/bot{token}/getUpdates",json=payload)
                result=res.json()
        except (httpx.TransportError,ValueError,KeyError):
            self.state="network_retry"; await asyncio.sleep(8); return
        if res.status_code != 200 or result.get("ok") is not True:
            self.state="api_error"
            self.db.event("WARN","source",f"خطای دریافت بات HTTP {res.status_code} یا مصرف‌کننده/Webhook دیگر")
            await asyncio.sleep(12); return
        for update in result.get("result",[]):
            uid=int(update["update_id"])
            post=update.get("channel_post")
            if post and matches_channel(cfg["channel"],post.get("chat") or {}):
                item=bot_message_payload(post)
                self.db.ingest(**item,live=True)
            # Commit offset only after durable ingestion; duplicate replay is idempotent.
            self.db.set_setting("bot_offset",uid)

    async def _user_tick(self,cfg):
        self.state="user_connecting"
        try: client=await self._client()
        except (SourceUnavailable,Exception) as ex:
            self.state="login_required"; await asyncio.sleep(8); return
        if not await client.is_user_authorized():
            self.state="login_required"; await asyncio.sleep(5); return
        if self._entity is None or getattr(self,"_entity_ref",None) != cfg["channel"]:
            try:
                entity=await client.get_entity(cfg["channel"])
                self._entity=entity
                self._entity_ref=cfg["channel"]
                if self._handler is not None:
                    client.remove_event_handler(self._handler)
                from telethon import events, utils
                expected=utils.get_peer_id(entity)
                async def on_new(event):
                    current=self.db.get_source()
                    if current["mode"]!="user" or not current["realtime_enabled"]:return
                    if event.chat_id != expected: return
                    try: await self._ingest_user(event.message,current,live=True)
                    except Exception:
                        log.exception("Failed to ingest Telegram event")
                        self.db.event("WARN","source","رویداد لحظه‌ای قابل ذخیره نبود؛ پیمایش دوره‌ای دوباره بررسی می‌کند")
                self._handler=on_new
                client.add_event_handler(on_new,events.NewMessage(chats=entity))
            except Exception:
                self.state="channel_error"
                self.db.event("WARN","source","کانال مبدأ با حساب فعلی قابل خواندن نیست")
                await asyncio.sleep(20); return
        self.state="listening_user"
        if cfg["history_enabled"]:
            cursor=int(self.db.get_setting("history_cursor") or (cfg["start_id"]-1))
            cursor=max(cursor,cfg["start_id"]-1)
            count=0
            try:
                async for msg in client.iter_messages(self._entity,min_id=cursor,reverse=True,limit=80):
                    if cfg["end_id"] is not None and msg.id > cfg["end_id"]:
                        self.db.set_setting("history_cursor",cfg["end_id"]); break
                    await self._ingest_user(msg,cfg)
                    self.db.set_setting("history_cursor",msg.id)
                    count+=1
            except Exception:
                self.state="history_retry"
                self.db.event("WARN","source","پیمایش آرشیو موقتاً ناموفق بود و مجدداً تلاش می‌شود")
            # Keep user client running and receiving NewMessage events between scans.
            await asyncio.sleep(1 if count == 80 else cfg["poll_seconds"])
        else:
            await asyncio.sleep(cfg["poll_seconds"])

    async def _ingest_user(self,msg,cfg,live=False):
        if not msg or not getattr(msg,"id",None): return
        if msg.id < cfg["start_id"] or (cfg["end_id"] is not None and msg.id>cfg["end_id"]): return
        protected=bool(getattr(msg,"noforwards",False))
        body=(getattr(msg,"message","") or "")
        kind="text";media="";name=""
        if getattr(msg,"media",None):
            if getattr(msg,"photo",None): kind="photo"
            elif getattr(msg,"video",None): kind="video"
            elif getattr(msg,"voice",None): kind="voice"
            elif getattr(msg,"audio",None): kind="audio"
            else: kind="document"
            mf=getattr(msg,"file",None)
            name=getattr(mf,"name",None) or f"{kind}_{msg.id}"
            size=int(getattr(mf,"size",0) or 0)
            if size and size>cfg["max_media_mb"]*1024*1024:
                protected=True
                self.db.event("WARN","source",f"رسانه پیام {msg.id} بزرگ‌تر از حد مجاز است")
            elif not protected:
                # Cache authorized media before acknowledging archive progress.
                filename=self.db.root/"media"/f"user_{msg.id}"
                actual=await msg.download_media(file=str(filename))
                if not actual:
                    raise SourceUnavailable("فایل رسانه دریافت نشد")
                actual_path=Path(actual).resolve()
                if actual_path.stat().st_size>cfg["max_media_mb"]*1024*1024:
                    actual_path.unlink(missing_ok=True)
                    raise SourceUnavailable("حجم واقعی فایل بیش از حد مجاز است")
                media=str(actual_path.relative_to(self.db.root.resolve()))
        self.db.ingest(msg.id,body,kind,"",media,name,protected,live=live)

    async def get_media(self,row):
        if row["media_kind"] == "text": return ""
        if row.get("media_path"):
            path=(self.db.root / row["media_path"]).resolve()
            if not path.is_relative_to(self.db.root.resolve()): raise SourceUnavailable("مسیر رسانه نامعتبر")
            if path.is_file(): return str(path)
        if self.db.get_source()["mode"] != "bot":
            raise SourceUnavailable("رسانه حساب کاربری در کش موجود نیست؛ بررسی دستی لازم است")
        async with self._media_lock:
            # Another destination may already have downloaded this file.
            current=self.db.media_path_for(row["message_id"])
            if current:
                path=(self.db.root/current).resolve()
                if not path.is_relative_to(self.db.root.resolve()):
                    raise SourceUnavailable("مسیر رسانه نامعتبر")
                if path.is_file(): return str(path)
            return await self._bot_file(row)

    async def _bot_file(self,row):
        token=self.db.get_source()["bot_token"]
        fid=row.get("media_ref")
        if not fid: raise SourceUnavailable("file_id رسانه وجود ندارد")
        try:
            async with httpx.AsyncClient(timeout=35,trust_env=True) as client:
                r=await client.post(f"https://api.telegram.org/bot{token}/getFile",json={"file_id":fid})
                obj=r.json()
                if obj.get("ok") is not True: raise SourceUnavailable("دریافت مشخصات فایل توسط تلگرام رد شد")
                meta=obj.get("result") or {}
                sz=int(meta.get("file_size") or 0)
                cap=self.db.get_source()["max_media_mb"]*1024*1024
                if sz and sz>cap: raise SourceUnavailable("فایل بزرگ‌تر از حد تنظیم‌شده است")
                file_path=str(meta.get("file_path", ""))
                if not file_path or '..' in file_path or file_path.startswith('/') or '\\' in file_path:
                    raise SourceUnavailable("آدرس فایل نامعتبر است")
                ext=Path(file_path).suffix
                if not re.fullmatch(r"\.[A-Za-z0-9]{1,8}",ext or ""):
                    ext=Path(row.get("media_name") or "").suffix
                if not re.fullmatch(r"\.[A-Za-z0-9]{1,8}",ext or ""): ext=".bin"
                dest=self.db.root/"media"/(f"bot_{row['msg_id']}_{row['message_id']}"+ext)
                tmp=dest.with_suffix(dest.suffix+".part")
                url=f"https://api.telegram.org/file/bot{token}/"+"/".join(quote(p,safe="") for p in file_path.split('/'))
                n=0
                async with client.stream("GET",url,follow_redirects=False) as download:
                    if download.status_code!=200: raise SourceUnavailable("دریافت رسانه از تلگرام ناموفق بود")
                    with tmp.open("wb") as f:
                        async for piece in download.aiter_bytes(65536):
                            n+=len(piece)
                            if n>cap: raise SourceUnavailable("فایل دانلودی بیش از حد مجاز است")
                            f.write(piece)
                os.replace(tmp,dest)
                relative=str(dest.relative_to(self.db.root))
                self.db.set_media_path(row["message_id"],relative)
                return str(dest)
        except (httpx.TransportError,httpx.TimeoutException):
            raise SourceUnavailable("شبکه در زمان دریافت رسانه قطع شد") from None
        finally:
            if 'tmp' in locals(): tmp.unlink(missing_ok=True)
