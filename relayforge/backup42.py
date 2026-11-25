"""Versioned authenticated backup envelope. No archive paths are extracted blindly."""
from __future__ import annotations
import hashlib, io, json, os, re, secrets, shutil, sqlite3, tempfile, time, zipfile
from pathlib import Path, PurePosixPath
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.fernet import Fernet
from cryptography.exceptions import InvalidTag
from .database import DEFAULT_SOURCE, PLATFORMS

MAGIC=b'RELAYFORGE-BACKUP-42\0'
MAX_BYTES=128*1024*1024
DB='relayforge.sqlite3'
SESSION=re.compile(r'(?:telegram_user_[0-9a-f]{24}|soroush_user_[0-9a-f]{24})\.session(?:-journal|-wal|-shm)?\Z')

def private_dir(p):
    p.mkdir(parents=True,exist_ok=True)
    if os.name!='nt': p.chmod(0o700)
    return p

def private_file(p, content):
    with p.open('xb') as f: f.write(content)
    if os.name!='nt': p.chmod(0o600)

def local_password(root):
    # Local automatic backups require the original key; portable backups use a user password.
    return (root/'secrets.key').read_text(encoding='ascii').strip()

def password_check(password):
    if not isinstance(password,str) or not 12<=len(password)<=256:
        raise ValueError('رمز پشتیبان باید ۱۲ تا ۲۵۶ نویسه باشد')

def derive(password,salt):
    password_check(password)
    return hashlib.scrypt(password.encode('utf8'),salt=salt,n=2**15,r=8,p=1,dklen=32,maxmem=64*1024*1024)

def seal(raw,password):
    if len(raw)>MAX_BYTES: raise ValueError('حجم پشتیبان بیشتر از ۱۲۸ مگابایت است؛ پشتیبان تنظیمات را انتخاب کنید')
    salt,nonce=secrets.token_bytes(16),secrets.token_bytes(12)
    header=MAGIC+salt+nonce
    return header+AESGCM(derive(password,salt)).encrypt(nonce,raw,header)

def unseal(blob,password):
    h=len(MAGIC)+28
    if len(blob)>MAX_BYTES+h+16 or len(blob)<h+16 or not blob.startswith(MAGIC): raise ValueError('فرمت یا حجم پشتیبان نامعتبر است')
    try:return AESGCM(derive(password,blob[len(MAGIC):len(MAGIC)+16])).decrypt(blob[len(MAGIC)+16:h],blob[h:],blob[:h])
    except InvalidTag:raise ValueError('رمز اشتباه است یا پشتیبان دستکاری شده است') from None

def copy_sqlite(src,dest):
    source=sqlite3.connect(str(src),timeout=20); target=sqlite3.connect(str(dest),timeout=20)
    try:source.backup(target)
    finally:target.close();source.close()

def strip_history(con):
    con.execute('DELETE FROM outbox');con.execute('DELETE FROM source_messages');con.execute('DELETE FROM activity')
    con.execute('DELETE FROM targets WHERE archived=1')
    con.execute("UPDATE targets SET enabled=0,next_allowed=0,fail_streak=0,last_error=''")
    for key,value in {'epoch':1,'bot_offset':0,'history_cursor':0,'live_cursor':None,'source_peer':None}.items():
        con.execute('UPDATE settings SET value=? WHERE key=?',(json.dumps(value),key))
    src=json.loads(con.execute("SELECT value FROM settings WHERE key='source'").fetchone()[0])
    con.execute("UPDATE settings SET value=? WHERE key='history_cursor'",(json.dumps(max(0,int(src.get('start_id',1))-1)),))

def prepare_db(path,mode):
    con=sqlite3.connect(path)
    try:
        con.execute('PRAGMA foreign_keys=ON');con.execute('PRAGMA trusted_schema=OFF')
        if mode=='settings':strip_history(con)
        con.execute("DELETE FROM settings WHERE key LIKE 'backup42_%'")
        con.execute("UPDATE settings SET value='false' WHERE key IN ('run_enabled','ai43_enabled')")
        con.commit()
        if mode=='settings':con.execute('VACUUM') # discard deleted history pages
    finally:con.close()

def collect_files(root):
    for p in root.iterdir():
        if p.is_file() and not p.is_symlink() and SESSION.fullmatch(p.name):yield p
    for sub in ('media','logs'):
        base=root/sub
        if base.exists() and not base.is_symlink():
            for p in base.rglob('*'):
                if p.is_file() and not p.is_symlink() and not any(x.is_symlink() for x in p.parents if x!=root.parent):yield p

def make_backup(root,password,mode='full',label='manual'):
    root=Path(root);password_check(password)
    if mode not in ('full','settings','database'):raise ValueError('نوع پشتیبان نامعتبر')
    dest=private_dir(root/'backups')/(f'{label}-{time.strftime("%Y%m%d-%H%M%S")}-{secrets.token_hex(4)}.rfbackup')
    with tempfile.TemporaryDirectory(prefix='rf42-',dir=private_dir(root/'backups')) as td:
        copy=Path(td)/DB;copy_sqlite(root/DB,copy);prepare_db(copy,mode)
        entries=[(copy,DB),(root/'secrets.key','secrets.key')]
        if mode=='full':entries += [(p,p.relative_to(root).as_posix()) for p in collect_files(root)]
        if sum(p.stat().st_size for p,_ in entries)>MAX_BYTES:raise ValueError('حجم داده بیش از ۱۲۸ مگابایت؛ حالت تنظیمات یا کپی آفلاین data را استفاده کنید')
        buf=io.BytesIO()
        with zipfile.ZipFile(buf,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=3) as z:
            z.writestr('manifest.json',json.dumps({'format':1,'version':'4.3.7','mode':mode,'created':time.time()}))
            for p,name in entries:z.write(p,name)
        private_file(dest,seal(buf.getvalue(),password))
    return dest

def allowed_name(name):
    parts=name.split('/')
    if any(not p or p in ('.','..') or ':' in p or '\\' in p or p.endswith(('.', ' ')) or re.match(r'(?i)^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)',p) for p in parts):return False
    return name in ('manifest.json',DB,'secrets.key') or (len(parts)==1 and SESSION.fullmatch(name)) or (len(parts)>1 and parts[0] in ('media','logs'))

def validate_and_rekey(path,oldkey,newkey,mode):
    old,new=Fernet(oldkey),Fernet(newkey)
    def convert(s):
        if not s:return ''
        if not isinstance(s,str) or not s.startswith('enc:'):raise ValueError('قالب توکن پشتیبان نامعتبر')
        return 'enc:'+new.encrypt(old.decrypt(s[4:].encode())).decode()
    con=sqlite3.connect(path)
    try:
        con.execute('PRAGMA trusted_schema=OFF');con.execute('PRAGMA foreign_keys=ON')
        tables={r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
        if tables!={'settings','targets','source_messages','outbox','activity'} or con.execute("SELECT 1 FROM sqlite_master WHERE type IN ('trigger','view') OR (type='table' AND upper(sql) LIKE '%VIRTUAL TABLE%')").fetchone():raise ValueError('ساختار دیتابیس پشتیبان نامعتبر')
        if con.execute('PRAGMA quick_check').fetchone()[0]!='ok' or con.execute('PRAGMA foreign_key_check').fetchone():raise ValueError('دیتابیس پشتیبان سالم نیست')
        source=json.loads(con.execute("SELECT value FROM settings WHERE key='source'").fetchone()[0]);
        if not isinstance(source,dict) or source.get('mode') not in ('bot','user'):raise ValueError('مبدأ پشتیبان نامعتبر')
        for field in ('bot_token','api_hash','phone'):source[field]=convert(source.get(field,''))
        con.execute("UPDATE settings SET value=? WHERE key='source'",(json.dumps(source),))
        for key in ('ai44_key_openai','ai44_key_gemini'):
            row=con.execute('SELECT value FROM settings WHERE key=?',(key,)).fetchone()
            if row:con.execute('UPDATE settings SET value=? WHERE key=?',(json.dumps(convert(json.loads(row[0]))),key))
        con.execute("DELETE FROM settings WHERE key LIKE 'ai44_catalog_%'")
        for route,token in con.execute('SELECT platform,token FROM targets').fetchall():
            if route.split(':',1)[0] not in PLATFORMS:raise ValueError('مقصد پشتیبان نامعتبر')
            con.execute('UPDATE targets SET token=? WHERE platform=?',(convert(token),route))
        if mode=='settings':strip_history(con)
        con.execute("DELETE FROM settings WHERE key LIKE 'backup42_%'")
        con.execute("UPDATE settings SET value='false' WHERE key IN ('run_enabled','ai43_enabled')")
        # Prevent silent replay after restoring an older snapshot; resume must be explicit.
        con.execute("UPDATE targets SET enabled=0")
        con.execute("UPDATE outbox SET status='uncertain',detail='ارسال در جریان در زمان پشتیبان' WHERE status='sending'")
        con.commit()
        if mode=='settings':con.execute('VACUUM')
    finally:con.close()

def stage_restore(root,blob,password,mode='full',include_sessions=True,include_media=True,local=False):
    root=Path(root);pending=root/'restore_pending'
    if pending.exists():raise ValueError('یک بازیابی در انتظار راه‌اندازی مجدد است')
    if mode not in ('full','settings'):raise ValueError('حالت بازیابی نامعتبر')
    if local:password=local_password(root)
    raw=unseal(blob,password)
    tmp=Path(tempfile.mkdtemp(prefix='restore-stage-',dir=root));private_dir(tmp)
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            info=z.infolist();names=[i.filename for i in info]
            if len(names)>20000 or len(set(n.casefold() for n in names))!=len(names) or sum(i.file_size for i in info)>MAX_BYTES or any(not allowed_name(n) for n in names):raise ValueError('محتویات پشتیبان نامعتبر یا بیش از حد است')
            if not {'manifest.json',DB,'secrets.key'}.issubset(names):raise ValueError('پشتیبان ناقص است')
            manifest=json.loads(z.read('manifest.json'))
            if manifest.get('format')!=1 or manifest.get('mode') not in ('full','settings','database'):raise ValueError('نسخه پشتیبان پشتیبانی نمی‌شود')
            if mode=='full' and manifest['mode']=='settings':raise ValueError('پشتیبان تنظیمات فقط به حالت تنظیمات قابل بازیابی است')
            for item in info:
                n=item.filename
                if n==DB or (mode=='full' and manifest['mode']=='full' and ((include_media and n.startswith('media/')) or (include_sessions and SESSION.fullmatch(n)) or n.startswith('logs/'))):
                    p=tmp/n;private_dir(p.parent);private_file(p,z.read(item))
            validate_and_rekey(tmp/DB,z.read('secrets.key'),(root/'secrets.key').read_bytes(),mode)
        # Only replace session/media when requested AND present as a full backup type.
        plan={'sessions':bool(mode=='full' and manifest['mode']=='full' and include_sessions),'media':bool(mode=='full' and manifest['mode']=='full' and include_media),'rollback':'restore-before-'+secrets.token_hex(8)}
        private_file(tmp/'plan.json',json.dumps(plan).encode());os.replace(tmp,pending)
    except Exception:
        shutil.rmtree(tmp,ignore_errors=True);raise
    return plan

def apply_pending(root):
    """Startup, under the original instance lock, before creating Database or logging.
    Idempotent after interruption: keep originals once, reapply staged data on restart.
    """
    root=Path(root);pending=root/'restore_pending'
    if not pending.exists():return False
    plan=json.loads((pending/'plan.json').read_text());rollback=private_dir(root/'backups'/plan['rollback'])
    marker=rollback/'ready'
    if not marker.exists():
        copy_sqlite(root/DB,rollback/DB)
        shutil.copy2(root/'secrets.key',rollback/'secrets.key')
        if plan['media'] and (root/'media').exists():shutil.copytree(root/'media',rollback/'media',dirs_exist_ok=True)
        if plan['sessions']:
            for p in root.iterdir():
                if p.is_file() and SESSION.fullmatch(p.name):shutil.copy2(p,rollback/p.name)
        private_file(marker,b'1')
    if plan['media']:
        if (root/'media').exists():shutil.rmtree(root/'media')
        if (pending/'media').exists():shutil.copytree(pending/'media',root/'media')
        else:private_dir(root/'media')
    if plan['sessions']:
        for p in root.iterdir():
            if p.is_file() and SESSION.fullmatch(p.name):p.unlink()
        for p in pending.iterdir():
            if p.is_file() and SESSION.fullmatch(p.name):shutil.copy2(p,root/p.name)
    if (pending/'logs').exists():
        shutil.copytree(pending/'logs',root/'restored_logs'/plan['rollback'],dirs_exist_ok=True)
    copy_sqlite(pending/DB,root/DB)
    # Rename first so a crash while deleting doesn't replay an incomplete stage.
    done=root/('restore-applied-'+secrets.token_hex(8));os.replace(pending,done);shutil.rmtree(done,ignore_errors=True)
    return True

def pre_upgrade(root,version):
    root=Path(root); marker=root/'release-version.txt'
    if not (root/DB).exists() or not (root/'secrets.key').exists():return
    if marker.exists() and marker.read_text().strip()==version:return
    # No migration has run yet. Existing v4.1 schema supports this snapshot.
    make_backup(root,local_password(root),'database','pre-upgrade')

def mark_version(root,version):
    (Path(root)/'release-version.txt').write_text(version,encoding='ascii')
