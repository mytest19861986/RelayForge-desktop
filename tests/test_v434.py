"""Regression coverage for encrypted AI keys and live model catalog (mock HTTP)."""
import json
import httpx
import pytest
from fastapi.testclient import TestClient
from relayforge import ai_settings as a, ai43, gemini431 as g, backup42 as backup
from relayforge.database import Database
from main import create_app

KEY='dummy-api-key-for-offline-test'
PROFILE={'brand':'','tone':'formal','destination':'','instructions':'','template':'{body}','locked':[]}

def enabled(tmp_path):
    db=Database(tmp_path);db.set_setting('ai43_enabled',True);return db

def mock_http(monkeypatch,handler):
    original=httpx.AsyncClient
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kw:original(transport=httpx.MockTransport(handler),**kw))

def test_key_encrypted_and_remove(tmp_path,monkeypatch):
    db=enabled(tmp_path);monkeypatch.setenv('RELAYFORGE_AI_API_KEY','env-test-key')
    a.save_key(db,'openai','save',KEY);assert a.credential(db,'openai')==(KEY,'saved')
    assert db.get_setting('ai44_key_openai').startswith('enc:') and KEY not in db.get_setting('ai44_key_openai')
    assert KEY not in json.dumps(a.public(db))
    a.save_key(db,'openai','remove','');assert a.credential(db,'openai')==('env-test-key','environment')

@pytest.mark.asyncio
async def test_openai_online_catalog_and_preview(tmp_path,monkeypatch):
    db=enabled(tmp_path);a.save_key(db,'openai','save',KEY)
    async def handler(req):
        assert req.headers['authorization']=='Bearer '+KEY
        if req.method=='GET':
            assert str(req.url)=='https://api.openai.com/v1/models'
            return httpx.Response(200,json={'data':[{'id':i} for i in ('gpt-test-model','gpt-image-1','gpt-4o-mini-tts','o3','text-embedding-3-small')]})
        assert json.loads(req.content)['model']=='gpt-test-model'
        return httpx.Response(200,json={'choices':[{'message':{'content':json.dumps({'body':'sample','title':'title','topic':'news','hashtags':''})}}]})
    mock_http(monkeypatch,handler);r=await a.refresh(db,'openai');assert r['text']==['gpt-test-model','o3'] and r['online']
    assert not db.get_setting('ai43_usage')
    out=await ai43.preview(db,'sample',PROFILE,'openai',True,'gpt-test-model');assert not out['published'] and out['output']=='sample'
    with pytest.raises(ValueError):await ai43.preview(db,'sample',PROFILE,'openai',True,'gpt-4o-mini')
    a.save_key(db,'openai','save','replacement-key');assert not a.public(db)['openai']['online']

@pytest.mark.asyncio
async def test_gemini_pages_and_dynamic_selection(tmp_path,monkeypatch):
    db=enabled(tmp_path);a.save_key(db,'gemini','save',KEY);calls=[]
    def entry(n,m=('generateContent',)):return {'name':'models/'+n,'supportedGenerationMethods':list(m)}
    async def handler(req):
        assert req.headers['x-goog-api-key']==KEY and KEY not in str(req.url)
        calls.append(req)
        if req.method=='GET':
            if req.url.params.get('pageToken')=='next':return httpx.Response(200,json={'models':[entry('gemini-new-tts'),entry('gemini-live-audio'),entry('gemini-image')]})
            return httpx.Response(200,json={'models':[entry('gemini-new-text'),entry('gemini-embedding',('embedContent',))],'nextPageToken':'next'})
        assert json.loads(req.content)['model']=='gemini-new-text'
        return httpx.Response(200,json={'status':'completed','steps':[{'type':'model_output','content':[{'type':'text','text':json.dumps({'body':'sample','title':'title','topic':'news','hashtags':''})}]}]})
    mock_http(monkeypatch,handler);r=await a.refresh(db,'gemini');assert r['text']==['gemini-new-text'] and r['tts']==['gemini-new-tts'];assert len(calls)==2
    out=await ai43.preview(db,'sample',PROFILE,'gemini',True,'gemini-new-text');assert out['output']=='sample' and not out['published']

@pytest.mark.asyncio
async def test_disabled_and_failed_refresh(tmp_path,monkeypatch):
    db=Database(tmp_path);a.save_key(db,'gemini','save',KEY);calls=[]
    async def handler(req):calls.append(req);return httpx.Response(403,text=KEY)
    mock_http(monkeypatch,handler)
    with pytest.raises(ValueError):await a.refresh(db,'gemini')
    assert not calls
    db.set_setting('ai43_enabled',True)
    with pytest.raises(ValueError,match='403') as e:await a.refresh(db,'gemini')
    assert KEY not in str(e.value) and not a.public(db)['gemini']['online']

def test_restore_rekeys_ai_credentials(tmp_path):
    src=enabled(tmp_path/'src');a.save_key(src,'openai','save',KEY);a.save_key(src,'gemini','save',KEY+'-g')
    src.set_setting('ai44_catalog_openai',{'fingerprint':a.fingerprint(KEY),'text':['gpt-test'],'tts':[],'loaded_at':1})
    blob=backup.make_backup(src.root,'test-password-long','settings').read_bytes();dest=Database(tmp_path/'dest')
    backup.stage_restore(dest.root,blob,'test-password-long','settings');backup.apply_pending(dest.root);db=Database(dest.root)
    assert a.credential(db,'openai')[0]==KEY and a.credential(db,'gemini')[0]==KEY+'-g'
    assert not db.get_setting('ai43_enabled') and not a.public(db)['openai']['online']
    assert db.get_setting('ai44_key_openai')!=src.get_setting('ai44_key_openai')

def test_settings_api_auth_and_limit(tmp_path):
    app=create_app(tmp_path,start_workers=False);base='http://127.0.0.1:8765';h={'X-Studio-Key':app.state.service.admin_key,'Origin':base}
    with TestClient(app,base_url=base,client=('127.0.0.1',50000)) as c:
        assert c.post('/api/ai-lab/key/openai',headers={'Origin':base},json={'key':KEY}).status_code==401
        assert c.post('/api/ai-lab/key/openai',headers=h,json={'key':KEY}).status_code==200
        r=c.get('/api/ai-lab',headers=h);assert KEY not in r.text and r.json()['providers']['openai']['key_source']=='saved'
        assert c.post('/api/ai-lab/models/openai',headers=h,json={}).status_code==400
        assert c.post('/api/ai-lab/preferences',headers=h,json={'provider':'openai','openai_model':'gpt-4o-mini','daily_limit':1}).status_code==200
        db=app.state.service.db;assert a.daily_limit(db)==1;g.reserve(db)
        with pytest.raises(ValueError,match='سقف'):g.reserve(db)
        assert c.get('/api/ai-lab',headers=h).json()['preferences']['openai_model']=='gpt-4o-mini'
        assert db.snapshot()['total_posts']==0
