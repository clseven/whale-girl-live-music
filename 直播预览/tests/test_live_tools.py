"""Live read tools, room boundaries and both model protocols; entirely offline."""
import ast
import copy
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
import yaml
from live_tools import LiveTools, TOOL_SPECS
from music import MusicService
from music_tools import MusicTools
from studio import Studio
from rules_config import DEFAULT_RULES, RuleConfig
from expense import ExpenseEstimate
from providers import DeepSeekProvider
from codex_resident import ResidentCodex
from test_integrations import FakeBridge, chat, gift


class LiveToolTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.root = Path(self.tmp.name)
        self.config = copy.deepcopy(DEFAULT_RULES)
        self.config['live_tools'].update(history_retention_messages=4, history_max_results=4)
        (self.root/'直播规则.yaml').write_text(yaml.safe_dump(self.config, allow_unicode=True), encoding='utf-8')
        self.rules = RuleConfig(self.root)
        self.music = MusicService(self.root, FakeBridge(), rules=self.rules)
        self.studio = Studio(self.root, self.music, lambda *args:None, self.rules)
        self.stats = Mock(return_value={'tokens':123,'income_yuan':4.8,'expense_yuan':.2})
        self.host = LiveTools(MusicTools(self.music), self.stats, self.studio.search_recent_chat, self.rules)

    def tearDown(self): self.tmp.cleanup()
    def session(self, uid='one', room='123'): return self.host.bind({'id':uid,'nickname':'观众'}, room, 'event')

    def remember(self, identifier, text, reply, uid='one', room='123'):
        event = chat(identifier,text,uid); event['roomId'] = room
        self.studio.rules.presentation.present({'id':identifier,'event':event,'result':{'reply':reply}})
        self.studio.rules.save()

    def test_read_tools_are_registered_without_exposing_reset_or_file_access(self):
        self.assertEqual({spec['name'] for spec in TOOL_SPECS},
            {'request_song','withdraw_song','get_music_queue','get_today_stats','search_recent_chat'})
        session = self.session()
        self.assertEqual(session.execute('reset_statistics',{})['status'],'error')
        self.assertEqual(session.execute('get_today_stats',{'path':'auth.json'})['status'],'error')
        self.stats.assert_not_called()

    def test_stats_reads_are_fresh_not_cached_like_music_mutations(self):
        session = self.session()
        self.assertEqual(session.execute('get_today_stats',{})['tokens'],123)
        self.stats.return_value['tokens'] = 124
        self.assertEqual(session.execute('get_today_stats',{})['tokens'],124)
        self.assertEqual(self.host.music_tools.receipts,{})

    def test_music_query_preserves_playing_and_waiting_count_distinction(self):
        self.music.bridge.snapshot['current'] = {'id':'1','title':'当前歌','artist':'歌手'}
        result = self.session().execute('get_music_queue',{})
        self.assertEqual(result['queue_count'],0)
        self.assertEqual(result['current_track']['title'],'当前歌')
        self.assertTrue(result['queue_count_excludes_current'])
        self.assertFalse(self.music.bridge.calls)

    def test_history_is_room_scoped_and_can_be_limited_to_current_viewer(self):
        self.remember('other','私有房间内容','其他回复',room='other')
        self.remember('a','吃蛋包饭了吗','还没呢',uid='one')
        self.remember('b','今天聊什么','聊音乐',uid='two')
        result = self.session().execute('search_recent_chat',{'query':'','only_me':True})
        self.assertEqual([r['content'] for r in result['records']],['吃蛋包饭了吗','还没呢'])
        result = self.session().execute('search_recent_chat',{'query':'音乐'})
        self.assertEqual(result['count'],1)
        self.assertNotIn('私有房间',str(result))
        result = self.session().execute('search_recent_chat',{'query':'','room_id':'other'})
        self.assertEqual(result['status'],'error')

    def test_archive_survives_clear_display_and_restart_but_remains_bounded(self):
        for i in range(3): self.remember(str(i),'问题'+str(i),'回复'+str(i))
        self.studio.clear_presentation()
        self.assertEqual(self.studio.presentation()['conversation'],[])
        restored = Studio(self.root,self.music,lambda *args:None,self.rules)
        result = restored.search_recent_chat('123','one','',20)
        self.assertEqual(result['count'],4)
        self.assertNotIn('问题0',str(result))
        self.assertFalse(result['history_is_complete'])

    def test_unscoped_legacy_records_are_not_exposed_as_current_room_history(self):
        self.studio.rules.presentation.state['conversation'] = [{'content':'旧版未标房间'}]
        self.assertEqual(self.session().execute('search_recent_chat',{'query':''})['count'],0)

    def test_query_arguments_and_shared_call_budget_are_enforced(self):
        for arguments in ({'query':'x'*101},{'query':'','limit':True},{'query':'','only_me':'true'}):
            self.assertEqual(self.session().execute('search_recent_chat',arguments)['status'],'error')
        session = self.session()
        for _ in range(3): self.assertEqual(session.execute('get_today_stats',{})['status'],'ok')
        self.assertEqual(session.execute('get_music_queue',{})['status'],'error')

    def test_deepseek_registers_live_tools_and_feeds_read_result_back(self):
        session = self.session(); bodies = []
        def stream(body,key,began,timeout):
            bodies.append(copy.deepcopy(body))
            if len(bodies)==1:
                return ({'role':'assistant','content':None,'tool_calls':[{'id':'stats','type':'function',
                    'function':{'name':'get_today_stats','arguments':'{}'}}]},None,None)
            self.assertEqual(json.loads(body['messages'][-1]['content'])['tokens'],123)
            return ({'role':'assistant','content':'今天用了123个token。'},1,None)
        with patch.object(DeepSeekProvider,'key',return_value='offline'), patch.object(DeepSeekProvider,'_stream',side_effect=stream):
            result = DeepSeekProvider(self.root,{}).generate([],'用了多少token','chat',session)
        self.assertEqual({t['function']['name'] for t in bodies[0]['tools']},{t['name'] for t in TOOL_SPECS})
        self.assertEqual(result['tool_calls'][0]['tool'],'get_today_stats')

    def test_codex_dispatches_same_read_tool_session(self):
        root, session = self.root, self.session()
        class FakeResident(ResidentCodex):
            def __init__(self):
                super().__init__('unused',root,lambda:{},'offline')
                self.thread_id='t'; self.process=SimpleNamespace(pid=42); self.sent=[]
            def start(self): pass
            def close(self): pass
            def _send(self,item): self.sent.append(item)
            def rpc(self,method,params,timeout=30):
                self.events.put({'id':1,'method':'item/tool/call','params':{'threadId':'t','turnId':'r','tool':'get_today_stats','arguments':{},'callId':'s'}})
                self.events.put({'method':'item/completed','params':{'threadId':'t','turnId':'r','item':{'type':'agentMessage','text':'用了123个token'}}})
                self.events.put({'method':'turn/completed','params':{'threadId':'t','turnId':'r','turn':{'status':'completed'}}})
                return {'turn':{'id':'r'}}
        resident = FakeResident(); result = resident.chat('多少token',tool_session=session)
        self.assertEqual(result['tool_calls'][0]['result']['tokens'],123)
        self.assertTrue(resident.sent[0]['result']['success'])

    def test_statistics_snapshot_separates_real_and_simulated_income(self):
        self.studio.ingest(gift('real',price=10,end=True))
        simulated = gift('simulation',price=99,end=True,group='sim'); simulated['simulation']=True
        self.studio.ingest(simulated)
        expenses = ExpenseEstimate(self.root,self.rules)
        expenses.record({'provider':'codex','reply':'好','usage':{'total_tokens':100}},'hi')
        source = ast.parse((Path(__file__).parents[1]/'server.py').read_text(encoding='utf-8'))
        functions = [n for n in source.body if isinstance(n,ast.FunctionDef) and n.name=='read_live_statistics']
        namespace = {'STUDIO':self.studio,'EXPENSES':expenses,'RULES':self.rules,'DOUYIN':SimpleNamespace(room_id='123')}
        exec(compile(ast.Module(body=functions,type_ignores=[]),'<live-stats-offline>','exec'),namespace)
        result = namespace['read_live_statistics']()
        self.assertEqual(result['income_yuan'],.5)
        self.assertEqual(result['simulated_gift_income_yuan'],4.95)
        self.assertEqual(result['tokens'],100)
        self.assertFalse(result['official_settlement_available'])


if __name__ == '__main__': unittest.main()
