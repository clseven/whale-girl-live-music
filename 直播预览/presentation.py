"""Shared, durable display state. Reading it never claims or plays audio."""
import copy
import uuid


class Presentation:
    LIMIT = 100

    def __init__(self, state, clock, archive_limit=lambda: 1000):
        self.state, self.clock = state, clock
        self.archive_limit = archive_limit
        for key, default in {'epoch': uuid.uuid4().hex, 'revision': 0,
                             'incoming': [], 'conversation': [], 'current': None,
                             'shown': [], 'archive': []}.items():
            self.state.setdefault(key, default)

    def changed(self):
        self.state['revision'] += 1

    def incoming(self, event, *, gift_key=None, gift_name='', count=1):
        name = event['user']['nickname']
        content = ('送出 '+gift_name+' ×'+str(count)) if event['type'] == 'gift' else event['data']['content']
        row_id = gift_key or str(event.get('roomId', 'local'))+':'+event['id']
        row = {'id': row_id, 'name': name, 'content': content, 'type': event['type'],
               'room_id': str(event.get('roomId', 'local')), 'user_id': str(event['user']['id']),
               'time': self.clock(), 'simulation': bool(event.get('simulation'))}
        existing = next((r for r in self.state['incoming'] if r['id'] == row_id), None)
        if existing:
            existing.update(row)
        else:
            self.state['incoming'].append(row)
            del self.state['incoming'][:-self.LIMIT]
        self.changed()

    def present(self, job):
        if job['id'] in self.state['shown']:
            return
        event, result = job['event'], job['result']
        data, kind = event['data'], event['type']
        text = ('送出 '+str(data.get('gift', '礼物'))+' ×'+str(data.get('count', 1))) if kind == 'gift' else data.get('content', '')
        now = self.clock()
        identity = {'room_id': str(event.get('roomId', 'local')), 'user_id': str(event['user']['id'])}
        rows = [
            {'id': job['id']+':user', 'name': event['user']['nickname'], 'content': text,
             'role': 'user', 'type': kind, 'time': now, **identity},
            {'id': job['id']+':assistant', 'name': '大肥鱼', 'content': result['reply'],
             'role': 'assistant', 'type': kind, 'time': now, **identity}]
        self.state['conversation'].extend(rows)
        self.state['archive'].extend(copy.deepcopy(rows))
        del self.state['archive'][:-self.archive_limit()]
        del self.state['conversation'][:-self.LIMIT]
        self.state['shown'].append(job['id'])
        del self.state['shown'][:-200]
        self.state['current'] = {'id': job['id'], 'text': result['reply'], 'time': now}
        self.changed()

    def clear(self):
        self.state.update(incoming=[], conversation=[], current=None)
        self.changed()

    def snapshot(self):
        return copy.deepcopy({key: self.state[key] for key in
                              ('epoch', 'revision', 'incoming', 'conversation', 'current')})
