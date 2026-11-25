import json, sqlite3, os
from pathlib import Path
from types import SimpleNamespace
import httpx, pytest
from fastapi.testclient import TestClient
from relayforge import richtext as r, ai43 as ai
from relayforge.database import Database
from relayforge.source import bot_message_payload, Collector
from relayforge.gateways import Post, send
from relayforge.engine import Engine
from main import create_app
TOKEN='123456:TEST_TOKEN_ABC'
TEXT='🔥 اطلاعیه مهم\nثبت‌نام ۲۵۰ هزار تومان'
BOLD={'type':'bold','start':2,'end':13}

def db_at(path):
    db=Database(path);db.update_source(dict(mode='bot',channel='@source',bot_token=TOKEN));db.update_target('bale',dict(token=TOKEN,chat_id='@bale',enabled=True));return db

def profile(**kw):return dict(brand='آکادمی',tone='formal',destination='بله',instructions='',template='{body}',locked=[],**kw) if not kw else {**profile(),**kw}

def test_utf16_roundtrip_and_caption():
    text='🔥 متن نیم‌فاصله'
    s=r.from_wire(text,[{'type':'bold','offset':3,'length':3},{'type':'italic','offset':1,'length':2}]);assert s==[{'type':'bold','start':2,'end':5}]
    assert r.wire(text,s)==[{'type':'bold','offset':3,'length':3}]
    d=bot_message_payload({'message_id':1,'caption':text,'caption_entities':r.wire(text,s),'photo':[{'file_id':'x'}]});assert d['entities']==s

def test_telethon_entity():
    e=type('MessageEntityBold',(),{})();e.offset=3;e.length=3
    assert r.from_wire('🔥 متن',[e],True)==[{'type':'bold','start':2,'end':5}]

@pytest.mark.parametrize('kind',['bold','italic','underline','strikethrough','code','pre','blockquote','spoiler'])
def test_native_styles(kind):
    text='الف 😀 ب';spans=[{'type':kind,'start':0,'end':len(text)}];out,opts,warnings=r.render('telegram',text,spans,caption=True);assert out==text and opts['caption_entities'][0]['length']==len(text)+1 and not warnings

def test_nested_and_malformed():
    text='الف ب';spans=[{'type':'bold','start':0,'end':5},{'type':'italic','start':0,'end':3},{'type':[],'start':0,'end':3},{'type':'code','start':True,'end':3}];assert len(r.normalize(text,spans))==2
    assert len(r.render('telegram',text,spans)[1]['entities'])==2
    assert r.render('bale',text,spans)[0]=='*الف ب*'

def test_link_safe():
    text='لینک';s={'type':'text_link','start':0,'end':4,'url':'https://example.com/a'};assert r.render('telegram',text,[s])[1]['entities'][0]['url']==s['url'];assert not r.normalize(text,[{**s,'url':'javascript:alert(1)'}])

def test_fallback_and_modes():
    s=[{'type':'bold','start':0,'end':3}];assert r.render('bale','متن خوب',s)[0]=='*متن* خوب'
    assert r.render('bale','متن #برنامه_نویسی',s)[0]=='*متن* #برنامه_نویسی'
    assert r.render('eitaa','متن &',s,'html')[0]=='<b>متن</b> &amp;'
    assert r.render('eitaa','متن خوب',s,'markdown')[1]=={'parse_mode':'Markdown'}
    assert r.render('eitaa','متن خوب',s)[0]=='<b>متن</b> خوب'
    assert r.render('eitaa','متن خوب',s,'plain')[0]=='متن خوب'
    assert r.render('rubika','متن خوب',s,'metadata')[1]['metadata']['meta_data_parts'][0]['type']=='Bold'
    assert r.render('rubika','متن 🔥',s,'metadata')[1]['metadata']['meta_data_parts'][0]['length']==3

def test_prefix_and_rescan_no_duplicate(tmp_path):
    db=db_at(tmp_path);db.update_source({'prefix':'🔥 '});assert db.ingest(1,'متن خوب');job=db.next_delivery('bale');db.finish_delivery(job['id'],'bale','sent','55');assert not db.ingest(1,'متن خوب',entities=[{'type':'bold','start':0,'end':3}]);assert not db.next_delivery('bale')
    with db.connection() as c:
        row=c.execute('select body,entities from source_messages').fetchone();assert row['body']=='🔥 متن خوب';assert json.loads(row['entities'])[0]['start']==2
    assert db.snapshot()['stats']=={'sent':1}

def test_old_schema_migration(tmp_path):
    db=db_at(tmp_path);db.ingest(1,'old');j=db.next_delivery('bale');db.finish_delivery(j['id'],'bale','sent','123')
    with db.connection() as c:c.execute('ALTER TABLE source_messages DROP COLUMN entities')
    fresh=Database(tmp_path);assert fresh.snapshot()['stats']=={'sent':1};assert fresh.target('bale')['token']==TOKEN
    with fresh.connection() as c:assert c.execute('select entities from source_messages').fetchone()[0]=='[]'

@pytest.mark.asyncio
async def test_gateway_wire(monkeypatch,tmp_path):
    captured=[]
    async def fake(client,url,**kw):
        captured.append(kw);return {'ok':True,'result':{'message_id':4}}
    monkeypatch.setattr('relayforge.gateways._post',fake);post=Post('🔥 متن',entities=({'type':'bold','start':2,'end':5},));await send('telegram',TOKEN,'@x',post);assert captured[-1]['json']['entities'][0]['offset']==3
    await send('bale',TOKEN,'@x',post);assert captured[-1]['json']['text']=='🔥 *متن*'
    media=tmp_path/'p.jpg';media.write_bytes(b'abc');await send('telegram',TOKEN,'@x',Post(post.text,'photo',str(media),post.entities));assert json.loads(captured[-1]['data']['caption_entities'])[0]['offset']==3
    await send('eitaa',TOKEN,'@x',Post(post.text,'photo',str(media),post.entities,'html'));assert captured[-1]['data']['caption']=='🔥 <b>متن</b>'

@pytest.mark.asyncio
async def test_engine_passes_entities(monkeypatch,tmp_path):
    db=db_at(tmp_path);db.ingest(1,'متن',entities=[{'type':'bold','start':0,'end':3}]);job=db.next_delivery('bale');eng=Engine(db);calls=[]
    async def fake(p,t,c,post):calls.append(post);return '1'
    monkeypatch.setattr('relayforge.engine.send',fake);await eng._deliver('bale',job);assert calls[0].entities[0]['type']=='bold';assert db.snapshot()['stats']=={'sent':1}

@pytest.mark.asyncio
async def test_local_lab_and_unsafe_template(tmp_path):
    db=db_at(tmp_path);text='آموزش پایتون، ۲۵۰ هزار تومان، ۱۰ آذر';result=await ai.preview(db,text,profile(template='{brand}\n{body}'))
    assert not result['blocked'] and not result['published'] and result['output'].startswith('آکادمی') and result['topic']=='آموزش'
    with pytest.raises(ValueError):await ai.preview(db,text,profile(template='{body.__class__}'))
    assert db.snapshot()['total_posts']==0

@pytest.mark.asyncio
async def test_fact_guard_and_consent(tmp_path,monkeypatch):
    db=db_at(tmp_path);text='نام علی؛ قیمت ۲۵۰ هزار تومان'
    with pytest.raises(ValueError):await ai.preview(db,text,profile(),provider='openai')
    with pytest.raises(ValueError):await ai.preview(db,text,profile(),provider='openai',consent=True)
    db.set_setting('ai43_enabled',True);monkeypatch.setenv('RELAYFORGE_AI_API_KEY','test-key')
    async def bad(*args):return ai.Draft('نام علی؛ قیمت ۲۵ هزار تومان','تیتر','خبر','')
    monkeypatch.setattr(ai.OpenAIPreview,'draft',bad);out=await ai.preview(db,text,profile(locked=['علی']),'openai',True);assert out['blocked'] and out['output']==text and out['missing'] and out['added'];assert db.get_setting('ai43_usage')['count']==1
    for _ in range(9):await ai.preview(db,text,profile(),'openai',True)
    with pytest.raises(ValueError,match='سقف'):await ai.preview(db,text,profile(),'openai',True)

def test_locked_names_and_links():
    assert ai.missing_facts('علی https://example.com ۲۵۰ هزار تومان','حسن https://other.com ۲۵۰ هزار تومان',['علی'])==(['https://example.com','علی'],['https://other.com'])

def test_lab_api_auth_no_publish(tmp_path,monkeypatch):
    app=create_app(tmp_path,start_workers=False);h={'X-Studio-Key':app.state.service.admin_key,'Origin':'http://127.0.0.1:8765'};db=app.state.service.db
    with TestClient(app,base_url='http://127.0.0.1:8765',client=('127.0.0.1',50000)) as c:
        assert c.get('/api/ai-lab').status_code==401
        assert not c.get('/api/ai-lab',headers=h).json()['enabled']
        assert c.post('/api/richtext',headers=h,json={'modes':r.DEFAULTS}).status_code==200
        assert c.post('/api/richtext',headers=h,json={'modes':{'x':'html'}}).status_code==422
        assert c.post('/api/ai-lab/profile/bale',headers=h,json=profile()).status_code==200
        assert c.get('/api/ai-lab',headers=h).json()['profiles']['bale']['brand']=='آکادمی'
        assert c.get('/api/ai-lab/sample',headers=h).status_code==404
        db.update_source(dict(mode='bot',channel='@source',bot_token=TOKEN));db.ingest(1,'متن مجاز');db.ingest(2,'محرمانه',protected=True)
        assert c.get('/api/ai-lab/sample',headers=h).json()['text']=='متن مجاز'
        result=c.post('/api/ai-lab/preview',headers=h,json={'text':'نمونه','profile':profile()});assert result.status_code==200 and not result.json()['published'];assert not db.snapshot()['stats']
        assert c.post('/api/ai-lab/preview',headers=h,json={'text':'نمونه','profile':profile(),'provider':'openai'}).status_code==400

@pytest.mark.asyncio
async def test_external_wire_request_and_safe_error(monkeypatch):
    captures=[]
    async def transport(req):
        captures.append(req);return httpx.Response(200,json={'choices':[{'message':{'content':json.dumps({'body':'sample','title':'title','topic':'general','hashtags':''})}}]})
    real=httpx.AsyncClient
    monkeypatch.setattr(ai.httpx,'AsyncClient',lambda **kw:real(transport=httpx.MockTransport(transport),**kw))
    result=await ai.OpenAIPreview('test-key','gpt-4o-mini').draft('sample',profile());assert result.body=='sample'
    req=captures[0];assert str(req.url)=='https://api.openai.com/v1/chat/completions' and req.headers['authorization']=='Bearer test-key'
    data=json.loads(req.content);assert data['max_completion_tokens']==1600 and 'tools' not in data and data['response_format']['type']=='json_object'

@pytest.mark.asyncio
async def test_user_archive_entities(tmp_path):
    db=db_at(tmp_path);db.update_source({'mode':'user','api_id':'123','api_hash':'0123456789abcdef0123456789abcdef','history_enabled':True});col=Collector(db)
    e=type('MessageEntityBold',(),{'offset':3,'length':3})();msg=SimpleNamespace(id=1,message='🔥 متن',entities=[e],media=None)
    await col._ingest_user(msg,db.get_source());job=db.next_delivery('bale');assert json.loads(job['entities'])==[{'type':'bold','start':2,'end':5}]
