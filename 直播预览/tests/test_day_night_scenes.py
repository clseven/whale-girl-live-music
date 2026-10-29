"""Time themes and ambient variety without models, audio or real clock delays."""
import copy
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
import yaml
from rules_config import DEFAULT_RULES, RuleConfig
from visuals import VisualDirector
from test_integrations import Clock


class ThemeSceneTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.clock=Clock()
        self.config=copy.deepcopy(DEFAULT_RULES);self.write()
        self.rules=RuleConfig(self.root)
        self.visual=VisualDirector(self.root,self.clock,chooser=lambda values:values[0],rules=self.rules)
    def tearDown(self):self.tmp.cleanup()
    def write(self):(self.root/'直播规则.yaml').write_text(yaml.safe_dump(self.config,allow_unicode=True),encoding='utf-8')
    def hour(self,hour):self.clock.value=datetime(2026,10,28,hour,tzinfo=timezone(timedelta(hours=8))).timestamp()

    def test_auto_time_boundaries_and_manual_override_survives_restart(self):
        for hour,expected in ((5,'night'),(6,'day'),(11,'day'),(17,'day'),(18,'night'),(23,'night')):
            self.hour(hour);self.assertEqual(self.visual.status()['time_theme']['effective'],expected)
        self.hour(9);self.assertEqual(self.visual.status()['time_theme']['label'],'早间营业中')
        self.visual.set_theme('night')
        restored=VisualDirector(self.root,self.clock,rules=self.rules)
        self.assertEqual(restored.status()['time_theme']['table'],'dark')
        self.assertEqual(restored.set_theme('auto')['time_theme']['table'],'light')
        with self.assertRaises(ValueError):self.visual.set_theme('invalid')

    def test_theme_does_not_reset_outfit_or_restart_current_action(self):
        self.visual.respond({'type':'chat','id':'food'},{'scene':'omelet','outfit':{'glasses':'round'}})
        before=self.visual.status()
        after=self.visual.set_theme('night')
        self.assertEqual(before['scene'],after['scene'])
        self.assertEqual(after['outfit']['glasses'],'round')
        self.assertEqual(after['activity'],'eat')
        self.assertEqual(after['time_theme']['table'],'dark')

    def test_all_idle_scenes_get_a_turn_before_a_repeat(self):
        self.visual.idle();seen=[]
        for _ in self.config['scenes']['pool']:
            state=self.visual.status();seen.append(state['scene']['name'])
            self.clock.advance(self.visual.scene_duration(state['scene']['name'],self.config['scenes'])+.01)
        self.assertEqual(len(seen),len(set(seen)))
        self.assertEqual(set(seen),set(self.config['scenes']['pool']))

    def test_meal_expires_into_next_scene_and_non_phone_props_use_rest(self):
        self.config['scenes']['pool']=['phone','omelet','parfait'];self.write()
        self.visual.idle();self.clock.advance(20)
        self.assertEqual(self.visual.status()['activity'],'eat')
        self.clock.advance(self.config['scenes']['meal_seconds']+.01)
        state=self.visual.status()
        self.assertEqual(state['scene']['name'],'parfait')
        self.assertEqual(state['activity'],'rest')

    def test_idle_gesture_expires_into_another_action(self):
        self.config['scenes']['pool']=['cat','selfie'];self.write()
        first=self.visual.status()
        self.assertEqual(first['reaction']['gesture'],'cat')
        self.assertEqual(first['activity'],'rest')
        self.clock.advance(self.config['scenes']['gesture_seconds']+.01)
        self.assertEqual(self.visual.status()['scene']['name'],'selfie')

    def test_explicit_chat_gesture_and_real_gift_gesture_keep_priority(self):
        request=self.visual.respond({'type':'chat','id':'cat'},{'scene':'cat','emotion':'happy'})
        self.assertEqual(request['reaction']['gesture'],'cat')
        gift=self.visual.respond({'type':'gift','id':'paw','data':{'gift':'喵喵爪'}},{})
        self.assertEqual(gift['reaction']['gesture'],'cat')
        self.assertEqual(gift['activity'],'rest')


if __name__=='__main__':unittest.main()
