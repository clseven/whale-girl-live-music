"""Model adapters and shared conversation state; no HTTP UI or speech logic here."""
import json
import base64
import os
import time
import urllib.request
import urllib.error
from abc import ABC, abstractmethod
from persona import LIVE_PERSONA, build_live_persona
from rules_config import RuleConfig
from visuals import split_visual_reply
from music_tools import TOOL_SPECS
from live_clock import beijing_time
from conversation_context import ConversationContext
from reply_protocol import strip_watch_tags
from model_debug import ModelDebug, CURRENT_TRACE

class ProviderError(RuntimeError):

    def __init__(self, message, status=502):
        super().__init__(message)
        self.status = status

class ModelProvider(ABC):

    @abstractmethod
    def generate(self, history, text, event, tool_session=None):
        pass

    @abstractmethod
    def reset(self):
        pass

class CodexProvider(ModelProvider):

    def __init__(self, resident, config, persona_provider=None):
        self.resident, self.config = (resident, config)
        self.persona_provider = persona_provider or (lambda: LIVE_PERSONA)
        self.current_persona = self.persona_provider()
        self.resident.persona_provider = self.persona_provider
        self.needs_sync = False

    def reset(self):
        self.needs_sync = True

    def generate(self, history, text, event, tool_session=None):
        desired = self.persona_provider()
        if desired != self.current_persona:
            self.current_persona = desired
            self.needs_sync = True
        if self.needs_sync:
            self.resident.reset()
            self.needs_sync = False
        images = getattr(tool_session, 'images', [])
        if images:
            return self.resident.chat(text, event, tool_session=tool_session, images=images)
        return self.resident.chat(text, event, tool_session=tool_session)

class DeepSeekProvider(ModelProvider):

    def __init__(self, root, config, persona_provider=None):
        self.root, self.config = (root, config)
        self.persona_provider = persona_provider or (lambda: LIVE_PERSONA)

    def reset(self):
        pass

    def key(self):
        key = os.environ.get('DEEPSEEK_API_KEY')
        secret_file = self.root / '.runtime' / 'secrets.json'
        if not key and secret_file.exists():
            key = json.loads(secret_file.read_text(encoding='utf-8')).get('deepseek_api_key')
        return key

    def generate(self, history, text, event, tool_session=None):
        key = self.key()
        if not key:
            raise ProviderError('DeepSeek 密钥未配置', 503)
        messages = [{'role': 'system', 'content': self.persona_provider()}]
        messages.append({'role': 'user', 'content': json.dumps({'event': event, 'message': text}, ensure_ascii=False)})
        images = getattr(tool_session, 'images', [])
        if images:
            from pathlib import Path
            messages[-1]['content'] = [{'type': 'text', 'text': messages[-1]['content']}] + [{'type': 'image_url', 'image_url': {'url': 'data:image/jpeg;base64,' + base64.b64encode(Path(p).read_bytes()).decode()}} for p in images]
        began = time.perf_counter()
        usage = {}
        try:
            for round_number in range(4):
                body = {'model': self.config.get('deepseek_model', 'deepseek-flash'), 'messages': messages, 'thinking': {'type': 'disabled'}, 'max_tokens': 512, 'stream': True, 'stream_options': {'include_usage': True}}
                if tool_session:
                    body['tools'] = [{'type': 'function', 'function': spec} for spec in getattr(tool_session, 'specs', TOOL_SPECS)]
                    body['tool_choice'] = 'none' if round_number == 3 else 'auto'
                remaining = 60 - (time.perf_counter() - began)
                if remaining <= 0:
                    raise TimeoutError('DeepSeek 工具与回复流程超过60秒，请重试')
                message, first_ms, round_usage = self._stream(body, key, began, remaining)
                for k, v in (round_usage or {}).items():
                    if type(v) in (int, float):
                        usage[k] = usage.get(k, 0) + v
                calls = message.get('tool_calls', [])
                if not calls:
                    reply = (message.get('content') or '').strip()
                    if not reply:
                        raise ProviderError('DeepSeek 没有返回正文')
                    return {'reply': reply[:1200], 'usage': usage or None, 'first_text_ms': first_ms, 'codex_pid': None, 'resident_starts': None, 'session_turns': len(history) + 1, 'tool_calls': tool_session.records if tool_session else []}
                if not tool_session or round_number == 3:
                    raise ProviderError('模型在本轮没有完成工具后的回复，请重试')
                messages.append(message)
                for call in calls:
                    function = call.get('function') or {}
                    result = tool_session.execute(function.get('name', ''), function.get('arguments', ''), call['id'])
                    trace = CURRENT_TRACE.get()
                    if trace:
                        trace.tool(function.get('name', ''), function.get('arguments', ''), result)
                    messages.append({'role': 'tool', 'tool_call_id': call['id'], 'content': json.dumps(result, ensure_ascii=False)})
        except urllib.error.HTTPError as exc:
            descriptions = {401: 'DeepSeek 密钥无效', 402: 'DeepSeek 余额不足', 429: 'DeepSeek 请求过于频繁', 400: 'DeepSeek 请求参数不被接受'}
            raise ProviderError(descriptions.get(exc.code, 'DeepSeek 服务错误（HTTP ' + str(exc.code) + '）'), 502) from None
        except urllib.error.URLError:
            raise ProviderError('DeepSeek 网络连接失败，请检查网络') from None

    def _stream(self, body, key, began, timeout):
        trace = CURRENT_TRACE.get()
        if trace:
            trace.request('deepseek.chat.completions', body)
        request = urllib.request.Request('https://api.deepseek.com/chat/completions', data=json.dumps(body, ensure_ascii=False).encode(), headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + key})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        chunks, calls, first_ms, usage, finished = ([], {}, None, None, False)
        with opener.open(request, timeout=timeout) as response:
            for line in response:
                if time.perf_counter() - began > 60:
                    raise TimeoutError('DeepSeek 超过60秒，请重试')
                if not line.startswith(b'data:'):
                    continue
                raw = line[5:].strip()
                if raw == b'[DONE]':
                    finished = True
                    break
                item = json.loads(raw)
                if item.get('error'):
                    raise ProviderError('DeepSeek 返回生成错误，请稍后重试')
                usage = item.get('usage') or usage
                for choice in item.get('choices', []):
                    delta = choice.get('delta') or {}
                    content = delta.get('content')
                    if content:
                        if first_ms is None:
                            first_ms = round((time.perf_counter() - began) * 1000)
                        chunks.append(content)
                    for piece in delta.get('tool_calls') or []:
                        index = piece.get('index', 0)
                        call = calls.setdefault(index, {'id': '', 'type': 'function', 'function': {'name': '', 'arguments': ''}})
                        if piece.get('id'):
                            call['id'] = piece['id']
                        function = piece.get('function') or {}
                        call['function']['name'] += function.get('name') or ''
                        call['function']['arguments'] += function.get('arguments') or ''
                        if len(call['function']['arguments']) > 4096 or len(calls) > 8:
                            raise ProviderError('模型工具参数过大')
                    if choice.get('finish_reason') == 'length':
                        raise ProviderError('DeepSeek 输出达到长度上限，请缩短输入后重试')
        if not finished:
            raise ProviderError('DeepSeek 响应不完整，请重试')
        message = {'role': 'assistant', 'content': ''.join(chunks) or None}
        if calls:
            if any((not c['id'] or not c['function']['name'] for c in calls.values())):
                raise ProviderError('DeepSeek 工具调用不完整')
            message['tool_calls'] = [calls[i] for i in sorted(calls)]
        if trace:
            trace.response({**message, 'usage': usage})
        return (message, first_ms, usage)

class ProviderManager:

    def __init__(self, resident, root, config, rules=None, context=None):
        self.config, self.path = (config, root / 'local-config.json')
        self.rules = rules or RuleConfig(root)
        self.debug = ModelDebug()

        def persona():
            settings = self.rules.snapshot()['persona']
            return build_live_persona(settings)
        self.adapters = {'codex': CodexProvider(resident, config, persona), 'deepseek': DeepSeekProvider(root, config, persona)}
        self.selected = config.get('provider', 'codex')
        self.context = context if context is not None else ConversationContext(root)
        self.context_room = 'local'
        self.last_provider = None
        self.tool_host = None
        self.visual_context = None

    @property
    def history(self):
        return self.context.recent(self.context_room)

    def model(self, name):
        return self.config.get('model', 'gpt-6.1-sol') if name == 'codex' else self.config.get('deepseek_model', 'deepseek-flash')

    def status(self):
        return {'provider': self.selected, 'model': self.model(self.selected), 'history_turns': len(self.history), 'context_window': self.context.window, 'context_storage': 'sqlite', 'tool_names': [spec['name'] for spec in getattr(self.tool_host, 'specs', TOOL_SPECS)] if self.tool_host else [], 'providers': [{'id': name, 'model': self.model(name), 'configured': bool(self.adapters[name].key()) if name == 'deepseek' else bool(self.adapters[name].resident.executable)} for name in self.adapters]}

    def select(self, name):
        if name not in self.adapters:
            raise ProviderError('未知模型供应商', 400)
        if name == 'deepseek' and (not self.adapters[name].key()):
            raise ProviderError('DeepSeek 密钥未配置', 503)
        updated = {**self.config, 'provider': name}
        temporary = self.path.with_suffix('.tmp')
        temporary.write_text(json.dumps(updated, ensure_ascii=False, indent=2), encoding='utf-8')
        temporary.replace(self.path)
        self.config.update(updated)
        self.selected = name
        return self.status()

    def reset(self):
        self.context.clear()
        for adapter in self.adapters.values():
            adapter.reset()
        self.last_provider = None

    def generate(self, text, event, user=None, room_id='local', event_id=None, gift_context=None):
        if not hasattr(self, 'debug'):
            self.debug = ModelDebug()
        trace = self.debug.start(provider=self.selected, model=self.model(self.selected), event=event, viewer=user, room_id=room_id, event_id=event_id, trigger=text)
        token = CURRENT_TRACE.set(trace)
        try:
            result = self._generate(text, event, user, room_id, event_id, gift_context)
            trace.finish(result)
            return {**{k: v for k, v in result.items() if not k.startswith('debug_')}, 'debug_id': trace.id}
        except Exception as exc:
            trace.finish(error=exc)
            raise
        finally:
            CURRENT_TRACE.reset(token)

    def _generate(self, text, event, user=None, room_id='local', event_id=None, gift_context=None):
        name = self.selected
        adapter = self.adapters[name]
        self.context_room = str(room_id)
        history = self.context.recent(self.context_room)
        session = None
        if self.tool_host and event in ('chat',):
            session = self.tool_host.bind(user, room_id, event_id)
        envelope = {'message': text, 'trusted_event': {'type': event, 'gift_received': event == 'gift'}}
        envelope['room_id'] = self.context_room
        envelope['viewer'] = {'id': str((user or {}).get('id') or ''), 'nickname': str((user or {}).get('nickname') or '朋友')}
        envelope['recent_conversation'] = history
        envelope['current_time'] = {**beijing_time(), 'note': '本轮回复开始时的北京时间；以此为准，不沿用旧轮次的时间。自然结合时段聊天，无需每次报时。'}
        if event == 'gift' and gift_context:
            envelope['trusted_event']['gift'] = gift_context['facts']
            envelope['gift_thanks'] = {k: v for k, v in gift_context.items() if k != 'facts'}
            envelope['gift_thanks']['recent_replies'] = [t['assistant'] for t in history if t['event'] == 'gift'][-6:]
        if session:
            context = session.context()
            envelope['music_context'] = context
        if self.visual_context:
            envelope['appearance_context'] = self.visual_context()
        model_input = json.dumps(envelope, ensure_ascii=False)
        trace = CURRENT_TRACE.get()
        if trace:
            persona = getattr(adapter, 'persona_provider', None)
            trace.input(persona() if callable(persona) else '', envelope, getattr(session, 'specs', []) if session else [])
            trace.store.update(trace.id, model_called=True)
        result = adapter.generate(history, model_input, event, session)
        original_output = result['reply']
        if trace:
            trace.store.update(trace.id, raw_output=original_output)
        raw_reply = strip_watch_tags(result['reply'])
        reply, visual = split_visual_reply(raw_reply)
        silent = False
        if not reply and (not silent):
            raise ProviderError('模型未返回可播放的回复正文，请重试')
        result = {**result, 'reply': '' if silent else reply, 'visual_request': visual}
        result['debug_raw_output'] = original_output
        history_text = json.dumps(gift_context['facts'], ensure_ascii=False) if event == 'gift' and gift_context else text
        if not silent:
            self.context.append(self.context_room, user, event, history_text, result['reply'], name, event_id)
        self.last_provider = name
        return {**result, 'provider': name, 'model': self.model(name)}
