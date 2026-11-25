"""RelayForge 4.0 | FastAPI local dashboard & Windows VPS runtime."""
from __future__ import annotations
import argparse, asyncio, contextlib, logging, os, secrets, sys, webbrowser, time
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Literal
from fastapi import FastAPI, Request, Depends, Header, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field
import uvicorn
from relayforge.database import Database, PLATFORMS
from relayforge.engine import Engine
from relayforge.source import SourceUnavailable
from relayforge.gateways import check_target, Rejected, Uncertain, RetryLater

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

class TargetSettings(BaseModel):
    model_config=ConfigDict(extra="forbid")
    token:str=""
    chat_id:str=Field(default="",max_length=200)
    enabled:bool=False
    interval_seconds:int=Field(default=0,ge=0,le=604800)
    include_backlog:bool=False

class NewTarget(BaseModel):
    provider:Literal["telegram","bale","rubika","eitaa","soroush"]

class ResetConfirm(BaseModel):
    ticket:str

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
        self.reset_ticket=None

    def authenticate(self,key:str|None):
        return isinstance(key,str) and secrets.compare_digest(key,self.admin_key)


def create_app(data_root:Path|None=None, start_workers=True, port=PORT) -> FastAPI:
    root = Path(data_root or (runtime_root()/"data"))
    service=Application(root)

    @contextlib.asynccontextmanager
    async def lifespan(app):
        if start_workers and service.db.get_setting("run_enabled"): service.engine.start()
        yield
        await service.engine.stop()

    app=FastAPI(title="RelayForge Control API",version="4.0.0",docs_url=None,redoc_url=None,openapi_url=None,lifespan=lifespan)
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
        result=await call_next(request)
        result.headers.update({
            "X-Content-Type-Options":"nosniff", "X-Frame-Options":"DENY",
            "Referrer-Policy":"no-referrer", "Cache-Control":"no-store",
            "Content-Security-Policy":"default-src 'self'; connect-src 'self'; img-src 'self' data:; script-src 'self'; style-src 'self'; frame-ancestors 'none'; form-action 'self'",
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

    @app.get("/api/status",dependencies=[Depends(auth)])
    async def status():return service.engine.status()

    @app.post("/api/source",dependencies=[Depends(auth)])
    async def source_save(data:SourceSettings):
        if service.engine.active: raise HTTPException(409,"ابتدا موتور را متوقف کنید")
        try:service.db.update_source(data.model_dump())
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
        if platform not in [t["platform"] for t in service.db.targets()]:raise HTTPException(404,"مقصد نامعتبر")
        if service.engine.active: raise HTTPException(409,"ابتدا موتور را متوقف کنید")
        try:service.db.update_target(platform,data.model_dump())
        except Exception as exc:raise safe_exception(exc) from None
        return {"ok":True}

    @app.post("/api/targets/{platform}/check",dependencies=[Depends(auth)])
    async def target_check(platform:str):
        if platform not in [t["platform"] for t in service.db.targets()]:raise HTTPException(404)
        try:
            d=service.db.target(platform)
            return await check_target(d["provider"],d["token"],d["chat_id"])
        except Exception as exc: raise safe_exception(exc) from None

    @app.post("/api/targets",dependencies=[Depends(auth)])
    async def target_add(data:NewTarget):
        if service.engine.active: raise HTTPException(409,"ابتدا موتور را متوقف کنید")
        try:return {"ok":True,"route":service.db.add_target(data.provider)}
        except Exception as exc:raise safe_exception(exc) from None

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
                service.db.set_setting("run_enabled",True)
                service.engine.start()
            service.reset_ticket=None
            return {"ok":True,"running":service.engine.active}

    @app.post("/api/backup",dependencies=[Depends(auth)])
    async def backup():
        try:return {"ok":True,"path":service.db.backup()}
        except Exception as exc:raise safe_exception(exc) from None

    @app.post("/api/reset/prepare",dependencies=[Depends(auth)])
    async def reset_prepare():
        if service.engine.active:raise HTTPException(409,"ابتدا موتور را متوقف کنید")
        ticket=secrets.token_urlsafe(32)
        service.reset_ticket=(ticket,time.monotonic()+60,service.db.get_setting("epoch"))
        return {"ticket":ticket}

    @app.post("/api/reset/commit",dependencies=[Depends(auth)])
    async def reset_commit(data:ResetConfirm):
        async with service.control:
            ticket=service.reset_ticket;service.reset_ticket=None
            if service.engine.active or not ticket or not secrets.compare_digest(ticket[0],data.ticket) or time.monotonic()>ticket[1] or service.db.get_setting("epoch")!=ticket[2]:raise HTTPException(409,"تأیید منقضی یا نامعتبر است")
            try:
                path=service.db.backup()
                service.db.reset_operation()
                await service.engine.collector.shutdown()
                return {"ok":True,"backup":path}
            except Exception as exc:raise safe_exception(exc) from None

    @app.post("/api/deliveries/{delivery_id}/resolve",dependencies=[Depends(auth)])
    async def resolve(delivery_id:int, data:ResolveIn):
        try:service.db.retry_or_resolve(delivery_id,data.action)
        except Exception as exc: raise safe_exception(exc) from None
        return {"ok":True}

    @app.get("/api/health")
    async def health():return {"status":"running","version":"4.0.0"}

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
        print("SELF TEST OK 4.0.0"); return
    if not 1024<=opt.port<=65535:parser.error("port out of range")
    data=opt.data_dir or runtime_root()/"data"
    def run(stop=None):
        with FileLock(data/"instance.lock"):
            setup_logging(data)
            app=create_app(data,port=opt.port)
            print(f"RelayForge 4.0 | http://127.0.0.1:{opt.port}",flush=True)
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
