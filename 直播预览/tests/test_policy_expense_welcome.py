"""Routing/accounting/welcomes with synthetic quotas and events; no model calls."""
import copy
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from expense import ExpenseEstimate, token_count
from model_policy import ModelPolicy, quota_windows
from providers import ProviderManager
from rules_config import RuleConfig
from studio import EventRules
from test_integrations import Clock, chat, gift


def limits(remaining, reset=101000, weekly_remaining=80):
    return {'rateLimitsByLimitId': {'codex': {
        'primary': {'usedPercent': 100-remaining, 'windowDurationMins': 300, 'resetsAt': reset},
        'secondary': {'usedPercent': 100-weekly_remaining, 'windowDurationMins': 10080, 'resetsAt': reset+86400}}}}


class ModelPolicyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.root = Path(self.tmp.name)
        self.clock = Clock(); self.rules = RuleConfig(self.root); self.lock = threading.Lock()
        self.payload = limits(50); self.selected = []
        self.models = SimpleNamespace(selected='codex', history=['keep me'])
        def select(name):
            self.models.selected = name; self.selected.append(name); return {'provider': name}
        self.models.select = select
        self.resident = SimpleNamespace(read_rate_limits=lambda: copy.deepcopy(self.payload))
        self.policy = ModelPolicy(self.root, self.rules, self.resident, self.models, self.lock, self.clock)
    def tearDown(self): self.tmp.cleanup()

    def test_window_classified_by_duration_not_slot(self):
        bucket = limits(4)['rateLimitsByLimitId']['codex']
        bucket['primary'], bucket['secondary'] = bucket['secondary'], bucket['primary']
        self.assertEqual(quota_windows({'rateLimits': bucket})['five_hour']['remaining_percent'], 4)

    def test_five_percent_is_allowed_strictly_below_switches(self):
        self.payload = limits(5); self.policy.check(); self.assertEqual(self.models.selected, 'codex')
        self.payload = limits(4); self.policy.check(); self.assertEqual(self.models.selected, 'deepseek')
        self.assertEqual(self.models.history, ['keep me'])
        self.assertEqual(self.policy.status()['windows']['five_hour']['resets_at'], 101000)

    def test_reset_time_alone_never_assumes_recovery(self):
        self.payload = limits(4); self.policy.check()
        self.clock.advance(1001); self.policy.check(); self.assertEqual(self.models.selected, 'deepseek')
        self.payload = limits(90, reset=120000); self.policy.check(); self.assertEqual(self.models.selected, 'codex')

    def test_cached_reset_survives_restart_and_recheck_is_scheduled(self):
        self.payload = limits(4, reset=100020); self.policy.check()
        self.assertEqual(self.policy.status()['next_check_at'], 100022)
        restored = ModelPolicy(self.root, self.rules, self.resident, self.models, self.lock, self.clock)
        self.assertEqual(restored.status()['windows']['five_hour']['resets_at'], 100020)

    def test_busy_generation_defers_switch_but_not_account_read(self):
        self.payload = limits(4); self.lock.acquire()
        try:
            self.policy.check(); self.assertEqual(self.models.selected, 'codex')
            self.assertEqual(self.policy.status()['windows']['five_hour']['remaining_percent'], 4)
        finally: self.lock.release()
        self.policy.apply_pending(); self.assertEqual(self.models.selected, 'deepseek')

    def test_manual_deepseek_stays_until_codex_selected(self):
        self.policy.manual_select('deepseek'); self.policy.check(); self.assertEqual(self.models.selected, 'deepseek')
        self.policy.manual_select('codex'); self.policy.check(); self.assertEqual(self.models.selected, 'codex')

    def test_unknown_quota_is_not_zero_and_network_failure_keeps_model(self):
        self.payload = {}; self.policy.check(); self.assertEqual(self.models.selected, 'codex')
        self.resident.read_rate_limits = lambda: (_ for _ in ()).throw(RuntimeError('network'))
        self.policy.check(); self.assertIn('network', self.policy.status()['error'])
        self.assertEqual(self.models.selected, 'codex')

    def test_exhausted_weekly_window_prevents_early_codex_return(self):
        self.payload = limits(90, weekly_remaining=0); self.policy.check()
        self.assertEqual(self.models.selected, 'deepseek')


class ExpenseTests(unittest.TestCase):
    def test_camel_case_and_snake_case_usage_without_double_count(self):
        self.assertEqual(token_count({'totalTokens': 100, 'inputTokens': 80, 'outputTokens': 20}, '', ''), (100, False))
        self.assertEqual(token_count({'total_tokens': 100, 'prompt_tokens': 80, 'completion_tokens': 20}, '', ''), (100, False))
        self.assertEqual(token_count(None, '你好', '你好呀'), (8, True))

    def test_cost_is_explicit_estimate_persists_and_separates_days(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); clock = Clock(); rules = RuleConfig(root)
            rules.data['expenses']['deepseek_flash']['period'] = 'off_peak'
            estimate = ExpenseEstimate(root, rules, clock)
            estimate.record({'provider': 'codex', 'reply': 'hi', 'usage': {'totalTokens': 1000000}}, '')
            estimate.record({'provider': 'rules', 'reply': 'welcome'}, '')
            self.assertEqual(estimate.status()['estimated_yuan'], 1)
            self.assertEqual(ExpenseEstimate(root, rules, clock).status()['requests'], 1)
            clock.advance(86400); self.assertEqual(estimate.status()['tokens'], 0)


class RecentHistoryTests(unittest.TestCase):
    def test_last_twelve_pairs_restore_from_shared_store(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manager = ProviderManager(SimpleNamespace(executable='unused'), root, {})
            manager.adapters['codex'] = SimpleNamespace(generate=lambda history, text, event, session: {'reply': '回复'}, reset=lambda: None)
            for i in range(15): manager.generate(str(i), 'chat')
            restored = ProviderManager(SimpleNamespace(executable='unused'), root, {})
            self.assertEqual(len(restored.history), 12)
            self.assertEqual(restored.history[0]['user'], '3')
            self.assertEqual(restored.context.path, manager.context.path)

    def test_explicit_context_reset_clears_persisted_pairs(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manager = ProviderManager(SimpleNamespace(executable='unused'), root, {})
            manager.adapters['codex'] = SimpleNamespace(generate=lambda history, text, event, session: {'reply': '回复'}, reset=lambda: None)
            manager.generate('你好', 'chat'); manager.reset()
            restored = ProviderManager(SimpleNamespace(executable='unused'), root, {})
            self.assertEqual(restored.history, [])


class WelcomeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.clock = Clock()
        self.rules = EventRules(Path(self.tmp.name), self.clock)
    def tearDown(self): self.tmp.cleanup()
    def member(self, eid='m1', uid='u1'):
        return {**chat(eid, '', uid=uid), 'type': 'member', 'data': {'memberCount': 20}}

    def test_no_timer_invents_an_arrival_and_active_chat_does_not_welcome(self):
        self.clock.advance(50); self.rules.due(); self.assertFalse(self.rules.state['jobs'])
        self.rules.ingest(chat('chat', '你好'))
        self.assertEqual(self.rules.ingest(self.member())['status'], 'ignored')

    def test_idle_arrival_makes_fixed_low_priority_welcome_without_model(self):
        self.clock.advance(46)
        self.assertEqual(self.rules.ingest(self.member())['status'], 'welcome_queued')
        job = self.rules.state['jobs'][0]
        self.assertEqual(job['event']['type'], 'welcome'); self.assertEqual(job['priority'], 1)
        self.assertIn('小水', job['event']['data']['reply'])
        self.assertEqual(self.rules.presentation.snapshot()['incoming'][0]['type'], 'member')

    def test_new_chat_cancels_queued_welcome_and_old_welcome_expires(self):
        self.clock.advance(46); self.rules.ingest(self.member())
        self.rules.ingest(chat('chat', '你好'))
        self.assertTrue(all(j['event']['type'] != 'welcome' for j in self.rules.state['jobs']))
        self.rules.state['jobs'].clear(); self.clock.advance(601); self.rules.ingest(self.member('m2'))
        self.clock.advance(16); self.rules.due(); self.assertFalse(self.rules.state['jobs'])

    def test_global_and_same_user_cooldown(self):
        self.clock.advance(46); self.rules.ingest(self.member())
        self.rules.state['jobs'].clear()
        self.assertEqual(self.rules.ingest(self.member('m2', 'u2'))['status'], 'ignored')
        self.clock.advance(61)
        self.assertEqual(self.rules.ingest(self.member('m3'))['status'], 'ignored')
        self.assertEqual(self.rules.ingest(self.member('m4', 'u2'))['status'], 'welcome_queued')


if __name__ == '__main__': unittest.main()
