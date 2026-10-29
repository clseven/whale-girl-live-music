"""Quota-based routing. Account reads never occupy the generation lock."""
import copy
import json
import math
import threading
import time
from music import atomic_json


def quota_windows(payload):
    buckets = payload.get('rateLimitsByLimitId') or {}
    bucket = buckets.get('codex') or payload.get('rateLimits') or {}
    result = {}
    for slot in ('primary', 'secondary'):
        window = bucket.get(slot) or {}
        used = window.get('usedPercent')
        minutes = window.get('windowDurationMins')
        if type(used) not in (int, float) or not math.isfinite(used):
            continue
        if minutes not in (300, 10080):
            continue
        reset = window.get('resetsAt')
        result['five_hour' if minutes == 300 else 'weekly'] = {
            'remaining_percent': max(0, min(100, 100-used)),
            'resets_at': reset if type(reset) in (int, float) and math.isfinite(reset) else None}
    return result


class ModelPolicy:
    def __init__(self, root, rules, resident, models, generation_lock, clock=time.time):
        self.rules, self.resident, self.models = rules, resident, models
        self.generation_lock, self.clock = generation_lock, clock
        self.path = root / '.runtime/provider-policy.json'
        self.lock, self.stop_event = threading.RLock(), threading.Event()
        saved = json.loads(self.path.read_text(encoding='utf-8')) if self.path.exists() else {}
        self.state = {'manual_deepseek': saved.get('manual_deepseek', False),
                      'windows': saved.get('windows', {}), 'checked_at': None,
                      'next_check_at': 0, 'reason': '等待读取 Codex 额度', 'error': ''}
        self.pending = None
        self.seen_notification = 0

    def status(self):
        with self.lock:
            return copy.deepcopy({**self.state, 'enabled': self.rules.snapshot()['model_policy']['enabled']})

    def save(self):
        atomic_json(self.path, self.state)

    def manual_select(self, name):
        with self.lock:
            result = self.models.select(name)
            self.state['manual_deepseek'] = name == 'deepseek'
            self.state['reason'] = '手动 DeepSeek 对比；切回 Codex 恢复自动' if name == 'deepseek' else 'Codex 优先，额度不足时自动切换'
            self.state['next_check_at'] = 0
            self.save()
            return {**result, 'policy': self.status()}

    def apply_pending(self, already_locked=False):
        acquired = already_locked or self.generation_lock.acquire(blocking=False)
        if not acquired:
            return
        try:
            with self.lock:
                if self.pending is None:
                    return
                windows, self.pending = self.pending, None
                policy = self.rules.snapshot()['model_policy']
                if not policy['enabled'] or self.state['manual_deepseek']:
                    return
                five, weekly = windows.get('five_hour'), windows.get('weekly')
                low = bool(five and five['remaining_percent'] < policy['fallback_below_percent'])
                exhausted = any(w['remaining_percent'] <= 0 for w in windows.values())
                if low or exhausted:
                    if self.models.selected != 'deepseek':
                        self.models.select('deepseek')
                    self.state['reason'] = 'Codex 五小时额度不足 '+str(policy['fallback_below_percent'])+'%，自动使用 DeepSeek' if low else 'Codex 额度已用完，自动使用 DeepSeek'
                elif (five and five['remaining_percent'] >= policy['return_at_percent']) or (not five and weekly and weekly['remaining_percent'] > 0):
                    if self.models.selected != 'codex':
                        self.models.select('codex')
                    self.state['reason'] = 'Codex 额度充足，优先使用 Codex'
                else:
                    self.state['reason'] = '未返回可判断的额度窗口，保持当前模型'
                self.save()
        finally:
            if not already_locked:
                self.generation_lock.release()

    def check(self):
        now = self.clock()
        try:
            payload = self.resident.read_rate_limits()
            windows = quota_windows(payload)
            config = self.rules.snapshot()['model_policy']
            five = windows.get('five_hour')
            wait = config['check_seconds']
            if five and five['remaining_percent'] < config['fallback_below_percent']:
                wait = config['fallback_check_seconds']
                if five.get('resets_at') and five['resets_at'] > now:
                    wait = min(wait, five['resets_at']-now+2)
            with self.lock:
                self.state.update(windows=windows, checked_at=now, next_check_at=now+wait, error='')
                self.pending = windows
                self.save()
            self.apply_pending()
        except Exception as exc:
            with self.lock:
                self.pending = None
                self.state.update(error='额度读取或切换失败：'+str(exc)[:180], next_check_at=now+self.rules.snapshot()['model_policy']['check_seconds'])
                self.save()

    def run(self):
        def loop():
            while not self.stop_event.is_set():
                try:
                    config = self.rules.snapshot()['model_policy']
                    if config['enabled']:
                        stamp = getattr(self.resident, 'rate_limits_updated_at', 0)
                        if stamp > self.seen_notification:
                            self.seen_notification = stamp
                            payload = getattr(self.resident, 'latest_rate_limits', {})
                            with self.lock:
                                self.pending = quota_windows(payload)
                                self.state.update(windows=self.pending, checked_at=self.clock())
                        if self.clock() >= self.state['next_check_at']:
                            self.check()
                        else:
                            self.apply_pending()
                except Exception as exc:
                    with self.lock:
                        self.state.update(error='自动切换暂未完成：'+str(exc)[:180], next_check_at=self.clock()+60)
                        self.save()
                self.stop_event.wait(1)
        threading.Thread(target=loop, name='quota-routing', daemon=True).start()

    def close(self):
        self.stop_event.set()
