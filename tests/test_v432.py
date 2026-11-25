from email.parser import BytesParser
from email.policy import default
from urllib.parse import parse_qs
import httpx
import pytest
from fastapi.testclient import TestClient
from main import create_app
from relayforge import richtext
from relayforge.gateways import Post, send
from relayforge.engine import Engine
from relayforge.database import Database

TEXT='🔥 آغاز <نمونه>\nاطلاعیه مهم & پایان #برنامه_نویسی'
BOLD='اطلاعیه مهم'
SPAN={'type':'bold','start':TEXT.index(BOLD),'end':TEXT.index(BOLD)+len(BOLD)}
EXPECTED='🔥 آغاز &lt;نمونه&gt;\n<b>اطلاعیه مهم</b> &amp; پایان #برنامه_نویسی'
TOKEN='123456:TEST_TOKEN_ABC'

@pytest.mark.parametrize('caption',[False,True])
def test_eitaa_html_lowercase_render(caption):
    assert richtext.render('eitaa',TEXT,[SPAN],caption=caption)==(EXPECTED,{'parse_mode':'html'},[])
    assert richtext.render('eitaa',TEXT,[SPAN],'plain',caption)==(TEXT,{},['قالب‌بندی در حالت متن ساده حفظ نمی‌شود'])

@pytest.mark.asyncio
@pytest.mark.parametrize('media',[False,True])
async def test_eitaa_http_wire(monkeypatch,tmp_path,media):
    requests=[]
    async def handler(req):
        await req.aread();requests.append(req)
        return httpx.Response(200,json={'ok':True,'result':{'message_id':432}})
    real=httpx.AsyncClient
    monkeypatch.setattr('relayforge.gateways.httpx.AsyncClient',lambda **kw:real(transport=httpx.MockTransport(handler),**kw))
    path=tmp_path/'image.jpg';path.write_bytes(b'example')
    result=await send('eitaa',TOKEN,'11306027',Post(TEXT,'photo' if media else 'text',str(path) if media else None,(SPAN,)))
    assert result=='432' and len(requests)==1
    req=requests[0]
    if media:
        assert req.url.path.endswith('/sendFile')
        msg=BytesParser(policy=default).parsebytes(b'Content-Type: '+req.headers['content-type'].encode()+b'\r\nMIME-Version: 1.0\r\n\r\n'+req.content)
        fields={p.get_param('name',header='content-disposition'):p.get_payload(decode=True) for p in msg.iter_parts()}
        assert fields['caption'].decode()==EXPECTED and fields['parse_mode']==b'html' and fields['chat_id']==b'11306027' and fields['file']==b'example'
    else:
        assert req.url.path.endswith('/sendMessage')
        assert parse_qs(req.content.decode())=={'chat_id':['11306027'],'text':[EXPECTED],'parse_mode':['html']}

def test_saved_plain_preserved(tmp_path):
    app=create_app(tmp_path,start_workers=False)
    app.state.service.db.set_setting('rich43_modes',{'eitaa':'plain','bale':'markdown_double'})
    h={'X-Studio-Key':app.state.service.admin_key,'Origin':'http://127.0.0.1:8765'}
    with TestClient(app,base_url='http://127.0.0.1:8765',client=('127.0.0.1',50000)) as c:
        cfg=c.get('/api/richtext',headers=h).json()
        assert cfg['modes']['eitaa']=='plain' and cfg['modes']['bale']=='markdown_double' and cfg['recommended']['eitaa']=='html'
        modes=cfg['modes'];modes['eitaa']='html'
        assert c.post('/api/richtext',headers=h,json={'modes':modes}).status_code==200
        assert c.get('/api/richtext',headers=h).json()['modes']['eitaa']=='html'
