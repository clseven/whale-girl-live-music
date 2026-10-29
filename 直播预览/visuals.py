"""Validated visual directives and persistent wardrobe, shared by both model adapters."""
import copy
import json
import random
import re
import threading
import time
from pathlib import Path
from datetime import datetime, timezone, timedelta
from music import atomic_json

from rules_config import RuleConfig, EMOTIONS, EFFECTS, GESTURES, PROPS, OUTFITS, SCENES

SCENE_PROPS = {'phone': 'none', 'selfie': 'none', 'blow_bubbles': 'none',
               'parfait': 'parfait', 'whale': 'whale', 'whale_table': 'whale_table',
               'dark_table': 'dark_table', 'omelet': 'omelet', 'cat': 'none', 'victory': 'none', 'claw': 'none'}
PROP_SCENES = {value: key for key, value in SCENE_PROPS.items() if value != 'none'}
PROP_SCENES['none'] = 'phone'
SCENE_GESTURES = {'cat', 'victory', 'claw'}


def clean_directive(data):
    if not isinstance(data, dict):
        return {}
    result = {}
    for key, allowed in [('emotion', EMOTIONS), ('effect', EFFECTS), ('gesture', GESTURES), ('prop', PROPS), ('scene', SCENES)]:
        if isinstance(data.get(key), str) and data[key] in allowed:
            result[key] = data[key]
    outfit = data.get('outfit')
    if isinstance(outfit, dict):
        result['outfit'] = {key: value for key, value in outfit.items()
                            if key in OUTFITS and isinstance(value, str) and value in OUTFITS[key]}
        if not result['outfit']:
            result.pop('outfit')
    return result


def split_visual_reply(raw):
    """Always remove internal metadata from speech, including a truncated tag."""
    raw = str(raw)
    directive = {}
    match = re.search(r'<visual>\s*(.*?)\s*</visual>', raw, re.S | re.I)
    if match and len(match.group(1)) <= 2048:
        try:
            directive = clean_directive(json.loads(match.group(1)))
        except (ValueError, TypeError):
            pass
    text = re.sub(r'<visual>.*?</visual>', '', raw, flags=re.S | re.I)
    text = re.sub(r'<visual\b.*$', '', text, flags=re.S | re.I).strip()
    return text[:400], directive


class VisualDirector:
    def __init__(self, root, clock=time.time, chooser=random.choice, rules=None):
        self.path = Path(root) / '.runtime/appearance-state.json'
        self.rulebook = rules or RuleConfig(root)
        self.clock, self.choose = clock, chooser
        self.lock = threading.RLock()
        self.state = {'outfit': {key: 'none' for key in OUTFITS}, 'prop': 'none',
                      'temporary': {}, 'last_gift_fun_at': None, 'reaction': {}, 'receipts': {}}
        if self.path.exists():
            saved = json.loads(self.path.read_text(encoding='utf-8'))
            self.state.update(saved)
        # Previous versions persisted meal props forever. Only wardrobe stays permanent.
        if not self.state.get('scene_version'):
            self.state.update(scene_version=1, prop='none', interaction_scene={})
            self.state['temporary'].pop('prop', None)
        self.state.setdefault('ambient_scene', {'name': 'phone', 'started': self.clock()})
        self.state.setdefault('interaction_scene', {})
        self.state.setdefault('time_mode', None)

    def _theme(self, config, now):
        mode = self.state.get('time_mode') or config['stage']['mode']
        hour = datetime.fromtimestamp(now, timezone(timedelta(hours=8))).hour
        effective = ('day' if config['stage']['day_start_hour'] <= hour < config['stage']['night_start_hour'] else 'night') if mode == 'auto' else mode
        return {'mode': mode, 'effective': effective, 'table': 'light' if effective == 'day' else 'dark',
                'label': ('早间营业中' if mode == 'auto' and hour < 12 else '白天营业中') if effective == 'day' else '夜间营业中'}

    def set_theme(self, mode):
        if mode not in ('auto', 'day', 'night'):
            raise ValueError('营业场景只能选 auto/day/night')
        with self.lock:
            self.state['time_mode'] = None if mode == 'auto' else mode
            atomic_json(self.path, self.state)
            return self.status()

    @staticmethod
    def scene_duration(name, policy):
        return policy['meal_seconds'] if name == 'omelet' else policy['gesture_seconds'] if name in SCENE_GESTURES else policy['interval_seconds']

    def _next_ambient(self, policy, now, current):
        pool = list(dict.fromkeys(policy['pool']))
        avoid = {current, self.state.pop('avoid_next_scene', None)}
        remaining = self.state.get('ambient_remaining', []) if self.state.get('ambient_pool') == pool else []
        remaining = [name for name in remaining if name in pool and name not in avoid]
        if not remaining:
            remaining = [name for name in pool if name not in avoid] or [name for name in pool if name != current] or pool[:]
        name = self.choose(remaining)
        remaining.remove(name)
        self.state.update(ambient_pool=pool, ambient_remaining=remaining)
        return {'name': name, 'started': now}

    def _scene(self, config, now):
        policy = config['scenes']
        interaction = self.state['interaction_scene']
        temporary_prop = self.state['temporary'].get('prop', {})
        if interaction.get('until', 0) > now:
            return interaction, 'chat'
        changed = False
        if interaction:
            self.state['interaction_scene'] = {}
            self.state['ambient_scene']['started'] = 0
            self.state['avoid_next_scene'] = interaction['name']
            changed = True
        if temporary_prop and temporary_prop.get('until', 0) <= now:
            self.state['temporary'].pop('prop', None)
            self.state.setdefault('avoid_next_scene', PROP_SCENES[temporary_prop['value']])
            self.state['ambient_scene']['started'] = 0
            changed = True
        ambient = self.state['ambient_scene']
        gesture_active = self.state['reaction'].get('until', 0) > now and self.state['reaction'].get('gesture', 'none') != 'none'
        if temporary_prop.get('until', 0) <= now and not gesture_active:
            if policy['enabled'] and (ambient['name'] not in policy['pool'] or now-ambient['started'] >= self.scene_duration(ambient['name'], policy)):
                ambient = self._next_ambient(policy, now, ambient['name'])
                self.state['ambient_scene'] = ambient
                changed = True
            elif not policy['enabled'] and ambient['name'] != 'phone':
                ambient = {'name': 'phone', 'started': now}
                self.state['ambient_scene'] = ambient
                changed = True
        if changed:
            atomic_json(self.path, self.state)
        return ambient, 'idle'

    def status(self):
        with self.lock:
            config = self.rulebook.snapshot()
            now = self.clock()
            scene, source = self._scene(config, now)
            outfit, prop = dict(self.state['outfit']), SCENE_PROPS[scene['name']]
            scene_name = scene['name']
            scene_id = source+':'+scene['name']+':'+str(scene.get('started', 0))
            activity = {'phone': 'phone', 'selfie': 'selfie', 'blow_bubbles': 'blow_bubbles', 'omelet': 'eat'}.get(scene['name'], 'rest')
            for slot, item in self.state['temporary'].items():
                if item['until'] > now:
                    if slot == 'prop':
                        prop = item['value']
                        activity = 'eat' if prop == 'omelet' else 'rest'
                        scene_id = 'gift:'+prop+':'+str(item['until'])
                        scene_name, source = PROP_SCENES[prop], 'gift'
                    elif slot in OUTFITS:
                        outfit[slot] = item['value']
            reaction = copy.deepcopy(self.state['reaction'])
            if reaction.get('until', 0) <= now:
                reaction = {'emotion': 'neutral', 'effect': 'none', 'gesture': 'none', 'until': 0}
            elif reaction.get('effect_until', reaction['until']) <= now:
                reaction['effect'] = 'none'
            if scene_name in SCENE_GESTURES and source in ('idle', 'chat') and reaction.get('gesture', 'none') == 'none' and (source == 'chat' or reaction['until'] <= now):
                reaction.update(gesture=scene_name, until=max(reaction['until'], scene.get('until', scene['started']+config['scenes']['gesture_seconds'])))
            if reaction.get('gesture', 'none') != 'none':
                activity = 'rest'
            return copy.deepcopy({'outfit': outfit, 'prop': prop, 'activity': activity,
                                  'scene': {'id': scene_id, 'name': scene_name, 'source': source}, 'reaction': reaction,
                                  'gift_fun_cooldown_seconds': config['visuals']['gift_fun_cooldown_seconds'],
                                  'renderer': config['renderer'], 'income': config['income'],
                                  'time_theme': self._theme(config, now),
                                  'allowed_emotions': config['visuals']['allowed_chat_emotions'],
                                  'chat_controls': {'allow_outfit': config['visuals']['allow_chat_outfit'],
                                                    'allow_props': config['visuals']['allow_chat_props']},
                                  'rules_error': self.rulebook.error, 'now': now})

    def idle(self):
        with self.lock:
            self.state['reaction'] = {}
            self.state['temporary'] = {}
            self.state['interaction_scene'] = {}
            self.state['ambient_scene'] = {'name': 'phone', 'started': self.clock()}
            atomic_json(self.path, self.state)
            return self.status()

    def respond(self, event, directive=None):
        with self.lock:
            key = str(event.get('id') or '')
            if key and key in self.state['receipts']:
                return self.status()
            config = self.rulebook.snapshot()
            visual = config['visuals']
            now = self.clock()
            request = clean_directive(directive)
            if request.get('emotion') not in visual['allowed_chat_emotions']:
                request.pop('emotion', None)
            reaction = {key: request.get(key, default) for key, default in
                        [('emotion', 'neutral'), ('effect', 'none'), ('gesture', 'none')]}
            reaction['until'] = now + visual['surprise_reaction_seconds' if reaction['emotion'] == 'stunned' else 'chat_reaction_seconds']
            if event.get('type') == 'gift':
                gift = event.get('data', {}).get('gift', event.get('data', {}).get('giftName', ''))
                rule = config['gift_rules'].get(gift, {})
                reaction.update(self.choose(visual['gift_reaction_pool']))
                reaction.update(rule.get('reaction', {}))
                reaction['until'] = now + rule.get('duration_seconds', visual['gift_reaction_seconds'])
                previous = self.state['last_gift_fun_at']
                # Explicit gift rewards must not be swallowed by random-dress cooldown.
                if visual['gift_fun_enabled'] and (rule.get('temporary') or previous is None or now-previous >= visual['gift_fun_cooldown_seconds']):
                    options = [item for item in visual['random_decorations'] if item['slot'] not in rule.get('exclude_random_slots', [])]
                    selected = rule.get('temporary') or (self.choose(options) if options else None)
                    if selected:
                        slot, value = selected['slot'], selected['value']
                        duration = selected.get('duration_seconds', visual['gift_fun_duration_seconds'])
                        if slot == 'prop' and value == 'omelet':
                            duration = min(duration, config['scenes']['meal_seconds'])
                        self.state['temporary'][slot] = {'value': value, 'until': now+duration}
                        self.state['last_gift_fun_at'] = now
            else:
                for slot, value in (request.get('outfit', {}) if visual['allow_chat_outfit'] else {}).items():
                    self.state['outfit'][slot] = value
                    self.state['temporary'].pop(slot, None)
                if ('prop' in request or 'scene' in request) and visual['allow_chat_props']:
                    name = request.get('scene') or PROP_SCENES[request['prop']]
                    duration = config['scenes']['meal_seconds'] if name == 'omelet' else config['scenes']['gesture_seconds'] if name in SCENE_GESTURES else config['scenes']['interaction_seconds']
                    self.state['interaction_scene'] = {'name': name, 'started': now, 'until': now+duration}
                    self.state['temporary'].pop('prop', None)
            reaction['effect_until'] = min(reaction['until'], now+visual['effect_seconds'])
            self.state['reaction'] = reaction
            value = self.status()
            if key:
                self.state['receipts'][key] = True
                self.state['receipts'] = dict(list(self.state['receipts'].items())[-200:])
            atomic_json(self.path, self.state)
            return value
