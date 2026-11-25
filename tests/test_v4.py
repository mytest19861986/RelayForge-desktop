import asyncio, json
import httpx, pytest
from fastapi.testclient import TestClient
from relayforge.database import Database, matches_filter
from relayforge.source import Collector
from relayforge.engine import Engine
from relayforge.gateways import _result, _post, Uncertain, RetryLater, copy_telegram, Rejected, check_target
from main import create_app

TOKEN='123456:TEST_TEST_TEST'
SRC=dict(mode='bot',channel='@mother',bot_token=TOKEN,start_id=30,end_id=200,history_enabled=False,realtime_enabled=True)
@pytest.fixture
def db(tmp_path):
    db=Database(tmp_path/'data'); db.update_source(SRC); return db

def route(db,p='telegram',chat='@dest',**extra):
    db.update_target(p,dict(token=TOKEN,chat_id=chat,enabled=True,**extra))

def test_realtime_outside_archive(db):
    route(db); assert not db.ingest(201,'old scan'); assert db.ingest(201,'new',live=True)
    assert db.next_delivery('telegram')['msg_id']==201

def test_live_off(db):
    db.update_source({**SRC,'realtime_enabled':False}); assert not db.ingest(201,'new',live=True)

def test_filter_exact_tag_and_exclude(db):
    db.update_source({**SRC,'filter_enabled':True,'hashtags':['آموزش'],'keywords':['هوش مصنوعی'],'exclude_words':['تبلیغ']}); route(db)
    for i,t in enumerate(['#آموزشی','#آموزش تبلیغ','#آموزش','هوش مصنوعی'],30): assert db.ingest(i,t)
    assert db.next_delivery('telegram')['msg_id']==32

def test_filter_all_and_normalization():
    cfg={'filter_enabled':True,'hashtags':['#ایران'],'keywords':['یک'],'filter_match':'all'}
    assert matches_filter('#ایران يك',cfg); assert not matches_filter('#ایران',cfg)

def test_transform_only_eligible(db):
    db.update_source({**SRC,'prefix':'شروع: ','suffix':' پایان'}); route(db); db.ingest(40,'text'); assert db.next_delivery('telegram')['body']=='شروع: text پایان'

def test_multiple_targets_independent(db):
    a=db.add_target('telegram'); b=db.add_target('eitaa');route(db);route(db,a,'@another');route(db,b,'@eitaa');db.ingest(50,'post')
    j=db.next_delivery('telegram');db.finish_delivery(j['id'],'telegram','failed',detail='rejected'); assert db.next_delivery(a)['msg_id']==50;assert db.next_delivery(b)['msg_id']==50

def test_future_only_default_and_opt_in(db):
    db.ingest(30,'old');route(db);assert db.next_delivery('telegram') is None
    route(db,'bale',include_backlog=True);assert db.next_delivery('bale')['msg_id']==30

def test_token_rotation_preserves_history(db):
    route(db);db.ingest(30,'x');j=db.next_delivery('telegram');db.finish_delivery(j['id'],'telegram','sent','12')
    db.update_target('telegram',{'token':'123456:NEW_TOKEN','chat_id':'@dest','enabled':True}); assert db.target('telegram')['token']=='123456:NEW_TOKEN';assert db.snapshot()['stats']['sent']==1

def test_reject_duplicate_destination(db):
    route(db); a=db.add_target('telegram')
    with pytest.raises(ValueError):route(db,a)

def test_uncertain_blocks_only_own_route(db):
    route(db);route(db,'bale');db.ingest(30,'x');db.ingest(31,'y');j=db.next_delivery('telegram');db.finish_delivery(j['id'],'telegram','uncertain');assert db.next_delivery('telegram') is None;assert db.next_delivery('bale')

def test_reference_only_goes_to_telegram(db):
    db.update_source({**SRC,'history_enabled':True});route(db);route(db,'eitaa');db.ingest(30,media_kind='copy',media_ref='@mother'); assert db.next_delivery('eitaa') is None;assert db.next_delivery('telegram')['media_kind']=='copy'

def test_reference_upgrade_by_live(db):
    db.update_source({**SRC,'history_enabled':True});route(db);route(db,'eitaa');db.ingest(30,media_kind='copy',media_ref='@mother');assert db.ingest(30,'actual text',live=True); assert db.next_delivery('eitaa')['body']=='actual text'

def test_bot_archive_filter_rejected(db):
    with pytest.raises(ValueError):db.update_source({**SRC,'history_enabled':True,'filter_enabled':True})

@pytest.mark.asyncio
async def test_bot_archive_cursor(db,monkeypatch):
    db.update_source({**SRC,'history_enabled':True,'realtime_enabled':False,'end_id':32});route(db)
    async def nosleep(*a):pass
    monkeypatch.setattr(asyncio,'sleep',nosleep);await Collector(db).tick();assert db.get_setting('history_cursor')==32;assert db.snapshot()['stats']['queued']==3

def test_reset_preserves_sent(db):
    route(db);db.ingest(30,'a');j=db.next_delivery('telegram');db.finish_delivery(j['id'],'telegram','sent','99');db.ingest(31,'b')
    b=db.backup();assert (db.root/'backups').exists();db.reset_operation();assert db.snapshot()['stats']=={'sent':1,'skipped':1};assert db.get_setting('epoch')==2
    db.update_source({**SRC,'channel':'@new'});route(db,chat='@newdest');assert db.ingest(30,'new');assert db.next_delivery('telegram')['body']=='new'

def test_reset_refuses_uncertain(db):
    route(db);db.ingest(30,'a');j=db.next_delivery('telegram');db.finish_delivery(j['id'],'telegram','uncertain')
    with pytest.raises(ValueError):db.reset_operation()

def test_result_requires_message_id():
    for data in ({},{'message_id':None},{'message_id':False},{'message_id':0},{'file_id':'123'}):
        with pytest.raises(Uncertain):_result('telegram',{'ok':True,'result':data})

@pytest.mark.asyncio
async def test_connect_error_retriable():
    async def bad(req):raise httpx.ConnectError('test')
    async with httpx.AsyncClient(transport=httpx.MockTransport(bad)) as c:
        with pytest.raises(RetryLater):await _post(c,'https://api.telegram.org/test')

@pytest.mark.asyncio
async def test_rate_limit_retriable():
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req:httpx.Response(429,json={'parameters':{'retry_after':60}}))) as c:
        with pytest.raises(RetryLater) as ex:await _post(c,'https://api.telegram.org/test')
        assert ex.value.delay==60

@pytest.mark.asyncio
async def test_read_timeout_uncertain():
    async def bad(req):raise httpx.ReadTimeout('test')
    async with httpx.AsyncClient(transport=httpx.MockTransport(bad)) as c:
        with pytest.raises(Uncertain):await _post(c,'https://api.telegram.org/test')

@pytest.mark.asyncio
async def test_copy_message_and_protection(monkeypatch):
    real=httpx.AsyncClient; calls=[]; protected=[False]
    def handle(req):
        method=req.url.path.split('/')[-1];payload=json.loads(req.content);calls.append(method)
        if method=='getChat':return httpx.Response(200,json={'ok':True,'result':{'id':1 if payload['chat_id']=='@mother' else 2,'has_protected_content':protected[0]}})
        return httpx.Response(200,json={'ok':True,'result':{'message_id':44}})
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kw:real(transport=httpx.MockTransport(handle)))
    assert await copy_telegram(TOKEN,'@mother','@dest',30)=='44'; assert calls[-1]=='copyMessage'
    protected[0]=True;calls.clear()
    with pytest.raises(Rejected):await copy_telegram(TOKEN,'@mother','@dest',30)
    assert 'copyMessage' not in calls

@pytest.mark.asyncio
async def test_telegram_target_checks_posting_right(monkeypatch):
    real=httpx.AsyncClient; can=[False]
    def handle(req):
        method=req.url.path.split('/')[-1];data={'getMe':{'id':123},'getChat':{'id':-100123,'type':'channel'},'getChatMember':{'status':'administrator','can_post_messages':can[0]}}[method]
        return httpx.Response(200,json={'ok':True,'result':data})
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kw:real(transport=httpx.MockTransport(handle)))
    with pytest.raises(Rejected):await check_target('telegram',TOKEN,'@dest')
    can[0]=True; assert (await check_target('telegram',TOKEN,'@dest'))['ok']

@pytest.mark.asyncio
async def test_temporary_error_keeps_queue_and_enabled(db,monkeypatch):
    from relayforge import engine
    route(db);db.ingest(30,'a');j=db.next_delivery('telegram')
    async def fail(*a):raise RetryLater(30)
    monkeypatch.setattr(engine,'send',fail);await Engine(db)._deliver('telegram',j);assert db.target('telegram')['enabled'];assert db.snapshot()['stats']['queued']==1

def test_api_reset_two_stage_and_secret_validation(tmp_path):
    app=create_app(tmp_path/'data',start_workers=False);h={'X-Studio-Key':app.state.service.admin_key,'Origin':'http://127.0.0.1:8765'}
    with TestClient(app,base_url='http://127.0.0.1:8765',client=('127.0.0.1',45678)) as c:
        assert c.post('/api/reset/commit',json={'ticket':'fake'},headers=h).status_code==409
        t=c.post('/api/reset/prepare',json={},headers=h).json()['ticket'];assert app.state.service.db.get_setting('epoch')==1
        assert c.post('/api/reset/commit',json={'ticket':t},headers=h).status_code==200
        assert c.post('/api/reset/commit',json={'ticket':t},headers=h).status_code==409
        r=c.post('/api/source',json={'mode':TOKEN,'channel':'@a'},headers=h);assert r.status_code==422 and TOKEN not in r.text

def test_api_add_route(tmp_path):
    app=create_app(tmp_path/'data',start_workers=False);h={'X-Studio-Key':app.state.service.admin_key,'Origin':'http://127.0.0.1:8765'}
    with TestClient(app,base_url='http://127.0.0.1:8765',client=('127.0.0.1',45678)) as c:
        r=c.post('/api/targets',json={'provider':'telegram'},headers=h);assert r.status_code==200;assert len(c.get('/api/status',headers=h).json()['targets'])==6
