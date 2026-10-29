"""Internal tags must never reach subtitles, history or TTS, for any event type."""
import html
import re


def normalize_protocol(text):
    value = html.unescape(str(text))
    return re.sub(r'\\(?=[<>/])', '', value)


def strip_watch_tags(text):
    value = normalize_protocol(text)
    value = re.sub(r'<watch\b[^>]*>.*?(?:</watch\s*>|$)', '', value, flags=re.S|re.I)
    value = re.sub(r'</watch\s*>', '', value, flags=re.I)
    value = re.sub(r'<watch\b.*$', '', value, flags=re.S|re.I)
    value = re.sub(r'```(?:json)?\s*```', '', value, flags=re.I)
    return value.strip()


def filter_watch_speech(text):
    """Drop implementation reports in autonomous video speech, never rewrite facts.

    Viewer-initiated questions about the implementation are handled as chat and
    remain answerable. Removing every sentence yields silence at the provider.
    """
    technical = re.compile(
        r'稀疏.{0,6}(?:截图|采样)|截图采样|这一帧|刚才那一帧|单帧截图|'
        r'(?:Agnes|agnes).{0,8}(?:识图|识别)|'
        r'(?:画面识别|识图).{0,10}(?:失败|没成功|不成功|未成功|没有成功)|'
        r'(?:我|本鲸).{0,12}(?:只能|只有|拿到|收到|看到|看见).{0,8}(?:截图|单帧|一帧)|'
        r'(?:没有|未接入).{0,4}(?:音轨|转录)|(?:我|本鲸).{0,5}没听到对白')
    sentences = re.findall(r'[^。！？!?；;\n]+[。！？!?；;\n]*', str(text))
    return ''.join(sentence for sentence in sentences if not technical.search(sentence)).strip()
