"""Bounded Gemini media options and quota diagnostics, no raw provider error exposure."""
import json, re, math, base64

RATIOS=('auto','1:1','16:9','9:16','4:3','3:4','3:2','2:3')
SIZES=('auto','1K','2K','4K')

def quota_hint(raw, retry_after=None):
    zero=False; daily=False; minute=False; free=False; seconds=None
    try:
        obj=json.loads(raw); err=obj.get('error',{}); details=err.get('details',[])
        for d in details if isinstance(details,list) else []:
            if not isinstance(d,dict):continue
            if d.get('@type')=='type.googleapis.com/google.rpc.QuotaFailure':
                for v in d.get('violations',[]) if isinstance(d.get('violations'),list) else []:
                    if not isinstance(v,dict):continue
                    metric=str(v.get('quotaMetric',''))[:300].lower(); ident=str(v.get('quotaId',''))[:300].lower()
                    daily |= 'perday' in ident or 'per_day' in metric
                    minute |= 'perminute' in ident or 'per_minute' in metric
                    free |= 'freetier' in ident or 'free_tier' in metric
                    zero |= str(v.get('quotaValue',''))=='0'
            if d.get('@type')=='type.googleapis.com/google.rpc.RetryInfo':
                m=re.fullmatch(r'(\d{1,8}(?:\.\d{1,9})?)s',str(d.get('retryDelay','')))
                if m:seconds=math.ceil(float(m[1]))
        # Google's message sometimes carries the quota limit when violations omit it.
        msg=err.get('message','');zero |= isinstance(msg,str) and bool(re.search(r'\blimit:\s*0(?=$|[\s,;])',msg,re.I))
    except (ValueError,TypeError,AttributeError):pass
    try:
        n=float(retry_after)
        if math.isfinite(n) and 0<=n<=864000:seconds=max(seconds or 0,math.ceil(n))
    except (ValueError,TypeError):pass
    if zero:return 'سهمیه صفر است؛ پروژه، Billing و دسترسی مدل را بررسی کنید.'
    if daily:return 'محدودیت روزانه مدل گزارش شده؛ Rate Limit پروژه را بررسی کنید.'
    if minute:reason='محدودیت دقیقه‌ای مدل یا توکن؛ بعداً دوباره امتحان کنید.'
    elif free:reason='محدودیت سهمیه رایگان؛ Billing و Rate Limit پروژه را بررسی کنید.'
    else:reason='علت دقیق سهمیه یا ظرفیت اعلام نشده؛ Rate Limit و Billing پروژه را بررسی کنید.'
    if seconds is not None:reason+=f' انتظار اعلام‌شده: {seconds} ثانیه.'
    return reason

def structural_review(original,output):
    return {'original_chars':len(original),'output_chars':len(output),'lines':len(output.splitlines()),'same_text':original.strip()==output.strip(),'hashtags':len(re.findall(r'(?<!\w)#[\w\u200c]+',output)),'semantic_verified':False}

def reference_part(reference):
    if reference is None:return None
    mime=reference.get('mime');data=reference.get('data','')
    if mime not in ('image/png','image/jpeg'):raise ValueError('تصویر مرجع فقط PNG یا JPEG')
    if not isinstance(data,str) or len(data)>4*((4*1024*1024+2)//3):raise ValueError('حداکثر تصویر مرجع ۴ مگابایت است')
    try:raw=base64.b64decode(data,validate=True)
    except (ValueError,TypeError):raise ValueError('تصویر مرجع نامعتبر') from None
    if not 0<len(raw)<=4*1024*1024 or not raw.startswith(b'\x89PNG\r\n\x1a\n' if mime=='image/png' else b'\xff\xd8\xff'):raise ValueError('تصویر مرجع نامعتبر')
    return {'inlineData':{'mimeType':mime,'data':data}}
