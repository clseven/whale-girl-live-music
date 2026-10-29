"""Shared broadcast state and multi-window audio ownership; entirely offline."""
import tempfile
import unittest
from pathlib import Path
from studio import Studio
from test_integrations import chat, gift


class PresentationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.studio = Studio(self.root, None, None)

    def tearDown(self):
        self.tmp.cleanup()

    def ready(self, event, reply='收到啦'):
        self.studio.ingest(event)
        job = self.studio.rules.state['jobs'][-1]
        job.update(state='ready', result={'reply': reply, 'audio_parts': ['/audio/private.mp3']})
        return job

    def test_unselected_chat_only_in_incoming_and_survives_coalescing(self):
        self.studio.ingest(chat('one', '第一句'))
        self.studio.ingest(chat('two', '第二句'))
        view = self.studio.presentation()
        self.assertEqual([r['content'] for r in view['incoming']], ['第一句', '第二句'])
        self.assertEqual(view['conversation'], [])
        self.assertEqual(len(self.studio.rules.state['jobs']), 1)

    def test_claim_publishes_the_same_pair_to_every_view_without_audio(self):
        job = self.ready(chat('one', '<script>弹幕</script>'), '你好呀')
        self.studio.claim('preview-one')
        view1 = self.studio.presentation()
        view2 = self.studio.presentation()
        self.assertEqual(view1, view2)
        self.assertEqual([r['content'] for r in view1['conversation']], ['<script>弹幕</script>', '你好呀'])
        self.assertEqual(view1['current']['id'], job['id'])
        self.assertNotIn('audio', str(view1))
        self.assertIsNone(self.studio.claim('preview-two')['response'])
        self.assertEqual(view1, self.studio.presentation())

    def test_new_window_and_server_restart_recover_display_history(self):
        job = self.ready(chat('one', '你好'))
        self.studio.claim('preview-one')
        self.studio.claim('preview-one', ack=job['id'])
        other = Studio(self.root, None, None)
        self.assertEqual(other.presentation(), self.studio.presentation())

    def test_clear_is_shared_and_old_retry_does_not_duplicate_history(self):
        job = self.ready(chat('one', '你好'))
        self.studio.claim('preview-one')
        self.studio.claim('preview-one', ack=job['id'], failed=True)
        self.studio.clear_presentation()
        self.studio.retry()
        self.studio.claim('preview-one')
        self.assertEqual(self.studio.presentation()['conversation'], [])
        self.assertEqual(self.studio.presentation()['incoming'], [])
        self.assertIsNone(self.studio.presentation()['current'])

    def test_combo_updates_one_incoming_row_and_text_gift_stays_chat(self):
        self.studio.ingest(gift('g1', count=1))
        self.studio.ingest(gift('g2', count=5, end=True))
        self.studio.ingest(chat('fake', '送出 抖音一号 ×1'))
        rows = self.studio.presentation()['incoming']
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]['content'], '送出 测试礼物 ×5')
        self.assertEqual(rows[1]['type'], 'chat')

    def test_incoming_is_bounded_without_losing_recent_rows(self):
        for i in range(110):
            self.studio.ingest(chat(str(i), '弹幕'+str(i)))
        rows = self.studio.presentation()['incoming']
        self.assertEqual(len(rows), 100)
        self.assertEqual(rows[0]['content'], '弹幕10')
        self.assertEqual(rows[-1]['content'], '弹幕109')

    def test_live_takes_idle_preview_audio_ownership_and_prevents_double_play(self):
        self.studio.claim('preview-one')
        job = self.ready(chat('one', '你好'))
        self.assertEqual(self.studio.claim('live-window', role='live')['response']['id'], job['id'])
        self.assertIsNone(self.studio.claim('preview-one')['response'])

    def test_live_waits_for_sentence_then_gets_next_reply_even_with_backlog(self):
        first = self.ready(chat('one', '你好', uid='a'))
        second = self.ready(chat('two', '再见', uid='b'))
        self.studio.claim('preview-one')
        self.assertIsNone(self.studio.claim('live-window', role='live')['response'])
        self.assertEqual(self.studio.owner, 'preview-one')
        self.assertIsNone(self.studio.claim('preview-one', ack=first['id'])['response'])
        self.assertEqual(self.studio.claim('live-window', role='live')['response']['id'], second['id'])

    def test_reading_snapshot_cannot_change_shared_state(self):
        self.studio.ingest(chat('one', '你好'))
        snapshot = self.studio.presentation()
        snapshot['incoming'].clear()
        self.assertEqual(len(self.studio.presentation()['incoming']), 1)

    def test_failed_audio_does_not_stop_next_model_job_and_retry_reuses_result(self):
        first = self.ready(chat('one', '你好', uid='a'))
        self.studio.ingest({**chat('two', '下一条', uid='b'), 'simulation': True})
        self.studio.claim('client-one')
        self.studio.claim('client-one', ack=first['id'], failed=True, audio_ready=False, failure_reason='NotAllowedError: autoplay')
        self.assertFalse(self.studio.playback_blocked)
        self.assertIn('NotAllowedError', self.studio.status()['audio_warning'])
        self.assertEqual(self.studio.rules.next_job()['event']['data']['content'], '下一条')
        self.studio.retry()
        replay = next(j for j in self.studio.rules.state['jobs'] if j['id'] == first['id'])
        self.assertEqual(replay['state'], 'ready')
        self.assertEqual(replay['result'], first['result'])

    def test_all_audio_blocked_can_continue_text_without_discarding_voice(self):
        first = self.ready(chat('one', '你好'))
        delivered = self.studio.claim('client-one', audio_ready=False)['response']
        self.assertTrue(delivered['text_only'])
        self.assertEqual(delivered['audio_parts'], [])
        self.assertEqual(len(self.studio.rules.state['voice_retries']), 1)
        self.studio.claim('client-one', ack=first['id'], audio_ready=False)
        self.assertEqual(self.studio.rules.state['jobs'], [])
        self.assertFalse(self.studio.playback_blocked)

    def test_blocked_window_yields_to_another_audio_ready_window(self):
        first = self.ready(chat('one', '你好'))
        self.studio.clients['healthy-window'] = {'audio_ready': True, 'time': __import__('time').time()}
        self.assertIsNone(self.studio.claim('blocked-window', audio_ready=False)['response'])
        self.assertEqual(self.studio.claim('healthy-window')['response']['id'], first['id'])


if __name__ == '__main__':
    unittest.main()
