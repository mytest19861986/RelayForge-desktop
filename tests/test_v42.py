"""New offline regressions: portable secrets, staged restore and delivery controls."""
import base64, io, json, sqlite3, zipfile
from pathlib import Path
from types import SimpleNamespace as NS
import httpx, pytest
from fastapi.testclient import TestClient
from relayforge.database import Database
from relayforge import backup42 as b
from relayforge.gateways import discover_rubika,check_target,Rejected
from relayforge.splus_user import can_post,Accounts,marker
from main import create_app
PASS='a-long-test-passphrase-42'
TOKEN='123456:TEST_TOKEN_ABC'

def setup(root):
    db=Database(root);db.update_source(dict(mode='bot',channel='@source',bot_token=TOKEN,start_id=1,end_id=5,history_enabled=True,realtime_enabled=False));db.update_target('bale',dict(token=TOKEN,chat_id='@bale',enabled=True));db.ingest(1,'secret-history-text');return db

def test_full_portable_restore(tmp_path):
    src=setup(tmp_path/'src');(src.root/'media'/'image.jpg').write_bytes(b'image');session='telegram_user_'+'a'*24+'.session';(src.root/session).write_bytes(b'fake-session');(src.root/'logs'/'relayforge.log').write_text('sample log');src.set_setting('run_enabled',True)
    path=b.make_backup(src.root,PASS);blob=path.read_bytes();assert TOKEN.encode() not in blob and not blob.startswith(b'PK')
    dest=setup(tmp_path/'dest');oldkey=(dest.root/'secrets.key').read_bytes();assert oldkey!=(src.root/'secrets.key').read_bytes();dest.update_target('bale',{'token':'987654:NEW_TOKEN_ABC'});b.stage_restore(dest.root,blob,PASS);assert dest.target('bale')['token']=='987654:NEW_TOKEN_ABC'
    assert b.apply_pending(dest.root);assert not b.apply_pending(dest.root);fresh=Database(dest.root);assert fresh.get_source()['bot_token']==TOKEN and fresh.target('bale')['token']==TOKEN and not fresh.target('bale')['enabled'];assert fresh.snapshot()['stats']=={'queued':1};assert (dest.root/'secrets.key').read_bytes()==oldkey and not fresh.get_setting('run_enabled');assert (dest.root/session).read_bytes()==b'fake-session';assert (dest.root/'media'/'image.jpg').read_bytes()==b'image';assert list((dest.root/'restored_logs').rglob('relayforge.log'))

def test_settings_only_no_history(tmp_path):
    src=setup(tmp_path/'src');src.event('INFO','system','secret-history-text');path=b.make_backup(src.root,PASS,'settings');plain=b.unseal(path.read_bytes(),PASS)
    with zipfile.ZipFile(io.BytesIO(plain)) as z:
        assert set(z.namelist())=={'manifest.json',b.DB,'secrets.key'};assert b'secret-history-text' not in z.read(b.DB)
    dest=Database(tmp_path/'dest');b.stage_restore(dest.root,path.read_bytes(),PASS,'settings');b.apply_pending(dest.root);fresh=Database(dest.root);assert fresh.target('bale')['token']==TOKEN and fresh.snapshot()['total_posts']==0 and not fresh.snapshot()['stats']

def test_bad_password_and_tamper(tmp_path):
    db=setup(tmp_path/'data');blob=b.make_backup(db.root,PASS).read_bytes()
    with pytest.raises(ValueError):b.stage_restore(db.root,blob,'wrong-password-123')
    with pytest.raises(ValueError):b.stage_restore(db.root,blob[:-1]+bytes([blob[-1]^1]),PASS)
    assert not (db.root/'restore_pending').exists()

@pytest.mark.parametrize('bad',['../outside','media/../../outside','media/C:evil','media/CON.txt','media/x\\evil','/absolute'])
def test_traversal_names_rejected(bad):assert not b.allowed_name(bad)

def test_reject_zip_traversal_with_correct_password(tmp_path):
    db=setup(tmp_path/'data');raw=io.BytesIO()
    with zipfile.ZipFile(raw,'w') as z:z.writestr('../escape','no')
    with pytest.raises(ValueError):b.stage_restore(db.root,b.seal(raw.getvalue(),PASS),PASS)
    assert not (tmp_path/'escape').exists() and not (db.root/'restore_pending').exists()

def test_database_backup_excludes_auto_password(tmp_path):
    db=setup(tmp_path/'data');db.set_setting('backup42_auto',{'enabled':True,'password':db.secret(PASS)});blob=b.make_backup(db.root,PASS,'database').read_bytes();dest=Database(tmp_path/'dest');b.stage_restore(dest.root,blob,PASS);b.apply_pending(dest.root);assert Database(dest.root).get_setting('backup42_auto') is None

def test_upgrade_backup_once(tmp_path):
    db=setup(tmp_path/'data');b.pre_upgrade(db.root,'4.2.0');paths=list((db.root/'backups').glob('pre-upgrade-*'));assert len(paths)==1;b.mark_version(db.root,'4.2.0');b.pre_upgrade(db.root,'4.2.0');assert len(list((db.root/'backups').glob('pre-upgrade-*')))==1;assert b.unseal(paths[0].read_bytes(),b.local_password(db.root)).startswith(b'PK')

def test_bulk_resolve_and_reset(tmp_path):
    db=setup(tmp_path/'data');db.update_target('eitaa',{'token':TOKEN,'chat_id':'@eitaa','enabled':True,'include_backlog':True});jobs=[db.next_delivery(p) for p in ('bale','eitaa')]
    for j in jobs:db.finish_delivery(j['id'],j['platform'],'uncertain')
    assert db.uncertain_rows()['total']==2
    with pytest.raises(ValueError):db.resolve_uncertain_bulk([jobs[0]['id'],99999],'skip')
    assert db.uncertain_rows()['total']==2
    assert db.resolve_uncertain_bulk([], 'skip', True)==2;db.reset_operation();assert db.snapshot()['stats']=={'skipped':2}

def headers(app):return {'X-Studio-Key':app.state.service.admin_key,'Origin':'http://127.0.0.1:8765'}

def test_api_export_restore_auth_and_guard(tmp_path):
    app=create_app(tmp_path/'data',start_workers=False);h=headers(app)
    with TestClient(app,base_url='http://127.0.0.1:8765',client=('127.0.0.1',50000)) as c:
        assert c.post('/api/backups/export',json={'mode':'settings','password':PASS},headers={'Origin':h['Origin']}).status_code==401
        r=c.post('/api/backups/export',json={'mode':'settings','password':PASS},headers=h);assert r.status_code==200 and r.content.startswith(b.MAGIC)
        restore_headers={**h,'X-Backup-Password':base64.b64encode(PASS.encode()).decode(),'Content-Type':'application/octet-stream'}
        assert c.post('/api/backups/restore?mode=settings&confirm=true',content=r.content,headers=restore_headers).status_code==200
        assert c.post('/api/engine/start',json={},headers=h).status_code==409
        assert c.get('/api/status',headers=h).json()['restore_pending']

def test_api_reset_skip_creates_backup_before_resolution(tmp_path):
    app=create_app(tmp_path/'data',start_workers=False);d=app.state.service.db;d.update_source(dict(mode='bot',channel='@source',bot_token=TOKEN));d.update_target('bale',dict(token=TOKEN,chat_id='@dest',enabled=True));d.ingest(1,'hi');j=d.next_delivery('bale');d.finish_delivery(j['id'],'bale','uncertain')
    with TestClient(app,base_url='http://127.0.0.1:8765',client=('127.0.0.1',50000)) as c:
        h=headers(app);r=c.post('/api/reset/prepare',json={},headers=h).json();assert r['uncertain']==1
        res=c.post('/api/reset/commit',json={'ticket':r['ticket'],'skip_uncertain':True},headers=h);assert res.status_code==200;assert d.snapshot()['stats']=={'skipped':1}
        p=Path(res.json()['backup']);raw=b.unseal(p.read_bytes(),b.local_password(d.root));f=tmp_path/'saved.sqlite';
        with zipfile.ZipFile(io.BytesIO(raw)) as z:f.write_bytes(z.read(b.DB))
        con=sqlite3.connect(f);assert con.execute('SELECT status FROM outbox').fetchone()[0]=='uncertain';con.close()

@pytest.mark.asyncio
async def test_rubika_official_chat_type_discovery(monkeypatch):
    import relayforge.gateways as g
    calls=[]
    async def fake(client,url,**kwargs):
        method=url.rsplit('/',1)[-1];calls.append(method)
        if method=='getUpdates':return {'status':'OK','data':{'updates':[{'chat_id':'c0one'},{'chat_id':'u0two'},{'chat_id':'c0one'}]}}
        if method=='getMe':return {'status':'OK','data':{'bot':{}}}
        chat=kwargs['json']['chat_id'];return {'status':'OK','data':{'chat':{'chat_id':chat,'chat_type':'Channel' if chat.startswith('c') else 'User','title':'demo'}}}
    monkeypatch.setattr(g,'_post',fake)
    assert await discover_rubika(TOKEN)==[{'chat_id':'c0one','title':'demo'}]
    assert (await check_target('rubika',TOKEN,'c0one'))['ok'];assert calls.count('getChat')==3
    with pytest.raises(Rejected):await check_target('rubika',TOKEN,'u0two')

def test_splus_channel_permission():
    assert can_post(NS(broadcast=True,creator=True));assert can_post(NS(broadcast=True,admin_rights=NS(post_messages=True)));assert not can_post(NS(broadcast=True,admin_rights=NS(post_messages=False)));assert not can_post(NS(broadcast=False,creator=True));assert marker('soroush')!=marker('soroush:other')

@pytest.mark.asyncio
async def test_splus_mock_send_checks_permission_and_uncertain(monkeypatch,tmp_path):
    import relayforge.splus_user as s
    from relayforge.gateways import Post,Uncertain
    from contextlib import asynccontextmanager
    class Fake:
        allowed=True; fail=False; sent=0
        async def is_user_authorized(self):return True
        async def get_entity(self,chat):return NS(broadcast=True,creator=self.allowed)
        async def send_message(self,*args,**kw):
            self.sent+=1
            if self.fail:raise TimeoutError()
            return NS(id=71)
    fake=Fake()
    @asynccontextmanager
    async def client(root,route):yield fake
    monkeypatch.setattr(s,'connected',client)
    a=Accounts(tmp_path)
    assert await a.send('soroush','-1001',Post('text'))=='71'
    fake.allowed=False
    with pytest.raises(Rejected):await a.send('soroush','-1001',Post('text'))
    assert fake.sent==1
    fake.allowed=True;fake.fail=True
    with pytest.raises(Uncertain):await a.send('soroush','-1001',Post('text'))
    assert fake.sent==2

def test_daily_auto_backup_on_start(tmp_path):
    import time
    app=create_app(tmp_path/'data',start_workers=True);d=app.state.service.db;d.set_setting('backup42_auto',{'enabled':True,'mode':'settings','password':d.secret(PASS)})
    with TestClient(app,base_url='http://127.0.0.1:8765',client=('127.0.0.1',50000)) as c:
        for _ in range(30):
            if d.get_setting('backup42_last'):break
            time.sleep(.1)
        assert d.get_setting('backup42_last') and len(list((d.root/'backups').glob('daily-*')))==1
