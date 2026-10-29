"""Compose music mutations with read-only live queries for either model adapter."""
import copy
import json
from music_tools import TOOL_SPECS as MUSIC_SPECS
READ_SPECS = [{'name': 'get_music_queue', 'description': '查询网易云当前歌曲和观众待播队列。观众问还有几首、前面是谁的歌、现在放什么时调用；待播数不含正在播放的一首，不根据旧对话猜测。只读，不播放或切歌。', 'parameters': {'type': 'object', 'properties': {}, 'additionalProperties': False}}, {'name': 'get_today_stats', 'description': '查询本直播间今日礼物收入折算、支出估算、token消耗和礼物数。观众问赚多少、花多少、用了多少token时调用。区分真实礼物和模拟测试；展示数字不是官方结算账单。只读，不能清空统计或修改额度。', 'parameters': {'type': 'object', 'properties': {}, 'additionalProperties': False}}, {'name': 'search_recent_chat', 'description': '查询当前直播间保留的已回应对话。观众问之前聊过什么、刚才说过某话题时调用。query填一个关键词，空字符串查询最近记录；only_me=true仅查当前观众。结果有限，不是全部历史；没找到不能假装记得。', 'parameters': {'type': 'object', 'properties': {'query': {'type': 'string', 'description': '关键词，空字符串表示最近对话', 'maxLength': 100}, 'limit': {'type': 'integer', 'minimum': 1, 'maximum': 20, 'description': '最多返回几条消息，默认6'}, 'only_me': {'type': 'boolean', 'description': '是否仅查当前观众与大肥鱼的对话，默认false'}}, 'required': ['query'], 'additionalProperties': False}}]
TOOL_SPECS = MUSIC_SPECS + READ_SPECS

class LiveTools:

    @property
    def specs(self):
        return TOOL_SPECS

    def __init__(self, music_tools, stats_reader, history_reader, rules):
        self.music_tools, self.stats_reader, self.history_reader, self.rules = (music_tools, stats_reader, history_reader, rules)

    def bind(self, user=None, room_id='local', event_id=None):
        user = user or {'id': 'local', 'nickname': '测试观众'}
        music = self.music_tools.bind(user, room_id, event_id)
        return LiveToolSession(self, music, str(room_id), str(user['id']))

class LiveToolSession:
    specs = TOOL_SPECS

    def __init__(self, host, music_session, room_id, viewer_id):
        self.host, self.music_session = (host, music_session)
        self.room_id, self.viewer_id = (room_id, viewer_id)
        self.records = music_session.records
        self.specs = host.specs
        self.images = []

    def context(self):
        return self.music_session.context()

    def execute(self, name, arguments, call_id=''):
        try:
            maximum = self.host.rules.snapshot()['live_tools']['max_calls_per_message']
            if len(self.records) >= maximum:
                raise ValueError('本条弹幕工具调用次数已达上限，请根据已知结果回复')
            if name not in {spec['name'] for spec in self.specs}:
                raise ValueError('当前模式没有这个工具')
            if name in {spec['name'] for spec in MUSIC_SPECS}:
                return self.music_session.execute(name, arguments, call_id)
            spec = next((spec for spec in READ_SPECS if spec['name'] == name), None)
            if spec is None:
                raise ValueError('没有这个工具，只可使用直播间已提供的工具')
            if isinstance(arguments, str):
                if len(arguments) > 4096:
                    raise ValueError('工具参数过长')
                arguments = json.loads(arguments)
            schema = spec['parameters']
            if not isinstance(arguments, dict) or set(arguments) - set(schema['properties']) or set(schema.get('required', [])) - set(arguments):
                raise ValueError('工具参数缺失或包含不允许的字段')
            if name == 'get_music_queue':
                result = {'status': 'ok', 'message': '已读取当前歌曲和待播队列', **self.music_session.context()}
            elif name == 'get_today_stats':
                result = {'status': 'ok', 'message': '已读取今日收支和token统计', **self.host.stats_reader()}
            else:
                query, limit, only_me = (arguments['query'], arguments.get('limit', 6), arguments.get('only_me', False))
                if not isinstance(query, str) or len(query) > 100 or type(limit) is not int or (not 1 <= limit <= 20) or (type(only_me) is not bool):
                    raise ValueError('关键词最多100字，limit为1到20的整数，only_me为布尔值')
                result = self.host.history_reader(self.room_id, self.viewer_id, query, limit, only_me)
        except Exception as exc:
            result = {'status': 'error', 'message': str(exc)[:250]}
        self.records.append({'tool': name, 'call_id': str(call_id)[:120], 'result': copy.deepcopy(result)})
        return result
