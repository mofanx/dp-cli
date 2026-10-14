# -*- coding:utf-8 -*-
"""
真实浏览器冒烟测试（integration）。

需要本机/CI 环境存在 Chrome/Chromium 可执行文件。用例覆盖 CLI 层
open → eval → close 的最小闭环；会话目录与页面服务器分别由 conftest
共享 fixture isolated_session_dir / http_server 提供。
"""
import json

import pytest
from click.testing import CliRunner

from dp_cli.main import cli


@pytest.mark.integration
@pytest.mark.timeout(60)
def test_open_eval_close_smoke(isolated_session_dir, http_server):
    """CLI 冒烟：open --headless → eval document.title → close。"""
    url = http_server({'index.html':
                       '<html><head><title>smoke-ok</title></head>'
                       '<body><div id="x">marker-42</div></body></html>'})
    runner = CliRunner()
    sess = 'smoke_test'
    try:
        r = runner.invoke(cli, ['open', url, '--headless', '-S', sess])
        assert r.exit_code == 0, r.output
        assert json.loads(r.output)['status'] == 'ok'

        r = runner.invoke(cli, ['eval', 'document.title', '-S', sess])
        assert r.exit_code == 0, r.output
        out = json.loads(r.output)
        assert out['status'] == 'ok'
        assert out['data']['result'] == 'smoke-ok'
    finally:
        runner.invoke(cli, ['close', '-S', sess])


@pytest.mark.integration
@pytest.mark.timeout(60)
def test_open_page_content_smoke(isolated_session_dir, http_server):
    """CLI 冒烟：页面 DOM 内容可通过 eval 读回。"""
    url = http_server({'index.html':
                       '<html><body><div id="x">marker-42</div></body></html>'})
    runner = CliRunner()
    sess = 'smoke_content'
    try:
        r = runner.invoke(cli, ['open', url, '--headless', '-S', sess])
        assert r.exit_code == 0, r.output

        r = runner.invoke(
            cli, ['eval', "document.getElementById('x').textContent",
                  '-S', sess])
        assert r.exit_code == 0, r.output
        out = json.loads(r.output)
        assert out['data']['result'] == 'marker-42'
    finally:
        runner.invoke(cli, ['close', '-S', sess])
