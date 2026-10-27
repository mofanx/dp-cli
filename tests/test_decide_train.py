

def test_record_post_state_field(tmp_path, monkeypatch):
    """post_state 快照落盘——真实流量执行后证据字段；unsure 语义不变。"""
    import dp_cli.decide_train as dt
    monkeypatch.setenv('DPX_RECORD_DECIDE', '1')
    monkeypatch.setattr(dt, '_file', lambda: tmp_path / 'r.jsonl')
    refs = {'1': {'role': 'link', 'name': 'Sign in'}}
    dt.record('s', ['click', '@e1'], refs,
              post_state={'url': 'https://x/logged'})
    dt.record('s', ['decide', '登录'], refs, unsure=True,
              post_state={'url': 'https://x/'})
    import json
    rows = [json.loads(l) for l in (tmp_path / 'r.jsonl').read_text().splitlines()]
    assert rows[0]['post_state']['url'] == 'https://x/logged'
    # unsure 记录带 post_state 但 unsure 标记保留——不冒充标签
    assert rows[1]['unsure'] is True and rows[1]['post_state']['url'] == 'https://x/'
