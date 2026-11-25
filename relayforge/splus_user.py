"""Optional experimental SPlusthon adapter. No automatic installation, no bot fallback.
Only a channel for which the logged-in account has post rights is selectable.
"""
import asyncio, hashlib, re, time, os
from contextlib import asynccontextmanager
from pathlib import Path
from .gateways import Rejected, Uncertain, RetryLater

PREFIX='user-session:'
def marker(route):return PREFIX+hashlib.sha256(route.encode()).hexdigest()[:24]
def is_user(token):return isinstance(token,str) and token.startswith(PREFIX)
def session_name(root,route):return str(Path(root)/('soroush_user_'+marker(route)[len(PREFIX):]))

def client_for(root,route):
    try:from splusthon import SoroushClient
    except ImportError:raise Rejected('حالت آزمایشی حساب سروش فقط با نصب اختیاری SPlusthon فعال می‌شود؛ راهنما را بخوانید') from None
    return SoroushClient(session_name(root,route),connection_retries=1,request_retries=0,auto_reconnect=False)

@asynccontextmanager
async def connected(root,route):
    client=client_for(root,route)
    try:
        await asyncio.wait_for(client.connect(),30)
        yield client
    finally:
        try:await client.disconnect()
        except Exception:pass
        p=Path(session_name(root,route)+'.session')
        if os.name!='nt' and p.exists():p.chmod(0o600)

def can_post(entity):
    return bool(getattr(entity,'broadcast',False) and (getattr(entity,'creator',False) or getattr(getattr(entity,'admin_rights',None),'post_messages',False)))

class Accounts:
    def __init__(self,root):self.root=root;self.pending={}
    async def code(self,route,phone):
        if not re.fullmatch(r'\+[0-9]{7,16}',phone):raise Rejected('شماره با کد کشور وارد شود')
        prior=self.pending.get(route)
        if prior and time.monotonic()-prior[2]<60:raise Rejected('برای درخواست مجدد کد ۶۰ ثانیه صبر کنید')
        async with connected(self.root,route) as c:
            if await c.is_user_authorized():return {'authorized':True,'note':'حساب از قبل وارد شده؛ کانال‌ها را دریافت کنید'}
            try:sent=await asyncio.wait_for(c.send_code_request(phone),45)
            except Exception as e:raise Rejected('دریافت کد سروش ناموفق: '+type(e).__name__) from None
            self.pending[route]=(phone,sent.phone_code_hash,time.monotonic())
        return {'authorized':False,'note':'کد را در سروش دریافت و وارد کنید'}
    async def verify(self,route,code,password):
        record=self.pending.get(route)
        if not record or time.monotonic()-record[2]>600:raise Rejected('ابتدا کد جدید درخواست کنید')
        async with connected(self.root,route) as c:
            try:
                if password and not code:await asyncio.wait_for(c.sign_in(password=password),45)
                else:await asyncio.wait_for(c.sign_in(phone=record[0],code=code,phone_code_hash=record[1]),45)
            except Exception as e:
                if type(e).__name__=='SessionPasswordNeededError':return {'authorized':False,'note':'رمز دومرحله‌ای لازم است؛ کد را خالی و رمز را وارد کنید'}
                raise Rejected('ورود سروش ناموفق: '+type(e).__name__) from None
            if not await c.is_user_authorized():raise Rejected('ورود حساب تأیید نشد')
        self.pending.pop(route,None);return {'authorized':True,'note':'ورود انجام شد؛ کانال‌های مجاز را دریافت کنید'}
    async def channels(self,route):
        async with connected(self.root,route) as c:
            if not await c.is_user_authorized():raise Rejected('حساب سروش وارد نشده')
            result=[]
            async for d in c.iter_dialogs(limit=300):
                if can_post(d.entity):result.append({'chat_id':str(d.id),'title':str(d.name)[:200]})
            return result
    async def check(self,route,chat_id):
        async with connected(self.root,route) as c:
            await self.entity(c,chat_id)
        return {'ok':True,'note':'حساب مجوز انتشار کانال را دارد؛ ارسال واقعی آزمایشی لازم است','chat_id':chat_id}
    async def entity(self,c,chat_id):
        if not await c.is_user_authorized():raise Rejected('نشست سروش وارد نشده')
        try:entity=await c.get_entity(int(chat_id) if str(chat_id).lstrip('-').isdigit() else chat_id)
        except Exception as e:raise Rejected('کانال سروش قابل دسترسی نیست: '+type(e).__name__) from None
        if not can_post(entity):raise Rejected('حساب سروش مالک یا مدیر دارای مجوز انتشار این کانال نیست')
        return entity
    async def send(self,route,chat_id,post):
        try:
            async with connected(self.root,route) as c:
                entity=await self.entity(c,chat_id)
                try:
                    result=await asyncio.wait_for(c.send_file(entity,post.path,caption=post.text,parse_mode=None) if post.path else c.send_message(entity,post.text,parse_mode=None),90)
                except Exception as e:
                    if type(e).__name__=='FloodWaitError':raise RetryLater(getattr(e,'seconds',30)) from None
                    raise Uncertain('پاسخ ارسال حساب سروش نامطمئن: '+type(e).__name__) from None
                mid=getattr(result,'id',None)
                if not mid:raise Uncertain('شناسه پیام سروش قابل تأیید نیست')
                return str(mid)
        except (Rejected,Uncertain,RetryLater):raise
        except Exception:raise RetryLater(30) from None
