"""A refreshed browser must not leave a reply permanently holding the queue."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from studio import Studio
from douyin import DouyinSource
from test_integrations import Clock, gift


class HandoffTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.clock = Clock()
        self.studio = Studio(Path(self.tmp.name), None, lambda *args: self.fail('Must reuse prepared reply'))
        self.studio.ingest(gift(end=True))
        self.job = self.studio.rules.state['jobs'][0]
        self.job.update(state='ready', result={'reply': '已生成的感谢', 'audio': '/audio/existing.mp3'})

    def tearDown(self):
        self.tmp.cleanup()

    def claim(self, client, **kwargs):
        with patch('studio.time.time', self.clock):
            return self.studio.claim(client, **kwargs)

    def test_refresh_handoff_uses_existing_reply_and_new_client_can_ack(self):
        first = self.claim('old-page')['response']
        self.clock.advance(16)
        again = self.claim('new-page')['response']
        self.assertEqual(first['id'], again['id'])
        self.assertEqual(again['audio'], '/audio/existing.mp3')
        self.assertEqual(self.studio.lease['client'], 'new-page')
        self.claim('new-page', ack=again['id'])
        self.assertEqual(self.studio.rules.state['jobs'], [])

    def test_other_client_polling_cannot_extend_original_lease(self):
        self.claim('old-page')
        until = self.studio.lease['until']
        self.clock.advance(5)
        self.assertTrue(self.claim('new-page')['another_player'])
        self.assertEqual(self.studio.lease['until'], until)
        self.clock.advance(11)
        self.assertIsNotNone(self.claim('new-page')['response'])

    def test_valid_late_ack_does_not_replay_completed_reply(self):
        first = self.claim('old-page')['response']
        self.clock.advance(25)
        self.claim('old-page', ack=first['id'])
        self.assertEqual(self.studio.rules.state['jobs'], [])


class CollectorReuseTests(unittest.TestCase):
    def test_backend_can_reuse_running_collector_without_reading_cookie(self):
        with tempfile.TemporaryDirectory() as directory:
            source = DouyinSource(Path(directory), lambda event: None)
            with patch.object(source, '_http', return_value={'rooms': []}) as http:
                source._start_hub()
                self.assertTrue(source.hub_available)
                self.assertIsNone(source.process)
                http.assert_called_once_with('/api/rooms', timeout=1)
                source.room_id = '12345'
                source.disconnect()
                self.assertEqual(http.call_args.args[0], '/api/rooms/12345/disconnect')


if __name__ == '__main__':
    unittest.main()
