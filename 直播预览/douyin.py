"""Local-only DyHub lifecycle + reconnecting event subscriber.

The collector is pinned in ../参考项目/dyhub. No daily browser cookies are read.
"""
import asyncio
import json
import os
import re
import shutil
import subprocess
import threading
import urllib.request
import urllib.error
import time
from pathlib import Path
import aiohttp
from music import atomic_json


class DouyinSource:
    PORT = 18764

    def __init__(self, root, on_event):
        self.root, self.on_event = Path(root), on_event
        self.hub = self.root.parent / '参考项目/dyhub'
        self.runtime = self.root / '.runtime/dyhub'
        self.runtime.mkdir(parents=True, exist_ok=True)
        self.process = None
        self.room_id = ''
        self.state, self.error = 'disconnected', ''
        self.last_event = None
        self.stop_event = threading.Event()
        self.thread = None
        self.lock = threading.RLock()
        self.generation = 0
        self.login_configured = None
        self.event_counts = {'chat': 0, 'gift': 0, 'member': 0}
        self.hub_available = False

    def status(self):
        with self.lock:
            return {'room_id': self.room_id, 'state': self.state, 'error': self.error,
                    'last_event': self.last_event, 'hub_pid': self.process.pid if self.process and self.process.poll() is None else None,
                    'login_configured': self.login_configured, 'event_counts': dict(self.event_counts)}

    def _http(self, path, data=None, timeout=60):
        request = urllib.request.Request('http://127.0.0.1:' + str(self.PORT) + path,
            data=json.dumps(data).encode() if data is not None else None,
            headers={'Content-Type': 'application/json'})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(request, timeout=timeout) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            try:
                detail = json.load(exc).get('error', '')
            except Exception:
                detail = ''
            raise RuntimeError(detail or '抖音采集服务返回 HTTP ' + str(exc.code)) from None

    def _start_hub(self):
        if self.process and self.process.poll() is None:
            return
        # Reuse the local collector during a backend restart. Its login session
        # stays in its own memory; never copy a Cookie into files or API output.
        try:
            existing = self._http('/api/rooms', timeout=1)
            if isinstance(existing.get('rooms'), list):
                self.hub_available = True
                return
        except Exception:
            pass
        entry = self.hub / 'dist/index.js'
        if not entry.exists():
            raise RuntimeError('抖音采集器未编译，请先运行 setup_integrations.py')
        env = os.environ.copy()
        # Do not route a local Douyin collector through Codex's dedicated Clash proxy.
        for name in ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'http_proxy', 'https_proxy', 'all_proxy'):
            env.pop(name, None)
        env.update(DYHUB_PORT=str(self.PORT), DYHUB_HOST='127.0.0.1', DYHUB_COLLECTOR='browser')
        log = open(self.runtime / 'collector.log', 'a', encoding='utf-8')
        try:
            self.process = subprocess.Popen([shutil.which('node') or 'node', str(entry)], cwd=self.runtime,
                env=env, stdout=log, stderr=log, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        finally:
            log.close()
        for _ in range(80):
            if self.process.poll() is not None:
                raise RuntimeError('抖音采集器启动失败，详情见 .runtime/dyhub/collector.log')
            try:
                self._http('/api/rooms', timeout=1)
                self.hub_available = True
                return
            except Exception:
                time.sleep(.25)
        raise RuntimeError('抖音采集器启动超时')

    @staticmethod
    def normalize_room(value):
        value = str(value or '').strip()
        match = re.fullmatch(r'(?:https?://live\.douyin\.com/)?([0-9]{3,25})/?(?:\?[^\s]*)?', value)
        if not match:
            raise ValueError('请输入抖音直播间数字号，或 https://live.douyin.com/数字 链接')
        return match[1]

    def connect(self, room_id, cookie=''):
        room = self.normalize_room(room_id)
        if not isinstance(cookie, str) or len(cookie) > 12000:
            raise ValueError('Cookie 格式或长度无效')
        with self.lock:
            if self.state == 'connecting':
                raise ValueError('正在连接，请稍候')
            old_room = self.room_id
            self.generation += 1
            generation = self.generation
            self.room_id, self.state, self.error = room, 'connecting', ''
        def launch():
            try:
                self._start_hub()
                if old_room:
                    self._http('/api/rooms/' + old_room + '/disconnect', {})
                if cookie.strip():
                    # Global session only in child memory; no implicit cookie persistence.
                    self._http('/api/cookie', {'cookie': cookie.strip(), 'persist': False})
                login = self._http('/api/cookie')
                with self.lock:
                    self.login_configured = bool(login.get('hasLogin'))
                    self.event_counts = {'chat': 0, 'gift': 0, 'member': 0}
                result = self._http('/api/rooms/connect', {'roomId': room}, timeout=100)
                with self.lock:
                    if generation != self.generation:
                        self._http('/api/rooms/' + room + '/disconnect', {})
                        return
                    room_state = (result.get('room') or {}).get('status', 'connected')
                    self.state = room_state
                asyncio.run(self._subscribe(room, generation))
            except Exception as exc:
                with self.lock:
                    if generation == self.generation:
                        self.state, self.error = 'error', str(exc)[:350]
        self.thread = threading.Thread(target=launch, name='douyin-events', daemon=True)
        self.thread.start()
        return self.status()

    async def _subscribe(self, room, generation):
        backoff = 1
        while not self.stop_event.is_set() and generation == self.generation:
            try:
                async with aiohttp.ClientSession(trust_env=False) as client:
                    async with client.ws_connect('http://127.0.0.1:' + str(self.PORT) + '/ws?roomId=' + room + '&types=chat,gift,member,roomStatus', heartbeat=15, max_msg_size=1000000) as ws:
                        backoff = 1
                        while not self.stop_event.is_set() and generation == self.generation:
                            try:
                                message = await asyncio.wait_for(ws.receive(), timeout=1)
                            except asyncio.TimeoutError:
                                continue
                            if message.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR, aiohttp.WSMsgType.CLOSE):
                                break
                            if message.type != aiohttp.WSMsgType.TEXT:
                                continue
                            event = json.loads(message.data)
                            if event.get('type', '').startswith('__'):
                                if event['type'] == '__error':
                                    with self.lock:
                                        self.error = str(event.get('error') or '采集连接错误')[:350]
                                        self.state = 'error'
                                continue
                            if event.get('roomId') != room:
                                continue
                            if event.get('type') == 'roomStatus':
                                with self.lock:
                                    state = event['data'].get('status', self.state)
                                    self.error = event['data'].get('message') or ''
                                    self.state = 'error' if self.error and state != 'live' else state
                            with self.lock:
                                self.last_event = time.time()
                                if event.get('type') in self.event_counts:
                                    self.event_counts[event['type']] += 1
                            self.on_event(event)
            except Exception as exc:
                with self.lock:
                    if generation == self.generation:
                        self.state, self.error = 'reconnecting', str(exc)[:250]
            if generation == self.generation:
                for _ in range(backoff*5):
                    if generation != self.generation or self.stop_event.is_set():
                        return
                    await asyncio.sleep(.2)
                backoff = min(backoff*2, 20)

    def disconnect(self):
        with self.lock:
            self.generation += 1
            room = self.room_id
            self.state, self.room_id = 'disconnected', ''
        if room and self.hub_available:
            self._http('/api/rooms/' + room + '/disconnect', {})
        return self.status()

    def close(self):
        self.stop_event.set()
        try:
            self.disconnect()
        except Exception:
            pass
        if self.process and self.process.poll() is None:
            if os.name == 'nt':
                subprocess.run(['taskkill', '/PID', str(self.process.pid), '/T', '/F'], capture_output=True)
            else:
                self.process.terminate()
