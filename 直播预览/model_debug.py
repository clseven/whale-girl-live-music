"""Bounded, in-memory model I/O diagnostics. Never collects private reasoning."""
import copy
import hashlib
import json
import re
import threading
import time
import uuid
from contextvars import ContextVar

CURRENT_TRACE = ContextVar('whale_model_debug', default=None)
PRIVATE_FIELDS = {'authorization','cookie','set-cookie','api_key','apikey','access_token',
                  'refresh_token','password','secret','reasoning_content','encrypted_content',
                  'chain_of_thought','analysis','reasoning'}


def scrub(value, depth=0):
    if depth > 18:
        return '[层级过深，已省略]'
    if isinstance(value,dict):
        return {str(k):('[私密字段，已省略]' if str(k).lower() in PRIVATE_FIELDS else scrub(v,depth+1))
                for k,v in value.items()}
    if isinstance(value,(list,tuple)):
        return [scrub(v,depth+1) for v in value[:200]]
    if isinstance(value,str):
        value=re.sub(r'sk-[A-Za-z0-9_-]{12,}', '[密钥已隐藏]',value)
        value=re.sub(r'(?i)bearer\s+[A-Za-z0-9._~-]+','Bearer [已隐藏]',value)
        value=re.sub(r'(?i)((?:sessionid(?:_ss)?|sid_guard|ttwid|msToken)=)[^;\s"\']+',r'\1[已隐藏]',value)
        if value.startswith('data:image/'):
            return '[图像数据已省略]'
        return value[:80000]+('\n[文本过长，已截断]' if len(value)>80000 else '')
    if value is None or type(value) in (int,float,bool):
        return value
    return str(value)[:200]


class ModelDebug:
    def __init__(self, max_records=60, max_bytes=8*1024*1024):
        self.max_records,self.max_bytes=max_records,max_bytes
        self.lock=threading.RLock()
        self.records=[]
        self.sizes={}
        self.total_bytes=0
        self.revision=0

    def start(self, **metadata):
        ident=uuid.uuid4().hex
        self.update(ident,create=True,metadata=metadata,state='generating',started_at=time.time(),
                    model_called=False,requests=[],responses=[],tools=[],playback='未准备')
        return Trace(self,ident)

    def clear(self):
        with self.lock:
            count=len(self.records)
            self.records.clear()
            self.sizes.clear()
            self.total_bytes=0
            self.revision+=1
            return {'cleared':count,'revision':self.revision}

    def update(self, ident, create=False, **values):
        if not ident:
            return
        with self.lock:
            record=next((r for r in self.records if r['id']==ident),None)
            if record is None:
                if not create:
                    return
                record={'id':ident}
                self.records.append(record)
            record.update(scrub(values))
            self.revision+=1
            record['revision']=self.revision
            size=lambda:len(json.dumps(record,ensure_ascii=False).encode())
            budget=min(self.max_bytes,512*1024)
            for field in ('requests','responses','tools','tool_calls','input','system_prompt','raw_output','metadata'):
                if size()<=budget:
                    break
                if field in record:
                    record[field]='[本轮详情过大，该字段已省略]'
                    record['truncated']=True
            previous=self.sizes.get(ident,0)
            self.sizes[ident]=size()
            self.total_bytes+=self.sizes[ident]-previous
            # Exact model prompts are repeated per turn, but never accumulate unboundedly.
            while len(self.records)>1 and (len(self.records)>self.max_records or
                    self.total_bytes>self.max_bytes):
                removed=self.records.pop(0)
                self.total_bytes-=self.sizes.pop(removed['id'],0)

    def append(self, ident, field, value):
        with self.lock:
            record=next((r for r in self.records if r['id']==ident),None)
            if record is None:
                return
            items=record.get(field,[])
            if not isinstance(items,list):
                return
            items=(items+[scrub(value)])[-16:]
            self.update(ident,**{field:items})

    def index(self):
        with self.lock:
            rows=[{k:copy.deepcopy(r.get(k)) for k in
                ('id','revision','metadata','state','started_at','ended_at','duration_ms','model_called',
                 'decision','silent_reason','reply','error','playback','usage')}
                for r in reversed(self.records)]
            return {'revision':self.revision,'records':rows,'capacity':self.max_records,
                    'note':'仅保存本次后台运行的近期记录；已隐藏凭证，不采集内部思维链。'}

    def detail(self,ident):
        with self.lock:
            value=next((r for r in self.records if r['id']==ident),None)
            return copy.deepcopy(value)


class Trace:
    def __init__(self,store,ident):
        self.store,self.id=store,ident
        self.began=time.perf_counter()

    def input(self,system_prompt,envelope,tools):
        prompt=system_prompt if isinstance(system_prompt,str) else ''
        self.store.update(self.id,system_prompt=prompt,
            prompt_version=hashlib.sha256(prompt.encode()).hexdigest()[:12],input=envelope,tool_definitions=tools)

    def request(self,transport,payload):
        self.store.append(self.id,'requests',{'time':time.time(),'transport':transport,'payload':payload})

    def response(self,value):
        # Deliberate allowlist: never capture provider reasoning payloads/events.
        self.store.append(self.id,'responses',{k:value.get(k) for k in ('content','tool_calls','usage') if k in value})

    def tool(self,name,arguments,result):
        self.store.append(self.id,'tools',{'tool':name,'arguments':arguments,'result':result})

    def finish(self,result=None,error=None):
        if error is not None:
            self.store.update(self.id,state='error',error=str(error),ended_at=time.time(),
                              duration_ms=round((time.perf_counter()-self.began)*1000))
            return
        result=result or {}
        self.store.update(self.id,state='silent' if result.get('silent') else 'completed',
            ended_at=time.time(),duration_ms=round((time.perf_counter()-self.began)*1000),
            raw_output=result.get('debug_raw_output',''),reply=result.get('reply',''),
            decision=result.get('watch_decision'),silent_reason=result.get('debug_silent_reason',''),
            usage=result.get('usage'),tool_calls=result.get('tool_calls',[]))
