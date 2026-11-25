"""Local encrypted credentials and live model discovery. No prompt or publication here."""
from __future__ import annotations
import hashlib, json, os, re, time
import httpx

PROVIDERS=('openai','gemini')
LEGACY={'openai':{'text':['gpt-4o-mini','gpt-4o','gpt-4.1-mini'],'tts':[]},'gemini':{'text':['gemini-3.8-flash','gemini-4-hydrogen'],'tts':['gemini-3.8-flash-tts','gemini-3.8-flash-lite-tts']}}
KEY_NAMES={'openai':('RELAYFORGE_AI_API_KEY',),'gemini':('RELAYFORGE_GEMINI_API_KEY','GEMINI_API_KEY')}
ID=re.compile(r'[A-Za-z0-9][A-Za-z0-9._:-]{0,199}\Z')

def credential(db,provider):
    saved=db.get_setting('ai44_key_'+provider)
    # Explicit removal of local key returns to environment fallback.
    if saved:
        try:return db.reveal(saved),'saved'
        except Exception:raise ValueError('کلید ذخیره‌شده قابل رمزگشایی نیست؛ کلید جدید وارد کنید') from None
    for env in KEY_NAMES[provider]:
        if (value:=os.environ.get(env,'').strip()):return value,'environment'
    return '', 'none'

def fingerprint(key):return hashlib.sha256(key.encode()).hexdigest()

def catalog(db,provider):
    key,_=credential(db,provider); stored=db.get_setting('ai44_catalog_'+provider) or {}
    return stored if key and stored.get('fingerprint')==fingerprint(key) else {}

def options(db,provider,kind):
    c=catalog(db,provider)
    return c.get(kind,[]) if c else LEGACY[provider].get(kind,[])

def check_model(db,provider,kind,model):
    if not ID.fullmatch(model) or model not in options(db,provider,kind):raise ValueError('مدل در فهرست این اتصال نیست؛ فهرست آنلاین را دریافت کنید')

def public(db):
    out={}
    for p in PROVIDERS:
        try:
            _,source=credential(db,p); c=catalog(db,p)
        except ValueError:source='invalid';c={}
        out[p]={'key_source':source,'text':c.get('text',LEGACY[p]['text']),'tts':c.get('tts',LEGACY[p]['tts']),'loaded_at':c.get('loaded_at'),'online':bool(c),'image':c.get('image',[])}
    return out

def save_key(db,provider,operation,key):
    if operation=='save':
        key=key.strip()
        if not 8<=len(key)<=512 or any(ord(ch)<33 or ord(ch)>126 for ch in key):raise ValueError('ساختار کلید API نامعتبر است')
        encrypted=db.secret(key)
    elif operation=='remove':encrypted=''
    else:raise ValueError('عملیات نامعتبر')
    # Update credentials and invalidate model list atomically.
    with db.connection() as c:
        for k,v in [('ai44_key_'+provider,encrypted),('ai44_catalog_'+provider,{})]:
            c.execute('INSERT INTO settings(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',(k,json.dumps(v)))

def daily_limit(db):return int(db.get_setting('ai44_daily_limit') or 10)

async def refresh(db,provider):
    if not db.get_setting('ai43_enabled'):raise ValueError('ابتدا مجوز AI خارجی را فعال و ذخیره کنید')
    key,_=credential(db,provider)
    if not key:raise ValueError('ابتدا کلید API این سرویس را ذخیره کنید')
    text=set();tts=set();images=set();page=None;seen=set()
    url='https://api.openai.com/v1/models' if provider=='openai' else 'https://generativelanguage.googleapis.com/v1beta/models'
    headers={'Authorization':'Bearer '+key} if provider=='openai' else {'x-goog-api-key':key}
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(20,connect=8),follow_redirects=False,trust_env=False) as client:
            for _ in range(20):
                params={} if provider=='openai' else {'pageSize':100,**({'pageToken':page} if page else {})}
                async with client.stream('GET',url,headers=headers,params=params) as resp:
                    if resp.status_code!=200:raise ValueError(f'دریافت مدل‌ها: HTTP {resp.status_code}؛ کلید، شبکه یا مجوز را بررسی کنید')
                    raw=bytearray()
                    async for chunk in resp.aiter_bytes():
                        raw.extend(chunk)
                        if len(raw)>2*1024*1024:raise ValueError('فهرست مدل‌ها بیش از حد بزرگ است')
                obj=json.loads(raw); rows=obj['data' if provider=='openai' else 'models']
                if not isinstance(rows,list):raise ValueError('ساختار فهرست مدل نامعتبر است')
                for row in rows:
                    if not isinstance(row,dict):continue
                    name=row.get('id' if provider=='openai' else 'name','')
                    if not isinstance(name,str):continue
                    name=name.removeprefix('models/')
                    if not ID.fullmatch(name):continue
                    lower=name.lower()
                    if provider=='openai':
                        # ListModels does not declare chat/JSON support: candidates only.
                        if re.match(r'^(gpt-|o[1-9](?:-|$)|chatgpt-)',lower) and not any(x in lower for x in ('audio','realtime','tts','image','transcribe','embedding','moderation','search','codex','instruct')):text.add(name)
                    elif 'generateContent' in row.get('supportedGenerationMethods',[]):
                        if 'tts' in lower:tts.add(name)
                        elif ('image' in lower and lower.startswith('gemini-')) or 'nano-banana' in lower:images.add(name)
                        elif lower.startswith('gemini-') and not any(x in lower for x in ('image','embedding','live','audio','robotics','computer-use','deep-research','transcribe','omni','customtools','decision')):text.add(name)
                page=obj.get('nextPageToken') if provider=='gemini' else None
                if not page:break
                if not isinstance(page,str) or page in seen:raise ValueError('صفحه‌بندی فهرست مدل نامعتبر است')
                seen.add(page)
            else:raise ValueError('فهرست مدل‌ها بیش از حد طولانی است')
    except (httpx.HTTPError,KeyError,TypeError,json.JSONDecodeError,UnicodeError):raise ValueError('ارتباط یا پاسخ فهرست مدل‌ها نامعتبر است') from None
    if credential(db,provider)[0]!=key:raise ValueError('کلید هنگام دریافت فهرست تغییر کرد؛ دوباره تلاش کنید')
    c={'text':sorted(text),'tts':sorted(tts),'image':sorted(images),'loaded_at':int(time.time()),'fingerprint':fingerprint(key)}
    db.set_setting('ai44_catalog_'+provider,c)
    return public(db)[provider]
