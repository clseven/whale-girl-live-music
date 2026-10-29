"""Rule-book integration checks with temporary files; never call a model."""
import copy
import tempfile
import unittest
from pathlib import Path
import yaml
from unittest.mock import patch
from types import SimpleNamespace
from rules_config import RuleConfig, DEFAULT_RULES
from visuals import VisualDirector
from studio import EventRules
from persona import LIVE_PERSONA, default_persona_settings, build_live_persona
from providers import ProviderManager
from test_integrations import Clock, gift


class RulesConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.path = self.root/'直播规则.yaml'
        self.clock = Clock()
        self.config = copy.deepcopy(DEFAULT_RULES)
        self.write()
        self.book = RuleConfig(self.root)
        self.visual = VisualDirector(self.root, self.clock, lambda values: values[0], rules=self.book)

    def tearDown(self):
        self.tmp.cleanup()

    def write(self):
        self.path.write_text('# 保留中文注释\n'+yaml.safe_dump(self.config, allow_unicode=True, sort_keys=False), encoding='utf-8')

    def event(self, name='小心心', eid='1'):
        return {'id': eid, 'type': 'gift', 'data': {'gift': name}}

    def test_cooldown_and_temporary_duration_reload_without_restart(self):
        self.visual.respond(self.event('墨镜'))
        self.config['visuals']['gift_fun_cooldown_seconds'] = 2
        self.config['visuals']['gift_fun_duration_seconds'] = 4
        self.write(); self.clock.advance(2)
        self.visual.respond(self.event(eid='2'))
        self.assertEqual(self.visual.state['last_gift_fun_at'], self.clock())
        self.assertEqual(self.visual.state['temporary']['glasses']['until'], self.clock()+4)

    def test_special_gift_can_be_added_and_removed_entirely_from_yaml(self):
        self.config['gift_rules'] = {'特别眼镜': {'temporary': {'slot': 'glasses', 'value': 'oval', 'duration_seconds': 7}}}
        self.config['visuals']['random_decorations'] = []
        self.write()
        result = self.visual.respond(self.event('特别眼镜'))
        self.assertEqual(result['outfit']['glasses'], 'oval')
        self.clock.advance(8)
        result = self.visual.respond(self.event('喵喵爪', '2'))
        self.assertEqual(result['outfit']['glasses'], 'none')
        self.assertEqual(result['reaction']['gesture'], 'none')

    def test_custom_thanks_reaction_and_duration_share_one_config(self):
        self.config['gift_rules'] = {'新礼物': {'thank_template': '谢谢{nickname}的{count}个{gift}！', 'reaction': {'gesture': 'victory'}, 'duration_seconds': 17}}
        self.config['visuals']['gift_reaction_pool'] = [{'emotion': 'starry', 'effect': 'flowers'}]
        self.write()
        studio = EventRules(self.root, self.clock, rulebook=self.book)
        studio.ingest(gift(end=True, name='新礼物'))
        event = studio.state['jobs'][0]['event']
        self.assertEqual(event['data']['reply'], '谢谢小水的1个新礼物！')
        result = self.visual.respond(event)
        self.assertEqual(result['reaction']['gesture'], 'victory')
        self.assertEqual(result['reaction']['emotion'], 'starry')
        self.assertEqual(result['reaction']['until'], self.clock()+17)

    def test_invalid_yaml_keeps_whole_last_good_snapshot_then_recovers(self):
        before = self.book.snapshot()
        self.path.write_text('visuals: [', encoding='utf-8')
        self.assertEqual(self.book.snapshot(), before)
        self.assertIn('上一份有效配置', self.book.error)
        self.config['visuals']['chat_reaction_seconds'] = 4
        self.write()
        self.assertEqual(self.book.snapshot()['visuals']['chat_reaction_seconds'], 4)
        self.assertEqual(self.book.error, '')

    def test_invalid_boundaries_templates_and_presets_reject_whole_update(self):
        before = self.book.snapshot()
        variants = []
        for field, bad in [('gift_fun_cooldown_seconds', -1), ('gift_fun_enabled', 'false')]:
            value = copy.deepcopy(self.config); value['visuals'][field] = bad; variants.append(value)
        value = copy.deepcopy(self.config); value['gift_rules']['墨镜']['temporary']['value'] = 'not-a-preset'; variants.append(value)
        value = copy.deepcopy(self.config); value['gift_rules']['喵喵爪']['thank_template'] = '{nickname.secret}'; variants.append(value)
        for value in variants:
            self.config = value; self.write()
            self.assertEqual(self.book.snapshot(), before)
            self.assertTrue(self.book.error)

    def test_duplicate_keys_and_typo_are_reported(self):
        for content in ['visuals:\n  gift_fun_cooldown_seconds: 1\n  gift_fun_cooldown_seconds: 2\n', 'visuals:\n  gift_fun_cooldown_second: 3\n']:
            self.path.write_text(content, encoding='utf-8')
            self.book.refresh()
            self.assertTrue(self.book.error)

    def test_comments_are_never_rewritten_by_runtime(self):
        original = self.path.read_bytes()
        self.visual.respond(self.event('墨镜'))
        self.visual.idle(); self.visual.status()
        self.assertEqual(self.path.read_bytes(), original)

    def test_same_size_edit_with_unchanged_filesystem_timestamp_reloads(self):
        import os
        initial=self.path.stat()
        self.config['visuals']['chat_reaction_seconds']=4
        self.write()
        self.assertEqual(self.path.stat().st_size,initial.st_size)
        os.utime(self.path,ns=(initial.st_atime_ns,initial.st_mtime_ns))
        self.assertEqual(self.book.snapshot()['visuals']['chat_reaction_seconds'],4)

    def test_deleted_yaml_keeps_good_rules_and_never_uses_legacy_json(self):
        self.config['visuals']['gift_fun_cooldown_seconds'] = 8; self.write()
        self.book.refresh(); self.path.unlink()
        self.assertEqual(self.book.snapshot()['visuals']['gift_fun_cooldown_seconds'], 8)
        self.assertTrue(self.book.error)

    def test_renderer_income_and_chat_permissions_reload(self):
        self.config['renderer']['omelet_motion_seconds'] = 2
        self.config['income']['yuan_per_diamond'] = .04
        self.config['visuals']['allow_chat_outfit'] = False
        self.config['visuals']['allow_chat_props'] = False
        self.config['visuals']['allowed_chat_emotions'] = ['neutral']
        self.write()
        result = self.visual.respond({'type': 'chat', 'id': 'chat'}, {'outfit': {'glasses': 'round'}, 'prop': 'omelet', 'emotion': 'happy'})
        self.assertEqual(result['outfit']['glasses'], 'none')
        self.assertEqual(result['prop'], 'none')
        self.assertEqual(result['reaction']['emotion'], 'neutral')
        self.assertEqual(result['renderer']['omelet_motion_seconds'], 2)
        self.assertEqual(result['income']['yuan_per_diamond'], .04)

    def test_studio_yaml_tier_update_applies_to_next_gift(self):
        studio = EventRules(self.root, self.clock, rulebook=self.book)
        self.config['studio']['gift_tiers'][1]['min_diamonds'] = 2
        self.write()
        studio.ingest(gift(end=True, price=10))
        self.assertEqual(studio.state['jobs'][0]['event']['data']['tier'], '用心')
        self.assertEqual(studio.config_path.name, '直播规则.yaml')

    def test_idle_interval_and_scene_pool_reload(self):
        self.config['scenes']['interval_seconds'] = 5
        self.config['scenes']['pool'] = ['phone', 'dark_table']
        self.write()
        self.clock.advance(5.1)
        self.assertEqual(self.visual.status()['scene']['name'], 'dark_table')

    def test_invalid_idle_pool_and_zero_interval_are_rejected(self):
        original = self.book.snapshot()
        self.config['scenes']['pool'] = ['not-a-scene']; self.write()
        self.assertEqual(self.book.snapshot(), original)
        self.assertTrue(self.book.error)
        self.config = copy.deepcopy(original); self.config['scenes']['interval_seconds'] = 0; self.write()
        self.assertEqual(self.book.snapshot(), original)

    def test_default_persona_is_migrated_verbatim(self):
        self.assertEqual(build_live_persona(default_persona_settings()), LIVE_PERSONA)

    def test_codex_persona_hot_reload_resets_thread_once_and_preserves_history(self):
        class FakeResident:
            def __init__(self): self.resets=[]; self.inputs=[]
            def reset(self): self.resets.append(self.persona_provider())
            def chat(self, text, event, tool_session=None):
                self.inputs.append(text)
                return {'reply':'你好呀～'}
        resident = FakeResident()
        manager = ProviderManager(resident, self.root, {'provider':'codex'}, rules=self.book)
        manager.generate('你好','chat')
        self.assertFalse(resident.resets)
        self.config['persona']['character'] = '你是大肥鱼，温柔地说话。'
        self.write()
        manager.generate('再打个招呼','chat')
        manager.generate('继续聊','chat')
        self.assertEqual(len(resident.resets), 1)
        self.assertTrue(resident.resets[0].startswith('你是大肥鱼，温柔地说话。'))
        self.assertIn('recent_conversation', resident.inputs[1])
        self.assertEqual(len(manager.history), 3)

    def test_deepseek_reads_same_updated_persona_without_extra_request(self):
        manager = ProviderManager(SimpleNamespace(), self.root, {'provider':'deepseek'}, rules=self.book)
        provider = manager.adapters['deepseek']
        self.config['persona']['character'] = '你是大肥鱼，说话简短亲切。'
        self.write()
        with patch.object(provider,'key',return_value='offline-test-key'), patch.object(provider,'_stream',return_value=({'role':'assistant','content':'你好呀～'},1,{})) as stream:
            provider.generate([], '你好','chat')
        self.assertEqual(stream.call_count,1)
        actual = stream.call_args.args[0]['messages'][0]['content']
        self.assertEqual(actual,build_live_persona(self.book.snapshot()['persona']))

    def test_invalid_persona_keeps_last_good_prompt(self):
        original = build_live_persona(self.book.snapshot()['persona'])
        self.config['persona']['character'] = ''
        self.write()
        self.assertEqual(build_live_persona(self.book.snapshot()['persona']), original)
        self.assertIn('人设不能为空', self.book.error)
        self.assertTrue(self.book.error)


if __name__ == '__main__':
    unittest.main()
