"""A display estimate, intentionally separate from provider billing and quota."""
import json
import threading
import time
from datetime import datetime, timezone, timedelta
from music import atomic_json


def token_count(usage, text, reply):
    usage = usage or {}
    for key in ('total_tokens', 'totalTokens'):
        value = usage.get(key)
        if type(value) in (int, float) and value >= 0:
            return int(value), False
    values = [usage.get('inputTokens', usage.get('prompt_tokens')), usage.get('outputTokens', usage.get('completion_tokens'))]
    if all(type(v) in (int, float) and v >= 0 for v in values):
        return int(sum(values)), False
    return max(1, round((len(text)+len(reply))*1.5)), True


class ExpenseEstimate:
    def __init__(self, root, rules, clock=time.time):
        self.path, self.rules, self.clock = root / '.runtime/expense-estimate.json', rules, clock
        self.lock = threading.RLock()
        self.days = json.loads(self.path.read_text(encoding='utf-8')) if self.path.exists() else {}
        if any('pricing_version' not in row for row in self.days.values()):
            # Old totals contain no provider/cache split. Keep them as legacy evidence,
            # rather than pretending they can be recalculated at the new rate.
            atomic_json(root / '.runtime/stats-backups' / ('expense-before-cache-pricing-'+str(time.time_ns())+'.json'), self.days)
            for row in self.days.values():
                if 'pricing_version' not in row:
                    row.update(legacy_estimated_yuan=row.get('estimated_yuan', 0),
                               legacy_tokens=row.get('tokens', 0), estimated_yuan=0, pricing_version=2)
            atomic_json(self.path, self.days)

    def day(self):
        return datetime.fromtimestamp(self.clock(), timezone(timedelta(hours=8))).date().isoformat()

    def record(self, result, text):
        if result.get('provider') not in ('codex', 'deepseek'):
            return
        count, approximated = token_count(result.get('usage'), text, result['reply'])
        policy = self.rules.snapshot()['expenses']
        usage = result.get('usage') or {}
        # Both providers use the same DeepSeek display prices, regardless of model.
        parts, incomplete = flash_tokens(usage, count)
        approximated = approximated or incomplete
        flash = policy['deepseek_flash']
        now = datetime.fromtimestamp(self.clock(), timezone(timedelta(hours=8)))
        peak = flash['period'] == 'peak' or (flash['period'] == 'auto' and now.weekday() < 5
            and now.date().isoformat() not in flash['off_peak_dates'] and (9 <= now.hour < 12 or 14 <= now.hour < 18))
        amount = sum(parts[k]*flash[k] for k in ('cache_hit', 'cache_miss', 'output'))/1000000
        amount *= flash['peak_multiplier'] if peak else 1
        with self.lock:
            row = self.days.setdefault(self.day(), {'tokens': 0, 'estimated_yuan': 0, 'requests': 0, 'approximated_requests': 0, 'pricing_version': 2})
            row['tokens'] += count
            row['estimated_yuan'] += amount
            row['requests'] += 1
            row['approximated_requests'] += int(approximated)
            provider = row.setdefault('providers', {}).setdefault(result['provider'], {'tokens': 0, 'estimated_yuan': 0, 'requests': 0})
            provider['tokens'] += count
            provider['estimated_yuan'] += amount
            provider['requests'] += 1
            if parts:
                for key, value in parts.items():
                    provider[key] = provider.get(key, 0)+value
            atomic_json(self.path, self.days)

    def status(self):
        with self.lock:
            return {'day': self.day(), 'basis': '展示估算，非实际账单',
                    **self.days.get(self.day(), {'tokens': 0, 'estimated_yuan': 0, 'requests': 0, 'approximated_requests': 0})}

    def reset(self):
        with self.lock:
            self.days = {}
            atomic_json(self.path, self.days)
            return self.status()


def flash_tokens(usage, total):
    """Missing cache counters mean uncached input; never invent a cache-hit ratio."""
    def valid(*keys):
        for key in keys:
            value = usage.get(key)
            if type(value) in (int, float) and value >= 0:
                return int(value)
        return None
    output = valid('completion_tokens', 'outputTokens')
    prompt = valid('prompt_tokens', 'inputTokens')
    hit = valid('prompt_cache_hit_tokens', 'cachedInputTokens')
    miss = valid('prompt_cache_miss_tokens')
    incomplete = output is None or prompt is None or (hit is None and miss is None)
    output = min(total, output or 0)
    prompt = prompt if prompt is not None else total-output
    if hit is None:
        hit = max(0, prompt-miss) if miss is not None else 0
    hit = min(prompt, hit)
    return {'cache_hit': hit, 'cache_miss': max(0, prompt-hit), 'output': output}, incomplete
