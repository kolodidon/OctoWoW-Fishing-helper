"""Windows shell links without PowerShell, pywin32, or scripting-host dependencies."""
import ctypes
from ctypes import wintypes
from pathlib import Path
import os

class GUID(ctypes.Structure):
    _fields_ = [('data1', wintypes.DWORD), ('data2', wintypes.WORD), ('data3', wintypes.WORD), ('data4', ctypes.c_ubyte * 8)]

def shortcut(path, target, working_dir, arguments='', description='Fishing bot'):
    ole = ctypes.OleDLL('ole32')
    ole.CoInitializeEx.argtypes = [ctypes.c_void_p, wintypes.DWORD]
    ole.CoInitializeEx.restype = ctypes.c_long
    ole.CLSIDFromString.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(GUID)]
    ole.CLSIDFromString.restype = ctypes.c_long
    ole.CoCreateInstance.argtypes = [ctypes.POINTER(GUID), ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p)]
    ole.CoCreateInstance.restype = ctypes.c_long
    def check(result):
        if result < 0: raise OSError('Shell shortcut HRESULT: ' + hex(result & 0xffffffff))
    def guid(value):
        result = GUID(); check(ole.CLSIDFromString(value, ctypes.byref(result))); return result
    def call(obj, index, types, *args):
        table = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        return ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *types)(table[index])(obj, *args)
    initialized = ole.CoInitializeEx(None, 2)
    if initialized < 0 and initialized != -2147417850: check(initialized)
    shell, persist = ctypes.c_void_p(), ctypes.c_void_p()
    try:
        clsid = guid('{00021401-0000-0000-C000-000000000046}')
        iid = guid('{000214F9-0000-0000-C000-000000000046}')
        check(ole.CoCreateInstance(ctypes.byref(clsid), None, 1, ctypes.byref(iid), ctypes.byref(shell)))
        for index, value in [(20, str(target)), (9, str(working_dir)), (11, arguments), (7, description)]:
            check(call(shell, index, [wintypes.LPCWSTR], value))
        iid_persist = guid('{0000010B-0000-0000-C000-000000000046}')
        check(call(shell, 0, [ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p)], ctypes.byref(iid_persist), ctypes.byref(persist)))
        check(call(persist, 6, [wintypes.LPCWSTR, wintypes.BOOL], str(Path(path).absolute()), True))
    finally:
        if persist: call(persist, 2, [])
        if shell: call(shell, 2, [])
        if initialized >= 0: ole.CoUninitialize()

def bot_shortcut(path, bot_dir):
    shortcut(path, os.environ.get('COMSPEC', r'C:\Windows\System32\cmd.exe'), bot_dir, '/d /c start.cmd', 'Run Fishing Bot')
