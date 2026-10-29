"""Plan gift wording using trusted receipts; never assign price/tier in the model."""

DEFAULT_GUIDANCE = {
    '普通': '轻松简短地谢一句，像收到一个小心意，10至25字，不使用受宠若惊的大反应。',
    '用心': '明显比普通档开心，可以俏皮接一句或玩礼物名的梗，15至35字，不表演成天大惊喜。',
    '惊喜': '先自然流露短暂惊喜，再认真感谢，20至45字，可以有一点嘴硬或得意。',
    '大惊喜': '比惊喜档更郑重、有被支持到的真实开心，25至55字，不堆感叹号、不卖惨、不许愿回报。',
    '价格未知': '只感谢这份礼物与心意，不猜测价格，不按贵重礼物夸张反应。',
}


def plan_gift_reply(event, config):
    data = event['data']
    name = data.get('gift', data.get('giftName', '礼物'))
    rule = config['gift_rules'].get(name, {})
    fallback = data.get('reply', '')
    if 'thank_template' in rule:
        fallback = rule['thank_template'].format(nickname=event['user']['nickname'], gift=name, count=data['count'])
    use_model = config['studio']['gift_reply_mode'] == 'model' and 'thank_template' not in rule
    tier = data.get('tier', '价格未知')
    tier_config = next((t for t in config['studio']['gift_tiers'] if t['name'] == tier), {})
    guidance = (data.get('tier_guidance') or tier_config.get('guidance') or DEFAULT_GUIDANCE.get(tier, '按这个档位，用自然口语感谢。'))
    if data.get('total_diamonds') is None:
        tier, guidance = '价格未知', DEFAULT_GUIDANCE['价格未知']
    nickname = event['user']['nickname']
    spoken = ''.join(c for c in nickname if c.isalpha() or c.isdecimal()).strip()[:24] or '这位朋友'
    action = rule.get('temporary') if config['visuals']['gift_fun_enabled'] else None
    batch = data.get('thanks_batch')
    if batch:
        fallback = data['reply']
    return {'use_model': use_model, 'fallback': fallback, 'context': {
        'facts': {'nickname': nickname, 'spoken_nickname': spoken, 'gift': name,
                  'count': data['count'], 'combo_total_count': data.get('combo_total_count', data['count']),
                  'total_diamonds': data.get('total_diamonds'), 'tier': tier,
                  **({'thanks_batch':batch, 'batch_note':'这是几份已确认的普通礼物合并感谢，简短提到这些人，不只感谢第一位，不改变各自数量。'} if batch else {})},
        'common_guidance': config['studio']['gift_reply_guidance'], 'tier_guidance': guidance,
        'planned_action': dict(action) if action else None,
    }}
