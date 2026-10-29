"""User playlist and requested songs share one native order; no live calls."""
import copy
import tempfile
import unittest
from pathlib import Path
from music import MusicService
from test_integrations import FakeBridge, Clock


def track(identifier):
    return {'id': str(identifier), 'title': '歌曲'+str(identifier), 'artist': '歌手'}


class PlaylistBridge(FakeBridge):
    def __init__(self):
        super().__init__()
        self.playlist = [track('bg1'), track('bg2'), track('bg3')]
        self.index = 0
        self.observe()

    def observe(self):
        self.snapshot['current'] = copy.deepcopy(self.playlist[self.index])
        self.snapshot['next'] = copy.deepcopy(self.playlist[self.index+1]) if self.index+1 < len(self.playlist) else None

    def advance(self):
        self.index += 1
        self.observe()

    def request(self, action, **kwargs):
        if action == 'execute' and kwargs.get('command') == 'InsertNext':
            self.calls.append((action, copy.deepcopy(kwargs)))
            self.playlist.insert(self.index+1, copy.deepcopy(kwargs['track']))
            self.observe()
            return {'outcome': 'verified', 'snapshot': copy.deepcopy(self.snapshot)}
        return super().request(action, **kwargs)


class PlaylistTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.bridge = PlaylistBridge()
        self.clock = Clock()
        self.music = MusicService(self.root, self.bridge, clock=self.clock)

    def tearDown(self):
        self.tmp.cleanup()

    def add(self, identifier):
        self.bridge.tracks = [track(identifier)]
        return self.music.order('歌', user_id=identifier, automatic=True)

    def test_background_request_request_background_in_one_native_playlist(self):
        self.add('r1'); self.add('r2')
        self.music.tick(); self.music.tick()
        self.assertEqual(self.bridge.snapshot['current']['id'], 'bg1')
        self.assertEqual(self.bridge.snapshot['next']['id'], 'r1')
        self.assertEqual([t['id'] for t in self.bridge.playlist], ['bg1','r1','bg2','bg3'])
        self.bridge.advance(); self.music.tick()
        self.assertEqual(self.music.current['track']['id'], 'r1')
        self.assertEqual(self.bridge.snapshot['next']['id'], 'r2')
        self.bridge.advance(); self.music.tick()
        self.assertEqual(self.music.current['track']['id'], 'r2')
        self.assertEqual(self.bridge.snapshot['next']['id'], 'bg2')
        self.bridge.advance(); self.music.tick()
        self.assertIsNone(self.music.current)
        self.assertFalse(self.music.enabled)
        self.assertEqual([t['id'] for t in self.bridge.playlist], ['bg1','r1','r2','bg2','bg3'])
        self.assertEqual([p['command'] for a,p in self.bridge.calls if a=='execute'], ['InsertNext','InsertNext'])

    def test_long_background_song_does_not_time_out_or_reinsert(self):
        self.add('r1'); self.music.tick()
        self.clock.advance(3600); self.music.tick()
        self.assertEqual(self.bridge.snapshot['current']['id'], 'bg1')
        self.assertEqual(len([a for a,_ in self.bridge.calls if a=='execute']), 1)

    def test_empty_player_can_start_without_waiting_for_an_impossible_song_end(self):
        self.bridge.snapshot['current'] = None
        self.add('r1'); self.music.tick()
        self.assertEqual([p['command'] for a,p in self.bridge.calls if a=='execute'], ['PlaySelected'])

    def test_guard_is_allowed_to_complete_transition_before_pausing(self):
        self.add('r1'); self.music.tick()
        self.bridge.snapshot.update(current=track('unexpected'), nextGuardState='waitingLateTarget')
        self.music.tick()
        self.clock.advance(3)
        self.bridge.snapshot['current'] = track('r1')
        self.music.tick()
        self.assertEqual(self.music.current['track']['id'], 'r1')

    def test_failed_handoff_preserves_requests_and_never_overwrites_background(self):
        self.add('r1'); self.music.tick()
        self.bridge.snapshot['current'] = track('unexpected')
        self.music.tick(); self.clock.advance(11)
        with self.assertRaisesRegex(RuntimeError, '未切到'):
            self.music.tick()
        self.assertEqual(len(self.music.queue), 1)
        self.assertEqual([p['command'] for a,p in self.bridge.calls if a=='execute'], ['InsertNext'])

    def test_staged_request_survives_restart_without_duplicate_native_insertion(self):
        self.add('r1'); self.music.tick()
        restored = MusicService(self.root, self.bridge, clock=self.clock)
        restored.start_requested_queue(); restored.tick()
        self.assertEqual([p['command'] for a,p in self.bridge.calls if a=='execute'], ['InsertNext'])
        self.assertEqual(self.bridge.snapshot['current']['id'], 'bg1')

    def test_verified_native_next_can_resume_after_restart_without_reinsertion(self):
        self.add('r1'); self.music.tick()
        restored = MusicService(self.root, self.bridge, clock=self.clock)
        restored.control('start'); restored.tick()
        self.assertTrue(restored.enabled)
        self.assertEqual([p['command'] for a,p in self.bridge.calls if a=='execute'], ['InsertNext'])

    def test_no_insert_capability_never_falls_back_to_interrupting_current_song(self):
        self.bridge.snapshot['capabilities']['insertNext'] = False
        self.add('r1')
        with self.assertRaisesRegex(RuntimeError, '下一首插入能力'):
            self.music.tick()
        self.assertEqual(self.bridge.snapshot['current']['id'], 'bg1')
        self.assertFalse(any(action == 'execute' for action, _ in self.bridge.calls))
        self.assertEqual(self.music.queue[0]['state'], 'waiting')


if __name__ == '__main__':
    unittest.main()
