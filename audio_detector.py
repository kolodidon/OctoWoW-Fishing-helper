"""Recognize the FishPing three-tone sequence by its time/frequency pattern."""
from collections import deque
from pathlib import Path
import wave
import numpy as np

class SignalDetector:
    def __init__(self,reference,rate=24000,threshold=0.82,min_rms=0.0001):
        self.rate=rate; self.threshold=threshold; self.min_rms=min_rms
        self.frame=round(rate*0.020); self.hop=round(rate*0.010)
        self.window=np.hanning(self.frame)
        frequencies=np.fft.rfftfreq(self.frame,1/rate)
        self.bands=[np.where(abs(frequencies-tone)<=60)[0] for tone in (1100,1750,1350)]
        with wave.open(str(reference),'rb') as wav:
            if wav.getsampwidth()!=2: raise ValueError('Reference must be PCM16 WAV.')
            source=np.frombuffer(wav.readframes(wav.getnframes()),dtype='<i2').astype(float)/32768
            source=source.reshape(-1,wav.getnchannels()).mean(axis=1)
            reference_rate=wav.getframerate()
        if reference_rate!=rate:
            source=np.interp(np.arange(round(len(source)*rate/reference_rate))*reference_rate/rate,np.arange(len(source)),source)
        self.reference=np.array([self.feature(source[i:i+self.frame])[0] for i in range(0,len(source)-self.frame+1,self.hop)])
        self.norm=float(np.linalg.norm(self.reference))
        if self.norm<0.1: raise ValueError('Reference does not contain the expected FishPing tones.')
        self.expected=self.reference.argmax(axis=1)
        self.active=self.reference.max(axis=1)>0.6
        self.history=deque(maxlen=len(self.reference))
        self.rms_history=deque(maxlen=len(self.reference))
        self.pending=np.empty(0)
        self.best_score=0
    def feature(self,frame):
        rms=float(np.sqrt(np.mean(frame*frame)))
        if rms<self.min_rms: return np.zeros(3),rms
        power=abs(np.fft.rfft(frame*self.window))**2
        total=float(power.sum())+1e-20
        return np.array([power[band].sum()/total for band in self.bands]),rms
    def feed(self,data):
        data=np.asarray(data,dtype=float)
        if data.ndim==2: data=data.mean(axis=1)
        self.pending=np.concatenate((self.pending,data))
        last={'matched':False,'score':0.0,'coverage':0.0,'rms':0.0}
        while len(self.pending)>=self.frame:
            feature,rms=self.feature(self.pending[:self.frame])
            self.pending=self.pending[self.hop:]
            self.history.append(feature); self.rms_history.append(rms)
            if len(self.history)<len(self.reference): continue
            observed=np.array(self.history)
            norm=float(np.linalg.norm(observed))
            score=float((observed*self.reference).sum()/(norm*self.norm)) if norm>1e-12 else 0
            coverage=min(float(np.mean(observed[self.active&(self.expected==i),i])) for i in range(3))
            level=float(np.mean(self.rms_history))
            self.best_score=max(self.best_score,score)
            last={'matched':bool(score>=self.threshold and coverage>=0.25 and level>=self.min_rms),'score':round(score,4),'coverage':round(coverage,4),'rms':round(level,6)}
            if last['matched']: return last
        return last

class LoopbackAudio:
    def __init__(self,rate=24000): self.rate=rate; self.context=None
    def __enter__(self):
        import soundcard as sc
        self.speaker=sc.default_speaker()
        self.device_name=self.speaker.name
        mic=sc.get_microphone(id=self.device_name,include_loopback=True)
        self.context=mic.recorder(samplerate=self.rate,channels=2,blocksize=1200)
        self.recorder=self.context.__enter__()
        return self
    def read(self,frames): return self.recorder.record(numframes=frames)
    def __exit__(self,*args):
        if self.context is not None: return self.context.__exit__(*args)
