"""One local Codex app-server process; one reusable conversation."""
import atexit
import json
import queue
import subprocess
import threading
import time
import os
import shutil
from pathlib import Path

from persona import LIVE_PERSONA
from live_tools import TOOL_SPECS
from model_debug import CURRENT_TRACE

class ResidentCodex:
    def __init__(self, executable, cwd, environment, model, service_tier='default'):
        self.executable, self.cwd, self.environment, self.model = executable, cwd, environment, model
        self.service_tier = service_tier
        self.process = None
        self.thread_id = None
        self.context_turn_id = None
        self.pending = {}
        self.pending_lock = threading.Lock()
        self.write_lock = threading.Lock()
        self.events = queue.Queue()
        self.counter = 0
        self.starts = 0
        self.turns = 0
        self.latest_rate_limits = {}
        self.rate_limits_updated_at = 0
        atexit.register(self.close)

    def close(self):
        proc, self.process = self.process, None
        self.thread_id = None
        self.context_turn_id = None
        if proc and proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)

    def _read(self, proc):
        for line in proc.stdout:
            if self.process is not proc:
                continue
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            if 'id' in obj and 'method' not in obj:
                with self.pending_lock:
                    target = self.pending.get(obj['id'])
                if target:
                    target.put(obj)
            elif 'id' in obj and 'method' in obj:
                if obj['method'] == 'item/tool/call':
                    # Keep the reader free to drain protocol messages while a tool runs.
                    self.events.put(obj)
                else:
                    self._send({'id': obj['id'], 'error': {'code': -32601, 'message': 'Only registered music tools are available'}})
            elif obj.get('method') == 'account/rateLimits/updated':
                self._remember_rate_limits(obj.get('params', {}))
            else:
                self.events.put(obj)
        if self.process is proc:
            self.events.put({'method': 'resident/exited'})

    def _send(self, obj):
        with self.write_lock:
            self.process.stdin.write(json.dumps(obj, ensure_ascii=False) + '\n')
            self.process.stdin.flush()

    def _remember_rate_limits(self, payload):
        if 'rateLimitsByLimitId' in payload:
            self.latest_rate_limits = payload
        else:
            bucket = payload.get('rateLimits') or {}
            buckets = {**self.latest_rate_limits.get('rateLimitsByLimitId', {})}
            if bucket.get('limitId'):
                buckets[bucket['limitId']] = {**buckets.get(bucket['limitId'], {}), **bucket}
            self.latest_rate_limits = {**payload, 'rateLimitsByLimitId': buckets}
        self.rate_limits_updated_at = time.time()

    def read_rate_limits(self):
        if not self.process or self.process.poll() is not None:
            raise RuntimeError('Codex 常驻连接尚未就绪')
        result = self.rpc('account/rateLimits/read', {}, timeout=8)
        self._remember_rate_limits(result)
        return result

    def rpc(self, method, params, timeout=30):
        trace=CURRENT_TRACE.get()
        if trace and method in ('thread/start','turn/start'):
            trace.request('codex.'+method,params)
        with self.pending_lock:
            self.counter += 1
            request_id = self.counter
            response_queue = self.pending[request_id] = queue.Queue()
        try:
            self._send({'id': request_id, 'method': method, 'params': params})
            result = response_queue.get(timeout=timeout)
            if 'error' in result:
                raise RuntimeError(str(result['error'].get('message', result['error'])))
            return result.get('result', {})
        finally:
            with self.pending_lock:
                self.pending.pop(request_id, None)

    def start(self):
        if self.process and self.process.poll() is None:
            return
        args = [self.executable, 'app-server', '--stdio', '--disable', 'shell_tool', '--disable', 'unified_exec',
                '-c', 'web_search="disabled"', '-c', 'project_doc_max_bytes=0',
                '-c', 'model_reasoning_effort="low"']
        # This process only needs the dynamic music tools registered below.
        # The CLI defaults still fetch remote plugin catalogs with a private home;
        # failed discovery can block a turn before the model request is sent.
        for feature in ('apps', 'plugins', 'remote_plugin', 'recommended_plugins'):
            args.extend(['--disable', feature])
        if self.service_tier == 'fast':
            args.extend(['--enable', 'fast_mode', '-c', 'service_tier="fast"'])
        # A private home isolates config, plugins, MCP, history, memory and token refresh.
        # Copy only existing login credentials, never the user's config or instructions.
        source_home = Path(os.environ.get('CODEX_HOME', str(Path.home()/'.codex')))
        isolated_home = Path(self.cwd) / 'codex-home'
        isolated_home.mkdir(exist_ok=True)
        auth_source = source_home / 'auth.json'
        if auth_source.exists():
            shutil.copy2(auth_source, isolated_home / 'auth.json')
        env = self.environment()
        env['CODEX_HOME'] = str(isolated_home)
        self.events = queue.Queue()
        self.latest_rate_limits = {}
        self.rate_limits_updated_at = 0
        self.error_log = open(Path(self.cwd) / 'codex-resident.log', 'a', encoding='utf-8')
        self.process = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.error_log,
                                        text=True, encoding='utf-8', cwd=self.cwd, env=env,
                                        creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        self.starts += 1
        threading.Thread(target=self._read, args=(self.process,), daemon=True).start()
        try:
            self.rpc('initialize', {'clientInfo': {'name': 'whale_local_preview', 'version': '0.3.0'},
                                    'capabilities': {'experimentalApi': True}})
            self._send({'method': 'initialized', 'params': {}})
            self.reset()
        except Exception:
            self.close()
            raise

    def reset(self):
        if not self.process or self.process.poll() is not None:
            self.start()
            return
        old_thread = self.thread_id
        result = self.rpc('thread/start', {'model': self.model, 'cwd': str(self.cwd), 'ephemeral': False,
                                          'serviceTier': self.service_tier,
                                          'approvalPolicy': 'never', 'sandbox': 'read-only',
                                          'baseInstructions': self.persona_provider() if callable(getattr(self, 'persona_provider', None)) else LIVE_PERSONA, 'developerInstructions': '',
                                          'dynamicTools': [{'type': 'function', 'name': spec['name'],
                                                            'description': spec['description'], 'inputSchema': spec['parameters']}
                                                           for spec in (self.tool_specs_provider() if callable(getattr(self, 'tool_specs_provider', None)) else TOOL_SPECS)],
                                          'config': {'project_doc_max_bytes': 0}})
        self.thread_id = result['thread']['id']
        self.context_turn_id = None
        self.turns = 0
        if old_thread and old_thread != self.thread_id:
            try:
                self.rpc('thread/archive', {'threadId': old_thread}, timeout=5)
            except Exception:
                # Cleanup failure must not lose an already prepared replacement.
                pass

    def _prepare_context_turn(self):
        """Keep the process warm; SQLite owns history, not previous native turns."""
        if self.context_turn_id:
            try:
                self.rpc('thread/revert', {'threadId': self.thread_id,
                                          'beforeTurnId': self.context_turn_id})
                self.context_turn_id = None
            except Exception:
                # Older/legacy thread stores may not support revert. A new thread
                # in the SAME app-server provides the same bounded input safely.
                self.reset()

    def chat(self, text, event='chat', timeout=60, tool_session=None, images=None):
        began = time.perf_counter()
        try:
            self.start()
            self._prepare_context_turn()
            while not self.events.empty():
                self.events.get_nowait()
            result = self.rpc('turn/start', {'threadId': self.thread_id, 'effort': 'low',
                                            'serviceTier': self.service_tier,
                                            'input': [{'type': 'text', 'text': json.dumps({'event': event, 'message': text}, ensure_ascii=False)}]+[
                                                {'type':'localImage', 'path':image} for image in (images or [])]})
            turn_id = result['turn']['id']
            self.context_turn_id = turn_id
            reply, usage, first_ms = '', None, None
            while True:
                remaining = timeout - (time.perf_counter()-began)
                if remaining <= 0:
                    raise queue.Empty()
                notification = self.events.get(timeout=remaining)
                method, params = notification.get('method'), notification.get('params', {})
                if method == 'resident/exited':
                    raise RuntimeError('Codex 常驻进程已退出，请重试')
                if params.get('threadId') not in (None, self.thread_id):
                    continue
                if params.get('turnId') not in (None, turn_id):
                    continue
                if method == 'item/tool/call':
                    if tool_session:
                        output = tool_session.execute(params.get('tool', ''), params.get('arguments', {}), params.get('callId', ''))
                    else:
                        output = {'status': 'error', 'message': '本轮没有直播间工具授权上下文'}
                    self._send({'id': notification['id'], 'result': {
                        'success': output.get('status') != 'error',
                        'contentItems': [{'type': 'inputText', 'text': json.dumps(output, ensure_ascii=False)}]}})
                    trace=CURRENT_TRACE.get()
                    if trace:
                        trace.tool(params.get('tool',''),params.get('arguments',{}),output)
                elif method == 'item/agentMessage/delta':
                    if first_ms is None:
                        first_ms = round((time.perf_counter()-began)*1000)
                elif method == 'item/completed' and params.get('item', {}).get('type') == 'agentMessage':
                    if params['item'].get('phase') != 'commentary':
                        reply = params['item'].get('text', '')
                elif method == 'thread/tokenUsage/updated':
                    usage = params.get('tokenUsage', {}).get('last')
                elif method == 'turn/completed':
                    turn = params.get('turn', {})
                    if turn.get('status') != 'completed':
                        raise RuntimeError((turn.get('error') or {}).get('message', 'Codex 回复未完成'))
                    break
            if not reply:
                raise RuntimeError('Codex 未返回文本')
            self.turns += 1
            trace=CURRENT_TRACE.get()
            if trace:
                trace.response({'content':reply,'usage':usage})
            return {'reply': reply[:1200], 'usage': usage, 'first_text_ms': first_ms,
                    'codex_pid': self.process.pid, 'resident_starts': self.starts, 'session_turns': self.turns,
                    'tool_calls': tool_session.records if tool_session else []}
        except queue.Empty:
            self.close()
            raise TimeoutError('Codex 超过60秒，已停止常驻进程；下次请求自动重建')
        except Exception:
            self.close()
            raise
