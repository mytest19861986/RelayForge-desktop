"""Regression tests for v4.3.6 (mock remote responses, real local MP4)."""
import base64, json, struct, zlib, shutil
import httpx, pytest
from fastapi.testclient import TestClient
from relayforge import ai436 as a, ai_settings as s, gemini431 as old
from relayforge.database import Database
from main import create_app, FileLock
KEY='dummy-v436-test-key'
MODELS=['gemini-2.5-flash-lite','gemini-3.5-flash','gemini-3.1-pro-preview']
IMAGE='gemini-3.1-flash-image'
PROFILE={'brand':'','tone':'formal','destination':'','instructions':'','template':'{body}','locked':[]}
@pytest.fixture
def db(tmp_path):
    d=Database(tmp_path);d.set_setting('ai43_enabled',True);s.save_key(d,'gemini','save',KEY)
    d.set_setting('ai44_catalog_gemini',{'fingerprint':s.fingerprint(KEY),'text':MODELS,'tts':list(old.TTS_MODELS),'image':[IMAGE],'loaded_at':1});return d

def mock(monkeypatch,responses):
    seen=[];real=httpx.AsyncClient
    async def handler(req):
        seen.append(req);r=responses.pop(0)
        if isinstance(r,Exception):raise r
        return r
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kw:real(transport=httpx.MockTransport(handler),**kw))
    return seen

def response(data):
    return httpx.Response(200,json={'candidates':[{'finishReason':'STOP','content':{'parts':[{'text':json.dumps(data)}]}}]})
DRAFT={'body':'sample','title':'title','topic':'news','hashtags':''}

def test_rank(db):
    assert a.ordered(db,'economy','')==MODELS
    assert a.ordered(db,'quality','')==MODELS[::-1]
    assert a.ordered(db,'manual',MODELS[1])==[MODELS[1]]
    assert a.ordered(db,'quality','',1)==MODELS[::-1][:1]

@pytest.mark.asyncio
@pytest.mark.parametrize('mode,expected',[('economy',MODELS),('quality',MODELS[::-1])])
async def test_fallback(db,monkeypatch,mode,expected):
    async def sleep(_):pass
    monkeypatch.setattr(a.asyncio,'sleep',sleep)
    req=mock(monkeypatch,[httpx.Response(503),httpx.Response(404),response(DRAFT)])
    r=await a.preview(db,'sample',PROFILE,True,'',mode,6,True)
    assert r['output']=='sample' and r['model_used']==expected[-1] and not r['published']
    assert [x['model'] for x in r['attempts']]==expected
    assert all(x.url.path.endswith(m+':generateContent') for x,m in zip(req,expected))
    assert all(KEY not in str(x.url) and x.headers['x-goog-api-key']==KEY for x in req)
    assert 'Unicode emoji' in json.loads(req[-1].content)['contents'][0]['parts'][0]['text']
    assert db.get_setting('ai43_usage')['count']==3 and db.snapshot()['total_posts']==0

@pytest.mark.asyncio
@pytest.mark.parametrize('status',[400,401,403,429,500])
async def test_stop_on_other_errors(db,monkeypatch,status):
    req=mock(monkeypatch,[httpx.Response(status,text=KEY)])
    with pytest.raises(ValueError,match=f'HTTP {status}') as exc:await a.preview(db,'sample',PROFILE,True,'','economy',6,False)
    assert len(req)==1 and KEY not in str(exc.value)

@pytest.mark.asyncio
async def test_no_consent_and_quota(db,monkeypatch):
    req=mock(monkeypatch,[httpx.Response(404)])
    with pytest.raises(ValueError):await a.preview(db,'sample',PROFILE,False,'','economy',6,False)
    assert not req;db.set_setting('ai44_daily_limit',1)
    with pytest.raises(ValueError,match='سقف'):await a.preview(db,'sample',PROFILE,True,'','economy',6,False)
    assert len(req)==1

@pytest.mark.asyncio
async def test_diagnostics_real_json(db,monkeypatch):
    req=mock(monkeypatch,[response({'result':'OK'}),httpx.Response(503),response({'result':'NO'})])
    r=[await a.diagnose(db,m,True) for m in MODELS]
    assert [x['ok'] for x in r]==[True,False,False] and [x['status'] for x in r]==[200,503,200]
    assert all('responseSchema' in json.loads(x.content)['generationConfig'] for x in req)
    assert db.get_setting('ai43_usage')['count']==3

@pytest.mark.asyncio
async def test_timeout_not_fallback(db,monkeypatch):
    req=mock(monkeypatch,[httpx.ReadTimeout(KEY)])
    with pytest.raises(ValueError,match='نامشخص'):await a.preview(db,'sample',PROFILE,True,'','economy',6,False)
    assert len(req)==1

@pytest.mark.asyncio
async def test_retry_after_and_limit(db,monkeypatch):
    req=mock(monkeypatch,[httpx.Response(503,headers={'Retry-After':'120'})])
    with pytest.raises(ValueError,match='طولانی'):await a.preview(db,'sample',PROFILE,True,'','quality',6,False)
    assert len(req)==1

@pytest.mark.asyncio
async def test_protect_facts(db,monkeypatch):
    mock(monkeypatch,[response({**DRAFT,'body':'قیمت ۲ تومان'})])
    r=await a.preview(db,'قیمت ۱۰ تومان',PROFILE,True,MODELS[0],'manual',6,False)
    assert r['blocked'] and r['output']=='قیمت ۱۰ تومان'

@pytest.mark.asyncio
async def test_podcast_router(db,monkeypatch):
    mock(monkeypatch,[response({'script':'Host: سلام\nGuest: درود'})])
    r=await a.podcast(db,'سلام',True,'','quality',6)
    assert r['model_used']==MODELS[-1] and not r['published'] and not r['blocked']

def png():
    def chunk(tag,data):return struct.pack('>I',len(data))+tag+data+struct.pack('>I',zlib.crc32(tag+data)&0xffffffff)
    return b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',16,16,8,2,0,0,0))+chunk(b'IDAT',zlib.compress((b'\0'+b'\x77\x44\xaa'*16)*16))+chunk(b'IEND',b'')

@pytest.mark.asyncio
@pytest.mark.parametrize('kind',['image','sticker','clip'])
async def test_media(db,monkeypatch,kind):
    if kind=='clip' and not shutil.which('ffmpeg'):pytest.skip('optional ffmpeg unavailable')
    data=base64.b64encode(png()).decode();req=mock(monkeypatch,[httpx.Response(200,json={'candidates':[{'finishReason':'STOP','content':{'parts':[{'inlineData':{'mimeType':'image/png','data':data}}]}}]})])
    r=await a.image(db,'خبر آموزشی',True,IMAGE,kind,'illustration');raw=base64.b64decode(r['media'])
    assert not r['published'] and r['kind']==kind and db.get_setting('ai43_usage')['count']==1
    assert raw[4:8]==b'ftyp' if kind=='clip' else raw==png()
    assert req[0].url.path.endswith(IMAGE+':generateContent')

@pytest.mark.asyncio
async def test_media_reject_missing_ffmpeg(db,monkeypatch):
    monkeypatch.setattr(a.shutil,'which',lambda _:None);req=mock(monkeypatch,[])
    with pytest.raises(ValueError,match='FFmpeg'):await a.image(db,'test',True,IMAGE,'clip','test')
    assert not req

def test_project_isolation(tmp_path):
    a1=create_app(tmp_path/'one',start_workers=False,port=8766);a2=create_app(tmp_path/'two',start_workers=False,port=8767)
    s1=a1.state.service;s2=a2.state.service
    s.save_key(s1.db,'gemini','save',KEY);assert s.credential(s2.db,'gemini')[1]!='saved'
    assert s1.admin_key!=s2.admin_key
    with FileLock(tmp_path/'one'/'instance.lock'), FileLock(tmp_path/'two'/'instance.lock'):
        with pytest.raises(RuntimeError):
            with FileLock(tmp_path/'one'/'instance.lock'):pass
    for app,port in ((a1,8766),(a2,8767)):
        base=f'http://127.0.0.1:{port}';h={'Origin':base,'X-Studio-Key':app.state.service.admin_key}
        with TestClient(app,base_url=base,client=('127.0.0.1',50000)) as c:
            assert c.get('/api/health').json()['version']=='4.3.11'
            assert c.post('/api/ai-lab/preferences',headers=h,json={'model_mode':'quality','max_models':3}).status_code==200
            assert c.get('/api/ai-lab',headers=h).json()['preferences']['model_mode']=='quality'
            assert 'v4.3.11' in c.get('/').text
