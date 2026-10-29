"""Offline income/source tests; no live gifts, model calls or speech requests."""
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from studio import EventRules
from providers import ProviderManager
from persona import MUSIC_PERSONA
from test_integrations import Clock, gift, chat


class IncomeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root, self.clock = Path(self.tmp.name), Clock()
        self.rules = EventRules(self.root, self.clock)
    def tearDown(self): self.tmp.cleanup()
    def totals(self, bucket='live'): return self.rules.income.summary()['lifetime'][bucket]

    def test_gift_shaped_chat_does_not_create_income_or_gift_job(self):
        self.rules.ingest(chat('chat1', '送出 抖音一号 ×1'))
        self.assertEqual(self.totals()['coins'], 0)
        self.assertEqual(self.totals()['gift_count'], 0)
        self.assertEqual(self.rules.state['jobs'][0]['event']['type'], 'chat')

    def test_real_gift_combo_and_duplicate_final(self):
        self.rules.ingest(gift('1', 1, price=10))
        self.rules.ingest(gift('2', 3, True, price=10))
        self.rules.ingest(gift('3', 3, True, price=10))
        self.assertEqual(self.totals(), {'coins':30,'gift_count':3,'unknown_price_count':0})

    def test_late_higher_frame_adds_only_new_value(self):
        self.rules.ingest(gift('1', 3, price=10))
        self.clock.advance(4);self.rules.due()
        self.rules.ingest(gift('2', 5, True, price=10))
        self.assertEqual(self.totals()['coins'], 50)
        self.assertEqual(self.totals()['gift_count'], 5)

    def test_simulated_gifts_are_separate_from_income(self):
        event = gift(end=True, price=99)
        event.update(simulation=True)
        self.rules.ingest(event)
        self.assertEqual(self.totals()['coins'], 0)
        self.assertEqual(self.totals()['gift_count'], 0)
        self.assertEqual(self.totals('simulation')['coins'], 99)

    def test_unknown_price_and_later_backfill(self):
        self.rules.ingest(gift('1',2,True,price=None))
        self.assertEqual(self.totals(), {'coins':0,'gift_count':2,'unknown_price_count':2})
        job_count = len(self.rules.state['jobs'])
        self.rules.ingest(gift('2',2,True,price=100))
        self.assertEqual(self.totals(), {'coins':200,'gift_count':2,'unknown_price_count':0})
        self.assertEqual(len(self.rules.state['jobs']), job_count)

    def test_simulation_price_cannot_fill_missing_live_price(self):
        test = gift('test',end=True,price=999)
        test.update(simulation=True)
        self.rules.ingest(test)
        self.rules.ingest(gift('live',end=True,price=None,group='live-group'))
        self.assertEqual(self.totals()['coins'], 0)
        self.assertEqual(self.totals()['unknown_price_count'], 1)

    def test_restart_and_old_frame_after_dedup_expiry(self):
        self.rules.ingest(gift('1',3,True,price=10))
        self.clock.advance(86401);self.rules.due()
        restored = EventRules(self.root,self.clock)
        restored.ingest(gift('late',3,True,price=10))
        self.assertEqual(restored.income.summary()['lifetime']['live']['coins'], 30)
        self.assertEqual(restored.income.summary()['lifetime']['live']['gift_count'], 3)

    def test_midnight_rollover_uses_first_frame_date_in_china(self):
        self.clock.value = datetime(2026,10,26,15,59,58,tzinfo=timezone.utc).timestamp()
        self.rules.ingest(gift('1',price=10))
        self.clock.advance(4);self.rules.due()
        summary = self.rules.income.summary()
        self.assertEqual(summary['day'], '2026-10-27')
        self.assertEqual(summary['today']['live']['coins'], 0)
        self.assertEqual(summary['lifetime']['live']['coins'], 10)
        self.assertEqual(self.rules.state['income']['days']['2026-10-26']['live']['coins'], 10)

    def test_feature_start_does_not_retroactively_count_old_settlements(self):
        old = gift('old',3,True,price=10)
        self.rules.ingest(old)
        self.rules.state.pop('income')
        self.rules.save()
        restored = EventRules(self.root,self.clock)
        restored.ingest(gift('new',5,True,price=10))
        self.assertEqual(restored.income.summary()['lifetime']['live']['coins'], 20)
        self.assertEqual(restored.income.summary()['lifetime']['live']['gift_count'], 2)

    def test_known_price_cannot_be_rewritten_by_duplicate(self):
        self.rules.ingest(gift('1',1,True,price=10))
        self.rules.ingest(gift('2',1,True,price=9999))
        self.assertEqual(self.totals()['coins'], 10)

    def test_model_receives_chat_type_and_false_gift_fact(self):
        manager = ProviderManager(SimpleNamespace(),self.root,{'provider':'codex'})
        captured = {}
        class Adapter:
            def generate(self, history, text, event, session):
                captured.update(json.loads(text))
                return {'reply':'文字版礼物呀，本鲸差点接住啦～'}
        manager.adapters['codex'] = Adapter()
        manager.generate('送出 抖音一号 ×1','chat')
        self.assertEqual(captured['trusted_event'], {'type':'chat','gift_received':False})
        self.assertEqual(captured['message'], '送出 抖音一号 ×1')
        self.assertIn('温柔、轻轻调侃', MUSIC_PERSONA)
        self.assertIn('不能确认收到礼物', MUSIC_PERSONA)


if __name__ == '__main__': unittest.main()
