"""Fail-isolated asynchronous delivery workers and source collector."""
from __future__ import annotations
import asyncio, logging
from .database import Database, PLATFORMS, provider
from .gateways import Post, send, Rejected, Uncertain, NotSupported, RetryLater, copy_telegram, check_target
from .source import Collector, SourceUnavailable
from .splus_user import Accounts, is_user, marker
from .richtext import load, render, DEFAULTS

log=logging.getLogger("relayforge.engine")

class Engine:
    def __init__(self,db:Database):
        self.db=db
        self.collector=Collector(db)
        self.splus=Accounts(db.root)
        self.active=False
        self.tasks=[]
        self.workers={}
        self.route_cache={}

    def start(self):
        if self.active: return
        self.active=True
        self.tasks=[asyncio.create_task(self._source_loop(),name="relay-source")]
        self.tasks.append(asyncio.create_task(self._supervise(),name="relay-supervisor"))

    async def stop(self):
        self.active=False
        for t in self.tasks:t.cancel()
        await asyncio.gather(*self.tasks,return_exceptions=True)
        for t in self.workers.values(): t.cancel()
        await asyncio.gather(*self.workers.values(),return_exceptions=True)
        self.workers.clear()
        self.db.recover()
        await self.collector.shutdown()

    async def _supervise(self):
        while self.active:
            for t in self.db.targets():
                p=t["platform"]
                if p not in self.workers or self.workers[p].done(): self.workers[p]=asyncio.create_task(self._target_loop(p))
            await asyncio.sleep(1)

    async def _source_loop(self):
        while self.active:
            try:
                await self.collector.tick()
            except asyncio.CancelledError: raise
            except Exception:
                log.warning("Telegram source loop encountered an error")
                self.db.event("WARN","source","خطای موقت در گردآوری؛ بدون حذف پیشرفت، تلاش دوباره")
                await asyncio.sleep(30)

    async def _target_loop(self,platform):
        while self.active:
            job=None
            try:
                job=self.db.next_delivery(platform)
                if job:
                    await self._deliver(platform,job)
                else:
                    await asyncio.sleep(1.2)
            except asyncio.CancelledError: raise
            except Exception:
                log.warning("Destination worker crashed: %s", platform)
                # Only this destination's in-flight attempt is affected.
                # Never mark another worker's live send uncertain.
                if job:
                    try:self.db.finish_delivery(job["id"],platform,"uncertain",detail="خطای داخلی کارگر پس از شروع ارسال")
                    except Exception:pass
                await asyncio.sleep(30)

    async def _deliver(self,platform,job):
        did=job["id"]
        interval=job["interval_seconds"]
        self.db.event("INFO",platform,f"شروع ارسال #{did} | پست مبدأ #{job['msg_id']} | نوع {job['media_kind']}")
        try:
            if job["protected"]: raise Rejected("محتوای محافظت‌شده منتشر نمی‌شود")
            if job["media_kind"]=="copy":
                if provider(platform)!="telegram": raise NotSupported("آرشیو BotFather فقط تلگرام")
                remote=await copy_telegram(job["token"],job["media_ref"],job["chat_id"],job["msg_id"])
            else:
                peer=self.db.get_setting("source_peer")
                if provider(platform)=="telegram" and peer:
                    key=(platform,job["token"],job["chat_id"],peer)
                    if key not in self.route_cache:
                        try: info=await check_target("telegram",job["token"],job["chat_id"])
                        except Uncertain: raise RetryLater(30) from None
                        if info["chat_id"]==str(peer): raise Rejected("کانال مقصد همان مبدأ است")
                        self.route_cache={k:v for k,v in self.route_cache.items() if k[0]!=platform}
                        self.route_cache[key]=True
                try: path=await self.collector.get_media(job)
                except SourceUnavailable: raise
                except Exception as exc:
                    self.db.defer_delivery(did,platform,30,"خطای دریافت رسانه پیش از ارسال: "+type(exc).__name__)
                    return
                spans=load(job["body"],job.get("entities","[]"))
                mode=(self.db.get_setting("rich43_modes") or {}).get(provider(platform),DEFAULTS.get(provider(platform),"plain"))
                formatted,options,warnings=render(provider(platform),job["body"],spans,mode,bool(path))
                if provider(platform)=="eitaa":
                    method="sendFile" if path else "sendMessage"
                    bold=sum(x["type"]=="bold" for x in spans)
                    self.db.event("INFO",platform,f"#{did} | {method} | mode={mode} | parse_mode={options.get('parse_mode','none')} | bold={bold} | tags={formatted.count('<b>')} | chars={len(formatted)}")
                for warning in warnings:self.db.event("WARN",platform,f"#{did}: {warning}")
                post=Post(job["body"],job["media_kind"],path,tuple(spans),mode)
                if provider(platform)=="soroush" and is_user(job["token"]):
                    if job["token"]!=marker(platform):raise Rejected("نشست مقصد سروش نامعتبر")
                    remote=await self.splus.send(platform,job["chat_id"],post)
                else:remote=await send(provider(platform),job["token"],job["chat_id"],post)
        except RetryLater as exc:
            self.db.defer_delivery(did,platform,exc.delay,"اتصال موقت یا محدودیت نرخ؛ تلاش مجدد امن")
            return
        except SourceUnavailable as exc:
            self.db.defer_delivery(did,platform,30,"دریافت رسانه: "+str(exc))
            return
        except (Rejected,NotSupported) as exc:
            self.db.finish_delivery(did,platform,"failed",detail=str(exc),interval=interval)
            return
        except Uncertain as exc:
            self.db.finish_delivery(did,platform,"uncertain",detail=str(exc),interval=interval)
            return
        except Exception:
            log.warning("Unexpected error in delivery %s", did)
            self.db.finish_delivery(did,platform,"uncertain",detail="خطای غیرمنتظره بعد از شروع ارسال؛ بررسی مقصد لازم است",interval=interval)
            return
        self.db.finish_delivery(did,platform,"sent",remote_id=str(remote),interval=interval)

    def status(self):
        result=self.db.snapshot()
        result.update({"version":"4.3.3","source_state":self.collector.state,"engine_running":self.active})
        return result
