import base64,json
import httpx,pytest
from fastapi.testclient import TestClient
from relayforge import media411 as m, ai436 as a, ai_settings as s
from relayforge.database import Database
from main import create_app
from test_v436 import png,mock
KEY='test-only-v411-key'
LITE='gemini-3.1-flash-lite-image'
PRO='gemini-3-pro-image'

@pytest.fixture
def db(tmp_path):
    d=Database(tmp_path);d.set_setting('ai43_enabled',True);s.save_key(d,'gemini','save',KEY)
    d.set_setting('ai44_catalog_gemini',{'fingerprint':s.fingerprint(KEY),'text':[],'tts':[],'image':[LITE,PRO],'loaded_at':1});return d

def quota(ident,value='10',delay='12.2s'):
    return {'error':{'code':429,'message':KEY,'details':[{'@type':'type.googleapis.com/google.rpc.QuotaFailure','violations':[{'quotaId':ident,'quotaValue':value}]},{'@type':'type.googleapis.com/google.rpc.RetryInfo','retryDelay':delay}]}}

def response():
    return httpx.Response(200,json={'candidates':[{'finishReason':'STOP','content':{'parts':[{'inlineData':{'mimeType':'image/png','data':base64.b64encode(png()).decode()}}]}}]})

@pytest.mark.parametrize('ident,value,expected',[('RequestsPerDayPerProject','1','روزانه'),('RequestsPerMinutePerModel','0','صفر'),('RequestsPerMinutePerModel','4','13 ثانیه'),('FreeTier','10','رایگان')])
def test_quota(ident,value,expected):
    result=m.quota_hint(json.dumps(quota(ident,value)),None)
    assert expected in result and KEY not in result

def test_quota_malformed():
    assert 'علت دقیق' in m.quota_hint(b'not json')
    assert 'صفر' in m.quota_hint(b'{"error":{"message":"limit: 0"}}')
    assert '20 ثانیه' in m.quota_hint(b'{}','20')

def test_same_key_catalog(db):
    s.save_key(db,'gemini','save',KEY);assert s.catalog(db,'gemini')['image']==[LITE,PRO]
    s.save_key(db,'gemini','save','test-other-key');assert not s.catalog(db,'gemini')

@pytest.mark.asyncio
@pytest.mark.parametrize('mode,expected',[('economy',LITE),('quality',PRO),('custom','custom-image-model'),('manual',PRO)])
async def test_modes(db,monkeypatch,mode,expected):
    seen=mock(monkeypatch,[response()]);out=await a.image(db,'topic',True,expected,'infographic','flat',model_mode=mode,aspect_ratio='16:9',image_size='2K',reference={'mime':'image/png','data':base64.b64encode(png()).decode()})
    req=json.loads(seen[0].content);assert out['model_used']==expected and out['kind']=='infographic' and out['published'] is False
    assert req['generationConfig']['imageConfig']=={'aspectRatio':'16:9','imageSize':'2K'}
    assert 'infographic' in req['contents'][0]['parts'][0]['text']
    assert req['contents'][0]['parts'][1]['inlineData']['mimeType']=='image/png'
    assert len(seen)==1 and db.get_setting('ai43_usage')['count']==1

@pytest.mark.asyncio
async def test_custom_no_catalog_and_no_consent(db,monkeypatch):
    db.set_setting('ai44_catalog_gemini',{});seen=mock(monkeypatch,[response()]);
    with pytest.raises(ValueError):await a.image(db,'topic',False,LITE,'image','flat',model_mode='custom')
    with pytest.raises(ValueError):await a.image(db,'topic',True,LITE,'image','flat',model_mode='manual')
    with pytest.raises(ValueError):await a.image(db,'topic',True,'../bad','image','flat',model_mode='custom')
    assert not seen
    await a.image(db,'topic',True,LITE,'image','flat',model_mode='custom');assert len(seen)==1

@pytest.mark.asyncio
async def test_bad_reference_before_charge(db,monkeypatch):
    seen=mock(monkeypatch,[])
    with pytest.raises(ValueError,match='مرجع'):await a.image(db,'topic',True,LITE,'image','flat',reference={'mime':'image/png','data':'YWJj'})
    assert not seen and not db.get_setting('ai43_usage')

@pytest.mark.asyncio
async def test_no_retry_429(db,monkeypatch):
    seen=mock(monkeypatch,[httpx.Response(429,json=quota('RequestsPerMinutePerModel'))])
    with pytest.raises(a.ProviderStatus,match='13 ثانیه') as exc:await a.image(db,'topic',True,'','image','flat',model_mode='economy')
    assert exc.value.status==429 and KEY not in str(exc.value) and len(seen)==1


def test_api_status_auth_body(tmp_path,monkeypatch):
    app=create_app(tmp_path,start_workers=False);db=app.state.service.db;db.set_setting('ai43_enabled',True);s.save_key(db,'gemini','save',KEY)
    seen=mock(monkeypatch,[httpx.Response(429,json=quota('RequestsPerDayPerProject','0'))]);h={'X-Studio-Key':app.state.service.admin_key,'Origin':'http://127.0.0.1:8765'}
    data={'text':'topic','model':LITE,'model_mode':'custom','consent':True}
    with TestClient(app,base_url=h['Origin'],client=('127.0.0.1',50000)) as c:
        assert c.post('/api/ai-lab/media',headers={'Origin':h['Origin']},json=data).status_code==401
        assert c.post('/api/ai-lab/media',headers={**h,'Origin':'https://evil.example'},json=data).status_code==403
        assert c.post('/api/ai-lab/media',headers=h,content=b'x'*(6*1024*1024+1)).status_code==413
        r=c.post('/api/ai-lab/media',headers=h,json=data);assert r.status_code==429 and 'صفر' in r.json()['detail'] and KEY not in r.text
        assert len(seen)==1
        html=c.get('/').text;js=c.get('/assets/main.js').text
        for i in ('media-refresh','media-mode','media-custom','media-ratio','media-size','media-reference'):assert f'id="{i}"' in html and i in js

def test_structural_review():
    q=m.structural_review('hello','hello\n#topic')
    assert q['lines']==2 and q['hashtags']==1 and not q['same_text'] and not q['semantic_verified']

def test_free_mode_structural_only(tmp_path):
    app=create_app(tmp_path,start_workers=False);h={'X-Studio-Key':app.state.service.admin_key,'Origin':'http://127.0.0.1:8765'}
    with TestClient(app,base_url=h['Origin'],client=('127.0.0.1',50000)) as c:
        r=c.post('/api/ai-lab/preview',headers=h,json={'text':'sample 12','profile':{},'fact_mode':'free'}).json()
        assert not r['fact_checked'] and not r['structure_review']['semantic_verified']
