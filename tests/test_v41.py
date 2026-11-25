"""Offline v4.1 regressions; no credentials or real publications."""
import asyncio, json, sys, types
from pathlib import Path
from types import SimpleNamespace
import httpx, pytest
from fastapi.testclient import TestClient
from relayforge.database import Database
from relayforge.engine import Engine
from relayforge.source import Collector
from relayforge.gateways import _post, send, Post, Rejected, RetryLater
from main import create_app

TOKEN='123456:TEST_TOKEN_ABC'
BOT=dict(mode='bot',channel='@mother',bot_token=TOKEN,start_id=30,end_id=32,history_enabled=True,realtime_enabled=False)
USER=dict(mode='user',channel='@mother',api_id='12345',api_hash='0123456789abcdef0123456789abcdef',phone='+10000000000',start_id=30,end_id=32,history_enabled=True,realtime_enabled=False,upgrade_archive=True)

@pytest.fixture
def db(tmp_path):
    d=Database(tmp_path/'data');d.update_source(BOT);return d

def target(db,route='bale',chat='@destination',**extra):
    db.update_target(route,dict(token=TOKEN,chat_id=chat,enabled=True,**extra))

def test_delete_bulk_preserves_history_and_other_route(db):
    target(db);target(db,'eitaa');extra=db.add_target('bale');target(db,extra,'@extra')
    db.ingest(30,'hello');db.ingest(31,'second')
    j=db.next_delivery('bale');db.finish_delivery(j['id'],'bale','sent','77')
    assert db.remove_targets(['bale',extra])==2
    assert db.target('bale')['archived'] and not db.target('bale')['token']
    assert 'bale' not in [t['platform'] for t in db.targets()]
    assert db.target('bale',True)['counts']=={'sent':1,'skipped':1}
    assert db.target('eitaa',True)['counts']=={'queued':2}
    assert len(Database(db.root).targets())==4
    assert db.report('deliveries','bale')['rows'][0]['archived']==1

def test_bulk_delete_is_atomic_when_one_uncertain(db):
    target(db);target(db,'eitaa');db.ingest(30,'hello');j=db.next_delivery('eitaa');db.finish_delivery(j['id'],'eitaa','uncertain')
    with pytest.raises(ValueError):db.remove_targets(['bale','eitaa'])
    assert not db.target('bale')['archived'] and not db.target('eitaa')['archived']

def test_clear_queue_only_own_pending(db):
    target(db);target(db,'eitaa');db.ingest(30,'first');db.ingest(31,'second')
    j=db.next_delivery('bale');db.finish_delivery(j['id'],'bale','sent','99')
    assert db.clear_queue('bale')==1
    assert db.target('bale',True)['counts']=={'sent':1,'skipped':1}
    assert db.target('eitaa',True)['counts']=={'queued':2}
    target(db,include_backlog=True);assert db.next_delivery('bale') is None

def test_readd_same_channel_no_replay_of_sent_backlog(db):
    target(db);db.ingest(30,'first');j=db.next_delivery('bale');db.finish_delivery(j['id'],'bale','sent','10')
    db.remove_targets(['bale']);r=db.add_target('bale');target(db,r,include_backlog=True)
    assert db.next_delivery(r) is None
    db.ingest(31,'new');assert db.next_delivery(r)['msg_id']==31

def test_reports_filters_pagination_and_recorded_channel(db):
    target(db);db.ingest(30,'first');db.ingest(31,'second');db.event('ERROR','bale','HTTP 400 Bad Request')
    assert db.report('events','bale','HTTP 400')['total']==1
    a=db.report('deliveries','bale',limit=1);b=db.report('deliveries','bale',limit=1,offset=1)
    assert a['total']==2 and a['rows'][0]['id']!=b['rows'][0]['id']
    assert db.report('deliveries','bale','30')['total']==1
    db.reset_operation();target(db,chat='@changed');assert {r['chat_id'] for r in db.report('deliveries','bale')['rows']}=={'@destination'}

def test_bot_archive_warning_per_destination(db):
    target(db);db.ingest(30,media_kind='copy',media_ref='@mother')
    assert db.target('bale',True)['archive_refs']==1
    assert db.next_delivery('bale') is None
    assert any('API ID/Hash' in x['message'] for x in db.report('events','bale')['rows'])

def test_upgrade_requires_same_source_and_explicit_permission(db):
    db.ingest(30,media_kind='copy',media_ref='@mother')
    with pytest.raises(ValueError):db.update_source(USER)
    db.set_setting('source_peer','-100123')
    with pytest.raises(ValueError):db.update_source({**USER,'upgrade_archive':False})
    with pytest.raises(ValueError):db.update_source({**USER,'channel':'@other'})
    db.update_source(USER);assert db.get_setting('source_peer')=='-100123' and db.get_setting('history_cursor')==29

@pytest.mark.asyncio
async def test_archive_rehydration_to_bale_and_eitaa_preserves_telegram_sent(db,monkeypatch):
    target(db,'telegram','@tg');target(db);target(db,'eitaa')
    db.ingest(30,media_kind='copy',media_ref='@mother');j=db.next_delivery('telegram');db.finish_delivery(j['id'],'telegram','sent','123')
    db.set_setting('source_peer','-100123');db.set_setting('history_cursor',32);db.update_source(USER)
    c=Collector(db);c._entity=SimpleNamespace(noforwards=False);c._entity_ref='@mother'
    class Fake:
        async def is_user_authorized(self):return True
        async def iter_messages(self,entity,min_id,reverse,limit):
            assert min_id==29 and reverse
            for mid in (30,31,32):
                yield SimpleNamespace(id=mid,message='old post '+str(mid),media=None,noforwards=False)
    async def fake_client():return Fake()
    async def no_sleep(*a):pass
    monkeypatch.setattr(c,'_client',fake_client);monkeypatch.setattr(asyncio,'sleep',no_sleep)
    await c.tick();assert db.get_setting('history_cursor')==32
    assert db.target('telegram',True)['sent_count']==1
    assert db.target('bale',True)['counts']=={'queued':3}
    assert db.target('eitaa',True)['counts']=={'queued':3}
    real=httpx.AsyncClient;requests=[]
    def handle(req):
        requests.append((req.url.host,req.url.path,json.loads(req.content) if req.headers.get('content-type','').startswith('application/json') else req.content.decode()))
        return httpx.Response(200,json={'ok':True,'result':{'message_id':91}})
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kw:real(transport=httpx.MockTransport(handle),**kw))
    e=Engine(db);e.collector=c
    for route in ('bale','eitaa'):
        job=db.next_delivery(route);assert job['msg_id']==30 and job['body']=='old post 30';await e._deliver(route,job)
        assert db.target(route,True)['sent_count']==1
    assert [x[0] for x in requests]==['tapi.bale.ai','eitaayar.ir']
    assert all('old post 30' in str(x[2]) or 'old+post+30' in str(x[2]) for x in requests)

@pytest.mark.asyncio
async def test_user_archive_photo_upload_mock_bale(db,monkeypatch):
    db.update_source(USER);target(db)
    class Msg:
        id=30;message='caption';media=True;photo=True;file=SimpleNamespace(name='old.jpg',size=4)
        async def download_media(self,file):
            p=Path(file+'.jpg');p.write_bytes(b'fake');return str(p)
    c=Collector(db);c._entity=SimpleNamespace(noforwards=False)
    await c._ingest_user(Msg(),db.get_source());j=db.next_delivery('bale');assert j['media_kind']=='photo'
    real=httpx.AsyncClient;seen=[]
    def handle(req):
        seen.append(req.url.path);assert b'caption' in req.content and b'fake' in req.content
        return httpx.Response(200,json={'ok':True,'result':{'message_id':42}})
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kw:real(transport=httpx.MockTransport(handle),**kw))
    engine=Engine(db);engine.collector=c;await engine._deliver('bale',j)
    assert seen[0].endswith('/sendPhoto') and db.target('bale',True)['sent_count']==1

@pytest.mark.asyncio
async def test_bale_error_detail_and_secret_redaction():
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req:httpx.Response(400,json={'ok':False,'description':'Bad Request: chat not found '+TOKEN+' https://private.example/'+TOKEN}))) as c:
        with pytest.raises(Rejected) as ex:await _post(c,'https://tapi.bale.ai/bot'+TOKEN+'/sendMessage')
    assert 'HTTP 400' in str(ex.value) and 'chat not found' in str(ex.value) and TOKEN not in str(ex.value) and 'private.example' not in str(ex.value)

@pytest.mark.asyncio
async def test_non_json_404_is_definite_rejection():
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req:httpx.Response(404,text='not found'))) as c:
        with pytest.raises(Rejected,match='404'):await _post(c,'https://tapi.bale.ai/bot'+TOKEN+'/sendMessage')

@pytest.mark.asyncio
async def test_api_level_rate_limit():
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req:httpx.Response(200,json={'ok':False,'error_code':429,'parameters':{'retry_after':61}}))) as c:
        with pytest.raises(RetryLater) as ex:await _post(c,'https://tapi.bale.ai/bot'+TOKEN+'/sendMessage')
        assert ex.value.delay==61

@pytest.mark.asyncio
async def test_media_error_before_send_is_retriable(db,monkeypatch):
    target(db);db.ingest(30,'photo','photo',media_ref='a');j=db.next_delivery('bale');e=Engine(db)
    async def bad(row):raise RuntimeError('simulated')
    monkeypatch.setattr(e.collector,'get_media',bad);await e._deliver('bale',j)
    assert db.target('bale',True)['counts']=={'queued':1}
    assert 'RuntimeError' in db.target('bale')['last_error']

@pytest.fixture
def client(tmp_path):
    app=create_app(tmp_path/'data',start_workers=False);headers={'X-Studio-Key':app.state.service.admin_key,'Origin':'http://127.0.0.1:8765'}
    with TestClient(app,base_url='http://127.0.0.1:8765',client=('127.0.0.1',12345)) as c:yield c,headers,app.state.service

def test_api_new_mutations_and_report_auth(client):
    c,h,s=client;target(s.db)
    assert c.post('/api/target-actions/remove',json={'routes':['bale'],'confirm':True},headers={'Origin':h['Origin']}).status_code==401
    assert c.get('/api/reports/events').status_code==401
    assert c.post('/api/target-actions/remove',json={'routes':['bale'],'confirm':False},headers=h).status_code==422
    assert c.post('/api/target-actions/remove',json={'routes':['bale'],'confirm':True},headers={**h,'Origin':'https://evil.example'}).status_code==403
    assert c.post('/api/target-actions/remove',json={'routes':['bale'],'confirm':True},headers=h).json()['removed']==1
    assert c.get('/api/reports/routes',headers=h).json()['routes'][1]['archived']==1

def test_api_send_test_explicit_and_reports(client,monkeypatch):
    import main
    c,h,s=client;target(s.db);calls=[]
    async def mock_send(*args):calls.append(args);return '55'
    monkeypatch.setattr(main,'send',mock_send)
    assert c.post('/api/targets/bale/test-send',json={},headers=h).status_code==422
    assert not calls
    assert c.post('/api/targets/bale/test-send',json={'confirm':True},headers=h).json()['remote_id']=='55'
    assert calls[0][0]=='bale' and calls[0][2]=='@destination'
    events=c.get('/api/reports/events?route=bale',headers=h).json()['rows'];assert any('55' in x['message'] for x in events)
    assert TOKEN not in json.dumps(events)

def test_api_clear_and_pause(client):
    c,h,s=client;s.db.update_source(BOT);target(s.db);s.db.ingest(30,'hello')
    assert c.post('/api/targets/bale/pause',json={'enabled':False},headers=h).status_code==200
    assert not s.db.target('bale')['enabled']
    assert c.post('/api/targets/bale/clear-queue',json={'confirm':True},headers=h).json()['cleared']==1

def test_api_archive_rescan_preserves_delivered(client):
    c,h,s=client;s.db.update_source(USER);target(s.db);s.db.ingest(30,'hello');j=s.db.next_delivery('bale');s.db.finish_delivery(j['id'],'bale','sent','22');s.db.set_setting('history_cursor',32)
    assert c.post('/api/source/rescan',json={'confirm':True},headers=h).status_code==200
    assert s.db.get_setting('history_cursor')==29 and not s.db.ingest(30,'hello')
    assert s.db.target('bale',True)['sent_count']==1
