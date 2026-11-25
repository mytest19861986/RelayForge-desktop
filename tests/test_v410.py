import asyncio, base64
from pathlib import Path
from fastapi.testclient import TestClient
from main import create_app
from relayforge import publish410 as p, style437 as st
from relayforge.gateways import Uncertain
import main
import pytest

@pytest.fixture
def client(tmp_path):
    app=create_app(tmp_path,start_workers=False);db=app.state.service.db
    for route,chat in [('telegram','@demo_telegram'),('bale','@demo_bale')]:
        db.update_target(route,{'token':'test_token_123','chat_id':chat,'enabled':False})
    h={'X-Studio-Key':app.state.service.admin_key,'Origin':'http://127.0.0.1:8765'}
    with TestClient(app,base_url='http://127.0.0.1:8765',client=('127.0.0.1',50000)) as c:yield c,h,app

def test_identity_analysis(client):
    c,h,app=client
    rows=[dict(text=f'خبر {i}\nمتن خبری\n━━━━\n📢 کانال آموزش\n@ownchannel\nhttps://t.me/ownchannel',entities=[],kind='text',date='',channel_title='کانال آموزش',channel_handle='@ownchannel') for i in range(30)]
    db=app.state.service.db
    s=st.save(db,asyncio.run(st.analyze(db,rows,30,'الگو','@ownchannel')))
    i=s['identity'];assert i['title']=='کانال آموزش' and i['handle']=='@ownchannel'
    assert '@ownchannel' in i['handle_candidates'] and 'https://t.me/ownchannel' in i['links'] and '━━━━' in i['separators']
    assert '📢 کانال آموزش' in i['signature_candidates']
    assert 'خبر 0' not in (Path(db.root)/'styles'/f"{s['id']}.json").read_text()
    assert c.get('/api/styles/'+s['id'],headers=h).json()['identity']==i

def test_footer_after_guard(client):
    c,h,_=client
    r=c.post('/api/ai-lab/preview',headers=h,json={'text':'متن نمونه ۲۵۰ تومان','profile':{},'fact_mode':'strict','identity_footer':'📢 کانال من\n@mychannel\nhttps://t.me/mychannel'});assert r.status_code==200,r.text
    result=r.json();assert not result['blocked'] and not result['fact_warning'] and result['output'].endswith('https://t.me/mychannel')
    assert p.append_footer('متن\n@mychannel','@mychannel')=='متن\n@mychannel'

def test_targets_and_text_send(client,monkeypatch):
    c,h,app=client;sent=[]
    async def fake(provider,token,chat,post):
        sent.append((provider,chat,post));return '123'
    monkeypatch.setattr(main,'send',fake)
    targets=c.get('/api/ai-lab/publish-targets',headers=h).json()['targets'];assert len(targets)==2 and all('token' not in t for t in targets)
    r=c.post('/api/ai-lab/publish',headers=h,json={'text':'تیتر\nمتن','routes':['telegram','bale'],'confirm':True,'bold_heading':True});assert r.status_code==200,r.text
    assert len(sent)==2 and sent[0][2].entities==({'type':'bold','start':0,'end':4},)
    assert all(x['status']=='sent' and x['remote_id']=='123' for x in r.json()['results']);assert not app.state.service.engine.active
    assert not r.json()['automatic_retry']

def test_media_cleanup_and_uncertain(client,monkeypatch):
    c,h,_=client;seen=[];raw=b'\x89PNG\r\n\x1a\nexample'
    async def fake(provider,token,chat,post):
        seen.append(post.path);assert post.kind=='photo' and Path(post.path).read_bytes()==raw
        if provider=='bale':raise Uncertain('uncertain')
        return '1'
    monkeypatch.setattr(main,'send',fake)
    r=c.post('/api/ai-lab/publish',headers=h,json={'text':'کپشن','routes':['telegram','bale'],'confirm':True,'attachment':{'name':'photo.png','data':base64.b64encode(raw).decode()}})
    assert r.status_code==200,r.text;assert [x['status'] for x in r.json()['results']]==['sent','uncertain'];assert len(seen)==2 and not Path(seen[0]).exists()

@pytest.mark.parametrize('change,code',[({'confirm':False},422),({'routes':['telegram','telegram']},422),({'routes':['missing']},400),({'text':'','attachment':None},422),({'attachment':{'name':'../bad.png','data':'YWJj'}},400),({'attachment':{'name':'bad.exe','data':'YWJj'}},400),({'attachment':{'name':'bad.png','data':'YWJj'}},400),({'attachment':{'name':'a.txt','data':'not base64!'}},400),({'attachment':{'name':'a.txt','data':'YWJj'},'text':'a'*951},400)])
def test_validation_no_send(client,monkeypatch,change,code):
    c,h,_=client
    async def fake(*args):pytest.fail('invalid request sent')
    monkeypatch.setattr(main,'send',fake)
    r=c.post('/api/ai-lab/publish',headers=h,json={**{'text':'text','routes':['telegram'],'confirm':True},**change});assert r.status_code==code,r.text

def test_auth_origin_and_frontend(client,monkeypatch):
    c,h,app=client
    async def fake(*args):pytest.fail('unauthorized send')
    monkeypatch.setattr(main,'send',fake)
    body={'text':'test','routes':['telegram'],'confirm':True}
    assert c.get('/api/ai-lab/publish-targets').status_code==401
    assert c.post('/api/ai-lab/publish',headers={'Origin':h['Origin']},json=body).status_code==401
    assert c.post('/api/ai-lab/publish',headers={**h,'Origin':'https://example.com'},json=body).status_code==403
    html=c.get('/').text;js=c.get('/assets/main.js').text
    for id in ('publish-text','publish-file','publish-send','publish-save','media-attach','tts-attach','style-use-identity','ai-identity-footer'):assert f'id="{id}"' in html and id in js
    assert 'value="free"' in html

def test_saved_identity_profile(client):
    c,h,_=client
    profile={'identity_footer':'کانال آزمایشی\n@testchannel'}
    r=c.post('/api/ai-lab/profile/telegram',headers=h,json=profile);assert r.status_code==200,r.text
    saved=c.get('/api/ai-lab',headers=h).json()['profiles']['telegram'];assert saved['identity_footer']==profile['identity_footer']
    r=c.post('/api/ai-lab/preview',headers=h,json={'text':'نمونه','profile':saved});assert r.status_code==200,r.text
    assert r.json()['output'].endswith('@testchannel') and not r.json()['blocked']

def test_size_guard(client,monkeypatch):
    c,h,_=client;monkeypatch.setattr(p,'MAX_REQUEST',20)
    r=c.post('/api/ai-lab/publish',headers=h,json={'text':'hello','routes':['telegram'],'confirm':True});assert r.status_code==413
