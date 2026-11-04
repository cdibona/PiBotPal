import importlib.util
import json
from pathlib import Path
from urllib.error import URLError

spec=importlib.util.spec_from_file_location('wait_ready',Path(__file__).parents[1]/'scripts/wait-ready.py')
ready=importlib.util.module_from_spec(spec)
spec.loader.exec_module(ready)


class Reply:
    status=200
    def __init__(self,state): self.state=state
    def __enter__(self): return self
    def __exit__(self,*args): pass
    def read(self,size): return json.dumps(self.state).encode()


def test_readiness_waits_for_correct_application_after_connection_refused():
    class Opener:
        attempts=0
        def open(self,url,timeout):
            assert url=='http://127.0.0.1:8765/health'
            self.attempts+=1
            if self.attempts==1: raise URLError('connection refused')
            if self.attempts==2: return Reply({'status':'ok','service':'other'})
            return Reply({'status':'ok','service':'PiBotPal','server_instance_id':'ready-instance'})
    opener=Opener()
    assert ready.wait_ready(timeout=1,interval=0,opener=opener)
    assert opener.attempts==3


def test_readiness_rejects_invalid_health_and_times_out(monkeypatch):
    class Opener:
        def open(self,*args,**kwargs): return Reply({'status':'ok','service':'wrong-process'})
    clock=iter([0,0,1])
    monkeypatch.setattr(ready.time,'monotonic',lambda:next(clock))
    assert not ready.wait_ready(timeout=.5,interval=0,opener=Opener())
