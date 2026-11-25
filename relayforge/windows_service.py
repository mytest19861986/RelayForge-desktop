"""SCM adapter for Windows. No credentials stored or privilege escalation."""
import os, threading

def dispatch(name, run):
    if os.name!='nt': raise RuntimeError('Windows Service is available only on Windows')
    import ctypes as C
    from ctypes import wintypes as W
    api=C.WinDLL('advapi32',use_last_error=True)
    MAIN=C.WINFUNCTYPE(None,W.DWORD,C.POINTER(W.LPWSTR))
    HANDLER=C.WINFUNCTYPE(W.DWORD,W.DWORD,W.DWORD,C.c_void_p,C.c_void_p)
    class Status(C.Structure):
        _fields_=[(n,W.DWORD) for n in ('kind','state','accepted','exit','specific','checkpoint','hint')]
    class Entry(C.Structure):
        _fields_=[('name',W.LPWSTR),('main',MAIN)]
    api.RegisterServiceCtrlHandlerExW.argtypes=[W.LPCWSTR,HANDLER,C.c_void_p];api.RegisterServiceCtrlHandlerExW.restype=W.HANDLE
    api.SetServiceStatus.argtypes=[W.HANDLE,C.POINTER(Status)];api.SetServiceStatus.restype=W.BOOL
    api.StartServiceCtrlDispatcherW.argtypes=[C.POINTER(Entry)];api.StartServiceCtrlDispatcherW.restype=W.BOOL
    handle=None; state=2; exit_code=0; lock=threading.RLock();stop=threading.Event()
    def report(value,code=0):
        nonlocal state
        with lock:
            state=value
            status=Status(16,value,1 if value==4 else 0,code,0,1 if value in (2,3) else 0,30000 if value in (2,3) else 0)
            if handle:api.SetServiceStatus(handle,C.byref(status))
    @HANDLER
    def handler(control,event,data,ctx):
        if control==1:report(3);stop.set()
        elif control==4:report(state)
        return 0
    @MAIN
    def start(argc,argv):
        nonlocal handle,exit_code
        handle=api.RegisterServiceCtrlHandlerExW(name,handler,None)
        if not handle:exit_code=2;return
        report(2)
        try:
            report(4);run(stop);report(1)
        except BaseException:
            exit_code=1;report(1,1064)
    table=(Entry*2)(Entry(name,start),Entry(None,MAIN()))
    if not api.StartServiceCtrlDispatcherW(table): raise OSError(C.get_last_error(),'Windows SCM dispatch failed')
    return exit_code
