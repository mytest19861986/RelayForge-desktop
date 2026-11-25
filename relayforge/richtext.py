"""Canonical half-open Unicode codepoint spans; Telegram wire offsets are UTF-16.
Pure rendering functions, no network or global state. Unsupported formatting is plain.
"""
from __future__ import annotations
import json, re
from urllib.parse import urlsplit

KINDS = {'bold','italic','underline','strikethrough','code','pre','text_link','blockquote','spoiler'}
TELETHON = {'MessageEntityBold':'bold','MessageEntityItalic':'italic','MessageEntityUnderline':'underline','MessageEntityStrike':'strikethrough','MessageEntityCode':'code','MessageEntityPre':'pre','MessageEntityTextUrl':'text_link','MessageEntityBlockquote':'blockquote','MessageEntitySpoiler':'spoiler'}
MODES = {'telegram':('native','plain'),'bale':('markdown','markdown_double','plain'),'eitaa':('plain','markdown_raw','markdown_double','markdown','html'),'rubika':('plain','metadata','metadata_codepoint'),'soroush':('plain',)}
DEFAULTS = {'telegram':'native','bale':'markdown','eitaa':'html','rubika':'plain','soroush':'plain'}
RECOMMENDED = {**DEFAULTS,'eitaa':'html','rubika':'metadata'}

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
    if mode in ('metadata','metadata_codepoint'):
        types={'bold':'Bold','italic':'Italic','underline':'Underline','strikethrough':'Strike','code':'Mono','pre':'Pre'}
        b=boundaries(text) if mode=='metadata' else list(range(len(text)+1))
        # Prioritize bold if the API's metadata count limit is reached.
        supported=sorted((s for s in spans if s['type'] in types),key=lambda s:(s['type']!='bold',s['start'],s['end']))[:30]
        parts=[{'type':types[s['type']],'from_index':b[s['start']],'length':b[s['end']]-b[s['start']]} for s in supported]
        if len(parts)!=len(spans):warnings.append('بعضی قالب‌ها در روبیکا به متن ساده تبدیل شدند')
        return text,{'metadata':{'meta_data_parts':parts}} if parts else {},warnings
    if mode in ('markdown','markdown_raw','markdown_double','html'):
        import html
        if mode=='html':
            tags={'bold':('<b>','</b>'),'italic':('<i>','</i>'),'underline':('<u>','</u>'),'strikethrough':('<s>','</s>'),'code':('<code>','</code>')}
        else:
            tags={'bold':('**','**'),'italic':('__','__')} if mode=='markdown_double' else {'bold':('*','*'),'italic':('_','_')}
        esc=html.escape if mode=='html' else lambda t:t
        # Format independent runs: an underscore in a hashtag must not disable a bold heading.
        # Bold wins overlaps; split each line and keep surrounding whitespace outside markers.
        selected=[]; occupied=set(); dropped=False
        for span in sorted(spans,key=lambda a:(a['type']!='bold',a['start'],-a['end'])):
            k=span['type']
            if k not in tags:dropped=True;continue
            a,z=span['start'],span['end']
            if mode=='html':
                candidates=[(a,z)]
            else:
                candidates=[]
                for match in re.finditer(r'[^\n\r]+',text[a:z]):
                    x,y=a+match.start(),a+match.end()
                    while x<y and text[x].isspace():x+=1
                    while y>x and text[y-1].isspace():y-=1
                    if x<y:candidates.append((x,y))
            for x,y in candidates:
                # Do not generate ambiguous markers within a span; preserve the original characters.
                if mode!='html' and (tags[k][0][0] in text[x:y] or '\\' in text[x:y]):dropped=True;continue
                if any(i in occupied for i in range(x,y)):dropped=True;continue
                occupied.update(range(x,y));selected.append((x,y,k))
        if not selected:return text,{},['قالب پشتیبانی نشده؛ متن ساده']
        parts=[];last=0
        for x,y,k in sorted(selected):
            parts.append(esc(text[last:x]));a,z=tags[k]
            # Bale parser requires separation around delimiters. Insert only at non-whitespace boundaries.
            before=' ' if platform=='bale' and x>0 and not text[x-1].isspace() else ''
            after=' ' if platform=='bale' and y<len(text) and not text[y].isspace() else ''
            parts.extend([before,a,esc(text[x:y]),z,after]);last=y
        parts.append(esc(text[last:]));out=''.join(parts)
        if len(out)>(950 if caption else 4000):return text,{},['قالب‌بندی از سقف طول عبور کرد؛ متن ساده']
        if dropped:warnings.append('بعضی قالب‌های تودرتو یا پشتیبانی‌نشده حذف شدند')
        if mode!='html' and re.search(r'[*_`]',text):warnings.append('نشانه‌های اصلی Markdown حفظ شدند؛ نمایش آن‌ها در مقصد را بررسی کنید')
        return out,({'parse_mode':'html' if mode=='html' else 'Markdown'} if platform=='eitaa' and mode in ('html','markdown') else {}),warnings
    return text,{},['قالب‌بندی در حالت متن ساده حفظ نمی‌شود']
