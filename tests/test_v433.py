from email.parser import BytesParser
from email.policy import default
from urllib.parse import parse_qs
import httpx
import pytest
from fastapi.testclient import TestClient
from main import create_app
from relayforge.database import Database
from relayforge.engine import Engine
from relayforge.source import bot_message_payload
from relayforge.richtext import render, wire

TOKEN='123456:TEST_TOKEN_ABC'
TEXT='🔥 مقدمه\nاطلاعیه مهم\nادامه مهم #برنامه_نویسی & <نمونه>'
BOLD={'type':'bold','start':TEXT.index('اطلاعیه'),'end':TEXT.index(' #')}

@pytest.mark.parametrize('media',[False,True])
@pytest.mark.asyncio
async def test_actual_queue_eitaa_wire(tmp_path,monkeypatch,media):
    db=Database(tmp_path);db.update_source(dict(mode='bot',channel='@source',bot_token=TOKEN));db.update_target('eitaa',dict(token=TOKEN,chat_id='12345',enabled=True));db.set_setting('rich43_modes',{'eitaa':'html'})
    raw={'message_id':12,'caption' if media else 'text':TEXT,'caption_entities' if media else 'entities':wire(TEXT,[BOLD])}
    if media:raw['photo']=[{'file_id':'sample-file'}]
    payload=bot_message_payload(raw)
    if media:
        path=tmp_path/'media'/'image.jpg';path.write_bytes(b'example');payload['media_path']='media/image.jpg'
    db.ingest(**payload);job=db.next_delivery('eitaa');requests=[]
    async def handler(req):
        await req.aread();requests.append(req);return httpx.Response(200,json={'ok':True,'result':{'message_id':555}})
    real=httpx.AsyncClient;monkeypatch.setattr('relayforge.gateways.httpx.AsyncClient',lambda **kw:real(transport=httpx.MockTransport(handler),**kw))
    await Engine(db)._deliver('eitaa',job)
    assert len(requests)==1 and db.snapshot()['stats']=={'sent':1}
    req=requests[0]
    if media:
        msg=BytesParser(policy=default).parsebytes(b'Content-Type: '+req.headers['content-type'].encode()+b'\r\nMIME-Version: 1.0\r\n\r\n'+req.content)
        fields={p.get_param('name',header='content-disposition'):p.get_payload(decode=True).decode() for p in msg.iter_parts()}
        body=fields['caption'];assert fields['parse_mode']=='html' and req.url.path.endswith('sendFile')
    else:
        fields=parse_qs(req.content.decode());body=fields['text'][0];assert fields['parse_mode']==['html'] and req.url.path.endswith('sendMessage')
    assert '<b>اطلاعیه مهم</b>\n<b>ادامه مهم</b>' in body and '&amp; &lt;نمونه&gt;' in body
    with db.connection() as c:
        assert c.execute("SELECT count(*) FROM activity WHERE message LIKE '%parse_mode=html%'").fetchone()[0]==1


def test_eitaa_mixed_styles_preserve_bold():
    text='تیتر\nزیرخط';spans=[{'type':'bold','start':0,'end':4},{'type':'underline','start':5,'end':len(text)}]
    out,params,warnings=render('eitaa',text,spans,'html');assert out=='<b>تیتر</b>\nزیرخط' and params=={'parse_mode':'html'} and warnings


def test_real_sample_uses_saved_mode_and_no_queue_mutation(tmp_path,monkeypatch):
    app=create_app(tmp_path,start_workers=False);db=app.state.service.db;db.update_source(dict(mode='bot',channel='@source',bot_token=TOKEN));db.update_target('eitaa',dict(token=TOKEN,chat_id='12345',enabled=True));db.set_setting('rich43_modes',{'eitaa':'html'});db.ingest(12,TEXT,entities=[BOLD]);db.ingest(13,'protected',protected=True)
    sent=[]
    async def fake(p,t,c,post):sent.append(post);return '99'
    monkeypatch.setattr('main.send',fake)
    h={'X-Studio-Key':app.state.service.admin_key,'Origin':'http://127.0.0.1:8765'}
    with TestClient(app,base_url='http://127.0.0.1:8765',client=('127.0.0.1',50000)) as c:
        url='/api/richtext/test/eitaa';body={'mode':'html','source_message_id':12,'confirm':True}
        assert c.post(url,headers=h,json={**body,'mode':'plain'}).status_code==400
        assert c.post(url,headers=h,json={**body,'source_message_id':13}).status_code==400
        r=c.post(url,headers=h,json=body);assert r.status_code==200,r.text
        assert r.json()['diagnostic']['parse_mode']=='html' and len(sent)==1 and sent[0].text==TEXT
        assert db.snapshot()['stats']=={'queued':1}
