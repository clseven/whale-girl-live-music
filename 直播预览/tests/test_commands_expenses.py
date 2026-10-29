"""Offline command admission, shared music execution and cache-aware estimates."""
import copy
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import Mock
import yaml
from expense import ExpenseEstimate, flash_tokens
from music import MusicService, atomic_json
from music_commands import is_song_command
from rules_config import DEFAULT_RULES, RuleConfig
from studio import Studio, EventRules
from test_integrations import FakeBridge, chat, Clock


class CommandTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.root = Path(self.tmp.name)
        self.render = Mock(side_effect=AssertionError('must not call model/TTS'))
        self.music = MusicService(self.root, FakeBridge())
        self.studio = Studio(self.root, self.music, self.render)
    def tearDown(self): self.tmp.cleanup()

    def test_only_explicit_format_bypasses_chat_selection(self):
        for text in ('点歌 晴天', '点歌：晴天', '点歌'):
            self.assertTrue(is_song_command(text))
        for text in ('点歌好麻烦', '帮我点歌 晴天', '我想听晴天'):
            self.assertFalse(is_song_command(text))
        self.studio.ingest(chat('one', '点歌 晴天 / 周杰伦'))
        self.assertIsNone(self.studio.rules.next_job())
        job = self.studio.rules.state['jobs'][0]
        self.assertEqual(job['event']['type'], 'song_command')
        result = self.studio.process_song_command(job)
        self.assertEqual(result['status'], 'queued')
        self.assertEqual(len(self.music.queue), 1)
        self.assertFalse(self.studio.rules.state['jobs'])
        self.render.assert_not_called()
        self.assertEqual(self.studio.presentation()['conversation'], [])
        self.assertEqual(self.studio.ingest(chat('one', '点歌 晴天'))['status'], 'duplicate')

    def test_duplicate_failure_is_visible_and_limits_are_shared(self):
        for eid in ('a', 'b'):
            self.studio.ingest(chat(eid, '点歌 晴天'))
            result = self.studio.process_song_command(self.studio.rules.state['jobs'][0])
        self.assertEqual(result['status'], 'already_queued')
        self.assertEqual(len(self.music.queue), 1)
        self.assertEqual(self.studio.status()['music_command']['status'], 'already_queued')
        self.studio.ingest(chat('empty', '点歌'))
        self.assertEqual(self.studio.process_song_command(self.studio.rules.state['jobs'][0])['status'], 'error')
        self.render.assert_not_called()

    def test_pending_commands_bounded_and_not_expired_with_chat(self):
        clock = Clock(); rules = EventRules(self.root, clock)
        for i in range(3): result = rules.ingest(chat(str(i), '点歌 晴天'))
        self.assertEqual(result['status'], 'rejected')
        self.assertEqual(len(rules.state['jobs']), 2)
        clock.advance(100); rules.due()
        self.assertEqual(len(rules.state['jobs']), 2)


class CachePriceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.root = Path(self.tmp.name)
        self.policy = copy.deepcopy(DEFAULT_RULES)
        self.policy['expenses']['deepseek_flash']['period'] = 'off_peak'
        self.write()
        self.rules = RuleConfig(self.root)
        self.clock = lambda: datetime(2026,10,28,10,tzinfo=timezone(timedelta(hours=8))).timestamp()
        self.expenses = ExpenseEstimate(self.root, self.rules, self.clock)
    def write(self): (self.root/'直播规则.yaml').write_text(yaml.safe_dump(self.policy, allow_unicode=True), encoding='utf-8')
    def tearDown(self): self.tmp.cleanup()
    def record(self, **extra):
        self.expenses.record({'provider':'deepseek','model':'deepseek-flash','reply':'好',
            'usage':{'prompt_tokens':1000000,'completion_tokens':1000,'prompt_cache_hit_tokens':900000,'prompt_cache_miss_tokens':100000}, **extra}, '你好')

    def test_real_cache_split_replaces_flat_rate_and_persists(self):
        self.record()
        self.assertAlmostEqual(self.expenses.status()['estimated_yuan'], .122)
        self.assertEqual(self.expenses.status()['tokens'], 1001000)
        restored = ExpenseEstimate(self.root, self.rules, self.clock)
        self.assertEqual(restored.status()['providers']['deepseek']['cache_hit'], 900000)
        self.record(provider='codex', model='gpt-6-luna')
        self.assertAlmostEqual(self.expenses.status()['estimated_yuan'], .122+.122)

    def test_codex_native_usage_has_the_same_cost_as_deepseek(self):
        self.record()
        self.expenses.record({'provider':'codex', 'model':'gpt-6-luna', 'reply':'好',
            'usage':{'totalTokens':1001000, 'inputTokens':1000000,
                     'cachedInputTokens':900000, 'outputTokens':1000}}, '你好')
        providers = self.expenses.status()['providers']
        self.assertEqual(providers['codex'], providers['deepseek'])
        self.assertAlmostEqual(providers['codex']['estimated_yuan'], .122)

    def test_codex_and_deepseek_share_peak_prices(self):
        self.policy['expenses']['deepseek_flash']['period'] = 'peak'; self.write()
        self.record()
        self.record(provider='codex', model='gpt-6-luna')
        providers = self.expenses.status()['providers']
        self.assertEqual(providers['codex'], providers['deepseek'])
        self.assertAlmostEqual(providers['codex']['estimated_yuan'], .244)

    def test_missing_cache_is_not_fabricated(self):
        parts, incomplete = flash_tokens({'prompt_tokens':1000,'completion_tokens':20},1020)
        self.assertEqual(parts, {'cache_hit':0,'cache_miss':1000,'output':20})
        self.assertTrue(incomplete)
        parts, _ = flash_tokens({'prompt_tokens':1000,'completion_tokens':20,'prompt_cache_miss_tokens':100},1020)
        self.assertEqual(parts['cache_hit'],900)

    def test_peak_schedule_and_holiday_override(self):
        self.policy['expenses']['deepseek_flash']['period']='auto'; self.write()
        self.record(); self.assertAlmostEqual(self.expenses.status()['estimated_yuan'], .244)
        self.policy['expenses']['deepseek_flash']['off_peak_dates']=['2026-10-28']; self.write()
        self.record(); self.assertAlmostEqual(self.expenses.status()['estimated_yuan'], .366)

    def test_old_estimate_is_backed_up_separated_and_tokens_preserved(self):
        path=self.root/'.runtime/expense-estimate.json'
        atomic_json(path, {'2026-10-28':{'tokens':10000,'estimated_yuan':.05,'requests':3,'approximated_requests':0}})
        self.expenses=ExpenseEstimate(self.root,self.rules,self.clock)
        status=self.expenses.status()
        self.assertEqual(status['tokens'],10000)
        self.assertEqual(status['estimated_yuan'],0)
        self.assertEqual(status['legacy_estimated_yuan'],.05)
        self.assertEqual(len(list((self.root/'.runtime/stats-backups').glob('expense-before-cache-pricing-*'))),1)
        self.record(); self.assertAlmostEqual(self.expenses.status()['estimated_yuan'],.122)
        self.assertEqual(self.expenses.status()['tokens'],1011000)


if __name__ == '__main__': unittest.main()
