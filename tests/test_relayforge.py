import asyncio, concurrent.futures, json
from pathlib import Path
import pytest
import httpx
from fastapi.testclient import TestClient
from relayforge.database import Database, PLATFORMS
from relayforge.source import matches_channel,bot_message_payload,Collector,SourceUnavailable
from relayforge.gateways import _result, Post, Rejected,Uncertain,send
from relayforge.engine import Engine
from main import create_app, FileLock

TOKEN='123456:EXAMPLE_EXAMPLE_EXAMPLE'
SOURCE={'mode':'bot','channel':'@source','bot_token':TOKEN,'history_enabled':False,'realtime_enabled':True,'start_id':10,'end_id':30,'poll_seconds':45,'max_media_mb':20}

def make_db(tmp_path):
    db=Database(tmp_path/'data');db.update_source(SOURCE);return db

@pytest.fixture
def db(tmp_path):return make_db(tmp_path)


def target(db,platform='telegram',enabled=True,interval=0):
    db.update_target(platform,{'token':TOKEN,'chat_id':'@dest','enabled':enabled,'interval_seconds':interval})

def test_initial_settings_encrypted(tmp_path):
    db=make_db(tmp_path)
    data=(db.db_path).read_bytes()
    assert TOKEN.encode() not in data
    assert db.get_source()['bot_token']==TOKEN
    assert db.get_source(public=True)['bot_token'] is True
    assert db.get_source(public=True)['phone'] is False

@pytest.mark.parametrize('platform',PLATFORMS)
def test_all_destinations(db,platform):
    target(db,platform)
    db.ingest(12,'hello')
    job=db.next_delivery(platform)
    assert job and job['msg_id']==12
    db.finish_delivery(job['id'],platform,'sent',remote_id='456')
    assert db.snapshot()['stats']['sent']==1


def test_one_failure_does_not_block_other(db):
    target(db,'bale');target(db,'eitaa')
    db.ingest(12,'hello')
    bale=db.next_delivery('bale');db.finish_delivery(bale['id'],'bale','failed',detail='denied')
    eitaa=db.next_delivery('eitaa')
    assert eitaa['msg_id']==12
    assert db.snapshot()['stats']['failed']==1


def test_recover_inflight_only(db):
    for p in ('telegram','bale'): target(db,p)
    db.ingest(10,'message')
    telegram=db.next_delivery('telegram')
    bale=db.next_delivery('bale');db.finish_delivery(bale['id'],'bale','sent')
    db2=Database(db.root)
    assert db2.snapshot()['stats']=={'uncertain':1,'sent':1}
    assert db2.next_delivery('telegram') is None


def test_restore_queue_without_duplicate(db):
    target(db);assert db.ingest(15,'hello')
    assert not db.ingest(15,'hello')
    job=db.next_delivery('telegram');db.finish_delivery(job['id'],'telegram','sent')
    assert Database(db.root).snapshot()['stats']['sent']==1


def test_skip_out_of_range(db):
    target(db)
    assert not db.ingest(9,'too low')
    assert not db.ingest(31,'too high')
    assert db.snapshot()['total_posts']==0


def test_protected_content_not_enqueued(db):
    target(db)
    assert db.ingest(15,'secret',protected=True)
    assert db.next_delivery('telegram') is None


def test_enable_after_ingest(db):
    db.ingest(15,'hello')
    db.update_target('rubika',{'token':TOKEN,'chat_id':'@dest','enabled':True,'include_backlog':True})
    assert db.next_delivery('rubika')['msg_id']==15


def test_interval_blocks_only_own_route(db):
    target(db,'telegram',interval=10800)
    target(db,'bale')
    db.ingest(15,'a');db.ingest(16,'b')
    job=db.next_delivery('telegram');db.finish_delivery(job['id'],'telegram','sent',interval=10800)
    assert db.next_delivery('telegram') is None
    assert db.next_delivery('bale')['msg_id']==15


def test_manual_resolution(db):
    target(db);db.ingest(15,'x')
    job=db.next_delivery('telegram');db.finish_delivery(job['id'],'telegram','uncertain')
    db.retry_or_resolve(job['id'],'retry')
    assert db.next_delivery('telegram') is not None


def test_reject_route_identity_change_after_send(db):
    target(db);db.ingest(15,'a')
    with pytest.raises(ValueError):db.update_target('telegram',{'chat_id':'@other','enabled':True})


def test_reject_source_identity_change_after_ingest(db):
    db.ingest(15,'a')
    with pytest.raises(ValueError):db.update_source({**SOURCE,'channel':'@different'})


def test_bot_history_requires_end(db):
    with pytest.raises(ValueError):db.update_source({**SOURCE,'history_enabled':True,'end_id':None})


def test_user_mode_validation(db):
    with pytest.raises(ValueError):db.update_source({'mode':'user','channel':'@source','api_id':'abc','api_hash':'short'})

@pytest.mark.parametrize('channel,chat,ok',[
 ('@source',{'id':-100,'username':'source'},True),('https://t.me/source',{'id':-100,'username':'source'},True),
 ('-100123',{'id':-100123,'username':'none'},True),('@source',{'id':-100,'username':'different'},False)
])
def test_channel_matching(channel,chat,ok):assert matches_channel(channel,chat) is ok


def test_bot_media_normalization():
    d=bot_message_payload({'message_id':42,'caption':'hello','photo':[{'file_id':'small'},{'file_id':'big'}]})
    assert d['msg_id']==42 and d['media_ref']=='big' and d['media_kind']=='photo'
    assert bot_message_payload({'message_id':43,'has_protected_content':True})['protected']


def test_result_parsing():
    assert _result('telegram',{'ok':True,'result':{'message_id':123}})=='123'
    assert _result('rubika',{'status':'OK','data':{'message_id':'456'}})=='456'
    with pytest.raises(Rejected):_result('eitaa',{'ok':False})
    with pytest.raises(Uncertain):_result('bale',{'result':{}})


@pytest.mark.asyncio
async def test_send_mocked_http(monkeypatch):
    seen=[]
    real=httpx.AsyncClient
    def transport(request):
        seen.append((request.url.host,request.url.path))
        return httpx.Response(200,json={'ok':True,'result':{'message_id':7}})
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kw:real(transport=httpx.MockTransport(transport),**kw))
    assert await send('telegram',TOKEN,'@dest',Post('hello'))=='7'
    assert await send('bale',TOKEN,'@dest',Post('hello'))=='7'
    assert await send('eitaa',TOKEN,'@dest',Post('hello'))=='7'
    assert seen[0][0]=='api.telegram.org' and seen[1][0]=='tapi.bale.ai'


@pytest.mark.asyncio
async def test_send_rejection_only_one_provider(monkeypatch):
    real=httpx.AsyncClient
    def transport(request):
        if request.url.host=='tapi.bale.ai':return httpx.Response(400,json={'ok':False})
        return httpx.Response(200,json={'ok':True,'result':{'message_id':5}})
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kw:real(transport=httpx.MockTransport(transport),**kw))
    with pytest.raises(Rejected):await send('bale',TOKEN,'@dest',Post('a'))
    assert await send('eitaa',TOKEN,'@dest',Post('a'))=='5'


@pytest.mark.asyncio
async def test_bot_long_poll_ingest_and_offset(db,monkeypatch):
    real=httpx.AsyncClient
    def transport(request):
        return httpx.Response(200,json={'ok':True,'result':[
            {'update_id':45,'channel_post':{'message_id':12,'chat':{'id':-100,'username':'source'},'text':'Welcome'}},
            {'update_id':46,'channel_post':{'message_id':14,'chat':{'id':-123,'username':'other'},'text':'Wrong'}}
        ]})
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kw:real(transport=httpx.MockTransport(transport),**kw))
    collector=Collector(db);await collector.tick()
    assert db.get_setting('bot_offset')==46 and db.snapshot()['total_posts']==1
    await collector.tick()
    assert db.snapshot()['total_posts']==1


@pytest.mark.asyncio
async def test_worker_isolation(db,monkeypatch):
    import relayforge.engine as module
    for p in ('bale','eitaa'):target(db,p)
    db.ingest(12,'hello')
    async def fake_send(platform,*args):
        if platform=='bale':raise Rejected('invalid')
        return 'ok'
    monkeypatch.setattr(module,'send',fake_send)
    engine=Engine(db)
    await engine._deliver('bale',db.next_delivery('bale'))
    await engine._deliver('eitaa',db.next_delivery('eitaa'))
    assert db.snapshot()['stats']['sent']==1 and db.snapshot()['stats']['failed']==1


@pytest.fixture
def local_app(tmp_path):
    app=create_app(tmp_path/'appdata',start_workers=False)
    key=app.state.service.admin_key
    with TestClient(app,base_url='http://127.0.0.1:8765',client=('127.0.0.1',45678)) as client:
        yield client,key,tmp_path


def test_dashboard_html(local_app):
    client,key,tmp=local_app
    assert client.get('/').status_code==200
    assert 'مرکز فرمان' in client.get('/').text
    assert client.get('/assets/main.css').status_code==200
    assert client.get('/assets/other.txt').status_code==404


def test_api_rejects_unauthorized(local_app):
    client,key,tmp=local_app
    assert client.get('/api/status').status_code==401
    assert client.post('/api/source',json=SOURCE,headers={'Origin':'http://127.0.0.1:8765'}).status_code==401


def test_api_status_redacts_secrets(local_app):
    client,key,tmp=local_app
    headers={'X-Studio-Key':key,'Origin':'http://127.0.0.1:8765'}
    assert client.post('/api/source',json=SOURCE,headers=headers).status_code==200
    status=client.get('/api/status',headers=headers)
    assert status.status_code==200 and status.json()['source']['bot_token'] is True
    assert TOKEN not in status.text


def test_api_blocks_cross_origin(local_app):
    client,key,tmp=local_app
    headers={'X-Studio-Key':key,'Origin':'https://evil.example'}
    assert client.post('/api/source',json=SOURCE,headers=headers).status_code==403


def test_api_blocks_unexpected_host(tmp_path):
    app=create_app(tmp_path/'db',start_workers=False)
    with TestClient(app,base_url='http://evil.example',client=('127.0.0.1',40000)) as client:
        assert client.get('/').status_code==403
    with TestClient(app,base_url='http://127.0.0.1:8765') as client:
        assert client.get('/').status_code==403


def test_api_target_save_and_stop(local_app):
    client,key,tmp=local_app
    headers={'X-Studio-Key':key,'Origin':'http://127.0.0.1:8765'}
    d={'token':TOKEN,'chat_id':'@destination','enabled':True,'interval_seconds':0}
    assert client.post('/api/targets/telegram',json=d,headers=headers).status_code==200
    assert client.get('/api/status',headers=headers).json()['targets'][0]['enabled']==1
    assert client.post('/api/targets/telegram',json={**d,'enabled':False,'token':''},headers=headers).status_code==200
    assert client.get('/api/status',headers=headers).json()['targets'][0]['enabled']==0


def test_api_invalid_target_and_input(local_app):
    client,key,tmp=local_app
    headers={'X-Studio-Key':key,'Origin':'http://127.0.0.1:8765'}
    assert client.post('/api/targets/wrong',json={'enabled':True},headers=headers).status_code==404
    assert client.post('/api/targets/eitaa',json={'enabled':True,'chat_id':'@xx'},headers=headers).status_code==400
    assert client.post('/api/source',json={**SOURCE,'start_id':0},headers=headers).status_code==422


def test_no_persistent_state_reset_on_restart(db):
    target(db);db.ingest(20,'post')
    first=db.next_delivery('telegram');db.finish_delivery(first['id'],'telegram','sent')
    assert Database(db.root).snapshot()['stats']['sent']==1
    assert Database(db.root).next_delivery('telegram') is None


def test_lock_prevents_second_process(tmp_path):
    with FileLock(tmp_path/'locked'):
        with pytest.raises(RuntimeError):
            with FileLock(tmp_path/'locked'): pass


def test_event_feed_never_includes_token(db):
    target(db)
    snap=json.dumps(db.snapshot(),ensure_ascii=False)
    assert TOKEN not in snap


def test_source_bot_offset_is_durable(db):
    db.set_setting('bot_offset',250)
    assert Database(db.root).get_setting('bot_offset')==250


def test_paused_target_not_dispatched(db):
    db.ingest(15,'hello')
    target(db,'bale',False)
    assert db.next_delivery('bale') is None


def test_token_updates_keep_secret_when_form_blank(db):
    target(db)
    db.update_target('telegram',{'token':'','chat_id':'@dest','enabled':True})
    assert db.target('telegram')['token']==TOKEN


def test_realtime_prioritized_when_interval_zero(db):
    target(db, 'telegram', interval=0)
    db.ingest(15,'older archive',live=False)
    db.ingest(16,'new live',live=True)
    assert db.next_delivery('telegram')['msg_id']==16


def test_scheduled_route_preserves_archive_order(db):
    target(db, 'bale', interval=180*60)
    db.ingest(15,'older',live=False)
    db.ingest(16,'new',live=True)
    assert db.next_delivery('bale')['msg_id']==15

@pytest.mark.asyncio
async def test_source_diagnostic_bot_admin(db, monkeypatch):
    real=httpx.AsyncClient
    def mock(request):
        path=request.url.path
        if path.endswith('/getMe'):body={'ok':True,'result':{'id':44}}
        elif path.endswith('/getChat'):body={'ok':True,'result':{'title':'Source Channel'}}
        elif path.endswith('/getChatMember'):body={'ok':True,'result':{'status':'administrator'}}
        else:raise AssertionError(path)
        return httpx.Response(200,json=body)
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kw:real(transport=httpx.MockTransport(mock),**kw))
    result=await Collector(db).check_source()
    assert result['ok'] is True and result['channel']=='Source Channel'

@pytest.mark.asyncio
async def test_source_diagnostic_not_admin(db, monkeypatch):
    real=httpx.AsyncClient
    def mock(request):
        if request.url.path.endswith('/getMe'):body={'ok':True,'result':{'id':44}}
        elif request.url.path.endswith('/getChat'):body={'ok':True,'result':{'title':'Source'}}
        else:body={'ok':True,'result':{'status':'member'}}
        return httpx.Response(200,json=body)
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kw:real(transport=httpx.MockTransport(mock),**kw))
    with pytest.raises(SourceUnavailable):await Collector(db).check_source()

@pytest.mark.asyncio
async def test_rubika_text_request_http(monkeypatch):
    real=httpx.AsyncClient
    def mock(request):
        assert request.url.host=='botapi.rubika.ir'
        assert request.url.path.endswith('/sendMessage')
        return httpx.Response(200,json={'status':'OK','data':{'message_id':'rt-99'}})
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kw:real(transport=httpx.MockTransport(mock),**kw))
    assert await send('rubika',TOKEN,'channel-123',Post('text'))=='rt-99'

@pytest.mark.asyncio
async def test_soroush_text_response_mock(monkeypatch):
    real=httpx.AsyncClient
    def mock(request):
        assert request.url.host=='api.splus.ir'
        return httpx.Response(200,json={'ok':True,'result':{'message_id':77}})
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kw:real(transport=httpx.MockTransport(mock),**kw))
    assert await send('soroush',TOKEN,'@dest',Post('message'))=='77'


def test_existing_route_preserved_on_reenable(db):
    target(db,'eitaa')
    db.ingest(11,'post')
    d=db.next_delivery('eitaa')
    db.finish_delivery(d['id'],'eitaa','sent')
    db.update_target('eitaa',{'enabled':False,'chat_id':'@dest'})
    db.update_target('eitaa',{'enabled':True,'chat_id':'@dest'})
    assert db.next_delivery('eitaa') is None
    assert db.snapshot()['stats']['sent']==1


def test_circuit_breaker_pauses_only_repeatedly_failing_target(db):
    target(db,'bale')
    target(db,'eitaa')
    for i in range(15,18):db.ingest(i,'text')
    for _ in range(3):
        job=db.next_delivery('bale')
        assert job is not None
        db.finish_delivery(job['id'],'bale','failed',detail='invalid token')
    assert db.target('bale')['enabled']==0
    assert db.target('eitaa')['enabled']==1
    assert db.next_delivery('eitaa') is not None
    db.update_target('bale',{'enabled':True,'chat_id':'@dest','token':''})
    assert db.target('bale')['fail_streak']==0



def test_additive_schema_migration_preserves_existing_target(tmp_path):
    import sqlite3
    root=tmp_path / 'legacy_v3'
    root.mkdir()
    con=sqlite3.connect(root / 'relayforge.sqlite3')
    con.execute("""CREATE TABLE targets (platform TEXT PRIMARY KEY,
        token TEXT NOT NULL DEFAULT '', chat_id TEXT NOT NULL DEFAULT '',
        enabled INTEGER NOT NULL DEFAULT 0, interval_seconds INTEGER NOT NULL DEFAULT 0,
        next_allowed REAL NOT NULL DEFAULT 0, updated REAL NOT NULL DEFAULT 0,
        last_error TEXT NOT NULL DEFAULT '')""")
    con.execute("INSERT INTO targets(platform,chat_id) VALUES('bale','@oldtarget')")
    con.commit(); con.close()
    migrated=Database(root)
    assert migrated.target('bale')['chat_id']=='@oldtarget'
    assert migrated.target('bale')['fail_streak']==0
