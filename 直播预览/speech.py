"""Shared Xiaoyi speech and an optional local meow-paw sound clip."""
import asyncio
import uuid
from pathlib import Path
import edge_tts

VOICE = 'zh-CN-XiaoyiNeural'


def synthesize(root, text, enabled=True, special=None):
    if not enabled:
        return {'audio': None, 'audio_parts': [], 'audio_error': None}
    root = Path(root)
    parts = []
    clip = next((root / 'assets/gift_audio' / ('meow_paw' + ext)
                 for ext in ('.mp3', '.wav', '.ogg')
                 if (root / 'assets/gift_audio' / ('meow_paw' + ext)).is_file()), None)
    spoken = text
    if special == 'meow_paw' and clip:
        spoken = text.removesuffix('，喵喵喵喵喵！') + '！'
    filename = uuid.uuid4().hex + '.mp3'
    target = root / 'audio' / filename
    async def run():
        await asyncio.wait_for(edge_tts.Communicate(spoken, VOICE).save(str(target)), timeout=25)
    try:
        asyncio.run(run())
        parts.append('/audio/' + filename)
        if special == 'meow_paw' and clip:
            parts.append('/assets/gift_audio/' + clip.name)
        return {'audio': parts[0], 'audio_parts': parts, 'audio_error': None}
    except Exception:
        target.unlink(missing_ok=True)
        return {'audio': None, 'audio_parts': [], 'audio_error': '语音生成失败，文字仍可用'}
