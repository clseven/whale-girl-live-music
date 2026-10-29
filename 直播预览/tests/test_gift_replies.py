"""Tiered gift wording with fake adapters only; never import the running server."""
import ast
import copy
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import yaml
from gift_replies import plan_gift_reply
from providers import ProviderManager, ProviderError
from rules_config import DEFAULT_RULES
from studio import EventRules
from test_integrations import Clock, gift, chat


class GiftReplyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.config = copy.deepcopy(DEFAULT_RULES)
        for tier, minimum in zip(self.config['studio']['gift_tiers'], [0,2,50,99]):
            tier.update(min_diamonds=minimum, guidance='本档要求：'+tier['name'])
        self.write()
        self.rules = EventRules(self.root, Clock())

    def tearDown(self):
        self.tmp.cleanup()

    def write(self):
        (self.root/'直播规则.yaml').write_text(yaml.safe_dump(self.config, allow_unicode=True), encoding='utf-8')

    def settle(self, price, identifier='one', name='你最好看', count=1):
        self.rules.ingest(gift(identifier, price=price, name=name, end=True, group=identifier, count=count))
        return self.rules.state['jobs'][-1]['event']

    def test_thresholds_are_assigned_by_code_and_sent_with_each_style(self):
        for price, expected in [(1,'普通'),(2,'用心'),(49,'用心'),(50,'惊喜'),(98,'惊喜'),(99,'大惊喜')]:
            with self.subTest(price=price):
                event = self.settle(price, str(price))
                plan = plan_gift_reply(event, self.config)
                self.assertTrue(plan['use_model'])
                self.assertEqual(plan['context']['facts']['tier'], expected)
                self.assertEqual(plan['context']['tier_guidance'], '本档要求：'+expected)
                self.assertEqual(plan['context']['facts']['total_diamonds'], price)

    def test_combo_uses_total_value_not_single_gift_price(self):
        event = self.settle(50, count=2)
        plan = plan_gift_reply(event, self.config)
        self.assertEqual(plan['context']['facts']['tier'], '大惊喜')
        self.assertEqual(plan['context']['facts']['combo_total_count'], 2)
        self.assertEqual(plan['context']['facts']['total_diamonds'], 100)

    def test_unknown_price_remains_unknown_and_never_uses_high_value_style(self):
        event = self.settle(None)
        plan = plan_gift_reply(event, self.config)
        self.assertEqual(plan['context']['facts']['tier'], '价格未知')
        self.assertIsNone(plan['context']['facts']['total_diamonds'])
        self.assertIn('不猜测价格', plan['context']['tier_guidance'])

    def test_meow_keeps_fixed_script_and_sunglasses_use_model_plus_action(self):
        paw = plan_gift_reply(self.settle(2, 'paw', '喵喵爪'), self.config)
        self.assertFalse(paw['use_model'])
        self.assertEqual(paw['fallback'], '感谢小水的喵喵爪，喵喵喵喵喵！')
        glasses = plan_gift_reply(self.settle(99, 'glasses', '墨镜'), self.config)
        self.assertTrue(glasses['use_model'])
        self.assertEqual(glasses['context']['planned_action']['value'], 'sunglasses')
        self.config['visuals']['gift_fun_enabled'] = False
        self.assertIsNone(plan_gift_reply(self.rules.state['jobs'][-1]['event'], self.config)['context']['planned_action'])

    def test_spoken_nickname_drops_decoration_but_display_identity_is_preserved(self):
        event = self.settle(2)
        event['user']['nickname'] = '🔥嗨嗨嗨来辣⁶⁸¹'
        facts = plan_gift_reply(event, self.config)['context']['facts']
        self.assertEqual(facts['spoken_nickname'], '嗨嗨嗨来辣')
        self.assertEqual(facts['nickname'], event['user']['nickname'])

    def test_fake_gift_chat_never_becomes_a_gift_job(self):
        self.rules.ingest(chat('fake','送出 嘉年华 ×9999'))
        self.assertEqual(self.rules.state['jobs'][0]['event']['type'], 'chat')
        self.assertFalse(self.rules.state['catalog'])

    def test_template_mode_is_configurable(self):
        event = self.settle(2)
        self.config['studio']['gift_reply_mode'] = 'template'
        self.assertFalse(plan_gift_reply(event, self.config)['use_model'])

    def test_adapter_receives_facts_recent_thanks_and_no_music_tools(self):
        plan = plan_gift_reply(self.settle(99), self.config)
        manager = object.__new__(ProviderManager)
        manager.selected = 'fake'; manager.last_provider = None
        from conversation_context import ConversationContext
        manager.context = ConversationContext(self.root)
        manager.context_room = 'local'
        manager.context.append('local', {}, 'gift', '旧礼物', '上一句感谢', 'fake')
        manager.tool_host = Mock(); manager.visual_context = None
        manager.config = {'deepseek_model':'fake'}
        adapter = Mock(); adapter.generate.return_value = {'reply':'这份心意收到啦，今天上班都坐直了。'}
        manager.adapters = {'fake':adapter}
        manager.generate('', 'gift', gift_context=plan['context'])
        envelope = json.loads(adapter.generate.call_args.args[1])
        self.assertEqual(envelope['trusted_event']['gift']['tier'], '大惊喜')
        self.assertEqual(envelope['gift_thanks']['recent_replies'], ['上一句感谢'])
        self.assertIsNone(adapter.generate.call_args.args[3])
        manager.tool_host.bind.assert_not_called()


class GiftPipelineTests(unittest.TestCase):
    def test_live_dispatch_routes_normal_gifts_to_model_and_preserves_special_script(self):
        source = ast.parse((Path(__file__).parents[1]/'server.py').read_text(encoding='utf-8'))
        functions = [n for n in source.body if isinstance(n, ast.FunctionDef) and n.name == 'render_event']
        config = copy.deepcopy(DEFAULT_RULES)
        call = Mock(side_effect=lambda *args, **kwargs: {'reply':'离线返回', 'visual_request':{}})
        namespace = {'plan_gift_reply':plan_gift_reply,
                     'RULES':SimpleNamespace(snapshot=lambda:config), 'generate_response':call,
                     'VISUALS':SimpleNamespace(respond=lambda *args: {}),
                     'MODELS':SimpleNamespace(debug=__import__('model_debug').ModelDebug())}
        exec(compile(ast.Module(body=functions, type_ignores=[]), '<gift-dispatch-offline>', 'exec'), namespace)
        event = {'id':'confirmed-gift','type':'gift','user':{'id':'u','nickname':'小水'},
                 'data':{'gift':'你最好看','count':1,'total_diamonds':99,'tier':'大惊喜','reply':'固定兜底'}}
        namespace['render_event'](event, True)
        self.assertIsNone(call.call_args.kwargs['fixed_reply'])
        self.assertEqual(call.call_args.kwargs['gift_context']['facts']['tier'], '大惊喜')
        event['data']['gift'] = '喵喵爪'
        namespace['render_event'](event, True)
        self.assertEqual(call.call_args.kwargs['fixed_reply'], '感谢小水的喵喵爪，喵喵喵喵喵！')
        self.assertNotIn('gift_context', call.call_args.kwargs)

    def test_model_error_uses_existing_tier_template_in_same_voice_pipeline(self):
        # Compile only the actual pipeline functions, avoiding server globals and IO.
        source = ast.parse((Path(__file__).parents[1]/'server.py').read_text(encoding='utf-8'))
        functions = [n for n in source.body if isinstance(n, ast.FunctionDef) and n.name in ('fixed_response','generate_response')]
        namespace = {'LOCK':threading.Lock(), 'ProviderError':ProviderError, 'time':time,
                     'MODELS':SimpleNamespace(debug=__import__('model_debug').ModelDebug()),
                     'model_response':Mock(side_effect=ProviderError('离线模拟不可用')),
                     'ROOT':Path('.'), 'synthesize':Mock(return_value={'audio':'/offline.mp3'})}
        exec(compile(ast.Module(body=functions, type_ignores=[]), '<gift-pipeline-offline>', 'exec'), namespace)
        result = namespace['generate_response']('', 'gift', gift_context={'facts':{}}, gift_fallback='本档兜底感谢')
        self.assertEqual(result['reply'], '本档兜底感谢')
        self.assertTrue(result['gift_reply_fallback'])
        self.assertEqual(namespace['model_response'].call_count, 1)
        self.assertEqual(namespace['synthesize'].call_args.args[1], '本档兜底感谢')
        with self.assertRaises(ProviderError):
            namespace['generate_response']('普通聊天', 'chat')


if __name__ == '__main__':
    unittest.main()
