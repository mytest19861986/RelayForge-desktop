"""Manual publication, bounded local attachments and channel identity helpers."""
from __future__ import annotations
import base64, binascii, collections, re, tempfile
from pathlib import Path
from contextlib import contextmanager
from pydantic import BaseModel, ConfigDict, Field
from typing import Literal

MAX_MEDIA=20*1024*1024
MAX_REQUEST=28*1024*1024
EXTENSIONS={'.jpg':'photo','.jpeg':'photo','.png':'photo','.webp':'photo','.gif':'animation','.mp4':'video','.mp3':'audio','.wav':'audio','.ogg':'voice','.m4a':'audio','.pdf':'document','.txt':'document','.docx':'document','.xlsx':'document','.pptx':'document','.zip':'document'}
HANDLE=re.compile(r'(?<![\w@])@[A-Za-z][A-Za-z0-9_]{3,31}\b')
URL=re.compile(r'https?://[^\s<>]+')
SEPARATOR=re.compile(r'^[\s━─═—–_\-•·*|✦◆●▪️]{3,80}$')

def identity(rows,source):
    """Only explicit metadata and recurring footer candidates; never infer a title as fact."""
    title=next((str(r.get('channel_title','')).strip()[:120] for r in rows if r.get('channel_title')), '')
    handle=next((str(r.get('channel_handle','')).strip() for r in rows if HANDLE.fullmatch(str(r.get('channel_handle','')).strip())), '')
    if not handle and HANDLE.fullmatch(source):handle=source
    signatures=collections.Counter(); links=collections.Counter(); handles=collections.Counter(); separators=collections.Counter(); placement=collections.Counter()
    for row in rows:
        lines=[x.strip() for x in row.get('text','').splitlines() if x.strip()]; tail=lines[-4:]
        for line in dict.fromkeys(tail):
            if SEPARATOR.fullmatch(line):separators[line]+=1
            elif len(line)<=160 and not any(ord(c)<32 for c in line) and (HANDLE.search(line) or URL.search(line) or re.match(r'^(?:📢|📣|🔔|عضویت|کانال|رسانه|منبع|آیدی|ارتباط|تبلیغات)',line)):
                signatures[line]+=1
        for h in dict.fromkeys(HANDLE.findall('\n'.join(tail))):handles[h]+=1
        for link in dict.fromkeys(URL.findall('\n'.join(tail))):
            if len(link)<=200:links[link]+=1
        for i,line in enumerate(lines):
            if URL.search(line) or HANDLE.search(line):placement['footer' if i>=len(lines)-4 else 'body']+=1
    threshold=max(2,(len(rows)+4)//5)
    def common(counter,limit):return [v for v,n in counter.most_common(limit) if n>=threshold]
    candidates=common(handles,5); link_candidates=common(links,5); footer=common(signatures,4); sep=common(separators,2)
    return {'title':title,'handle':handle,'handle_candidates':candidates,'links':link_candidates,'signature_candidates':footer,'separators':sep,'link_placement':dict(placement),'review_required':True}

def append_footer(text,footer):
    seen={line.strip() for line in text.splitlines() if line.strip()}; lines=[]
    for line in footer.splitlines():
        line=line.strip()
        if line and line not in seen:lines.append(line);seen.add(line)
    result=text.rstrip()+ ('\n\n'+'\n'.join(lines) if lines else '')
    if len(result)>4000:raise ValueError('متن همراه امضا بیش از ۴۰۰۰ نویسه است')
    return result

class Attachment(BaseModel):
    model_config=ConfigDict(extra='forbid')
    name:str=Field(min_length=1,max_length=180)
    data:str=Field(min_length=1,max_length=4*((MAX_MEDIA+2)//3))

class Publication(BaseModel):
    model_config=ConfigDict(extra='forbid')
    text:str=Field(default='',max_length=4000)
    routes:list[str]=Field(min_length=1,max_length=10)
    confirm:Literal[True]
    attachment:Attachment|None=None
    bold_heading:bool=False

@contextmanager
def attachment_file(attachment):
    if attachment is None:
        yield '', 'text';return
    name=attachment.name
    if not re.fullmatch(r'[^/\\:\x00-\x1f<>|?*]{1,180}',name) or name.startswith('.') or name.rstrip(' .')!=name:raise ValueError('نام فایل نامعتبر است')
    suffix=Path(name).suffix.lower();kind=EXTENSIONS.get(suffix)
    if not kind:raise ValueError('نوع فایل پشتیبانی نمی‌شود')
    try:raw=base64.b64decode(attachment.data,validate=True)
    except (ValueError,binascii.Error):raise ValueError('داده فایل نامعتبر است') from None
    if not 0<len(raw)<=MAX_MEDIA:raise ValueError('حداکثر اندازه فایل ۲۰ مگابایت است')
    if suffix in ('.jpg','.jpeg') and not raw.startswith(b'\xff\xd8\xff'):raise ValueError('فایل JPEG معتبر نیست')
    if suffix=='.png' and not raw.startswith(b'\x89PNG\r\n\x1a\n'):raise ValueError('فایل PNG معتبر نیست')
    if suffix=='.gif' and raw[:6] not in (b'GIF87a',b'GIF89a'):raise ValueError('فایل GIF معتبر نیست')
    if suffix=='.webp' and (raw[:4]!=b'RIFF' or raw[8:12]!=b'WEBP'):raise ValueError('فایل WebP معتبر نیست')
    if suffix=='.pdf' and not raw.startswith(b'%PDF-'):raise ValueError('فایل PDF معتبر نیست')
    # The attachment is treated as opaque bytes, never executed or unpacked.
    with tempfile.TemporaryDirectory(prefix='relayforge-publish-') as tmp:
        # Use a safe fixed basename (Windows reserved names cannot be supplied).
        p=Path(tmp)/('attachment'+suffix);p.write_bytes(raw)
        yield str(p),kind
