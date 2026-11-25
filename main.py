"""RelayForge 4.3.7 | FastAPI local dashboard & Windows VPS runtime."""
from __future__ import annotations
import argparse, asyncio, base64, contextlib, logging, os, secrets, sys, webbrowser, time
from relayforge import backup42 as backups
from relayforge.splus_user import is_user, marker
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Literal
from fastapi import FastAPI, Request, Depends, Header, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field
import uvicorn
from relayforge.database import Database, PLATFORMS
from relayforge.engine import Engine
from relayforge.source import SourceUnavailable
from relayforge import richtext, ai43, gemini431, ai_settings, ai436, style437
from relayforge.gateways import check_target, send, Post, Rejected, Uncertain, RetryLater, discover_rubika

APP_NAME="RelayForge"
PORT=8765


def runtime_root() -> Path:
    if "__compiled__" in globals():
        return Path(sys.argv[0]).resolve().parent
    if getattr(sys,"frozen",False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent

def web_root() -> Path:
    return Path(__file__).resolve().parent/"web"

class FileLock:
    def __init__(self,path:Path):
        self.path=path; self.f=None
    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.f=open(self.path,"a+b")
        self.f.seek(0); self.f.write(b"1"); self.f.flush();self.f.seek(0)
        try:
            if os.name=="nt":
                import msvcrt
                msvcrt.locking(self.f.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(self.f.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError:
            self.f.close()
            raise RuntimeError("نسخه دیگری از برنامه همین پوشه را اجرا کرده است") from None
        return self
    def __exit__(self,*args):
        if self.f:
            try:
                self.f.seek(0)
                if os.name=="nt":
                    import msvcrt
                    msvcrt.locking(self.f.fileno(),msvcrt.LK_UNLCK,1)
                else:
                    import fcntl
                    fcntl.flock(self.f.fileno(),fcntl.LOCK_UN)
            finally:self.f.close()

class SourceSettings(BaseModel):
    model_config=ConfigDict(extra="forbid")
    mode:Literal["bot","user"]
    channel:str=Field(min_length=1,max_length=250)
    bot_token:str=""
    api_id:str=""
    api_hash:str=""
    phone:str=""
    start_id:int=Field(default=1,ge=1)
    end_id:int|None=None
    history_enabled:bool=False
    realtime_enabled:bool=True
    poll_seconds:int=Field(default=45,ge=10,le=3600)
    max_media_mb:int=Field(default=20,ge=1,le=100)
    filter_enabled:bool=False
    keywords:list[str]=Field(default_factory=list,max_length=100)
    hashtags:list[str]=Field(default_factory=list,max_length=100)
    exclude_words:list[str]=Field(default_factory=list,max_length=100)
    filter_match:Literal["any","all"]="any"
    prefix:str=Field(default="",max_length=500)
    suffix:str=Field(default="",max_length=500)
    upgrade_archive:bool=False

class TargetSettings(BaseModel):
    model_config=ConfigDict(extra="forbid")
    token:str=""
    chat_id:str=Field(default="",max_length=200)
    enabled:bool=False
    interval_seconds:int=Field(default=0,ge=0,le=604800)
    include_backlog:bool=False

class NewTarget(BaseModel):
    provider:Literal["telegram","bale","rubika","eitaa","soroush"]

class ConfirmAction(BaseModel):
    model_config=ConfigDict(extra="forbid")
    confirm:Literal[True]

class RemoveTargets(ConfirmAction):
    routes:list[str]=Field(min_length=1,max_length=50)

class PauseTarget(BaseModel):
    model_config=ConfigDict(extra="forbid")
    enabled:bool

class TestMessage(ConfirmAction):
    text:str=Field(default="پیام آزمایشی RelayForge 4.3.7",min_length=1,max_length=500)

class RichModes(BaseModel):
    model_config=ConfigDict(extra="forbid")
    modes:dict[str,str]

class RichPreview(BaseModel):
    model_config=ConfigDict(extra="forbid")
    platform:Literal["telegram","bale","eitaa","rubika","soroush"]
    mode:str=""
    text:str=Field(min_length=1,max_length=4000)
    entities:list[dict]=Field(default_factory=list,max_length=256)
    caption:bool=False

class RichTest(ConfirmAction):
    mode:str=""
    source_message_id:int|None=Field(default=None,ge=1)

class AIEnable(BaseModel):
    model_config=ConfigDict(extra="forbid")
    enabled:bool

class AIProfile(BaseModel):
    model_config=ConfigDict(extra="forbid")
    brand:str=Field(default="",max_length=120)
    tone:Literal["formal","friendly","news","educational","sales","concise","custom"]="formal"
    instructions:str=Field(default="",max_length=1000)
    destination:str=Field(default="",max_length=200)
    template:str=Field(default="{body}",max_length=1500)
    locked:list[str]=Field(default_factory=list,max_length=30)

class AIKeyInput(BaseModel):
    model_config=ConfigDict(extra="forbid")
    operation:Literal["save","remove"]="save"
    key:str=Field(default="",max_length=512)

class AIPreferences(BaseModel):
    model_config=ConfigDict(extra="forbid")
    provider:Literal["local","openai","gemini"]="local"
    openai_model:str=Field(default="",max_length=200)
    gemini_model:str=Field(default="",max_length=200)
    tts_model:str=Field(default="",max_length=200)
    daily_limit:int=Field(default=10,ge=1,le=1000)
    model_mode:Literal["manual","economy","quality"]="manual"
    max_models:int=Field(default=6,ge=1,le=20)
    api_path:Literal["legacy","generateContent"]="generateContent"

class AIPreview(BaseModel):
    model_config=ConfigDict(extra="forbid")
    provider:Literal["local","openai","gemini"]="local"
    text:str=Field(min_length=1,max_length=6000)
    profile:AIProfile
    model:str="gpt-4o-mini"
    consent:bool=False
    model_mode:Literal["manual","economy","quality"]="manual"
    max_models:int=Field(default=6,ge=1,le=20)
    emoji:bool=False
    api_path:Literal["legacy","generateContent"]="legacy"

class StyleAnalyze(BaseModel):
    model_config=ConfigDict(extra="forbid")
    name:str=Field(min_length=1,max_length=100)
    channel:str=Field(default="",max_length=250)
    count:Literal[30,120,300,500]=120
    use_ai:bool=False
    consent:bool=False

class StyleEdit(BaseModel):
    model_config=ConfigDict(extra="forbid")
    name:str=Field(min_length=1,max_length=100)
    key:Literal["media","brief","long","standard"]
    instructions:str=Field(max_length=1000)
    template:str=Field(max_length=1500)

class GeminiScript(BaseModel):
    model_config=ConfigDict(extra="forbid")
    text:str=Field(min_length=1,max_length=4000)
    model:str="gemini-3.8-flash"
    consent:bool=False
    model_mode:Literal["manual","economy","quality"]="manual"
    max_models:int=Field(default=6,ge=1,le=20)
    api_path:Literal["legacy","generateContent"]="legacy"

class AIDiagnostic(BaseModel):
    model_config=ConfigDict(extra="forbid")
    model:str=Field(max_length=200)
    consent:bool=False

class AIMedia(BaseModel):
    model_config=ConfigDict(extra="forbid")
    text:str=Field(min_length=1,max_length=4000)
    model:str=Field(max_length=200)
    kind:Literal["image","sticker","clip"]="image"
    style:str=Field(default="editorial illustration",max_length=300)
    consent:bool=False

class GeminiSpeech(BaseModel):
    model_config=ConfigDict(extra="forbid")
    text:str=Field(min_length=1,max_length=6000)
    model:str="gemini-3.8-flash-tts"
    mode:Literal["narration","podcast"]="narration"
    voice:str="Kore"
    voice2:str="Puck"
    style:str="neutral"
    consent:bool=False

class SplusLogin(BaseModel):
    phone:str=Field(default="",max_length=25)
    code:str=Field(default="",max_length=20)
    password:str=Field(default="",max_length=256)

class SplusSelect(ConfirmAction):
    chat_id:str=Field(min_length=1,max_length=200)

class BackupIn(BaseModel):
    mode:Literal["full","settings","database"]="full"
    password:str=Field(min_length=12,max_length=256)

class AutoBackupIn(BaseModel):
    enabled:bool
    mode:Literal["database","settings","full"]="database"
    password:str=Field(default="",max_length=256)

class BulkResolve(ConfirmAction):
    ids:list[int]=Field(default_factory=list,max_length=500)
    all_rows:bool=False
    action:Literal["retry","mark_sent","skip"]

class ResetConfirm(BaseModel):
    ticket:str
    skip_uncertain:bool=False

class VerifyCode(BaseModel):
    code:str=""
    password:str=""

class ResolveIn(BaseModel):
    action:Literal["retry","mark_sent","skip"]

class Application:
    def __init__(self,data_path:Path):
        self.db=Database(data_path)
        keyfile=data_path/"dashboard-key.txt"
        if not keyfile.exists():
            with keyfile.open("x",encoding="utf8") as f:f.write(secrets.token_urlsafe(42))
            if os.name!="nt":keyfile.chmod(0o600)
        self.admin_key=keyfile.read_text(encoding="utf8").strip()
        self.engine=Engine(self.db)
        self.log=logging.getLogger("relayforge")
        self.control=asyncio.Lock()
        self.http_mutation=asyncio.Lock()
        self.reset_ticket=None

    def authenticate(self,key:str|None):
        return isinstance(key,str) and secrets.compare_digest(key,self.admin_key)


def create_app(data_root:Path|None=None, start_workers=True, port=PORT) -> FastAPI:
    root = Path(data_root or (runtime_root()/"data"))
    service=Application(root)

    async def daily_backup():
        while True:
            try:
                cfg=service.db.get_setting('backup42_auto') or {}
                if cfg.get('enabled') and time.time()-float(service.db.get_setting('backup42_last') or 0)>=86400:
                    async with service.http_mutation, service.control:
                        if not (root/'restore_pending').exists() and (cfg.get('mode')!='full' or not service.engine.active):
                            if cfg.get('mode')=='full':await service.engine.collector.shutdown()
                            password=service.db.reveal(cfg['password'])
                            backups.make_backup(root,password,cfg['mode'],'daily')
                            service.db.set_setting('backup42_last',time.time());service.db.event('INFO','system','پشتیبان رمزگذاری‌شده روزانه ساخته شد')
            except asyncio.CancelledError:raise
            except Exception:service.db.event('WARN','system','پشتیبان روزانه ناموفق؛ حجم، دسترسی و تنظیمات بررسی شود')
            await asyncio.sleep(60)

    @contextlib.asynccontextmanager
    async def lifespan(app):
        if start_workers and service.db.get_setting("run_enabled") and not (root/'restore_pending').exists(): service.engine.start()
        task=asyncio.create_task(daily_backup()) if start_workers else None
        try:yield
        finally:
            if task:
                task.cancel();await asyncio.gather(task,return_exceptions=True)
            await service.engine.stop()

    app=FastAPI(title="RelayForge Control API",version="4.3.7",docs_url=None,redoc_url=None,openapi_url=None,lifespan=lifespan)
    app.state.service=service

    @app.middleware("http")
    async def local_security(request:Request,call_next):
        hostname=request.url.hostname
        host=request.headers.get("host", "")
        remote=request.client.host if request.client else ""
        if hostname not in ("127.0.0.1","localhost") or remote not in ("127.0.0.1","::1","localhost"):
            return JSONResponse({"detail":"فقط استفاده محلی مجاز است"},status_code=403)
        if request.method not in ("GET","HEAD"):
            origin=request.headers.get("origin","")
            if origin not in (f"http://127.0.0.1:{port}",f"http://localhost:{port}"):
                return JSONResponse({"detail":"درخواست خارج از داشبورد مجاز نیست"},status_code=403)
        mutates=request.method not in ("GET","HEAD") or request.url.path=="/api/source/auth"
        if mutates:
            async with service.http_mutation:
                if (root/"restore_pending").exists():
                    return JSONResponse({"detail":"بازیابی در انتظار راه‌اندازی مجدد است؛ ابتدا برنامه را ببندید و دوباره اجرا کنید"},status_code=409)
                result=await call_next(request)
        else:result=await call_next(request)
        result.headers.update({
            "X-Content-Type-Options":"nosniff", "X-Frame-Options":"DENY",
            "Referrer-Policy":"no-referrer", "Cache-Control":"no-store",
            "Content-Security-Policy":"default-src 'self'; connect-src 'self'; img-src 'self' data: blob:; media-src 'self' blob:; script-src 'self'; style-src 'self'; frame-ancestors 'none'; form-action 'self'",
        })
        return result

    from fastapi.exceptions import RequestValidationError
    @app.exception_handler(RequestValidationError)
    async def invalid_input(request, exc):
        return JSONResponse({"detail":"مقادیر ورودی نامعتبر است"},status_code=422)

    def auth(x_studio_key:str|None=Header(None)):
        if not service.authenticate(x_studio_key):
            raise HTTPException(401,"کلید ورود به داشبورد نامعتبر است")

    def safe_exception(exc):
        if isinstance(exc,(ValueError,SourceUnavailable,Rejected,Uncertain,RetryLater)):
            return HTTPException(status_code=400,detail=str(exc)[:250])
        # Avoid returning tokens from provider errors or Telethon exceptions.
        service.log.warning("Dashboard operation failed: %s",type(exc).__name__)
        return HTTPException(500,"خطای داخلی؛ گزارش محلی را بررسی کنید")

    @app.get("/")
    async def index():return FileResponse(web_root()/"index.html",media_type="text/html; charset=utf-8")
    @app.get("/assets/{filename}")
    async def asset(filename:str):
        if filename not in ("main.css","main.js"):raise HTTPException(404)
        mime="text/css" if filename.endswith("css") else "text/javascript"
        return FileResponse(web_root()/filename,media_type=mime)

    @app.get('/api/richtext',dependencies=[Depends(auth)])
    async def rich_config():
        return {'modes':{**richtext.DEFAULTS,**(service.db.get_setting('rich43_modes') or {})},'available':richtext.MODES,'recommended':richtext.RECOMMENDED}

    @app.post('/api/richtext',dependencies=[Depends(auth)])
    async def rich_save(data:RichModes):
        if service.engine.active:raise HTTPException(409,'ابتدا موتور را متوقف کنید')
        if set(data.modes)!=set(PLATFORMS) or any(v not in richtext.MODES[k] for k,v in data.modes.items()):raise HTTPException(422,'حالت قالب‌بندی نامعتبر')
        service.db.set_setting('rich43_modes',data.modes)
        return {'ok':True}

    @app.post('/api/richtext/preview',dependencies=[Depends(auth)])
    async def rich_preview(data:RichPreview):
        if data.mode and data.mode not in richtext.MODES[data.platform]:raise HTTPException(422,'حالت نامعتبر')
        text,options,warnings=richtext.render(data.platform,data.text,data.entities,data.mode or None,data.caption)
        return {'text':text,'options':options,'warnings':warnings}

    @app.post('/api/richtext/test/{platform}',dependencies=[Depends(auth)])
    async def rich_test(platform:str,data:RichTest):
        async with service.control:
            if service.engine.active:raise HTTPException(409,'ابتدا موتور را متوقف کنید')
            if platform not in [t['platform'] for t in service.db.targets()]:raise HTTPException(404)
            try:
                d=service.db.target(platform);p=d['provider'];mode=data.mode or (service.db.get_setting('rich43_modes') or {}).get(p,richtext.DEFAULTS[p])
                if mode not in richtext.MODES[p]:raise ValueError('حالت قالب نامعتبر')
                saved=(service.db.get_setting('rich43_modes') or {}).get(p,richtext.DEFAULTS[p])
                if data.source_message_id is not None:
                    if mode!=saved:raise ValueError('ابتدا روش انتخاب‌شده را ذخیره کنید؛ تست پست واقعی از تنظیم ذخیره‌شده استفاده می‌کند')
                    with service.db.connection() as c:
                        row=c.execute("SELECT *, id AS message_id FROM source_messages WHERE epoch=? AND msg_id=? AND protected=0 AND eligible=1 AND media_kind<>'copy'",(service.db.get_setting('epoch'),data.source_message_id)).fetchone()
                    if not row:raise ValueError('پست مجاز در عملیات فعلی یافت نشد')
                    row=dict(row);text=row['body'];spans=tuple(richtext.load(text,row['entities']))
                    path=await service.engine.collector.get_media(row)
                    if row['media_kind']!='text' and not path:raise ValueError('رسانه در دسترس نیست؛ تست ناقص انجام نمی‌شود')
                    post=Post(text,row['media_kind'],path,spans,saved)
                else:
                    text='🔥 آزمایش قالب‌بندی RelayForge\nمتن پررنگ و متن مورب\n#برنامه_نویسی'
                    spans=tuple({'type':k,'start':text.index(v),'end':text.index(v)+len(v)} for k,v in [('bold','متن پررنگ'),('italic','متن مورب')])
                    post=Post(text,entities=spans,format_mode=mode)
                rendered,opts,warnings=richtext.render(p,post.text,post.entities,post.format_mode,bool(post.path))
                diagnostic={'method':'sendFile' if post.path else 'sendMessage','mode':mode,'saved_mode':saved,'parse_mode':opts.get('parse_mode'),'bold_count':sum(x['type']=='bold' for x in spans),'html_bold_tags':rendered.count('<b>'),'warnings':warnings} if p=='eitaa' else {'mode':mode,'saved_mode':saved,'warnings':warnings}
                remote=await service.engine.splus.send(platform,d['chat_id'],post) if p=='soroush' and d['token']==marker(platform) else await send(p,d['token'],d['chat_id'],post)
                service.db.event('INFO',platform,'تست قالب‌بندی ارسال شد؛ نمایش در پیام‌رسان را دستی بررسی کنید')
                return {'ok':True,'remote_id':str(remote),'note':'نمایش بولد را در برنامه مقصد بررسی کنید'+('؛ روش انتخابی هنوز ذخیره نشده است' if mode!=saved else ''),'diagnostic':diagnostic}
            except Exception as exc:raise safe_exception(exc) from None

    @app.get('/api/ai-lab',dependencies=[Depends(auth)])
    async def ai_config():
        providers=ai_settings.public(service.db)
        return {'enabled':bool(service.db.get_setting('ai43_enabled')),'key_available':providers['openai']['key_source'] in ('saved','environment'),'gemini_key_available':providers['gemini']['key_source'] in ('saved','environment'),'gemini_models':providers['gemini']['text'],'tts_models':providers['gemini']['tts'],'voices':gemini431.VOICES,'profiles':service.db.get_setting('ai43_profiles') or {},'usage':service.db.get_setting('ai43_usage') or {},'prompt_version':ai43.PROMPT_VERSION,'providers':providers,'preferences':service.db.get_setting('ai44_preferences') or {},'daily_limit':ai_settings.daily_limit(service.db)}

    @app.post('/api/ai-lab/key/{provider}',dependencies=[Depends(auth)])
    async def ai_key(provider:Literal['openai','gemini'],data:AIKeyInput):
        try:ai_settings.save_key(service.db,provider,data.operation,data.key)
        except Exception as exc:raise safe_exception(exc) from None
        return {'ok':True,'providers':ai_settings.public(service.db)}

    @app.post('/api/ai-lab/models/{provider}',dependencies=[Depends(auth)])
    async def ai_models(provider:Literal['openai','gemini']):
        try:return await asyncio.wait_for(ai_settings.refresh(service.db,provider),timeout=90)
        except TimeoutError:raise HTTPException(400,'زمان دریافت فهرست مدل پایان یافت') from None
        except Exception as exc:raise safe_exception(exc) from None

    @app.post('/api/ai-lab/preferences',dependencies=[Depends(auth)])
    async def ai_preferences(data:AIPreferences):
        try:
            for p,k,m in [('openai','text',data.openai_model),('gemini','text',data.gemini_model),('gemini','tts',data.tts_model)]:
                if m:ai_settings.check_model(service.db,p,k,m)
            service.db.set_setting('ai44_preferences',data.model_dump(exclude={'daily_limit'}))
            service.db.set_setting('ai44_daily_limit',data.daily_limit)
        except Exception as exc:raise safe_exception(exc) from None
        return {'ok':True}

    @app.post('/api/ai-lab/enable',dependencies=[Depends(auth)])
    async def ai_enable(data:AIEnable):
        service.db.set_setting('ai43_enabled',data.enabled);return {'ok':True}

    @app.get('/api/ai-lab/sample',dependencies=[Depends(auth)])
    async def ai_sample():
        with service.db.connection() as c:
            row=c.execute("SELECT msg_id,body,entities FROM source_messages WHERE epoch=? AND protected=0 AND eligible=1 AND media_kind<>'copy' AND body<>'' ORDER BY id DESC LIMIT 1",(service.db.get_setting('epoch'),)).fetchone()
        if not row:raise HTTPException(404,'نمونه متنی مجاز موجود نیست')
        return {'msg_id':row['msg_id'],'text':row['body'][:6000],'entities':richtext.load(row['body'],row['entities'])}

    @app.get('/api/styles',dependencies=[Depends(auth)])
    async def styles_list():return {'styles':style437.list_profiles(service.db)}

    @app.get('/api/styles/{id}',dependencies=[Depends(auth)])
    async def styles_get(id:str):
        try:return style437.get(service.db,id)
        except Exception as exc:raise safe_exception(exc) from None

    @app.post('/api/styles/analyze',dependencies=[Depends(auth)])
    async def styles_analyze(data:StyleAnalyze):
        try:
            if not data.name.strip():raise ValueError('نام الگو لازم است')
            if data.use_ai and not data.consent:raise ValueError('رضایت ارسال نمونه به Gemini لازم است')
            if data.channel:
                async with service.control:
                    if service.engine.active:raise ValueError('برای تحلیل کانال دیگر ابتدا موتور را متوقف کنید')
                    rows=await asyncio.wait_for(style437.remote_samples(service.engine.collector,data.channel,data.count),timeout=120)
                source=str(style437.channel_ref(data.channel))
            else:
                rows=style437.local_samples(service.db,data.count);source='local archive'
            result=await asyncio.wait_for(style437.analyze(service.db,rows,data.count,data.name,source,data.use_ai,data.consent),timeout=240)
            return style437.save(service.db,result)
        except TimeoutError:raise HTTPException(400,'زمان تحلیل پایان یافت؛ خودکار تکرار نمی‌شود') from None
        except Exception as exc:raise safe_exception(exc) from None

    @app.post('/api/styles/{id}/edit',dependencies=[Depends(auth)])
    async def styles_edit(id:str,data:StyleEdit):
        try:return style437.edit(service.db,id,**data.model_dump())
        except Exception as exc:raise safe_exception(exc) from None

    @app.post('/api/ai-lab/profile/{route}',dependencies=[Depends(auth)])
    async def ai_profile(route:str,data:AIProfile):
        if route not in [t['platform'] for t in service.db.targets()]:raise HTTPException(404)
        values=data.model_dump()
        if any(not x.strip() or len(x)>200 for x in values['locked']):raise HTTPException(422,'عبارت محافظت‌شده نامعتبر')
        try:ai43.template_render(data.template,dict(body='نمونه',brand=data.brand,title='',destination=data.destination,hashtags=''))
        except ValueError as exc:raise safe_exception(exc) from None
        profiles=service.db.get_setting('ai43_profiles') or {};profiles[route]=values;service.db.set_setting('ai43_profiles',profiles)
        return {'ok':True}

    @app.post('/api/ai-lab/preview',dependencies=[Depends(auth)])
    async def ai_preview(data:AIPreview):
        if any(not x.strip() or len(x)>200 for x in data.profile.locked):raise HTTPException(422,'عبارت محافظت‌شده نامعتبر')
        try:
            if data.provider=="gemini" and (data.api_path=="generateContent" or data.model_mode!="manual"):
                return await asyncio.wait_for(ai436.preview(service.db,data.text,data.profile.model_dump(),data.consent,data.model,data.model_mode,data.max_models,data.emoji),timeout=240)
            profile=data.profile.model_dump()
            if data.emoji and data.provider in ("openai","gemini"):profile["instructions"]+=" Add a few relevant Unicode emoji."
            return await ai43.preview(service.db,data.text,profile,data.provider,data.consent,data.model)
        except TimeoutError:raise HTTPException(400,'زمان پردازش پایان یافت؛ نتیجه نامشخص') from None
        except Exception as exc:raise safe_exception(exc) from None

    @app.post('/api/ai-lab/podcast-script',dependencies=[Depends(auth)])
    async def podcast_script(data:GeminiScript):
        try:
            if data.api_path=="generateContent" or data.model_mode!="manual":return await asyncio.wait_for(ai436.podcast(service.db,data.text,data.consent,data.model,data.model_mode,data.max_models),timeout=240)
            return await asyncio.wait_for(gemini431.podcast_script(service.db,data.text,data.consent,data.model),timeout=120)
        except TimeoutError:raise HTTPException(400,'زمان ساخت متن پادکست پایان یافت؛ درخواست تکرار نمی‌شود') from None
        except Exception as exc:raise safe_exception(exc) from None

    @app.post('/api/ai-lab/diagnose',dependencies=[Depends(auth)])
    async def ai_diagnose(data:AIDiagnostic):
        try:return await asyncio.wait_for(ai436.diagnose(service.db,data.model,data.consent),timeout=60)
        except TimeoutError:raise HTTPException(400,'زمان تست پایان یافت؛ نتیجه نامشخص') from None
        except Exception as exc:raise safe_exception(exc) from None

    @app.post('/api/ai-lab/media',dependencies=[Depends(auth)])
    async def ai_media(data:AIMedia):
        try:return await asyncio.wait_for(ai436.image(service.db,**data.model_dump()),timeout=180)
        except TimeoutError:raise HTTPException(400,'زمان ساخت رسانه پایان یافت؛ درخواست تکرار نمی‌شود') from None
        except Exception as exc:raise safe_exception(exc) from None

    @app.post('/api/ai-lab/speech',dependencies=[Depends(auth)])
    async def ai_speech(data:GeminiSpeech):
        try:return await asyncio.wait_for(gemini431.speech(service.db,**data.model_dump()),timeout=240)
        except TimeoutError:raise HTTPException(400,'زمان ساخت صوت پایان یافت؛ درخواست تکرار نمی‌شود') from None
        except Exception as exc:raise safe_exception(exc) from None

    @app.get("/api/status",dependencies=[Depends(auth)])
    async def status():
        result=service.engine.status();result["restore_pending"]=(root/"restore_pending").exists();return result

    @app.post("/api/source",dependencies=[Depends(auth)])
    async def source_save(data:SourceSettings):
        async with service.control:
            if service.engine.active: raise HTTPException(409,"ابتدا موتور را متوقف کنید")
            try:
                old=service.db.get_source()
                if data.upgrade_archive and old["mode"]=="bot" and data.mode=="user" and data.channel==old["channel"] and not service.db.get_setting("source_peer"):
                    info=await service.engine.collector.check_source()
                    if not info.get("peer_id"): raise ValueError("شناسه عددی مبدأ قبلی قابل تأیید نیست؛ پروژه جدا بسازید")
                    service.db.set_setting("source_peer",info["peer_id"])
                service.db.update_source(data.model_dump())
            except Exception as exc:raise safe_exception(exc) from None
            return {"ok":True}

    @app.post("/api/source/check",dependencies=[Depends(auth)])
    async def source_check():
        try:return await service.engine.collector.check_source()
        except Exception as exc:raise safe_exception(exc) from None

    @app.post("/api/source/request-code",dependencies=[Depends(auth)])
    async def request_code():
        if service.engine.active: raise HTTPException(409,"ابتدا موتور را متوقف کنید")
        try:return {"message":await service.engine.collector.request_code()}
        except Exception as exc:raise safe_exception(exc) from None

    @app.post("/api/source/verify",dependencies=[Depends(auth)])
    async def verify_code(data:VerifyCode):
        if service.engine.active: raise HTTPException(409,"ابتدا موتور را متوقف کنید")
        try:return {"message":await service.engine.collector.confirm_code(data.code,data.password)}
        except Exception as exc:raise safe_exception(exc) from None

    @app.get("/api/source/auth",dependencies=[Depends(auth)])
    async def account_status():
        try:return {"authorized":await service.engine.collector.authorized()}
        except Exception as exc:raise safe_exception(exc) from None

    @app.post("/api/targets/{platform}",dependencies=[Depends(auth)])
    async def target_save(platform:str, data:TargetSettings):
        async with service.control:
            if platform not in [t["platform"] for t in service.db.targets()]:raise HTTPException(404,"مقصد نامعتبر")
            if service.engine.active: raise HTTPException(409,"ابتدا موتور را متوقف کنید")
            try:
                if is_user(data.token):raise ValueError("حساب سروش را از بخش ورود حساب انتخاب کنید")
                service.db.update_target(platform,data.model_dump())
            except Exception as exc:raise safe_exception(exc) from None
            return {"ok":True}

    @app.post("/api/targets/{platform}/check",dependencies=[Depends(auth)])
    async def target_check(platform:str):
        if platform not in [t["platform"] for t in service.db.targets()]:raise HTTPException(404)
        try:
            d=service.db.target(platform)
            if d["provider"]=="soroush" and is_user(d["token"]) and service.engine.active:raise ValueError("برای بررسی حساب سروش ابتدا موتور را متوقف کنید")
            result=await service.engine.splus.check(platform,d["chat_id"]) if d["provider"]=="soroush" and d["token"]==marker(platform) else await check_target(d["provider"],d["token"],d["chat_id"])
            service.db.event("INFO",platform,"تست API: "+result["note"])
            return result
        except Exception as exc:
            err=safe_exception(exc);service.db.event("ERROR",platform,"تست API: "+str(err.detail));raise err from None

    @app.post("/api/targets",dependencies=[Depends(auth)])
    async def target_add(data:NewTarget):
        if service.engine.active: raise HTTPException(409,"ابتدا موتور را متوقف کنید")
        try:return {"ok":True,"route":service.db.add_target(data.provider)}
        except Exception as exc:raise safe_exception(exc) from None

    @app.post("/api/target-actions/remove",dependencies=[Depends(auth)])
    async def target_remove(data:RemoveTargets):
        async with service.control:
            if service.engine.active: raise HTTPException(409,"ابتدا موتور را متوقف کنید")
            try: return {"ok":True,"removed":service.db.remove_targets(data.routes)}
            except Exception as exc: raise safe_exception(exc) from None

    @app.post("/api/targets/{platform}/pause",dependencies=[Depends(auth)])
    async def target_pause(platform:str,data:PauseTarget):
        if platform not in [t["platform"] for t in service.db.targets()]: raise HTTPException(404)
        try: service.db.update_target(platform,{"enabled":data.enabled})
        except Exception as exc: raise safe_exception(exc) from None
        return {"ok":True}

    @app.post("/api/targets/{platform}/clear-queue",dependencies=[Depends(auth)])
    async def target_clear(platform:str,data:ConfirmAction):
        async with service.control:
            if service.engine.active: raise HTTPException(409,"ابتدا موتور را متوقف کنید")
            try: return {"ok":True,"cleared":service.db.clear_queue(platform)}
            except Exception as exc: raise safe_exception(exc) from None

    @app.post("/api/targets/{platform}/test-send",dependencies=[Depends(auth)])
    async def target_test_send(platform:str,data:TestMessage):
        async with service.control:
            if service.engine.active: raise HTTPException(409,"برای تست مستقل ابتدا موتور را متوقف کنید")
            if platform not in [t["platform"] for t in service.db.targets()]: raise HTTPException(404)
            try:
                d=service.db.target(platform)
                if not d["token"] or not d["chat_id"]: raise ValueError("ابتدا توکن و شناسه مقصد را ذخیره کنید")
                service.db.event("INFO",platform,"ارسال آزمایشی دستی آغاز شد")
                remote=await service.engine.splus.send(platform,d["chat_id"],Post(data.text)) if d["provider"]=="soroush" and d["token"]==marker(platform) else await send(d["provider"],d["token"],d["chat_id"],Post(data.text))
                service.db.event("INFO",platform,"ارسال آزمایشی موفق؛ شناسه پیام مقصد "+str(remote))
                return {"ok":True,"remote_id":str(remote)}
            except Exception as exc:
                err=safe_exception(exc);service.db.event("ERROR",platform,"ارسال آزمایشی: "+str(err.detail));raise err from None

    @app.get("/api/reports/routes",dependencies=[Depends(auth)])
    async def report_routes():return {"routes":service.db.report_routes()}

    @app.get("/api/reports/{kind}",dependencies=[Depends(auth)])
    async def reports(kind:Literal["events","deliveries"],route:str=Query("",max_length=100),q:str=Query("",max_length=250),limit:int=Query(100,ge=1,le=500),offset:int=Query(0,ge=0)):
        return service.db.report(kind,route,q,limit,offset)

    @app.post("/api/source/rescan",dependencies=[Depends(auth)])
    async def source_rescan(data:ConfirmAction):
        async with service.control:
            if service.engine.active:raise HTTPException(409,"ابتدا موتور را متوقف کنید")
            src=service.db.get_source()
            if not src["history_enabled"]:raise HTTPException(400,"ابتدا آرشیو را فعال و ذخیره کنید")
            service.db.set_setting("history_cursor",src["start_id"]-1)
            service.db.event("INFO","source","پیمایش مجدد آرشیو درخواست شد؛ شناسه‌های قبلاً ثبت شده تکرار نمی‌شوند")
            return {"ok":True}

    @app.post("/api/engine/{action}",dependencies=[Depends(auth)])
    async def engine_control(action:Literal["start","stop","restart"]):
        async with service.control:
            if action in ("stop","restart"):
                service.db.set_setting("run_enabled",False)
                await service.engine.stop()
            if action in ("start","restart"):
                src=service.db.get_source(); targets=service.db.targets()
                if not src["channel"] or not any(t["enabled"] for t in targets): raise HTTPException(400,"مبدأ و حداقل یک مقصد را تنظیم کنید")
                if not src["history_enabled"] and not src["realtime_enabled"]: raise HTTPException(400,"آرشیو یا دریافت لحظه‌ای را فعال کنید")
                if src["mode"]=="bot" and src["history_enabled"] and not src["realtime_enabled"] and not any(t["enabled"] and t["provider"]=="telegram" for t in targets): raise HTTPException(400,"آرشیو BotFather فقط برای مقصد تلگرام امکان‌پذیر است")
                if src["mode"]=="bot" and src["history_enabled"] and any(t["enabled"] and t["provider"]!="telegram" for t in targets):
                    service.db.event("WARN","source","آرشیو BotFather به مقصدهای غیرتلگرام ارسال نمی‌شود؛ برای این مقصدها فقط پست جدید یا روش API ID/Hash")
                service.db.set_setting("run_enabled",True)
                service.engine.start()
            service.reset_ticket=None
            return {"ok":True,"running":service.engine.active}

    @app.post("/api/backup",dependencies=[Depends(auth)])
    async def backup():
        try:return {"ok":True,"path":service.db.backup()}
        except Exception as exc:raise safe_exception(exc) from None

    @app.post('/api/targets/{platform}/splus/{action}',dependencies=[Depends(auth)])
    async def splus_account(platform:str,action:Literal['code','verify','channels'],data:SplusLogin):
        async with service.control:
            if service.engine.active:raise HTTPException(409,'ابتدا موتور را متوقف کنید')
            try:
                d=service.db.target(platform)
                if d['provider']!='soroush' or d['archived']:raise ValueError('مقصد سروش‌پلاس لازم است')
                if action=='code':return await service.engine.splus.code(platform,data.phone)
                if action=='verify':return await service.engine.splus.verify(platform,data.code,data.password)
                return {'channels':await service.engine.splus.channels(platform)}
            except Exception as exc:raise safe_exception(exc) from None

    @app.post('/api/targets/{platform}/splus-select',dependencies=[Depends(auth)])
    async def splus_select(platform:str,data:SplusSelect):
        async with service.control:
            if service.engine.active:raise HTTPException(409,'ابتدا موتور را متوقف کنید')
            try:
                d=service.db.target(platform)
                if d['provider']!='soroush' or d['archived']:raise ValueError('مقصد سروش‌پلاس لازم است')
                await service.engine.splus.check(platform,data.chat_id)
                service.db.update_target(platform,{'token':marker(platform),'chat_id':data.chat_id,'enabled':False})
                return {'ok':True}
            except Exception as exc:raise safe_exception(exc) from None

    @app.post('/api/backups/export',dependencies=[Depends(auth)])
    async def export_backup(data:BackupIn):
        async with service.control:
            if data.mode=='full' and service.engine.active:raise HTTPException(409,'برای پشتیبان کامل ابتدا موتور را متوقف کنید')
            try:
                if data.mode=='full':await service.engine.collector.shutdown()
                path=backups.make_backup(root,data.password,data.mode)
                return FileResponse(path,media_type='application/octet-stream',filename=path.name)
            except Exception as exc:raise safe_exception(exc) from None

    @app.get('/api/backups',dependencies=[Depends(auth)])
    async def backup_list():
        cfg=service.db.get_setting('backup42_auto') or {}
        files=[{'name':p.name,'bytes':p.stat().st_size,'created':p.stat().st_mtime,'local_key':p.name.startswith(('pre-reset-','pre-upgrade-'))} for p in sorted((root/'backups').glob('*.rfbackup'),key=lambda p:p.stat().st_mtime,reverse=True) if not p.is_symlink()]
        return {'files':files,'auto_enabled':bool(cfg.get('enabled')),'auto_mode':cfg.get('mode','database'),'last':service.db.get_setting('backup42_last') or 0,'pending':(root/'restore_pending').exists()}

    @app.get('/api/backups/download/{name}',dependencies=[Depends(auth)])
    async def download_backup(name:str):
        p=root/'backups'/name
        if not name.endswith('.rfbackup') or p.name!=name or p.is_symlink() or not p.is_file():raise HTTPException(404)
        return FileResponse(p,media_type='application/octet-stream',filename=name)

    @app.post('/api/backups/auto',dependencies=[Depends(auth)])
    async def auto_backup(data:AutoBackupIn):
        async with service.control:
            try:
                cfg=service.db.get_setting('backup42_auto') or {}
                password=data.password or (service.db.reveal(cfg['password']) if cfg.get('password') else '')
                if data.enabled:backups.password_check(password)
                service.db.set_setting('backup42_auto',{'enabled':data.enabled,'mode':data.mode,'password':service.db.secret(password) if data.enabled else ''})
                return {'ok':True}
            except Exception as exc:raise safe_exception(exc) from None

    @app.post('/api/backups/restore',dependencies=[Depends(auth)])
    async def restore_backup(request:Request,mode:Literal['full','settings']='full',sessions:bool=True,media:bool=True,confirm:bool=False,x_backup_password:str=Header('')):
        if not confirm:raise HTTPException(400,'تأیید نهایی بازیابی لازم است')
        async with service.control:
            if service.engine.active:raise HTTPException(409,'ابتدا موتور را متوقف کنید')
            try:
                password=base64.b64decode(x_backup_password,validate=True).decode('utf8')
                content=bytearray()
                async for part in request.stream():
                    if len(content)+len(part)>backups.MAX_BYTES+128:raise ValueError('حجم پشتیبان بیش از حد مجاز')
                    content.extend(part)
                await service.engine.collector.shutdown()
                plan=backups.stage_restore(root,bytes(content),password,mode,sessions,media)
                service.db.set_setting('run_enabled',False)
                return {'ok':True,'restart_required':True,'rollback':plan['rollback']}
            except Exception as exc:raise safe_exception(exc) from None

    @app.get('/api/uncertain',dependencies=[Depends(auth)])
    async def uncertain_list():return service.db.uncertain_rows()

    @app.post('/api/uncertain/resolve',dependencies=[Depends(auth)])
    async def uncertain_resolve(data:BulkResolve):
        async with service.control:
            if service.engine.active:raise HTTPException(409,'ابتدا موتور را متوقف کنید')
            try:return {'ok':True,'resolved':service.db.resolve_uncertain_bulk(data.ids,data.action,data.all_rows)}
            except Exception as exc:raise safe_exception(exc) from None

    @app.post('/api/targets/{platform}/discover',dependencies=[Depends(auth)])
    async def discover(platform:str):
        async with service.control:
            if service.engine.active:raise HTTPException(409,'ابتدا موتور را متوقف کنید')
            try:
                d=service.db.target(platform)
                if d['archived'] or d['provider']!='rubika':raise ValueError('مقصد روبیکا انتخاب شود')
                rows=await discover_rubika(d['token'])
                return {'channels':rows,'note':'اگر کانالی دیده نشد، ربات را منشن کنید یا شناسه را دستی وارد کنید'}
            except Exception as exc:raise safe_exception(exc) from None

    @app.post("/api/reset/prepare",dependencies=[Depends(auth)])
    async def reset_prepare():
        if service.engine.active:raise HTTPException(409,"ابتدا موتور را متوقف کنید")
        ticket=secrets.token_urlsafe(32)
        service.reset_ticket=(ticket,time.monotonic()+60,service.db.get_setting("epoch"))
        return {"ticket":ticket,"uncertain":service.db.uncertain_rows()["total"]}

    @app.post("/api/reset/commit",dependencies=[Depends(auth)])
    async def reset_commit(data:ResetConfirm):
        async with service.control:
            ticket=service.reset_ticket;service.reset_ticket=None
            if service.engine.active or not ticket or not secrets.compare_digest(ticket[0],data.ticket) or time.monotonic()>ticket[1] or service.db.get_setting("epoch")!=ticket[2]:raise HTTPException(409,"تأیید منقضی یا نامعتبر است")
            try:
                path=backups.make_backup(root,backups.local_password(root),"database","pre-reset")
                if data.skip_uncertain:service.db.resolve_uncertain_bulk([], "skip", True)
                service.db.reset_operation()
                await service.engine.collector.shutdown()
                return {"ok":True,"backup":str(path)}
            except Exception as exc:raise safe_exception(exc) from None

    @app.post("/api/deliveries/{delivery_id}/resolve",dependencies=[Depends(auth)])
    async def resolve(delivery_id:int, data:ResolveIn):
        try:service.db.retry_or_resolve(delivery_id,data.action)
        except Exception as exc: raise safe_exception(exc) from None
        return {"ok":True}

    @app.get("/api/health")
    async def health():return {"status":"running","version":"4.3.7"}

    return app


def setup_logging(data:Path):
    (data/"logs").mkdir(parents=True,exist_ok=True)
    h=RotatingFileHandler(data/"logs"/"relayforge.log",encoding="utf-8",maxBytes=3_000_000,backupCount=5)
    h.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s"))
    logging.basicConfig(level=logging.INFO,handlers=[h,logging.StreamHandler(sys.stdout)],force=True)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("telethon").setLevel(logging.WARNING)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--no-browser",action="store_true")
    parser.add_argument("--data-dir",type=Path)
    parser.add_argument("--port",type=int,default=PORT)
    parser.add_argument("--self-test",action="store_true")
    parser.add_argument("--windows-service",action="store_true")
    opt=parser.parse_args()
    if opt.self_test:
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            app=create_app(Path(tmp),start_workers=False,port=opt.port)
            assert (web_root()/"index.html").is_file() and len(app.state.service.db.targets())==5
        print("SELF TEST OK 4.3.7"); return
    if not 1024<=opt.port<=65535:parser.error("port out of range")
    data=opt.data_dir or runtime_root()/"data"
    def run(stop=None):
        with FileLock(data/"instance.lock"):
            backups.apply_pending(data)
            backups.pre_upgrade(data,"4.3.7")
            setup_logging(data)
            app=create_app(data,port=opt.port)
            backups.mark_version(data,"4.3.7")
            print(f"RelayForge 4.3.7 | http://127.0.0.1:{opt.port}",flush=True)
            print("Dashboard password file:",data/"dashboard-key.txt",flush=True)
            import threading
            if not opt.no_browser and stop is None:
                threading.Timer(2.2,lambda:webbrowser.open(f"http://127.0.0.1:{opt.port}")).start()
            server=uvicorn.Server(uvicorn.Config(app,host="127.0.0.1",port=opt.port,workers=1,access_log=False,log_level="warning",timeout_graceful_shutdown=10))
            if stop is not None:
                def watcher():
                    stop.wait();server.should_exit=True
                threading.Thread(target=watcher,daemon=True).start()
            server.run()
    if opt.windows_service:
        from relayforge.windows_service import dispatch
        return dispatch("RelayForgeStudio",run)
    run()

if __name__=="__main__":sys.exit(main() or 0)
