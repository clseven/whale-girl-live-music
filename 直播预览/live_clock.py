"""Shared Beijing time for the audience clock and both model adapters."""
import time
from datetime import datetime, timedelta, timezone

BEIJING = timezone(timedelta(hours=8))


def beijing_time(timestamp=None):
    stamp = time.time() if timestamp is None else timestamp
    now = datetime.fromtimestamp(stamp, BEIJING)
    return {'timezone': 'Asia/Shanghai', 'utc_offset': '+08:00',
            'unix_ms': round(stamp * 1000), 'iso': now.isoformat(timespec='seconds'),
            'date': now.strftime('%Y-%m-%d'), 'time': now.strftime('%H:%M:%S'),
            'weekday': '星期' + '一二三四五六日'[now.weekday()]}
