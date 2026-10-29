"""Gift gross-value accounting in raw diamondCount units; no payout estimates."""
import copy
from datetime import datetime, timezone, timedelta

CHINA = timezone(timedelta(hours=8))


def empty_totals():
    return {'coins': 0, 'gift_count': 0, 'unknown_price_count': 0}


class GiftIncome:
    def __init__(self, state, clock):
        self.state, self.clock = state, clock
        state.setdefault('started_at', clock())
        state.setdefault('receipts', {})
        state.setdefault('lifetime', {'live': empty_totals(), 'simulation': empty_totals()})
        state.setdefault('days', {})

    def day(self, timestamp=None):
        return datetime.fromtimestamp(self.clock() if timestamp is None else timestamp, CHINA).date().isoformat()

    def has_receipt(self, key, simulation):
        return ('simulation:' if simulation else 'live:') + key in self.state['receipts']

    def record(self, key, raw_count, unit_price, simulation=False, day=None, baseline_count=0):
        """Upsert a cumulative combo; repeats, timeout finals and repricing are idempotent."""
        bucket = 'simulation' if simulation else 'live'
        receipt_id = bucket + ':' + key
        old = self.state['receipts'].get(receipt_id)
        if old:
            baseline_count, day = old['baseline_count'], old['day']
            raw_count = max(raw_count, old['raw_count'])
            if old['unit_price'] is not None:
                unit_price = old['unit_price']  # a later replay cannot change a known price
        else:
            day = day or self.day()
        count = max(0, raw_count-baseline_count)
        value = count*unit_price if unit_price is not None else 0
        unknown = count if unit_price is None else 0
        receipt = {'raw_count': raw_count, 'baseline_count': baseline_count, 'count': count,
                   'unit_price': unit_price, 'coins': value, 'unknown_price_count': unknown, 'day': day}
        deltas = {'coins': value-(old['coins'] if old else 0),
                  'gift_count': count-(old['count'] if old else 0),
                  'unknown_price_count': unknown-(old['unknown_price_count'] if old else 0)}
        daily = self.state['days'].setdefault(day, {'live': empty_totals(), 'simulation': empty_totals()})
        for totals in (self.state['lifetime'][bucket], daily[bucket]):
            for field, delta in deltas.items():
                totals[field] += delta
        self.state['receipts'][receipt_id] = receipt

    def summary(self):
        today = self.day()
        return copy.deepcopy({'unit': '钻石', 'basis': '礼物总值，非平台到账',
            'started_at': self.state['started_at'], 'day': today,
            'today': self.state['days'].get(today, {'live': empty_totals(), 'simulation': empty_totals()}),
            'lifetime': self.state['lifetime']})

    def reset(self):
        # Retain receipt baselines so old gift frames cannot refill cleared totals.
        for receipt in self.state['receipts'].values():
            receipt.update(baseline_count=receipt['raw_count'], count=0, coins=0,
                           unknown_price_count=0, day=self.day())
        self.state.update(started_at=self.clock(), days={},
                          lifetime={'live': empty_totals(), 'simulation': empty_totals()})
        return self.summary()
