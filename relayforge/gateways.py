"""Provider adapters. One API failure never raises outside its destination worker.
Only documented provider hosts are accepted; no arbitrary target URLs.
"""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit
import mimetypes, re
import httpx

class Rejected(Exception): pass
class Uncertain(Exception): pass
class NotSupported(Exception): pass
class RetryLater(Exception):
    def __init__(self,delay=30): self.delay=max(30,min(86400,int(delay))); super().__init__("خطای موقت؛ تلاش مجدد")

def safe_description(obj, token=""):
    if not isinstance(obj,dict): return ""
    desc=obj.get("description") or obj.get("error_message") or obj.get("status_det") or ""
    if not isinstance(desc,str): return ""
    if token: desc=desc.replace(token,"[TOKEN]")
    desc=re.sub(r"https?://[^\s]+","[URL]",desc)
    desc=re.sub(r"\b\d{5,}:[A-Za-z0-9_-]{5,}","[TOKEN]",desc)
    return " ".join(desc.split())[:250]

def validate_token(token):
    if (token or "").startswith("user-session:") or not re.fullmatch(r"[A-Za-z0-9:_-]{5,250}",token or ""): raise Rejected("قالب توکن API نامعتبر")

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
    except (httpx.ConnectError,httpx.ConnectTimeout,httpx.PoolTimeout):
        raise RetryLater(30) from None
    except (httpx.TimeoutException, httpx.TransportError) as ex:
        # Do not log URL: token is part of URL. No blind retry after POST.
        raise Uncertain("ارتباط با API مقصد قطع شد؛ رسیدن پیام نامشخص است") from None
    # Never include the URL or raw body (contains token or private data).
    path=urlsplit(url).path
    method=path.rsplit("/",1)[-1]
    if method not in {"getMe","getChat","getChatMember","getUpdates","sendMessage","sendPhoto","sendVideo","sendAudio","sendVoice","sendDocument","sendAnimation","sendFile","copyMessage","requestSendFile"}: method="upload"
    token=path.rsplit("/",2)[-2] if "/" in path else ""
    if token.startswith("bot"): token=token[3:]
    try: obj=response.json()
    except ValueError: obj=None
    description=safe_description(obj,token)
    suffix=(" | "+description) if description else ""
    if response.status_code==429:
        try: delay=int((response.json().get("parameters") or {}).get("retry_after") or response.headers.get("Retry-After") or 30)
        except (ValueError,TypeError,AttributeError): delay=30
        raise RetryLater(delay)
    if response.status_code >= 500 or response.status_code == 408:
        raise Uncertain(f"پاسخ سرور مقصد HTTP {response.status_code}؛ وضعیت ارسال نامطمئن")
    if response.status_code >= 300 and response.status_code < 400:
        raise Rejected("انتقال HTTP به آدرس دیگر از API پذیرفته نمی‌شود")
    if response.status_code >= 400:
        raise Rejected(f"{method}: درخواست مقصد رد شد (HTTP {response.status_code})"+suffix)
    if not isinstance(obj, dict): raise Uncertain(f"{method}: پاسخ API مقصد JSON معتبر نیست")
    if obj.get("ok") is False or ("status" in obj and str(obj["status"]).upper() not in ("OK","SUCCESS")):
        code=obj.get("error_code")
        # Some providers return HTTP 200 with an API-level rate-limit code.
        if str(code)=="429":
            try: delay=int((obj.get("parameters") or {}).get("retry_after",30))
            except (ValueError,TypeError,AttributeError): delay=30
            raise RetryLater(delay)
        code=str(code) if isinstance(code,int) else str(response.status_code)
        if "status" in obj and isinstance(obj["status"],str):
            status=re.sub(r"[^A-Za-z0-9_-]","",obj["status"])[:40]
            if status: suffix=" | status="+status+suffix
        raise Rejected(f"{method}: API مقصد درخواست را رد کرد (code {code})"+suffix)
    return obj


def _result(platform, obj, require_id=True):
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
    if not require_id: return data
    value=data.get("message_id") if isinstance(data,dict) else data
    if isinstance(value,bool) or not isinstance(value,(int,str)) or not str(value).strip() or str(value)=="0":
        raise Uncertain("شناسه پیام مقصد قابل تأیید نیست")
    return str(value)

async def send(platform: str, token: str, chat_id: str, post: Post):
    if platform not in BASE: raise Rejected("سرویس مقصد ناشناخته")
    if not token or not chat_id: raise Rejected("توکن یا شناسه کانال مقصد وارد نشده")
    validate_token(token)
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
    kind = {"photo":"Image","video":"Video","audio":"Music","voice":"Voice","animation":"Gif","document":"File"}.get(post.kind,"File")
    init = await _post(client,address+"requestSendFile",json={"type":kind})
    _result("rubika",init,False)
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
    validate_token(token)
    async with httpx.AsyncClient(trust_env=True) as client:
        res=await _post(client,BASE[platform]+token+"/getMe",json={} if platform=="rubika" else None)
    _result(platform,res,False)
    return {"ok":True,"note":"اعتبار API بررسی شد؛ مجوز انتشار کانال هنوز تأیید نشده"}

async def check_target(platform,token,chat_id):
    validate_token(token)
    if platform=="rubika":
        async with httpx.AsyncClient(trust_env=True) as client:
            base=BASE[platform]+token+"/"
            _result(platform,await _post(client,base+"getMe"),False)
            data=_result(platform,await _post(client,base+"getChat",json={"chat_id":chat_id}),False)
        chat=data.get("chat",data) if isinstance(data,dict) else {}
        if not isinstance(chat,dict) or str(chat.get("chat_id",""))!=chat_id:raise Rejected("شناسه کانال روبیکا تأیید نشد؛ از تشخیص شناسه استفاده کنید")
        if str(chat.get("chat_type",chat.get("type",""))).lower()!="channel":raise Rejected("مقصد روبیکا باید کانال باشد")
        return {"ok":True,"chat_id":chat_id,"note":"شناسه کانال تأیید شد؛ برای مجوز انتشار پیام آزمایشی بفرستید"}
    if platform not in ("telegram","bale"): return await check_token(platform,token)
    address=BASE[platform]+token+"/"
    async with httpx.AsyncClient(trust_env=True) as client:
        me=_result(platform,await _post(client,address+"getMe"),False)
        chat=_result(platform,await _post(client,address+"getChat",json={"chat_id":chat_id}),False)
        member=_result(platform,await _post(client,address+"getChatMember",json={"chat_id":chat_id,"user_id":me["id"]}),False)
    if platform=="bale" and chat.get("type")=="channel" and member.get("status")=="administrator" and "can_post_messages" not in member:
        return {"ok":True,"chat_id":str(chat["id"]),"note":"بازو مدیر کانال است؛ API مجوز انتشار را صریحاً اعلام نکرد. پیام آزمایشی واقعی ارسال کنید"}
    if chat.get("type")!="channel" or not (member.get("status")=="creator" or (member.get("status")=="administrator" and member.get("can_post_messages") is True)):
        raise Rejected("ربات مجوز انتشار در کانال مقصد ندارد")
    return {"ok":True,"chat_id":str(chat["id"]),"note":"API مجوز انتشار را تأیید کرد (بدون ارسال پیام؛ انتشار واقعی هنوز تست نشده)"}

async def copy_telegram(token,source,chat_id,msg_id):
    validate_token(token)
    address=BASE["telegram"]+token+"/"
    async with httpx.AsyncClient(trust_env=True) as client:
        # Read-only preflight: uncertain read errors are safe to retry, not a send.
        try:
            src=_result("telegram",await _post(client,address+"getChat",json={"chat_id":source}),False)
            dst=_result("telegram",await _post(client,address+"getChat",json={"chat_id":chat_id}),False)
        except Uncertain: raise RetryLater(30) from None
        if src.get("has_protected_content") or src.get("id")==dst.get("id"): raise Rejected("مبدأ محافظت‌شده یا برابر مقصد است")
        return _result("telegram",await _post(client,address+"copyMessage",json={"chat_id":chat_id,"from_chat_id":source,"message_id":msg_id}))

async def discover_rubika(token):
    """Only channel IDs from bot updates, verified via getChat. No guessed IDs."""
    validate_token(token);address=BASE['rubika']+token+'/'
    async with httpx.AsyncClient(trust_env=True) as client:
        payload=_result('rubika',await _post(client,address+'getUpdates',json={'limit':50}),False)
        rows=payload.get('updates',[]) if isinstance(payload,dict) else []
        ids=list(dict.fromkeys(str(x.get('chat_id','')) for x in rows if isinstance(x,dict)))[:50]
        result=[]
        for cid in ids:
            if not cid or len(cid)>200:continue
            try:
                info=_result('rubika',await _post(client,address+'getChat',json={'chat_id':cid}),False)
                chat=info.get('chat',info) if isinstance(info,dict) else {}
                if isinstance(chat,dict) and str(chat.get('chat_type',chat.get('type',''))).lower()=='channel' and str(chat.get('chat_id',''))==cid:
                    result.append({'chat_id':cid,'title':str(chat.get('title',''))[:200]})
            except Rejected:continue
        return result
