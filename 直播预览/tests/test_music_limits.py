"""Offline request admission checks; never search/play with a real connector."""
import copy
import tempfile
import threading
import unittest
from pathlib import Path
import yaml
from music import MusicService
from studio import EventRules
from rules_config import DEFAULT_RULES
from test_integrations import FakeBridge, Clock, chat


class MusicLimitsTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.root=Path(self.tmp.name);self.clock=Clock();self.bridge=FakeBridge()
        self.config=copy.deepcopy(DEFAULT_RULES)
        self.path=self.root/'直播规则.yaml';self.write()
        self.music=MusicService(self.root,self.bridge,clock=self.clock)

    def tearDown(self):self.tmp.cleanup()
    def write(self):self.path.write_text(yaml.safe_dump(self.config,allow_unicode=True,sort_keys=False),encoding='utf-8')
    def track(self,i):return {'id':str(2000000+i),'title':'歌曲'+str(i),'artist':'歌手','album':''}
    def add(self,i,user=None):
        self.bridge.tracks=[self.track(i)]
        return self.music.order('歌曲'+str(i),user_id=user or 'u'+str(i))

    def test_twelve_waiting_tracks_and_thirteenth_rejected_before_native_calls(self):
        for i in range(12):self.add(i)
        before=len(self.bridge.calls)
        with self.assertRaisesRegex(ValueError,'12首'):
            self.music.order('第十三首','u13',automatic=True)
        self.assertEqual(len(self.music.queue),12)
        self.assertEqual(len(self.bridge.calls),before)
        self.assertEqual(self.music.status()['queue_count'],12)

    def test_each_viewer_can_only_hold_two_waiting_tracks(self):
        self.add(1,'same');self.add(2,'same')
        with self.assertRaisesRegex(ValueError,'2首'):self.add(3,'same')
        self.assertEqual(len(self.music.queue),2)

    def test_duplicate_id_is_normalized_and_blocked_across_viewers(self):
        self.add(1,'a')
        self.bridge.tracks=[{**self.track(1),'id':2000001}]
        with self.assertRaisesRegex(ValueError,'已经在排队'):self.music.order('同首歌曲','b')
        self.assertEqual(len(self.music.queue),1)

    def test_native_current_song_cannot_be_queued_again(self):
        self.bridge.snapshot['current']=self.track(1)
        with self.assertRaisesRegex(ValueError,'已经在播放'):self.add(1)

    def test_finished_song_cooldown_expires_and_survives_restart(self):
        self.add(1);self.music.enabled=True;self.music.tick();self.music.tick()
        self.bridge.snapshot['current']=self.track(1)
        self.music.tick()
        self.bridge.snapshot['current']={'id':'other'}
        self.music.tick()
        restored=MusicService(self.root,self.bridge,clock=self.clock)
        self.bridge.tracks=[self.track(1)]
        with self.assertRaisesRegex(ValueError,'刚放过'):restored.order('歌曲1','another')
        self.clock.advance(600.1)
        self.assertEqual(restored.order('歌曲1','another')['status'],'queued')

    def test_replay_window_can_be_disabled_from_yaml(self):
        self.music.recent=[{'track':self.track(1),'finished_at':self.clock()}]
        self.config['music']['replay_cooldown_seconds']=0;self.write()
        self.assertEqual(self.add(1)['status'],'queued')

    def test_limit_hot_reload_does_not_discard_accepted_songs(self):
        for i in range(3):self.add(i)
        self.config['music']['max_pending']=2;self.write()
        with self.assertRaisesRegex(ValueError,'2首'):self.add(4)
        self.assertEqual(len(self.music.queue),3)
        self.assertEqual(self.music.status()['limits']['max_pending'],2)

    def test_concurrent_requests_cannot_overfill_queue(self):
        errors=[]
        def enqueue(i):
            try:
                with self.music.lock:self.music._enqueue(self.track(i),'u'+str(i),'观众')
            except ValueError as exc:errors.append(str(exc))
        threads=[threading.Thread(target=enqueue,args=(i,)) for i in range(20)]
        for t in threads:t.start()
        for t in threads:t.join()
        self.assertEqual(len(self.music.queue),12)
        self.assertEqual(len(errors),8)

    def test_chat_queue_is_bounded_but_ready_replies_do_not_expire(self):
        self.config['studio']['chat_max_pending']=2;self.write()
        rules=EventRules(self.root,self.clock)
        for i in range(3):rules.ingest(chat(str(i),'消息'+str(i),uid=str(i)))
        self.assertEqual(len(rules.state['jobs']),2)
        self.assertEqual(rules.state['jobs'][0]['event']['data']['content'],'消息1')
        rules.state['jobs'][0]['state']='ready'
        self.clock.advance(31);rules.due()
        self.assertEqual(len(rules.state['jobs']),1)
        self.assertEqual(rules.state['jobs'][0]['state'],'ready')


if __name__=='__main__':unittest.main()
