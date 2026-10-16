"""_lazydeps 记录器包懒代理测试。

覆盖：vendored 函数行为对拍、上游引用点钉住（升级变红不绿炸）、
桩安装与 openpyxl 豁免、ByteRecorder 代理自愈回载、NO_STUB 逃生。

上游 4.1.1.3+ 将 DataRecorder 改名为 DrissionRecord，测试按实际
安装的包名适配，两种形态都必须绿。
"""
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from dp_cli import _lazydeps


def _recorder_pkgs():
    """返回当前环境实际安装的记录器包名列表。"""
    pkgs = [n for n in _lazydeps._CANDIDATES
            if importlib.util.find_spec(n) is not None]
    if not pkgs:
        pytest.skip('DrissionRecord/DataRecorder 均未安装')
    return pkgs


def _load_real_tools(pkg):
    """直接加载真 <pkg>/tools.py（绕过桩；顶层 import openpyxl
    在测试环境允许）。"""
    spec = importlib.util.find_spec(pkg)
    path = Path(spec.submodule_search_locations[0]) / 'tools.py'
    spec2 = importlib.util.spec_from_file_location('_dr_tools_real', path)
    mod = importlib.util.module_from_spec(spec2)
    spec2.loader.exec_module(mod)
    return mod


@pytest.mark.unit
def test_vendored_funcs_parity(tmp_path):
    """vendored make_valid_name/get_usable_path 与真实现输出对拍。"""
    for real in map(_load_real_tools, _recorder_pkgs()):
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
    pkgs = [n for n in _lazydeps._CANDIDATES
            if importlib.util.find_spec(n) is not None]
    assert pkgs, 'DrissionRecord/DataRecorder 均未安装'
    dp_root = Path(importlib.util.find_spec('DrissionPage')
                   .submodule_search_locations[0])
    for rel, symbol in [
        ('_pages/chromium_base.py', 'make_valid_name'),
        ('_elements/chromium_element.py', None),
        ('_units/downloader.py', 'get_usable_path'),
        ('_functions/web.py', 'make_valid_name'),
    ]:
        src = (dp_root / rel).read_text(encoding='utf-8')
        assert any(
            f'from {p}.tools import' in src and (symbol is None or symbol in src)
            for p in pkgs
        ), f'{rel} 不再引用 {pkgs} tools {symbol or ""}'
    dk_spec = importlib.util.find_spec('DownloadKit')
    if dk_spec is not None:
        dk_root = Path(dk_spec.submodule_search_locations[0])
        msrc = (dk_root / 'mission.py').read_text(encoding='utf-8')
        assert any(f'from {p} import ByteRecorder' in msrc for p in pkgs)
        ssrc = (dk_root / 'setter.py').read_text(encoding='utf-8')
        assert any(f'from {p} import Recorder' in ssrc for p in pkgs)


# ── 子进程探针：桩行为必须在干净进程里验证（pytest 进程已装桩）──

_PROBE = r'''
import json, os, sys
import dp_cli
PKG = {pkg!r}
out = {{
    'stub': PKG in sys.modules and 'openpyxl' not in sys.modules,
}}
import DrissionPage
out['drission_ok'] = hasattr(DrissionPage, 'ChromiumPage')
out['openpyxl_absent'] = 'openpyxl' not in sys.modules
import importlib
CLS = 'Recorder' if PKG == 'DrissionRecord' else 'ByteRecorder'
obj = getattr(importlib.import_module(PKG), CLS)(cache_size=8)
out['healed'] = type(obj).__name__ == CLS
# 真模块已回载（实例的类来自真实包路径，非桩代理）
out['real_module'] = type(obj).__module__.startswith(PKG + '.')
out['openpyxl_after_heal'] = 'openpyxl' in sys.modules
print(json.dumps(out))
'''

_NOSTUB_PROBE = r'''
import json, sys
import dp_cli
import DrissionPage
# 原生 import：PKG 若出现，必须是真模块（无桩 __getattr__）
PKG = {pkg!r}
mod = sys.modules.get(PKG)
print(json.dumps({{
    'drission_ok': hasattr(DrissionPage, 'ChromiumPage'),
    'stub_absent': mod is None or getattr(mod, '__getattr__', None) is None,
}}))
'''


def _run_probe(code, tmp_path, extra_env=None, **fmt):
    env = dict(os.environ, HOME=str(tmp_path))
    if extra_env:
        env.update(extra_env)
    if fmt:
        code = code.format(**fmt)
    r = subprocess.run([sys.executable, '-c', code],
                       capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout.strip().splitlines()[-1])


@pytest.mark.unit
def test_stub_skips_openpyxl_and_heals(tmp_path):
    """桩安装→import DrissionPage 不拉 openpyxl→实例化 ByteRecorder
    自愈回载真模块（download 路径不踩雷）。"""
    out = _run_probe(_PROBE, tmp_path, pkg=_recorder_pkgs()[0])
    assert out['stub'] is True
    assert out['drission_ok'] is True
    assert out['openpyxl_absent'] is True       # openpyxl 税被推迟
    assert out['healed'] is True
    assert out['real_module'] is True           # 回载的是真实现非代理
    # 注：DrissionRecord 自身已懒载 openpyxl，heal 后不必然加载，不断言


@pytest.mark.unit
def test_no_stub_env_escape(tmp_path):
    """DP_CLI_NO_STUB=1 完全禁用桩：DrissionPage 原生全量 import。"""
    out = _run_probe(_NOSTUB_PROBE, tmp_path,
                     extra_env={'DP_CLI_NO_STUB': '1'},
                     pkg=_recorder_pkgs()[0])
    assert out['drission_ok'] is True
    assert out['stub_absent'] is True  # 无桩 → 走原生模块


@pytest.mark.unit
def test_heal_loader_retries_real_import(tmp_path):
    """桩导致 DrissionPage exec 失败时，自愈 loader 撤销桩重试——
    模拟：先污染 sys.modules['<pkg>.tools'] 造缺口，再 import。"""
    code = r'''
import sys, json
import dp_cli
# 人为破坏 tools 桩（模拟上游改用未覆盖符号且不触 __getattr__ 的场景）
tools = sys.modules[{pkg!r} + '.tools']
del tools.make_valid_name, tools.get_usable_path, tools.get_long, tools.__getattr__
import DrissionPage
# 自愈回退：DrissionPage 正常载入为真模块（桩已撤销）
print(json.dumps({{'ok': hasattr(DrissionPage, 'ChromiumPage')}}))
'''
    out = _run_probe(code, tmp_path, pkg=_recorder_pkgs()[0])
    assert out['ok'] is True
