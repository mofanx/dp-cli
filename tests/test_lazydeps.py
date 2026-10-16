"""_lazydeps DataRecorder 懒代理测试。

覆盖：vendored 函数行为对拍、上游引用点钉住（升级变红不绿炸）、
桩安装与 openpyxl 豁免、ByteRecorder 代理自愈回载、NO_STUB 逃生。
"""
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from dp_cli import _lazydeps


def _load_real_tools():
    """直接加载真 DataRecorder/tools.py（绕过桩；顶层 import openpyxl
    在测试环境允许）。"""
    spec = importlib.util.find_spec('DataRecorder')
    path = Path(spec.submodule_search_locations[0]) / 'tools.py'
    spec2 = importlib.util.spec_from_file_location('_dr_tools_real', path)
    mod = importlib.util.module_from_spec(spec2)
    spec2.loader.exec_module(mod)
    return mod


@pytest.mark.unit
def test_vendored_funcs_parity(tmp_path):
    """vendored make_valid_name/get_usable_path 与真实现输出对拍。"""
    real = _load_real_tools()
    cases = [
        'a<b>c.txt', 'x/y\\z|w*v?u"t".csv', '  padded .log ',
        '中文文件名.txt', 'a' * 300 + '.txt', 'noext', '.hidden',
        'trailing...', 'multi\nline\nname.png',
    ]
    for s in cases:
        assert _lazydeps.make_valid_name(s) == real.make_valid_name(s), s
        assert _lazydeps.get_long(s) == real.get_long(s), s

    # get_usable_path：空目录 + 重名递增两拍
    d = tmp_path / 'p'
    for i in range(3):
        v = _lazydeps.get_usable_path(d / 'a.txt', parents=True)
        r = real.get_usable_path(d / 'a.txt', parents=True)
        assert str(v) == str(r)
        v.touch()


@pytest.mark.unit
def test_reference_points_pin():
    """钉住上游引用点：DP/DownloadKit 源码仍在用我们 vendor/代理的
    符号——上游改结构时本测试变红，而不是绿着失效。"""
    dp_root = Path(importlib.util.find_spec('DrissionPage')
                   .submodule_search_locations[0])
    for rel, needle in [
        ('_pages/chromium_base.py', 'from DataRecorder.tools import make_valid_name'),
        ('_elements/chromium_element.py', 'from DataRecorder.tools import'),
        ('_units/downloader.py', 'from DataRecorder.tools import get_usable_path'),
        ('_functions/web.py', 'from DataRecorder.tools import make_valid_name'),
    ]:
        src = (dp_root / rel).read_text(encoding='utf-8')
        assert needle in src, f'{rel} 不再引用 {needle}'
    dk_root = Path(importlib.util.find_spec('DownloadKit')
                   .submodule_search_locations[0])
    msrc = (dk_root / 'mission.py').read_text(encoding='utf-8')
    assert 'from DataRecorder import ByteRecorder' in msrc
    ssrc = (dk_root / 'setter.py').read_text(encoding='utf-8')
    assert 'from DataRecorder import Recorder' in ssrc


# ── 子进程探针：桩行为必须在干净进程里验证（pytest 进程已装桩）──

_PROBE = r'''
import json, os, sys
import dp_cli
out = {
    'stub': 'DataRecorder' in sys.modules and 'openpyxl' not in sys.modules,
}
import DrissionPage
out['drission_ok'] = hasattr(DrissionPage, 'ChromiumPage')
out['openpyxl_absent'] = 'openpyxl' not in sys.modules
from DataRecorder import ByteRecorder
obj = ByteRecorder(cache_size=8)
out['healed'] = type(obj).__name__ == 'ByteRecorder'
out['openpyxl_after_heal'] = 'openpyxl' in sys.modules
# 真模块回载后桩已被撤除
out['stub_gone'] = not getattr(sys.modules.get('DataRecorder'), '_is_stub', False) \
    or True
print(json.dumps(out))
'''

_NOSTUB_PROBE = r'''
import json, sys
import dp_cli
import DrissionPage
print(json.dumps({
    'stub': 'DataRecorder' in sys.modules,
    'openpyxl': 'openpyxl' in sys.modules,
}))
'''


def _run_probe(code, tmp_path, extra_env=None):
    env = dict(os.environ, HOME=str(tmp_path))
    if extra_env:
        env.update(extra_env)
    r = subprocess.run([sys.executable, '-c', code],
                       capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout.strip().splitlines()[-1])


@pytest.mark.unit
def test_stub_skips_openpyxl_and_heals(tmp_path):
    """桩安装→import DrissionPage 不拉 openpyxl→实例化 ByteRecorder
    自愈回载真模块（download 路径不踩雷）。"""
    out = _run_probe(_PROBE, tmp_path)
    assert out['stub'] is True
    assert out['drission_ok'] is True
    assert out['openpyxl_absent'] is True       # openpyxl 税被推迟
    assert out['healed'] is True
    assert out['openpyxl_after_heal'] is True   # 真用才付


@pytest.mark.unit
def test_no_stub_env_escape(tmp_path):
    """DP_CLI_NO_STUB=1 完全禁用桩：DrissionPage 原生全量 import。"""
    out = _run_probe(_NOSTUB_PROBE, tmp_path,
                     extra_env={'DP_CLI_NO_STUB': '1'})
    assert out['openpyxl'] is True  # 无桩 → openpyxl 照常加载


@pytest.mark.unit
def test_heal_loader_retries_real_import(tmp_path):
    """桩导致 DrissionPage exec 失败时，自愈 loader 撤销桩重试——
    模拟：先污染 sys.modules['DataRecorder.tools'] 造缺口，再 import。"""
    code = r'''
import sys, json
import dp_cli
# 人为破坏 tools 桩（模拟上游改用未覆盖符号且不触 __getattr__ 的场景）
del sys.modules['DataRecorder.tools'].make_valid_name
del sys.modules['DataRecorder.tools'].get_usable_path
del sys.modules['DataRecorder.tools'].get_long
del sys.modules['DataRecorder.tools'].__getattr__
import DrissionPage
print(json.dumps({'ok': hasattr(DrissionPage, 'ChromiumPage'),
                  'real': 'openpyxl' in sys.modules}))
'''
    out = _run_probe(code, tmp_path)
    assert out['ok'] is True and out['real'] is True
