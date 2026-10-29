"""Queue cleanup preserves money, cancels old playback and rejects late callbacks."""
import ast
import copy
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock
from music import MusicService, atomic_json
from music_tools import MusicTools
from studio import Studio
from providers import ProviderError
from test_integrations import FakeBridge, chat, gift


class CleanupTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.bridge=FakeBridge();self.music=MusicService(self.root,self.bridge)
        self.tools=MusicTools(self.music)
        self.studio=Studio(self.root,self.music,Mock(side_effect=AssertionError('No model or TTS')),command_tools=self.tools)
        self.lock=threading.Lock()
        source=ast.parse((Path(__file__).parents[1]/'server.py').read_text(encoding='utf-8'))
        function=next(n for n in source.body if isinstance(n,ast.FunctionDef) and n.name=='clear_session_queues')
        scope={'LOCK':self.lock,'STUDIO':self.studio,'MUSIC':self.music,'MUSIC_TOOLS':self.tools,
               'RUNTIME':self.root/'.runtime','atomic_json':atomic_json,'ProviderError':ProviderError,'time':__import__('time')}
        exec(compile(ast.Module(body=[function],type_ignores=[]),'<clear-offline>','exec'),scope)
        self.clear=scope['clear_session_queues']
    def tearDown(self):self.tmp.cleanup()

    def test_clear_settles_pending_gift_income_and_removes_all_playback_work(self):
        self.music.order('晴天')
        self.studio.ingest(gift('gift',count=3,price=10,end=False))
        self.studio.ingest(chat('old','旧消息'))
        job=self.studio.rules.state['jobs'][0]
        job.update(state='ready',result={'reply':'旧回复','audio':'/audio/old.mp3'})
        self.studio.claim('offline-page')
        self.studio.rules.state['voice_retries'].append(copy.deepcopy(job))
        result=self.clear()
        self.assertEqual(result['songs_cleared'],1)
        self.assertEqual(result['combos'],1)
        self.assertIsNotNone(result['backup'])
        saved=json.loads((self.root/'.runtime/session-backups'/result['backup']).read_text(encoding='utf-8'))
        self.assertEqual(len(saved['music']['queue']),1)
        self.assertEqual(self.studio.rules.income.summary()['lifetime']['live']['coins'],30)
        self.assertFalse(self.studio.rules.state['jobs'])
        self.assertFalse(self.studio.rules.state['voice_retries'])
        self.assertIsNone(self.studio.lease)
        self.assertFalse(self.music.queue)
        self.assertIsNone(self.studio.presentation()['current'])
        self.assertEqual(MusicService(self.root,self.bridge).queue,[])
        self.assertFalse(any(action=='execute' for action,_ in self.bridge.calls))
        self.clear()
        self.assertEqual(self.studio.rules.income.summary()['lifetime']['live']['coins'],30)
        self.assertEqual(self.studio.status()['playback_epoch'],2)

    def test_generation_busy_returns_409_without_discarding_requests(self):
        self.studio.ingest(chat('pending','待处理'))
        self.lock.acquire()
        with self.assertRaises(ProviderError) as result:self.clear()
        self.lock.release()
        self.assertEqual(result.exception.status,409)
        self.assertEqual(len(self.studio.rules.state['jobs']),1)

    def test_old_ack_cannot_remove_a_new_response(self):
        self.studio.ingest(chat('old','旧消息'))
        old=self.studio.rules.state['jobs'][0]
        old.update(state='ready',result={'reply':'旧回复'})
        old_id=self.studio.claim('offline-page')['response']['id']
        self.clear()
        self.studio.ingest(chat('new','新消息',uid='new'))
        new=self.studio.rules.state['jobs'][0]
        new.update(state='ready',result={'reply':'新回复'})
        response=self.studio.claim('offline-page',ack=old_id)['response']
        self.assertEqual(response['id'],new['id'])
        self.assertEqual(len(self.studio.rules.state['jobs']),1)
        self.assertFalse(self.studio.rules.state['voice_retries'])

    def test_cancelled_command_callback_cannot_resurrect_notice_or_job(self):
        self.studio.ingest(chat('song','点歌 晴天'))
        job=copy.deepcopy(self.studio.rules.state['jobs'][0])
        self.clear()
        self.studio.song_commands.execute=Mock(return_value={'status':'queued','track':{'title':'旧歌'}})
        self.assertEqual(self.studio.process_song_command(job)['status'],'cancelled')
        self.assertIsNone(self.studio.rules.state['music_command'])
        self.assertFalse(self.studio.rules.state['jobs'])


if __name__=='__main__':unittest.main()
