"""Canonical half-open Unicode codepoint spans; Telegram wire offsets are UTF-16.
Pure rendering functions, no network or global state. Unsupported formatting is plain.
"""
from __future__ import annotations
import json, re
from urllib.parse import urlsplit

KINDS = {'bold','italic','underline','strikethrough','code','pre','text_link','blockquote','spoiler'}
TELETHON = {'MessageEntityBold':'bold','MessageEntityItalic':'italic','MessageEntityUnderline':'underline','MessageEntityStrike':'strikethrough','MessageEntityCode':'code','MessageEntityPre':'pre','MessageEntityTextUrl':'text_link','MessageEntityBlockquote':'blockquote','MessageEntitySpoiler':'spoiler'}
MODES = {'telegram':('native','plain'),'bale':('markdown','plain'),'eitaa':('plain','markdown','html'),'rubika':('plain','metadata'),'soroush':('plain',)}
DEFAULTS = {'telegram':'native','bale':'markdown','eitaa':'plain','rubika':'plain','soroush':'plain'}

def boundaries(text):
    out=[0]
    for ch in text: out.append(out[-1]+(2 if ord(ch)>0xffff else 1))
    return out

def safe_url(value):
    if not isinstance(value,str) or len(value)>2000 or any(ch.isspace() or ord(ch)<32 for ch in value):return False
    try:
        u=urlsplit(value);return u.scheme in ('http','https') and bool(u.hostname) and not u.username and not u.password
    except ValueError:return False

def normalize(text, spans):
    result=[]
    if not isinstance(spans,(list,tuple)):return result
    for item in spans[:256]:
        if not isinstance(item,dict):continue
        k,s,e=item.get('type'),item.get('start'),item.get('end')
        if not isinstance(k,str) or k not in KINDS or type(s) is not int or type(e) is not int or not 0<=s<e<=len(text):continue
        out={'type':k,'start':s,'end':e}
        if k=='text_link':
            if not safe_url(item.get('url')):continue
            out['url']=item['url']
        if k=='pre' and isinstance(item.get('language'),str) and re.fullmatch(r'[A-Za-z0-9_+-]{1,32}',item['language']):out['language']=item['language']
        if out not in result:result.append(out)
    return sorted(result,key=lambda x:(x['start'],-x['end'],x['type']))

def from_wire(text, entities, telethon=False):
    idx={v:i for i,v in enumerate(boundaries(text))};spans=[]
    for e in (entities or [])[:256]:
        if telethon:
            kind=TELETHON.get(type(e).__name__);get=lambda n:getattr(e,n,None)
        elif isinstance(e,dict):kind=e.get('type');get=e.get
        else:continue
        o,l=get('offset'),get('length')
        if type(o) is not int or type(l) is not int or o not in idx or o+l not in idx:continue
        spans.append({'type':kind,'start':idx[o],'end':idx[o+l],'url':get('url'),'language':get('language')})
    return normalize(text,spans)

def load(text, raw):
    try:return normalize(text,json.loads(raw) if isinstance(raw,str) else raw)
    except (ValueError,TypeError):return []

def shifted(text,spans,prefix,suffix):
    final=(prefix+text+suffix)[:15000];n=len(prefix)
    return final,normalize(final,[{**s,'start':s['start']+n,'end':s['end']+n} for s in normalize(text,spans)])

def wire(text,spans):
    b=boundaries(text)
    return [{'type':s['type'],'offset':b[s['start']],'length':b[s['end']]-b[s['start']],**{k:s[k] for k in ('url','language') if k in s}} for s in normalize(text,spans)]

def render(platform,text,spans,mode=None,caption=False):
    """Return text, API parameters, warnings. Never retry a rejected send automatically."""
    spans=normalize(text,spans);mode=mode or DEFAULTS.get(platform,'plain');warnings=[]
    if not spans:return text,{},warnings
    if mode not in MODES.get(platform,('plain',)):mode='plain'
    if mode=='native':return text,{'caption_entities' if caption else 'entities':wire(text,spans)},warnings
    if mode=='metadata':
        # Index semantics for astral characters have not been verified against live Rubika.
        if any(ord(c)>0xffff for c in text):return text,{},['روبیکا: اندیس ایموجی هنوز تأیید نشده؛ متن ساده']
        types={'bold':'Bold','italic':'Italic','underline':'Underline','strikethrough':'Strike'}
        parts=[{'type':types[s['type']],'from_index':s['start'],'length':s['end']-s['start']} for s in spans if s['type'] in types]
        if len(parts)!=len(spans):warnings.append('بعضی قالب‌ها در روبیکا به متن ساده تبدیل شدند')
        return text,{'metadata':{'meta_data_parts':parts}} if parts else {},warnings
    if mode in ('markdown','html'):
        # Conservative renderer: non-overlapping bold/italic only; avoid corrupting original punctuation.
        import html
        tags={'bold':('*','*'),'italic':('_','_')} if mode=='markdown' else {'bold':('<b>','</b>'),'italic':('<i>','</i>'),'underline':('<u>','</u>'),'strikethrough':('<s>','</s>'),'code':('<code>','</code>')}
        if mode=='markdown' and re.search(r'[*_`\\\[\]]',text):return text,{},['نشانه‌های Markdown در متن اصلی؛ برای جلوگیری از تغییر محتوا، متن ساده ارسال شد']
        supported=[s for s in spans if s['type'] in tags];parts=[];last=0
        esc=html.escape if mode=='html' else lambda t:t
        for s in supported:
            if s['start']<last or '\n' in text[s['start']:s['end']] and mode=='markdown':continue
            if mode=='markdown' and text[s['start']:s['end']].strip()!=text[s['start']:s['end']]:continue
            a,z=tags[s['type']];parts.extend([esc(text[last:s['start']]),a,esc(text[s['start']:s['end']]),z]);last=s['end']
        if not parts:return text,{},['قالب پشتیبانی نشده؛ متن ساده']
        parts.append(esc(text[last:]));out=''.join(parts)
        if len(out)>(950 if caption else 4000):return text,{},['قالب‌بندی از سقف طول عبور کرد؛ متن ساده']
        if len(parts)//4!=len(spans):warnings.append('بعضی قالب‌های تودرتو یا پشتیبانی‌نشده حذف شدند')
        # Bale Markdown is normally auto-detected; Eitaa parse_mode is experimental.
        return out,({'parse_mode':'HTML' if mode=='html' else 'Markdown'} if platform=='eitaa' else {}),warnings
    return text,{},['قالب‌بندی در حالت متن ساده حفظ نمی‌شود']
