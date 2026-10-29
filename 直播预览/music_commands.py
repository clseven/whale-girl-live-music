"""A narrow explicit-command shortcut into the same viewer-scoped music tools."""
import re
from music import parse_request
from music_tools import MusicTools


def is_song_command(text):
    # “点歌好麻烦” is conversation; only a separate command word is routed here.
    return bool(re.match(r'^点歌(?:\s|[：:]|$)', text.strip()))


class SongCommands:
    def __init__(self, music, tools=None):
        self.tools = tools or MusicTools(music)

    def execute(self, event):
        text = re.sub(r'^点歌\s*[：:]?\s*', '', event['data']['content'].strip())
        try:
            request = parse_request(text)
        except ValueError as exc:
            return {'status': 'error', 'message': str(exc), 'queued': False}
        args = {key: request.get(key, '') for key in ('title', 'artist', 'song_id')}
        session = self.tools.bind(event['user'], event.get('roomId', 'local'), event['id'])
        return session.execute('request_song', args, 'explicit-command')
