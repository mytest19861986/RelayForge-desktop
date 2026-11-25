"""Offline regression tests for bounded explicit HTTP 503 retries."""
import base64
import json
import httpx
import pytest
from relayforge import ai43, ai_settings, gemini431 as g
from relayforge.database import Database

KEY='dummy-gemini-key-test'
PROFILE={'brand':'','tone':'formal','destination':'','instructions':'','template':'{body}','locked':[]}
MODEL=g.TEXT_MODELS[0]

@pytest.fixture
def ctx(tmp_path,monkeypatch):
    db=Database(tmp_path);db.set_setting('ai43_enabled',True)
    ai_settings.save_key(db,'gemini','save',KEY)
    waits=[];requests=[]
    real=httpx.AsyncClient
    async def sleep(delay):waits.append(delay)
    monkeypatch.setattr(g.asyncio,'sleep',sleep)
    monkeypatch.setattr(g.random,'uniform',lambda *_:0)
    def setup(responses):
        async def handler(req):
            requests.append(req)
            result=responses.pop(0)
            if isinstance(result,Exception):raise result
            if callable(result):return result(req)
            return result
        monkeypatch.setattr(httpx,'AsyncClient',lambda **kw:real(transport=httpx.MockTransport(handler),**kw))
    return db,waits,requests,setup

def response(data=None):
    return httpx.Response(200,json={'status':'completed','steps':[{'type':'model_output','content':[{'type':'text','text':json.dumps(data or {'body':'sample','title':'title','topic':'news','hashtags':''})}]}]})

async def preview(db):return await ai43.preview(db,'sample',PROFILE,'gemini',True,MODEL)

@pytest.mark.asyncio
async def test_503_recovery_preserves_model_and_budget(ctx):
    db,waits,requests,setup=ctx;setup([httpx.Response(503),httpx.Response(503),response()])
    r=await preview(db)
    assert r['output']=='sample' and not r['published'] and waits==[2,4]
    assert db.get_setting('ai43_usage')['count']==3 and len(requests)==3
    assert len({req.content for req in requests})==1
    assert all(json.loads(req.content)['model']==MODEL and req.headers['x-goog-api-key']==KEY for req in requests)
    assert db.snapshot()['total_posts']==0

@pytest.mark.asyncio
async def test_503_exhaustion(ctx):
    db,waits,requests,setup=ctx;setup([httpx.Response(503,text=KEY)]*3)
    with pytest.raises(ValueError,match='HTTP 503.*3') as e:await preview(db)
    assert KEY not in str(e.value) and len(requests)==3 and waits==[2,4]
    assert db.get_setting('ai43_usage')['count']==3

@pytest.mark.asyncio
async def test_budget_blocks_retry(ctx):
    db,waits,requests,setup=ctx;db.set_setting('ai44_daily_limit',1);setup([httpx.Response(503)])
    with pytest.raises(ValueError,match='HTTP 503.*سقف'):await preview(db)
    assert len(requests)==1 and db.get_setting('ai43_usage')['count']==1

@pytest.mark.asyncio
@pytest.mark.parametrize('status',[400,401,403,404,429,500,502,504])
async def test_other_http_errors_not_retried(ctx,status):
    db,waits,requests,setup=ctx;setup([httpx.Response(status,text=KEY)])
    with pytest.raises(ValueError,match=f'HTTP {status}') as e:await preview(db)
    assert KEY not in str(e.value) and len(requests)==1 and not waits
    assert db.get_setting('ai43_usage')['count']==1

@pytest.mark.asyncio
async def test_unknown_timeout_not_retried(ctx):
    db,waits,requests,setup=ctx;setup([httpx.ReadTimeout(KEY)])
    with pytest.raises(ValueError,match='نامشخص') as e:await preview(db)
    assert KEY not in str(e.value) and len(requests)==1 and not waits

@pytest.mark.asyncio
async def test_retry_after(ctx):
    db,waits,requests,setup=ctx;setup([httpx.Response(503,headers={'Retry-After':'7'}),response()])
    assert (await preview(db))['output']=='sample' and waits==[7] and len(requests)==2

@pytest.mark.asyncio
async def test_long_retry_after_does_not_retry(ctx):
    db,waits,requests,setup=ctx;setup([httpx.Response(503,headers={'Retry-After':'60'})])
    with pytest.raises(ValueError,match='زمان انتظار طولانی'):await preview(db)
    assert len(requests)==1 and not waits

@pytest.mark.asyncio
async def test_permission_revocation_during_retry(ctx):
    db,waits,requests,setup=ctx
    def revoke(req):
        db.set_setting('ai43_enabled',False);return httpx.Response(503)
    setup([revoke])
    with pytest.raises(ValueError,match='فعال'):await preview(db)
    assert len(requests)==1 and db.get_setting('ai43_usage')['count']==1

@pytest.mark.asyncio
async def test_podcast_retry(ctx):
    db,waits,requests,setup=ctx;setup([httpx.Response(503),response({'script':'Host: سلام\nGuest: درود'})])
    result=await g.podcast_script(db,'سلام',True,MODEL)
    assert result['script'].startswith('Host:') and not result['published'] and len(requests)==2

@pytest.mark.asyncio
async def test_speech_retry(ctx):
    db,waits,requests,setup=ctx
    audio=httpx.Response(200,json={'status':'completed','steps':[{'type':'model_output','content':[{'type':'audio','mime_type':'audio/L16;rate=24000','data':base64.b64encode(b'\0\0'*100).decode()}]}]})
    setup([httpx.Response(503),audio])
    r=await g.speech(db,'سلام',True,g.TTS_MODELS[0],'narration','Kore','Puck','neutral')
    assert base64.b64decode(r['audio']).startswith(b'RIFF') and not r['published'] and len(requests)==2
