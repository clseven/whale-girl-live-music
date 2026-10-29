import asyncio
import json
from pathlib import Path
import edge_tts

ROOT = Path(__file__).parent
TEXT = '欢迎来到本鲸的直播间！谢谢你送的小心心，今天也要开心哦。想听什么歌，发弹幕告诉我吧。'

async def main():
    out = ROOT / 'audio'
    out.mkdir(exist_ok=True)
    available = {v['ShortName'] for v in await edge_tts.list_voices()}
    candidates = [('zh-CN-XiaoxiaoNeural', '晓晓 · 原速'), ('zh-CN-XiaoyiNeural', '晓伊 · 原速'), ('zh-CN-XiaoxiaoNeural', '晓晓 · 轻微升调')]
    results = []
    for i, (voice, label) in enumerate(candidates):
        if voice not in available:
            print(f'Unavailable: {voice}', flush=True)
            continue
        filename = f'voice-{i+1}.mp3'
        try:
            await edge_tts.Communicate(TEXT, voice, pitch='+12Hz' if i == 2 else '+0Hz', rate='+5%' if i == 2 else '+0%').save(str(out / filename))
            results.append({'label': label, 'voice': voice, 'src': 'audio/' + filename})
            print(f'Created {filename}: {label}', flush=True)
        except Exception as exc:
            print(f'Failed {voice}: {exc}', flush=True)
    (out / 'samples.json').write_text(json.dumps(results, ensure_ascii=False), encoding='utf-8')
    if not results:
        raise RuntimeError('No audio samples generated')

asyncio.run(main())
