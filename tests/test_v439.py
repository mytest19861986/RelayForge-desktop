"""Four fact modes: actual preview API, mocked external providers, no publication."""
import json
import pytest
import httpx
from fastapi.testclient import TestClient
from relayforge import ai43 as a, ai436 as g, ai_settings as settings, gemini431
from main import create_app
from test_v436 import mock, response, MODELS, KEY
PROFILE={'brand':'','tone':'formal','destination':'','instructions':'','template':'{body}','locked':['علی']}
SOURCE='علی؛ قیمت ۲۵۰ هزار تومان؛ https://example.com'
CHANGED='حسن؛ قیمت ۵۰۰ تومان؛ https://other.example'
DRAFT={'body':CHANGED,'title':'تیتر','topic':'خبر','hashtags':''}

@pytest.mark.parametrize('mode,blocked,warning,checked',[('strict',True,True,True),('balanced',False,True,True),('creative',False,True,True),('free',False,False,False)])
def test_modes(mode,blocked,warning,checked):
    r=a.draft_result(SOURCE,CHANGED,CHANGED,PROFILE,mode)
    assert r['blocked']==blocked and r['fact_warning']==warning and r['fact_checked']==checked
    assert r['output']==(SOURCE if blocked else CHANGED)
    assert not r['published'] and r['review_required'] and r['candidate']==CHANGED
    if mode=='free':assert r['missing']==r['added']==[]
    else:assert 'علی' in r['missing']

@pytest.mark.parametrize('new',['علی؛ قیمت 250 هزار تومان؛ https://example.com','علی؛ قیمت ۲۵۰٬۰۰۰ تومان؛ https://example.com','۱. علی؛ قیمت 250,000 تومان؛ https://example.com\n۲. قیمت ۲۵۰ هزار تومان'])
def test_balanced_equivalent_money(new):
    r=a.draft_result(SOURCE,new,new,PROFILE,'balanced')
    assert not r['fact_warning'] and r['output']==new

def test_free_never_runs_guard(monkeypatch):
    def fail(*args):raise AssertionError('fact comparison ran')
    monkeypatch.setattr(a,'missing_facts',fail);monkeypatch.setattr(a,'balanced_facts',fail)
    r=a.draft_result(SOURCE,CHANGED,CHANGED,PROFILE,'free')
    assert r['output']==CHANGED and not r['fact_checked']

@pytest.mark.parametrize('provider,path,mode',[('openai','legacy','manual'),('gemini','legacy','manual'),('gemini','generateContent','manual'),('gemini','generateContent','quality')])
def test_free_provider_api(tmp_path,monkeypatch,provider,path,mode):
    app=create_app(tmp_path,start_workers=False);db=app.state.service.db;db.set_setting('ai43_enabled',True)
    settings.save_key(db,provider,'save',KEY)
    db.set_setting('ai44_catalog_gemini',{'fingerprint':settings.fingerprint(KEY),'text':MODELS,'tts':list(gemini431.TTS_MODELS),'image':[],'loaded_at':1})
    seen=[]
    if provider=='openai':
        seen=mock(monkeypatch,[httpx.Response(200,json={'choices':[{'message':{'content':json.dumps(DRAFT)}}]})])
    elif path=='legacy':
        async def structured(key,model,prompt,fields,**kw):
            seen.append(prompt);return DRAFT
        monkeypatch.setattr(gemini431,'structured',structured)
    else:
        seen=mock(monkeypatch,[response(DRAFT)])
    model='gpt-4o-mini' if provider=='openai' else MODELS[0]
    body={'text':SOURCE,'profile':PROFILE,'provider':provider,'model':model,'consent':True,'fact_mode':'free','api_path':path,'model_mode':mode}
    h={'X-Studio-Key':app.state.service.admin_key,'Origin':'http://127.0.0.1:8765'}
    with TestClient(app,base_url='http://127.0.0.1:8765',client=('127.0.0.1',50000)) as c:
        assert c.post('/api/ai-lab/preview',json=body,headers={'Origin':'http://127.0.0.1:8765'}).status_code==401
        assert c.post('/api/ai-lab/preview',json={**body,'fact_mode':'unknown'},headers=h).status_code==422
        assert c.post('/api/ai-lab/preview',json={**body,'consent':False},headers=h).status_code==400
        r=c.post('/api/ai-lab/preview',json=body,headers=h);assert r.status_code==200,r.text
        out=r.json();assert out['output']==CHANGED and not out['blocked'] and not out['fact_checked'] and not out['published']
        assert db.snapshot()['total_posts']==0
        assert 'value="free"' in c.get('/').text and 'id="ai-fact-mode"' in c.get('/').text
    assert len(seen)==1
    if provider=='openai':
        messages=json.loads(seen[0].content)['messages'];profile=json.loads(messages[1]['content'])['profile']
        assert profile['fact_mode']=='free' and profile['locked']==[]
        assert 'no RelayForge fact-preservation requirement' in messages[0]['content']
    else:
        prompt=seen[0] if path=='legacy' else json.loads(seen[0].content)['contents'][0]['parts'][0]['text']
        assert 'no RelayForge fact-preservation requirement' in prompt and '"locked": []' in prompt
