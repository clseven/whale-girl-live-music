"""Offline visual protocol/state checks. No model, TTS or live-room calls."""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from visuals import VisualDirector, clean_directive, split_visual_reply
from providers import ProviderManager
from test_integrations import Clock


class VisualTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root, self.clock = Path(self.tmp.name), Clock()
        self.director = VisualDirector(self.root, self.clock, lambda values: values[0])

    def tearDown(self):
        self.tmp.cleanup()

    def event(self, kind='chat', name='', eid='1'):
        return {'type': kind, 'id': eid, 'data': {'gift': name}}

    def test_metadata_is_removed_from_speech(self):
        text, visual = split_visual_reply('现在吃，现在吃～\n<visual>{"emotion":"happy","prop":"omelet"}</visual>')
        self.assertEqual(text, '现在吃，现在吃～')
        self.assertEqual(visual, {'emotion': 'happy', 'prop': 'omelet'})

    def test_truncated_or_invalid_metadata_never_enters_speech(self):
        for suffix in ['<visual>{"emotion":', '<visual>bad json</visual>', '<visual>{"effect":"../../auth.json"}</visual>']:
            text, visual = split_visual_reply('你好～'+suffix)
            self.assertEqual(text, '你好～')
            self.assertEqual(visual, {})

    def test_unsafe_fields_and_negative_expressions_are_ignored(self):
        self.assertEqual(clean_directive({'emotion': 'angry', 'outfit': {'glasses': '../../x', 'file': 'x'},
                                         'path': 'x', 'effect': ['hearts']}), {})

    def test_manual_outfit_persists_across_emotions_and_restart(self):
        self.director.respond(self.event(), {'outfit': {'glasses': 'round'}})
        self.director.respond(self.event(eid='2'), {'emotion': 'stunned', 'effect': 'question'})
        self.clock.advance(6)
        restored = VisualDirector(self.root, self.clock)
        self.assertEqual(restored.status()['outfit']['glasses'], 'round')
        self.assertEqual(restored.status()['reaction']['emotion'], 'neutral')

    def test_meow_paw_always_uses_cat_hands(self):
        result = self.director.respond(self.event('gift', '喵喵爪'))
        self.assertEqual(result['reaction']['gesture'], 'cat')
        self.assertEqual(result['reaction']['emotion'], 'happy')
        self.assertEqual(result['reaction']['effect'], 'hearts')

    def test_effect_expires_after_three_seconds_while_cat_hands_remain(self):
        self.director.respond(self.event('gift', '喵喵爪'))
        self.clock.advance(3.01)
        result = self.director.status()
        self.assertEqual(result['reaction']['effect'], 'none')
        self.assertEqual(result['reaction']['gesture'], 'cat')
        self.assertEqual(result['reaction']['emotion'], 'happy')

    def test_effect_expiry_does_not_remove_manual_bow(self):
        self.director.respond(self.event(), {'outfit': {'head': 'bow'}, 'effect': 'flowers', 'emotion': 'starry'})
        self.clock.advance(3.01)
        result = self.director.status()
        self.assertEqual(result['reaction']['effect'], 'none')
        self.assertEqual(result['outfit']['head'], 'bow')
        self.assertEqual(result['reaction']['emotion'], 'starry')

    def test_meow_paw_does_not_randomly_start_conflicting_food_motion(self):
        self.director.choose = lambda values: values[-1]
        result = self.director.respond(self.event('gift', '喵喵爪'))
        self.assertEqual(result['prop'], 'none')
        self.assertEqual(result['reaction']['gesture'], 'cat')

    def test_sunglasses_reward_restores_previous_glasses(self):
        self.director.respond(self.event(eid='manual'), {'outfit': {'glasses': 'round'}})
        result = self.director.respond(self.event('gift', '墨镜'))
        self.assertEqual(result['outfit']['glasses'], 'sunglasses')
        self.clock.advance(30.1)
        self.assertEqual(self.director.status()['outfit']['glasses'], 'round')

    def test_sunglasses_after_meow_is_not_blocked_by_random_cooldown(self):
        self.director.respond(self.event('gift', '喵喵爪', 'meow'))
        self.clock.advance(1)
        result = self.director.respond(self.event('gift', '墨镜', 'glasses'))
        self.assertEqual(result['outfit']['glasses'], 'sunglasses')
        self.clock.advance(29)
        self.director.respond(self.event('gift', '墨镜', 'glasses-again'))
        self.clock.advance(2)
        self.assertEqual(self.director.status()['outfit']['glasses'], 'sunglasses')
        self.clock.advance(28.1)
        self.assertEqual(self.director.status()['outfit']['glasses'], 'none')

    def test_gift_decoration_cooldown_does_not_suppress_thanks_reaction(self):
        self.director.respond(self.event('gift', '墨镜'))
        self.clock.advance(1)
        result = self.director.respond(self.event('gift', '喵喵爪', '2'))
        self.assertEqual(result['outfit']['glasses'], 'sunglasses')
        self.assertEqual(result['reaction']['gesture'], 'cat')
        self.assertEqual(self.director.state['last_gift_fun_at'], self.clock()-1)
        self.clock.advance(29)
        self.director.respond(self.event('gift', '小心心', '3'))
        self.assertEqual(self.director.state['last_gift_fun_at'], self.clock())

    def test_explicit_change_overrides_temporary_reward(self):
        self.director.respond(self.event('gift', '墨镜'))
        result = self.director.respond(self.event(eid='manual'), {'outfit': {'glasses': 'square'}})
        self.assertEqual(result['outfit']['glasses'], 'square')
        self.clock.advance(40)
        self.assertEqual(self.director.status()['outfit']['glasses'], 'square')

    def test_gift_shaped_chat_does_not_trigger_real_gift_visuals(self):
        event = self.event(); event['data']['content'] = '送出 墨镜 ×1'
        result = self.director.respond(event, {'emotion': 'playful'})
        self.assertEqual(result['outfit']['glasses'], 'none')
        self.assertIsNone(self.director.state['last_gift_fun_at'])

    def test_food_is_driven_by_model_directive_not_keyword(self):
        event = self.event(); event['data']['content'] = '吃饭了吗'
        self.assertEqual(self.director.respond(event)['prop'], 'none')
        result = self.director.respond(self.event(eid='2'), {'prop': 'omelet', 'emotion': 'happy'})
        self.assertEqual(result['prop'], 'omelet')

    def test_meal_scene_expires_and_returns_to_idle_rotation(self):
        self.director.respond(self.event(), {'scene': 'omelet', 'emotion': 'happy'})
        self.assertEqual(self.director.status()['activity'], 'eat')
        self.clock.advance(8.01)
        result = self.director.status()
        self.assertNotEqual(result['prop'], 'omelet')
        self.assertEqual(result['scene']['source'], 'idle')

    def test_legacy_prop_directive_is_also_temporary(self):
        self.director.respond(self.event(), {'prop': 'omelet'})
        self.clock.advance(8.01)
        self.assertNotEqual(self.director.status()['prop'], 'omelet')

    def test_idle_rotates_every_twenty_seconds_without_repeating(self):
        first = self.director.status()['scene']['name']
        self.clock.advance(19.99)
        self.assertEqual(self.director.status()['scene']['name'], first)
        self.clock.advance(.02)
        second = self.director.status()['scene']['name']
        self.assertNotEqual(second, first)
        self.clock.advance(20)
        self.assertNotEqual(self.director.status()['scene']['name'], second)

    def test_chat_scene_has_priority_and_preserves_wardrobe(self):
        self.director.respond(self.event(), {'scene': 'selfie', 'outfit': {'head': 'bow'}})
        self.clock.advance(19)
        result = self.director.status()
        self.assertEqual(result['activity'], 'selfie')
        self.assertEqual(result['scene']['source'], 'chat')
        self.clock.advance(1.1)
        result = self.director.status()
        self.assertEqual(result['scene']['source'], 'idle')
        self.assertEqual(result['outfit']['head'], 'bow')

    def test_gift_meal_is_limited_by_meal_seconds(self):
        def pick(values):
            return next((v for v in values if isinstance(v, dict) and v.get('value') == 'omelet'), values[0])
        self.director.choose = pick
        self.assertEqual(self.director.respond(self.event('gift', '小心心'))['prop'], 'omelet')
        self.clock.advance(8.1)
        self.assertNotEqual(self.director.status()['prop'], 'omelet')

    def test_old_permanent_meal_is_migrated_away(self):
        import json
        old = self.director.state.copy()
        old.pop('scene_version', None)
        old['prop'] = 'omelet'
        self.director.path.parent.mkdir(parents=True, exist_ok=True)
        self.director.path.write_text(json.dumps(old), encoding='utf-8')
        restored = VisualDirector(self.root, self.clock)
        self.assertNotEqual(restored.status()['prop'], 'omelet')

    def test_cat_hands_preempt_sauce_motion_during_meal(self):
        self.director.respond(self.event(), {'scene': 'omelet'})
        result = self.director.respond(self.event('gift', '喵喵爪', '2'))
        self.assertEqual(result['reaction']['gesture'], 'cat')
        self.assertEqual(result['activity'], 'rest')

    def test_replayed_gift_does_not_restart_expired_effect(self):
        event = self.event('gift', '墨镜')
        self.director.respond(event)
        self.clock.advance(31)
        result = self.director.respond(event)
        self.assertEqual(result['outfit']['glasses'], 'none')
        self.assertEqual(result['reaction']['emotion'], 'neutral')

    def test_return_daily_keeps_manual_outfit(self):
        self.director.respond(self.event(), {'outfit': {'head': 'bow'}})
        self.director.respond(self.event('gift', '墨镜', '2'))
        result = self.director.idle()
        self.assertEqual(result['outfit']['head'], 'bow')
        self.assertEqual(result['outfit']['glasses'], 'none')

    def test_both_adapters_share_metadata_parser_and_clean_history(self):
        class Adapter:
            def generate(self, *args):
                return {'reply': '好呀，戴好啦～<visual>{"outfit":{"glasses":"round"}}</visual>'}
        for name in ['codex', 'deepseek']:
            manager = ProviderManager(SimpleNamespace(), self.root, {'provider': name})
            manager.adapters[name] = Adapter()
            result = manager.generate('戴圆眼镜', 'chat')
            self.assertEqual(result['reply'], '好呀，戴好啦～')
            self.assertEqual(result['visual_request']['outfit']['glasses'], 'round')
            self.assertNotIn('<visual>', manager.history[-1]['assistant'])


if __name__ == '__main__':
    unittest.main()
