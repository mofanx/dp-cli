"""snapshot combobox ref 注入 option 候选——r2 批注：补单测防回归。

验证：run_js 产 sel_map 时 refs[combobox] 得 options 字段+name 并入；
run_js 异常时 refs 原样落盘（不吞错也不阻断）。
"""
import json
import pytest
from click.testing import CliRunner
from dp_cli.main import cli


@pytest.fixture
def runner():
    return CliRunner()


class _Page:
    def __init__(self, js_ret=None, js_exc=None):
        self._js_ret, self._js_exc = js_ret, js_exc
        self.url = 'https://x/p'
        self.title = 'P'

    def run_js(self, script):
        if self._js_exc:
            raise self._js_exc
        return self._js_ret


def _a11y_with_combobox():
    return {'tree': {'role': 'WebArea', 'name': 'P', 'children': [
        {'role': 'button', 'name': '保存', 'locator': 'css:#b1'},
        {'role': 'combobox', 'name': 'Language', 'locator': 'css:#sel'},
        {'role': 'link', 'name': '取消', 'locator': 'css:#a1'},
    ]}, 'page': {'url': 'https://x/p', 'title': 'P'}}


def _run(runner, monkeypatch, tmp_path, page, saved):
    import dp_cli.commands.snapshot_cmd as smod
    monkeypatch.setattr(smod, '_get_page', lambda s: page)
    monkeypatch.setattr(smod, 'take_a11y_snapshot',
                        lambda *a, **k: _a11y_with_combobox())
    monkeypatch.setattr(smod, 'save_refs',
                        lambda s, u, refs: saved.update(refs))
    return runner.invoke(cli, ['snapshot', '-S', 'tsnap', '-f', 'json'])


@pytest.mark.unit
def test_combobox_options_injected(runner, monkeypatch, tmp_path):
    sel = [['Language', ['English', '中文', '日本語']]]
    saved = {}
    r = _run(runner, monkeypatch, tmp_path, _Page(js_ret=sel), saved)
    assert r.exit_code == 0, r.output
    combo = next(v for v in saved.values() if v.get('role') == 'combobox')
    assert combo['options'] == ['English', '中文', '日本語']
    assert '日本語' in combo['name']


@pytest.mark.unit
def test_combobox_js_failure_preserves_refs(runner, monkeypatch, tmp_path):
    saved = {}
    r = _run(runner, monkeypatch, tmp_path,
             _Page(js_exc=RuntimeError('js boom')), saved)
    assert r.exit_code == 0, r.output
    combo = next(v for v in saved.values() if v.get('role') == 'combobox')
    assert 'options' not in combo
    assert combo['name'] == 'Language'   # 原样落盘未被破坏


@pytest.mark.unit
def test_combobox_single_select_fallback(runner, monkeypatch, tmp_path):
    # 无 aria-label 时单 select 页仍按唯一池注入
    sel = [['', ['USD', 'EUR', 'JPY']]]
    saved = {}
    r = _run(runner, monkeypatch, tmp_path, _Page(js_ret=sel), saved)
    assert r.exit_code == 0, r.output
    combo = next(v for v in saved.values() if v.get('role') == 'combobox')
    assert combo['options'] == ['USD', 'EUR', 'JPY']
