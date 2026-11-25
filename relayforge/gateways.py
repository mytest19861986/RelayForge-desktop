"""Provider adapters. One API failure never raises outside its destination worker.
Only documented provider hosts are accepted; no arbitrary target URLs.
"""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit
import mimetypes
import httpx

class Rejected(Exception): pass
class Uncertain(Exception): pass
class NotSupported(Exception): pass

@dataclass(frozen=True)
class Post:
    text: str
    kind: str = "text"
    path: str = ""

BASE = {
    "telegram": "https://api.telegram.org/bot",
    "bale": "https://tapi.bale.ai/bot",
    "eitaa": "https://eitaayar.ir/api/",
    "rubika": "https://botapi.rubika.ir/v3/",
    "soroush": "https://api.splus.ir/bot",  # official-style endpoint, needs live verification
}

METHODS = {
    "photo": ("sendPhoto", "photo"), "video": ("sendVideo", "video"),
    "audio": ("sendAudio", "audio"), "voice": ("sendVoice", "voice"),
    "document": ("sendDocument", "document"), "animation": ("sendAnimation", "animation"),
}

async def _post(client: httpx.AsyncClient, url: str, *, json=None, data=None, files=None):
    try:
        response = await client.post(url, json=json, data=data, files=files, follow_redirects=False, timeout=45)
    except (httpx.TimeoutException, httpx.TransportError) as ex:
        # Do not log URL: token is part of URL. No blind retry after POST.
        raise Uncertain("ارتباط با API مقصد قطع شد؛ رسیدن پیام نامشخص است") from None
    if response.status_code >= 500 or response.status_code in (408, 429):
        raise Uncertain(f"پاسخ سرور مقصد HTTP {response.status_code}؛ وضعیت ارسال نامطمئن")
    if response.status_code >= 300 and response.status_code < 400:
        raise Rejected("انتقال HTTP به آدرس دیگر از API پذیرفته نمی‌شود")
    try: obj = response.json()
    except ValueError: raise Uncertain("پاسخ API مقصد JSON معتبر نیست") from None
    if response.status_code >= 400:
        raise Rejected(f"درخواست از طرف مقصد رد شد (HTTP {response.status_code})")
    if not isinstance(obj, dict): raise Uncertain("شکل پاسخ مقصد قابل تأیید نیست")
    return obj


def _result(platform, obj):
    if platform == "rubika":
        # Rubika Bot API: status=OK; data.message_id, sometimes data is object.
        if str(obj.get("status", "")).upper() not in ("OK", "SUCCESS"):
            if "status" in obj: raise Rejected("API روبیکا درخواست را رد کرد")
            raise Uncertain("روبیکا وضعیت پاسخ مشخصی برنگرداند")
        data = obj.get("data") or {}
    else:
        if obj.get("ok") is False: raise Rejected("API مقصد درخواست را نپذیرفت (ok=false)")
        if obj.get("ok") is not True: raise Uncertain("API مقصد ok=true مشخصی برنگرداند")
        data = obj.get("result") or {}
    if isinstance(data, dict):
        return str(data.get("message_id", data.get("file_id", "")))
    return str(data) if isinstance(data, (int,str)) else ""

async def send(platform: str, token: str, chat_id: str, post: Post):
    if platform not in BASE: raise Rejected("سرویس مقصد ناشناخته")
    if not token or not chat_id: raise Rejected("توکن یا شناسه کانال مقصد وارد نشده")
    if len(post.text) > 4000: raise Rejected("متن بیش از ۴۰۰۰ نویسه است؛ پیام ناقص ارسال نمی‌شود")
    if not post.path and not post.text: raise Rejected("پیام بدون محتواست")
    if post.path and not Path(post.path).is_file(): raise Rejected("رسانه قابل خواندن نیست")
    if post.path and platform in ("telegram", "bale", "soroush") and len(post.text) > 950:
        raise Rejected("کپشن رسانه از حد قابل‌اطمینان فراتر است")
    address = BASE[platform] + token + "/"
    if platform == "eitaa": chat_id = chat_id.lstrip("@")
    async with httpx.AsyncClient(trust_env=True) as client:
        if platform == "rubika":
            return await _send_rubika(client,address,chat_id,post)
        if not post.path:
            data = {"chat_id":chat_id,"text":post.text}
            res = await _post(client,address+"sendMessage",data=data if platform=="eitaa" else None,
                              json=data if platform!="eitaa" else None)
            return _result(platform,res)
        if platform == "eitaa":
            method, field = "sendFile", "file"
        else:
            method, field = METHODS.get(post.kind, METHODS["document"])
        mime = mimetypes.guess_type(post.path)[0] or "application/octet-stream"
        with open(post.path,"rb") as f:
            res=await _post(client,address+method,data={"chat_id":chat_id,"caption":post.text},
                            files={field:(Path(post.path).name,f,mime)})
        return _result(platform,res)

async def _send_rubika(client,address,chat_id,post):
    if not post.path:
        return _result("rubika",await _post(client,address+"sendMessage",json={"chat_id":chat_id,"text":post.text}))
    kind = {"photo":"Image","video":"Video","audio":"Audio","voice":"Voice","animation":"File","document":"File"}.get(post.kind,"File")
    init = await _post(client,address+"requestSendFile",json={"type":kind})
    _result("rubika",init)
    data = init.get("data") if isinstance(init.get("data"),dict) else init
    upload = data.get("upload_url","")
    parsed = urlsplit(upload)
    # Provider-returned upload URL cannot direct our server to local network or another domain.
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not (host == "rubika.ir" or host.endswith(".rubika.ir")) or parsed.username or parsed.password or parsed.port not in (None,443):
        raise Rejected("نشانی آپلود روبیکا در دامنه امن مورد انتظار نیست")
    with open(post.path,"rb") as f:
        uploaded = await _post(client,upload,files={"file":(Path(post.path).name,f,mimetypes.guess_type(post.path)[0] or "application/octet-stream")})
    udata = uploaded.get("data") if isinstance(uploaded.get("data"),dict) else uploaded
    file_id = udata.get("file_id")
    if not file_id: raise Uncertain("روبیکا file_id معتبر پس از آپلود برنگرداند")
    return _result("rubika",await _post(client,address+"sendFile",json={"chat_id":chat_id,"file_id":file_id,"text":post.text}))

async def check_token(platform,token):
    if platform not in BASE: raise Rejected("پیام‌رسان ناشناخته")
    if not token: raise Rejected("توکن وارد نشده")
    async with httpx.AsyncClient(trust_env=True) as client:
        res=await _post(client,BASE[platform]+token+"/getMe",json={} if platform=="rubika" else None)
    _result(platform,res)
    return {"ok":True,"note":"اعتبار API بررسی شد؛ مجوز انتشار کانال باید جداگانه تست شود"}
