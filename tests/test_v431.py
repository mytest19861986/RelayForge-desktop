import base64, io, json, wave
import httpx, pytest
from fastapi.testclient import TestClient
from relayforge import richtext as r, ai43 as ai, gemini431 as g
from relayforge.database import Database
from relayforge.gateways import send, Post
from main import create_app

TEXT='🔥 اطلاعیه مهم\n#برنامه_نویسی\nپایان: امروز!'
SPAN={'type':'bold','start':2,'end':13}
TOKEN='123456:TEST_TOKEN_ABC'
PROFILE={'brand':'','tone':'formal','destination':'بله','instructions':'','template':'{body}','locked':[]}

def enabled(tmp_path,monkeypatch):
    db=Database(tmp_path);db.set_setting('ai43_enabled',True);monkeypatch.setenv('RELAYFORGE_GEMINI_API_KEY','TEST_GEMINI_SECRET');return db

def mock_api(monkeypatch,block,status=200):
    seen=[]; real=httpx.AsyncClient
    async def handler(req):
        seen.append(req);return httpx.Response(status,json={'status':'completed','steps':[{'type':'thought','content':[{'type':'text','text':'not output'}]},{'type':'model_output','content':[block]}]})
    monkeypatch.setattr(g.httpx,'AsyncClient',lambda **kw:real(transport=httpx.MockTransport(handler),**kw));return seen

def text_block(data):return {'type':'text','text':json.dumps(data,ensure_ascii=False)}
def wave_sample():
    b=io.BytesIO()
    with wave.open(b,'wb') as f:f.setnchannels(1);f.setsampwidth(2);f.setframerate(24000);f.writeframes(b'\0\0'*1200)
    return b.getvalue()

def test_bale_hashtag_and_emoji():
    out,opts,warnings=r.render('bale',TEXT,[SPAN]);assert out.startswith('🔥 *اطلاعیه مهم*\n#برنامه_نویسی') and not opts and warnings
    assert r.render('bale',TEXT,[SPAN],'markdown_double')[0].startswith('🔥 **اطلاعیه مهم**')

def test_multiline_bold_trim_and_punctuation():
    t='خبر: امروز\n فردا!';s={'type':'bold','start':4,'end':len(t)-1};out,_,_=r.render('bale',t,[s]);assert out=='خبر: *امروز*\n *فردا* !'

def test_bold_prioritized_over_italic():
    t='الف ب پ';s=[{'type':'italic','start':0,'end':len(t)},{'type':'bold','start':4,'end':5}];assert '*ب*' in r.render('bale',t,s)[0]

@pytest.mark.parametrize('mode,expected,params',[('markdown_raw','*اطلاعیه مهم*',{}),('markdown_double','**اطلاعیه مهم**',{}),('markdown','*اطلاعیه مهم*',{'parse_mode':'Markdown'}),('html','<b>اطلاعیه مهم</b>',{'parse_mode':'HTML'})])
def test_eitaa_modes(mode,expected,params):
    out,options,_=r.render('eitaa',TEXT,[SPAN],mode);assert expected in out and options==params

def test_rubika_emoji_offsets():
    assert r.render('rubika',TEXT,[SPAN],'metadata')[1]['metadata']['meta_data_parts'][0]=={'type':'Bold','from_index':3,'length':11}
    assert r.render('rubika',TEXT,[SPAN],'metadata_codepoint')[1]['metadata']['meta_data_parts'][0]['from_index']==2

@pytest.mark.asyncio
async def test_eitaa_bale_actual_payload(monkeypatch,tmp_path):
    calls=[]
    async def fake(client,url,**kw):calls.append((url,kw));return {'ok':True,'result':{'message_id':123}}
    monkeypatch.setattr('relayforge.gateways._post',fake)
    await send('bale',TOKEN,'@x',Post(TEXT,entities=(SPAN,)));assert '*اطلاعیه مهم*' in calls[-1][1]['json']['text']
    await send('eitaa',TOKEN,'@x',Post(TEXT,entities=(SPAN,),format_mode='markdown_raw'));assert calls[-1][1]['data']['chat_id']=='x' and '*اطلاعیه مهم*' in calls[-1][1]['data']['text'] and 'parse_mode' not in calls[-1][1]['data']
    f=tmp_path/'test.jpg';f.write_bytes(b'abc');await send('eitaa',TOKEN,'@x',Post(TEXT,'photo',str(f),(SPAN,),'markdown_raw'));assert '*اطلاعیه مهم*' in calls[-1][1]['data']['caption'] and calls[-1][0].endswith('/sendFile')

@pytest.mark.asyncio
async def test_gemini_preview_wire(tmp_path,monkeypatch):
    db=enabled(tmp_path,monkeypatch);seen=mock_api(monkeypatch,text_block({'body':'نمونه ۲۵۰ تومان','title':'خبر','topic':'آموزش','hashtags':''}))
    out=await ai.preview(db,'نمونه ۲۵۰ تومان',PROFILE,'gemini',True,g.TEXT_MODELS[0]);assert not out['blocked'] and not out['published'];req=seen[0];assert str(req.url)==g.URL and req.headers['x-goog-api-key']=='TEST_GEMINI_SECRET' and 'TEST_GEMINI_SECRET' not in str(req.url);p=json.loads(req.content);assert p['store'] is False and p['response_format']['schema']['required']==['body','title','topic','hashtags'] and 'tools' not in p;assert db.get_setting('ai43_usage')['count']==1

@pytest.mark.asyncio
async def test_gemini_facts_and_consent(tmp_path,monkeypatch):
    db=enabled(tmp_path,monkeypatch);seen=mock_api(monkeypatch,text_block({'body':'نمونه ۲۵ تومان','title':'خبر','topic':'آموزش','hashtags':''}))
    with pytest.raises(ValueError):await ai.preview(db,'نمونه ۲۵۰ تومان',PROFILE,'gemini',False,g.TEXT_MODELS[0])
    assert not seen
    out=await ai.preview(db,'نمونه ۲۵۰ تومان',PROFILE,'gemini',True,g.TEXT_MODELS[0]);assert out['blocked'] and out['output']=='نمونه ۲۵۰ تومان'

@pytest.mark.asyncio
async def test_podcast_script(tmp_path,monkeypatch):
    db=enabled(tmp_path,monkeypatch);seen=mock_api(monkeypatch,text_block({'script':'Host: قیمت ۲۵۰ تومان\nGuest: ممنون'}));r=await g.podcast_script(db,'قیمت ۲۵۰ تومان',True,g.TEXT_MODELS[0]);assert not r['blocked'] and r['review_required'] and not r['published'];assert len(seen)==1

@pytest.mark.parametrize('mode,text',[('narration','سلام دنیا'),('podcast','Host: سلام\nGuest: درود')])
@pytest.mark.asyncio
async def test_speech_wire_and_wav(mode,text,tmp_path,monkeypatch):
    db=enabled(tmp_path,monkeypatch);raw=wave_sample();seen=mock_api(monkeypatch,{'type':'audio','mime_type':'audio/wav','data':base64.b64encode(raw).decode()});out=await g.speech(db,text,True,g.TTS_MODELS[0],mode,'Kore','Puck','neutral');assert base64.b64decode(out['audio'])==raw and not out['published'];p=json.loads(seen[0].content);assert p['response_format']=={'type':'audio'};sc=p['generation_config']['speech_config'];parts=p['input'][0]['content'];assert ('speakers' in sc)==(mode=='podcast');assert 'speaker' in parts[0]['annotations'][0] if mode=='podcast' else 'speaker' not in parts[0]['annotations'][0];assert db.snapshot()['total_posts']==0

@pytest.mark.asyncio
async def test_invalid_dialogue_and_voices_no_charge(tmp_path,monkeypatch):
    db=enabled(tmp_path,monkeypatch);seen=mock_api(monkeypatch,{})
    with pytest.raises(ValueError):await g.speech(db,'Host: فقط یک گوینده',True,g.TTS_MODELS[0],'podcast','Kore','Puck','neutral')
    with pytest.raises(ValueError):await g.speech(db,'Host: سلام\nGuest: درود',True,g.TTS_MODELS[0],'podcast','Kore','Kore','neutral')
    assert not seen and not db.get_setting('ai43_usage')

@pytest.mark.asyncio
async def test_error_no_secret_no_retry(tmp_path,monkeypatch):
    db=enabled(tmp_path,monkeypatch);seen=mock_api(monkeypatch,{},429)
    with pytest.raises(ValueError,match='HTTP 429') as err:await g.speech(db,'سلام',True,g.TTS_MODELS[0],'narration','Kore','Puck','neutral')
    assert 'TEST_GEMINI_SECRET' not in str(err.value) and len(seen)==1 and db.get_setting('ai43_usage')['count']==1

def test_pcm_and_invalid_audio():
    x=g.wav_bytes({'mime_type':'audio/L16;rate=24000','data':base64.b64encode(b'\0\0'*100).decode()});assert x.startswith(b'RIFF')
    with pytest.raises(ValueError):g.wav_bytes({'mime_type':'audio/wav','data':'%%%invalid'})
    with pytest.raises(ValueError):g.wav_bytes({'mime_type':'audio/wav','data':base64.b64encode(b'RIFF').decode()})

def test_new_api_auth_and_config(tmp_path,monkeypatch):
    monkeypatch.setenv('RELAYFORGE_GEMINI_API_KEY','SECRET');app=create_app(tmp_path,start_workers=False);h={'X-Studio-Key':app.state.service.admin_key,'Origin':'http://127.0.0.1:8765'}
    with TestClient(app,base_url='http://127.0.0.1:8765',client=('127.0.0.1',50000)) as c:
        for path in ['speech','podcast-script']:
            assert c.post('/api/ai-lab/'+path,headers={'Origin':'http://127.0.0.1:8765'},json={'text':'سلام'}).status_code==401
            assert c.post('/api/ai-lab/'+path,headers=h,json={'text':'سلام'}).status_code==400
        cfg=c.get('/api/ai-lab',headers=h).json();assert cfg['gemini_key_available'] and 'SECRET' not in json.dumps(cfg)
        cfg=c.get('/api/richtext',headers=h).json();assert cfg['recommended']['eitaa']=='markdown_raw' and cfg['modes']['eitaa']=='plain'
