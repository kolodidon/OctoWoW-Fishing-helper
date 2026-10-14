"""Cast, find by cursor, recognize FishPing audio, click, repeat with deadlines."""
import argparse
import ctypes
from ctypes import wintypes
import hashlib
import json
from pathlib import Path
import threading
import time
import math
from audio_detector import SignalDetector, LoopbackAudio

BASE = Path(__file__).resolve().parent

def grid_points(width, height, step):
    """Whole client grid, ordered from the central fishing area outward."""
    points = [(x,y) for y in range(step//2,height,step) for x in range(step//2,width,step)]
    cx,cy = width*0.5,height*0.4
    points.sort(key=lambda p:(max(abs(p[0]-cx),abs(p[1]-cy)),abs(p[1]-cy),p[0]))
    return points

class StopRequested(Exception): pass

class ConsecutiveSearchFailures(Exception): pass

def parse_minutes(text):
    try:
        value=float(text.strip().replace(',','.'))
    except ValueError:
        raise ValueError('Enter a positive number of minutes, for example 60.') from None
    if not math.isfinite(value) or value<=0:
        raise ValueError('Minutes must be a finite number greater than zero.')
    return value

class RunTimer:
    def __init__(self,stop,minutes):
        self.expired=False
        self.started=time.monotonic()
        self.deadline=self.started+minutes*60
        def finish():
            self.expired=True
            stop.set()
        self.timer=threading.Timer(minutes*60,finish)
        self.timer.daemon=True
        self.timer.start()
    def cancel(self): self.timer.cancel()

class Windows:
    class POINT(ctypes.Structure):
        _fields_=[('x',wintypes.LONG),('y',wintypes.LONG)]
    class RECT(ctypes.Structure):
        _fields_=[('left',wintypes.LONG),('top',wintypes.LONG),('right',wintypes.LONG),('bottom',wintypes.LONG)]
    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_=[('biSize',wintypes.DWORD),('biWidth',wintypes.LONG),('biHeight',wintypes.LONG),('biPlanes',wintypes.WORD),('biBitCount',wintypes.WORD),('biCompression',wintypes.DWORD),('biSizeImage',wintypes.DWORD),('biXPelsPerMeter',wintypes.LONG),('biYPelsPerMeter',wintypes.LONG),('biClrUsed',wintypes.DWORD),('biClrImportant',wintypes.DWORD)]
    def __init__(self):
        self.u=ctypes.WinDLL('user32',use_last_error=True)
        self.g=ctypes.WinDLL('gdi32',use_last_error=True)
        class CURSORINFO(ctypes.Structure):
            _fields_=[('cbSize',wintypes.DWORD),('flags',wintypes.DWORD),('hCursor',wintypes.HANDLE),('ptScreenPos',Windows.POINT)]
        class ICONINFO(ctypes.Structure):
            _fields_=[('fIcon',wintypes.BOOL),('xHotspot',wintypes.DWORD),('yHotspot',wintypes.DWORD),('hbmMask',wintypes.HANDLE),('hbmColor',wintypes.HANDLE)]
        self.CURSORINFO,self.ICONINFO=CURSORINFO,ICONINFO
        signatures=[
            (self.u.FindWindowW,[wintypes.LPCWSTR,wintypes.LPCWSTR],wintypes.HWND),
            (self.u.GetForegroundWindow,[],wintypes.HWND),
            (self.u.IsWindow,[wintypes.HWND],wintypes.BOOL),
            (self.u.GetWindowThreadProcessId,[wintypes.HWND,ctypes.POINTER(wintypes.DWORD)],wintypes.DWORD),
            (self.u.PostMessageW,[wintypes.HWND,wintypes.UINT,ctypes.c_size_t,ctypes.c_ssize_t],wintypes.BOOL),
            (self.u.GetClientRect,[wintypes.HWND,ctypes.POINTER(self.RECT)],wintypes.BOOL),
            (self.u.ClientToScreen,[wintypes.HWND,ctypes.POINTER(self.POINT)],wintypes.BOOL),
            (self.u.SetCursorPos,[ctypes.c_int,ctypes.c_int],wintypes.BOOL),
            (self.u.GetCursorInfo,[ctypes.POINTER(CURSORINFO)],wintypes.BOOL),
            (self.u.GetIconInfo,[wintypes.HANDLE,ctypes.POINTER(ICONINFO)],wintypes.BOOL),
            (self.u.DrawIconEx,[wintypes.HDC,ctypes.c_int,ctypes.c_int,wintypes.HANDLE,ctypes.c_int,ctypes.c_int,wintypes.UINT,wintypes.HBRUSH,wintypes.UINT],wintypes.BOOL),
            (self.g.CreateCompatibleDC,[wintypes.HDC],wintypes.HDC),
            (self.g.CreateDIBSection,[wintypes.HDC,ctypes.c_void_p,wintypes.UINT,ctypes.POINTER(ctypes.c_void_p),wintypes.HANDLE,wintypes.DWORD],wintypes.HBITMAP),
            (self.g.SelectObject,[wintypes.HDC,wintypes.HANDLE],wintypes.HANDLE),
            (self.g.DeleteObject,[wintypes.HANDLE],wintypes.BOOL),
            (self.g.DeleteDC,[wintypes.HDC],wintypes.BOOL),
            (self.g.GdiFlush,[],wintypes.BOOL),
            (self.u.mouse_event,[wintypes.DWORD,wintypes.DWORD,wintypes.DWORD,wintypes.DWORD,ctypes.c_size_t],None),
            (self.u.keybd_event,[wintypes.BYTE,wintypes.BYTE,wintypes.DWORD,ctypes.c_size_t],None)]
        for fn,args,returns in signatures: fn.argtypes,fn.restype=args,returns
        self.u.SetProcessDPIAware()
        self.dc=self.g.CreateCompatibleDC(None)
        info=self.BITMAPINFOHEADER()
        info.biSize=ctypes.sizeof(info); info.biWidth=96; info.biHeight=-96; info.biPlanes=1; info.biBitCount=32
        self.bits=ctypes.c_void_p()
        self.bitmap=self.g.CreateDIBSection(self.dc,ctypes.byref(info),0,ctypes.byref(self.bits),None,0)
        if not self.dc or not self.bitmap or not self.bits.value: raise ctypes.WinError(ctypes.get_last_error())
        self.old=self.g.SelectObject(self.dc,self.bitmap)
    def close(self):
        self.g.SelectObject(self.dc,self.old); self.g.DeleteObject(self.bitmap); self.g.DeleteDC(self.dc)
    def bounds(self,title):
        handle=self.u.FindWindowW(None,title)
        if not handle: raise RuntimeError('WoW window not found: '+title)
        r=self.RECT(); p=self.POINT()
        if not self.u.GetClientRect(handle,ctypes.byref(r)) or not self.u.ClientToScreen(handle,ctypes.byref(p)): raise ctypes.WinError(ctypes.get_last_error())
        if r.right<100 or r.bottom<100: raise RuntimeError('WoW window is minimized or too small.')
        return handle,(p.x,p.y,r.right,r.bottom)
    def require_focus(self,handle):
        if self.u.GetForegroundWindow()!=handle: raise RuntimeError('WoW lost focus; test stopped.')
    def window_process(self,handle):
        process=wintypes.DWORD()
        if not self.u.GetWindowThreadProcessId(handle,ctypes.byref(process)) or not process.value:
            raise ctypes.WinError(ctypes.get_last_error())
        return process.value
    def close_game(self,handle,expected_process):
        if not self.u.IsWindow(handle): return 'already_closed'
        if self.window_process(handle)!=expected_process:
            raise RuntimeError('Original WoW window no longer belongs to the same process; close skipped.')
        # Targeted equivalent of Alt+F4: SC_CLOSE to the original game window.
        # This does not send a shortcut to whichever application is currently focused.
        if not self.u.PostMessageW(handle,0x0112,0xF060,0):
            raise ctypes.WinError(ctypes.get_last_error())
        return 'close_requested'
    def move(self,x,y):
        if not self.u.SetCursorPos(x,y): raise ctypes.WinError(ctypes.get_last_error())
    def cast(self):
        self.u.keybd_event(0x31,0x02,0,0)
        self.u.keybd_event(0x31,0x02,2,0)
    def apply_lure(self):
        self.u.keybd_event(0x32,0x03,0,0)
        self.u.keybd_event(0x32,0x03,2,0)
    def open_container(self):
        self.u.keybd_event(0x33,0x04,0,0)
        self.u.keybd_event(0x33,0x04,2,0)
    def right_click(self,stop):
        if stop.is_set(): raise StopRequested()
        self.u.mouse_event(0x08,0,0,0,0)
        try:
            interrupted=stop.wait(0.030)
        finally:
            self.u.mouse_event(0x10,0,0,0,0)
        if interrupted: raise StopRequested()
    def cursor(self):
        state=self.CURSORINFO(); state.cbSize=ctypes.sizeof(state)
        if not self.u.GetCursorInfo(ctypes.byref(state)): raise ctypes.WinError(ctypes.get_last_error())
        if not state.hCursor or not state.flags&1: return None
        icon=self.ICONINFO()
        if not self.u.GetIconInfo(state.hCursor,ctypes.byref(icon)): raise ctypes.WinError(ctypes.get_last_error())
        try:
            ctypes.memset(self.bits,0,96*96*4)
            if not self.u.DrawIconEx(self.dc,0,0,state.hCursor,0,0,0,None,3): raise ctypes.WinError(ctypes.get_last_error())
            self.g.GdiFlush()
            digest=hashlib.blake2s(ctypes.string_at(self.bits,96*96*4),digest_size=16).hexdigest()
            return {'shape':digest,'hotspot':[icon.xHotspot,icon.yHotspot],'handle':int(state.hCursor),'position':[state.ptScreenPos.x,state.ptScreenPos.y]}
        finally:
            if icon.hbmMask: self.g.DeleteObject(icon.hbmMask)
            if icon.hbmColor: self.g.DeleteObject(icon.hbmColor)

def identity(cursor):
    return None if cursor is None else (cursor['shape'],tuple(cursor['hotspot']))

def scan(api,handle,bounds,baseline,stop,step=60,settle=0.040,timeout=25):
    left,top,width,height=bounds
    started=time.monotonic(); checked=0
    for x,y in grid_points(width,height,step):
        if stop.is_set(): raise StopRequested()
        if time.monotonic()-started>=timeout: break
        api.require_focus(handle)
        api.move(left+x,top+y)
        if stop.wait(min(settle,max(0,timeout-(time.monotonic()-started)))): raise StopRequested()
        if time.monotonic()-started>=timeout: break
        cursor=api.cursor(); checked+=1
        if identity(cursor) is not None and identity(cursor)!=identity(baseline):
            # Stay at this point and confirm; ignore delayed cursor updates from the previous point.
            if stop.wait(min(0.020,max(0,timeout-(time.monotonic()-started)))): raise StopRequested()
            if time.monotonic()-started>=timeout: break
            api.require_focus(handle)
            confirmed=api.cursor()
            if identity(confirmed)==identity(cursor):
                return {'result':'cursor_changed','point':[left+x,top+y],'checked':checked,'elapsed':round(time.monotonic()-started,3),'baseline':baseline,'detected':confirmed}
    return {'result':'not_found','checked':checked,'elapsed':round(time.monotonic()-started,3),'baseline':baseline}

def watch_audio(api,handle,stop,deadline,score_threshold=0.82,min_rms=0.0001,on_sample=None,audio_factory=None):
    detector=SignalDetector(BASE/'FishBite-reference.wav',threshold=score_threshold,min_rms=min_rms)
    samples=0; last=None
    factory=audio_factory or LoopbackAudio
    if stop.is_set(): raise StopRequested()
    # Start a fresh recorder after locating the bobber, avoiding buffered cast sounds.
    with factory(detector.rate) as audio:
        print('Listening for the three-tone signal on:',audio.device_name,flush=True)
        while time.monotonic()<deadline:
            if stop.is_set(): raise StopRequested()
            api.require_focus(handle)
            frames=min(1200,int(max(0,deadline-time.monotonic())*detector.rate))
            if frames<1: break
            data=audio.read(frames)
            if stop.is_set(): raise StopRequested()
            if time.monotonic()>=deadline: break
            last=detector.feed(data); samples+=1
            if on_sample is not None: on_sample(last)
            if last['matched']:
                return {'result':'audio_signal','audio_score':last['score'],'audio_coverage':last['coverage'],'audio_rms':last['rms'],'best_audio_score':round(detector.best_score,4),'samples':samples}
    return {'result':'bite_timeout','best_audio_score':round(detector.best_score,4),'samples':samples,'last_audio_sample':last}

def run_cycles(api,handle,bounds,baseline,stop,on_result,step=60,settle=0.040,timeout=5,cast_delay=2,recast_delay=1,limit=0,iteration_seconds=30,score_threshold=0.82,min_rms=0.0001,on_sample=None,audio_factory=None,lure_interval=600,lure_delay=5.5):
    cycle=0
    consecutive_misses=0
    next_lure_at=0.0
    while not limit or cycle<limit:
        if stop.is_set(): raise StopRequested()
        api.require_focus(handle)
        # The original empty-water hand remains the reference for every cast.
        # Do not learn the gear left under the mouse after a previous find.
        api.move(*baseline['position'])
        if stop.wait(0.1): raise StopRequested()
        api.require_focus(handle)
        # Apply only between casts; the first cycle always starts with a lure.
        if time.monotonic()>=next_lure_at:
            if stop.is_set(): raise StopRequested()
            api.apply_lure()
            next_lure_at=time.monotonic()+lure_interval
            print('Lure on 2. Waiting',lure_delay,'seconds for application.',flush=True)
            if stop.wait(lure_delay): raise StopRequested()
            api.require_focus(handle)
        cast_at=time.monotonic()
        if stop.is_set(): raise StopRequested()
        api.cast(); cycle+=1
        deadline=cast_at+iteration_seconds
        print('Cycle',cycle,'— cast on 1.',flush=True)
        if stop.wait(min(cast_delay,max(0,deadline-time.monotonic()))): raise StopRequested()
        result=scan(api,handle,bounds,baseline,stop,step,settle,min(timeout,max(0,deadline-time.monotonic())))
        result['cycle']=cycle
        result['found']=result['result']=='cursor_changed'
        consecutive_misses=0 if result['found'] else consecutive_misses+1
        result['consecutive_misses']=consecutive_misses
        result['search_elapsed']=result['elapsed']
        result['clicked']=False
        if result['result']=='cursor_changed':
            print('FOUND:',result['point'],'search:',result['elapsed'],'s. Waiting for the three-tone sound.',flush=True)
            def sample_callback(sample):
                if on_sample is not None: on_sample({'cycle':cycle,'elapsed':round(time.monotonic()-cast_at,3),**sample})
            bite=watch_audio(api,handle,stop,deadline,score_threshold,min_rms,sample_callback,audio_factory)
            result.update(bite)
            if bite['result']=='audio_signal' and time.monotonic()+0.030<deadline:
                if stop.is_set(): raise StopRequested()
                api.require_focus(handle)
                api.right_click(stop)
                result['clicked']=True
                print('SIGNAL -> click. Match score:',bite['audio_score'],flush=True)
            else:
                result['result']='bite_timeout'
                print('30-second iteration ended without a click; recasting.',flush=True)
        else:
            print('NOT FOUND within search limit; recasting now.',flush=True)
        result['iteration_elapsed']=round(time.monotonic()-cast_at,3)
        on_result(result)
        if consecutive_misses>=10:
            if stop.is_set(): raise StopRequested()
            raise ConsecutiveSearchFailures('Bobber not found in 10 consecutive searches; possible disconnect. Stopping early.')
        # Loot handling follows the completed fishing iteration. Give fish loot
        # time to finish, then open one container and wait before the next cast.
        if result['clicked']:
            if stop.wait(recast_delay): raise StopRequested()
            api.require_focus(handle)
            if stop.is_set(): raise StopRequested()
            api.open_container()
            print('Container macro on 3. Waiting 1 second for loot.',flush=True)
            if stop.wait(1.0): raise StopRequested()
        elif stop.is_set():
            raise StopRequested()

def main():
    import keyboard
    parser=argparse.ArgumentParser()
    parser.add_argument('--step',type=int,default=60)
    parser.add_argument('--settle-ms',type=float,default=40)
    parser.add_argument('--timeout',type=float,default=5,help='Maximum seconds spent searching after the bobber appears')
    parser.add_argument('--window-title',default='World of Warcraft')
    parser.add_argument('--cast-delay',type=float,default=2)
    parser.add_argument('--recast-delay',type=float,default=1)
    parser.add_argument('--audio-score',type=float,default=0.82)
    parser.add_argument('--audio-min-rms',type=float,default=0.0001)
    parser.add_argument('--lure-interval',type=float,default=600,help='Seconds between lure applications, including the first application; installer supplies 300 or 600')
    args=parser.parse_args()
    if args.step<4 or args.settle_ms<1 or args.timeout<=0 or args.cast_delay<0 or args.recast_delay<0: parser.error('Invalid grid settings.')
    if not 0<args.audio_score<=1 or args.audio_min_rms<0: parser.error('Invalid audio settings.')
    if not math.isfinite(args.lure_interval) or args.lure_interval<=0: parser.error('Lure interval must be a finite positive number.')
    stop=threading.Event()
    keyboard.add_hotkey('f8',stop.set); keyboard.add_hotkey('esc',stop.set)
    api=None
    run_timer=None
    handle=None
    game_process=None
    report={'result':'starting','cycles_completed':0,'found':0,'missed':0,'clicked':0,'bite_timeouts':0}
    session=time.strftime('%Y-%m-%d %H:%M:%S')
    def record_cycle(result):
        report['cycles_completed']+=1
        report['found' if result['found'] else 'missed']+=1
        report['clicked']+=int(result['clicked'])
        report['bite_timeouts']+=int(result['result']=='bite_timeout')
        report['last_cycle']=result
        report['consecutive_misses']=result['consecutive_misses']
        with (BASE/'cycles.jsonl').open('a',encoding='utf-8') as log:
            log.write(json.dumps({'session':session,**result},ensure_ascii=False)+'\n')
        (BASE/'last-result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    last_display=[0.0]
    def record_sample(sample):
        with (BASE/'audio-matches.jsonl').open('a',encoding='utf-8') as log:
            log.write(json.dumps({'session':session,**sample},ensure_ascii=False)+'\n')
        now=time.monotonic()
        if now-last_display[0]>=1 or sample['matched']:
            last_display[0]=now
            print('Audio match:',sample['score'],'tone coverage:',sample['coverage'],'level:',sample['rms'],flush=True)
    try:
        print('Fishing test: cast on 1, find by cursor, recognize the three-tone sound, click, repeat.')
        print('Search limit: 5 seconds. Whole iteration: 30 seconds from cast. Grid: 60 pixels.')
        print('Lure macro on 2: before the first cast and every',args.lure_interval,'seconds between casts; application wait: 5.5 seconds.')
        print('Container macro on 3: once after each successful catch, with loot pauses before and after.')
        print('Stops early after 10 consecutive bobber-search misses; game stays open.')
        print('Stop at any time: F8 or Esc. Closing this window also ends the test.')
        print('Enter minutes, then switch to WoW and leave the mouse over empty water during the 10-second countdown.')
        while True:
            if stop.is_set(): raise StopRequested()
            try:
                minutes=parse_minutes(input('How many minutes should the script work? '))
                break
            except ValueError as exc:
                print(exc)
        report['requested_minutes']=minutes
        for seconds in range(10,0,-1):
            print(seconds,flush=True)
            if stop.wait(1): raise StopRequested()
        api=Windows()
        handle,bounds=api.bounds(args.window_title)
        game_process=api.window_process(handle)
        api.require_focus(handle)
        baseline=api.cursor()
        if baseline is None: raise RuntimeError('Windows cursor is hidden. The game may draw a software cursor; this test cannot read that shape.')
        position=baseline['position']; left,top,width,height=bounds
        if not (left<=position[0]<left+width and top<=position[1]<top+height): raise RuntimeError('Place the cursor over empty water inside WoW before starting.')
        # Require an unchanged starting hand, before any game input.
        if stop.wait(0.25): raise StopRequested()
        if identity(api.cursor())!=identity(baseline): raise RuntimeError('Starting cursor changed. Start with the mouse stationary over empty water.')
        report['result']='running'
        report['bounds']=bounds
        run_timer=RunTimer(stop,minutes)
        print('Running for',minutes,'minutes from now. F8 or Esc stops earlier.',flush=True)
        run_cycles(api,handle,bounds,baseline,stop,record_cycle,args.step,args.settle_ms/1000,args.timeout,args.cast_delay,args.recast_delay,iteration_seconds=30,score_threshold=args.audio_score,min_rms=args.audio_min_rms,on_sample=record_sample,lure_interval=args.lure_interval)
    except ConsecutiveSearchFailures as exc:
        report.update(result='consecutive_search_failures',stop_reason=str(exc),consecutive_misses=10)
        print(str(exc),flush=True)
        print('Game left open. Check the connection before restarting the script.',flush=True)
    except (StopRequested,KeyboardInterrupt):
        if run_timer is not None and run_timer.expired:
            report['result']='duration_finished'
            try:
                report['game_close']=api.close_game(handle,game_process)
                print('Requested running time has elapsed. Script stopped; close request sent to WoW.')
            except Exception as exc:
                report['game_close_error']=str(exc)
                print('Script stopped on timer, but WoW could not be closed:',exc)
        else:
            report['result']='manually_stopped'; print('Stopped manually.')
    except Exception as exc:
        import traceback
        report.update(result='error',error=str(exc),traceback=traceback.format_exc())
        print(report['traceback'])
    finally:
        if run_timer is not None:
            run_timer.cancel()
            report['run_elapsed_seconds']=round(time.monotonic()-run_timer.started,3)
        if api is not None: api.close()
        keyboard.unhook_all_hotkeys()
        (BASE/'last-result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print('Result saved to last-result.json.')

if __name__=='__main__': main()
