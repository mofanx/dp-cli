"""import 预算守卫：防顶层 import 退化。

背景：每条 `dp` 命令是独立进程，DrissionPage 冷 import ≈260ms
（其中 DataRecorder→openpyxl 链 ~130ms）。所有 `from DrissionPage`
必须保持函数内延迟 import——非浏览器命令绝不加载它。
子进程隔离保证不受 pytest 同进程已加载模块影响。
"""
import json
import os
import subprocess
import sys

import pytest

_PROBE = (
    "import sys, json;"
    "from click.testing import CliRunner;"
    "from dp_cli.main import cli;"
    "CliRunner().invoke(cli, {argv});"
    "print('DR=' + json.dumps(sorted(m for m in sys.modules"
    "    if m == 'DrissionPage' or m.startswith('DrissionPage.'))))"
)


def _probe(argv, tmp_path):
    env = dict(os.environ, HOME=str(tmp_path))
    r = subprocess.run([sys.executable, '-c', _PROBE.format(argv=repr(argv))],
                       capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr
    line = [l for l in r.stdout.splitlines() if l.startswith('DR=')]
    assert line, r.stdout
    return json.loads(line[0][3:])


@pytest.mark.unit
@pytest.mark.parametrize('argv', [['-V'], ['--help'], ['list'], ['catalog'],
                                  ['catalog', '--json']])
def test_non_browser_commands_skip_drission_import(argv, tmp_path):
    """list/catalog/-V/--help 等不碰浏览器的命令不得加载 DrissionPage。"""
    assert _probe(argv, tmp_path) == []


@pytest.mark.unit
def test_browser_command_imports_drission_on_demand(tmp_path):
    """浏览器命令在需要时才 import：open --port 连死端口也触发加载。"""
    mods = _probe(['open', '--port', '1'], tmp_path)
    assert 'DrissionPage' in mods


@pytest.mark.unit
def test_session_module_import_is_light(tmp_path):
    """import dp_cli.session 本身不拉起 DrissionPage（顶层零依赖）。"""
    code = ("import sys; import dp_cli.session;"
            "print(any(m.startswith('DrissionPage') for m in sys.modules))")
    r = subprocess.run([sys.executable, '-c', code],
                       capture_output=True, text=True,
                       env=dict(os.environ, HOME=str(tmp_path)))
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == 'False'
