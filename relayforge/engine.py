"""Fail-isolated asynchronous delivery workers and source collector."""
from __future__ import annotations
import asyncio, logging
from .database import Database, PLATFORMS
from .gateways import Post, send, Rejected, Uncertain, NotSupported
from .source import Collector, SourceUnavailable

log=logging.getLogger("relayforge.engine")

class Engine:
    def __init__(self,db:Database):
        self.db=db
        self.collector=Collector(db)
        self.active=False
        self.tasks=[]

    def start(self):
        if self.active: return
        self.active=True
        self.tasks=[asyncio.create_task(self._source_loop(),name="relay-source")]
        self.tasks.extend(asyncio.create_task(self._target_loop(p),name="relay-"+p) for p in PLATFORMS)

    async def stop(self):
        self.active=False
        for t in self.tasks:t.cancel()
        await asyncio.gather(*self.tasks,return_exceptions=True)
        await self.collector.shutdown()

    async def _source_loop(self):
        while self.active:
            try:
                await self.collector.tick()
            except asyncio.CancelledError: raise
            except Exception:
                log.exception("Telegram source loop encountered an error")
                self.db.event("WARN","source","خطای موقت در گردآوری؛ بدون حذف پیشرفت، تلاش دوباره")
                await asyncio.sleep(10)

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
                log.exception("Destination worker crashed: %s", platform)
                # Only this destination's in-flight attempt is affected.
                # Never mark another worker's live send uncertain.
                if job:
                    try:self.db.finish_delivery(job["id"],platform,"uncertain",detail="خطای داخلی کارگر پس از شروع ارسال")
                    except Exception:pass
                await asyncio.sleep(6)

    async def _deliver(self,platform,job):
        did=job["id"]
        interval=job["interval_seconds"]
        try:
            if job["protected"]: raise Rejected("محتوای محافظت‌شده منتشر نمی‌شود")
            path=await self.collector.get_media(job)
            remote=await send(platform,job["token"],job["chat_id"],Post(job["body"],job["media_kind"],path))
        except SourceUnavailable as exc:
            if job["tries"] >= 2:
                self.db.finish_delivery(did,platform,"failed",detail="دریافت محتوای مبدأ پس از چند تلاش ناموفق بود",interval=interval)
            else:
                self.db.defer_delivery(did,platform,45,"دریافت رسانه از تلگرام موقتاً ناموفق بود")
            return
        except (Rejected,NotSupported) as exc:
            self.db.finish_delivery(did,platform,"failed",detail=str(exc),interval=interval)
            return
        except Uncertain as exc:
            self.db.finish_delivery(did,platform,"uncertain",detail=str(exc),interval=interval)
            return
        except Exception:
            log.exception("Unexpected error in delivery %s", did)
            self.db.finish_delivery(did,platform,"uncertain",detail="خطای غیرمنتظره بعد از شروع ارسال؛ بررسی مقصد لازم است",interval=interval)
            return
        self.db.finish_delivery(did,platform,"sent",remote_id=str(remote),interval=interval)

    def status(self):
        result=self.db.snapshot()
        result.update({"version":"3.0.2","source_state":self.collector.state,"engine_running":self.active})
        return result
