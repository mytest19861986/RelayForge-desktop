import asyncio,json
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from main import create_app
from relayforge import style437 as st

def rows(n=160):
    return [dict(text=f'خبر {i}\nمتن 🔥 #خبر',entities=[{'type':'bold','start':0,'end':3}],kind='photo' if i%3==0 else 'text',date='') for i in range(n)]

def test_sample_spread_dedupe():
    r=rows();s=st.sample(r+[r[0]],120);assert len(s)==120 and s[0]==r[0] and s[-1]==r[-1]
    assert st.metrics(s)['bold_percent']==100

@pytest.mark.parametrize('value,expected',[('@SampleChannel','@SampleChannel'),('https://t.me/SampleChannel','@SampleChannel'),('-100123456789',-100123456789)])
def test_ref(value,expected):assert st.channel_ref(value)==expected

@pytest.mark.parametrize('value',['https://bad.test/a','https://t.me/+private','../x','@abc/1'])
def test_invalid_ref(value):
    with pytest.raises(ValueError):st.channel_ref(value)

def test_profile_save(tmp_path):
    app=create_app(tmp_path,start_workers=False);db=app.state.service.db
    x=asyncio.run(st.analyze(db,rows(),120,'test','archive'));assert (x['train_count'],x['holdout_count'])==(90,30)
    assert len(x['profiles'])==2 and x['model'] is None
    st.save(db,x);assert st.get(db,x['id'])==x;assert st.list_profiles(db)==[x]
    assert 'خبر 0' not in (tmp_path/'styles'/f"{x['id']}.json").read_text()
    with pytest.raises(ValueError):st.get(db,'../test')

def test_minimum(tmp_path):
    with pytest.raises(ValueError):asyncio.run(st.analyze(None,rows(5),30,'x','archive'))

def test_consent():
    with pytest.raises(ValueError):asyncio.run(st.analyze(None,rows(),120,'x','archive',True,False))

def test_ai_bounded(tmp_path,monkeypatch):
    db=create_app(tmp_path,start_workers=False).state.service.db
    async def fake(db,consent,mode,model,prompt,fields,maximum):
        payload=json.loads(prompt[prompt.index('{'):]);assert len(payload['examples'])<=16;assert all(len(x['text'])<=500 for x in payload['examples']);assert consent
        return {k:'دستور ساختار' for k in fields},'gemini-test',[]
    monkeypatch.setattr(st.ai436,'route',fake)
    r=asyncio.run(st.analyze(db,rows(),120,'x','archive',True,True));assert r['model']=='gemini-test';assert 'دستور ساختار' in r['profiles'][0]['instructions']

def test_api(tmp_path,monkeypatch):
    app=create_app(tmp_path,start_workers=False);db=app.state.service.db
    monkeypatch.setattr(st,'local_samples',lambda db,count:rows());h={'X-Studio-Key':app.state.service.admin_key,'Origin':'http://127.0.0.1:8765'}
    with TestClient(app,base_url='http://127.0.0.1:8765',client=('127.0.0.1',50000)) as c:
        assert c.get('/api/styles').status_code==401
        r=c.post('/api/styles/analyze',headers=h,json={'name':'آزمایش','count':120});assert r.status_code==200,r.text
        x=r.json();assert c.get('/api/styles/'+x['id'],headers=h).json()==x
        assert c.get('/api/styles',headers=h).json()['styles']==[x]
        assert c.post('/api/styles/analyze',headers=h,json={'name':'x','use_ai':True}).status_code==400
        html=c.get('/').text;assert 'data-studio="channel"' in html and 'v4.3.8' in html
        assert not app.state.service.engine.active

def test_edit(tmp_path):
    db=create_app(tmp_path,start_workers=False).state.service.db
    x=st.save(db,asyncio.run(st.analyze(db,rows(),120,'x','archive')));key=x['profiles'][0]['key']
    y=st.edit(db,x['id'],'نام جدید',key,'لحن جدید','{title}\n{body}')
    assert y['name']=='نام جدید' and st.get(db,x['id'])['profiles'][0]['template']=='{title}\n{body}'
    with pytest.raises(ValueError):st.edit(db,x['id'],'x',key,'x','{bad}')
