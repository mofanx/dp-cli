# -*- coding:utf-8 -*-
"""model pull / model-status mock 测试。

假 _urlopen 返回小 tar.gz 包——覆盖：下载+sha256+解压+mv 全流程、
sha 不匹配拒绝、幂等 skip、Range 断点续传、残缺备份重装、status JSON 字段。
全程不触网、不动真实 ~/.dp_cli。
"""
import hashlib
import io
import json
import tarfile
from pathlib import Path

import pytest
from click.testing import CliRunner

from dp_cli.main import cli
import dp_cli.commands.model as mod

pytestmark = pytest.mark.unit


def _make_tgz(files: dict) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode='w:gz') as tar:
        for name, data in files.items():
            b = data.encode() if isinstance(data, str) else data
            ti = tarfile.TarInfo(name)
            ti.size = len(b)
            tar.addfile(ti, io.BytesIO(b))
    return buf.getvalue()


def _model_tgz(extra: dict = None) -> bytes:
    files = {
        'bge_v24_tuned/config.json': '{}',
        'bge_v24_tuned/tokenizer.json': '{}',
        'bge_v24_tuned/model.safetensors': 'weights',
        'bge_v24_tuned/onnx/model_int8.onnx': 'onnx',
    }
    files.update(extra or {})
    return _make_tgz(files)


class _Resp:
    """urlopen 假响应——支持 .status/.headers/.read(n)。"""

    def __init__(self, data: bytes, status: int = 200):
        self._io = io.BytesIO(data)
        self.status = status
        self.headers = {'Content-Length': str(len(data))}

    def read(self, n=-1):
        return self._io.read(n)


def _patch_agent_dirs(monkeypatch, mdir):
    """引擎探测链重定向——agent._bge_dirs 直读真实 HOME，测试只看 tmp。"""
    import dp_cli.agent as agent_mod
    monkeypatch.setattr(agent_mod, '_bge_dirs', lambda: [mdir])
    monkeypatch.setattr(agent_mod, '_ort', lambda: None)


@pytest.fixture
def fake_net(tmp_path, monkeypatch):
    """隔离 dp_dir + 假 HTTP（支持 Range 206）。"""
    d = tmp_path / '.dp_cli'
    monkeypatch.setattr(mod, '_dp_dir', lambda: d)
    # 进度条走 stderr.buffer——CliRunner 会把它合进 result.output，
    # 测试环境静默防 JSON 解析被污染（真机仍是 stderr 正确语义）
    monkeypatch.setattr(mod, '_write_err', lambda s: None)
    blob = _model_tgz()
    monkeypatch.setattr(mod, '_MODEL_SHA256',
                        hashlib.sha256(blob).hexdigest())
    calls = []

    def _fake(req, timeout=60):
        rng = req.get_header('Range')
        calls.append(rng)
        if rng:
            start = int(rng.split('=')[1].split('-')[0])
            return _Resp(blob[start:], status=206)
        return _Resp(blob, status=200)

    monkeypatch.setattr(mod, '_urlopen', _fake)
    _patch_agent_dirs(monkeypatch, d / 'bge_model')
    return {'dir': d, 'blob': blob, 'calls': calls}


def _invoke(*args):
    return CliRunner().invoke(cli, list(args))


def _pull_ok(env):
    r = _invoke('model', 'pull')
    assert r.exit_code == 0, r.output + str(r.exception or '')
    out = json.loads(r.output)
    assert out['status'] == 'ok', out
    return out


# ── pull 全流程 ───────────────────────────────────────────────────────────────

def test_pull_full_flow(fake_net):
    mdir = fake_net['dir'] / 'bge_model'
    out = _pull_ok(fake_net)
    d = out['data']
    assert d['installed'] is True and d['sha256']
    assert (mdir / 'config.json').exists()
    assert (mdir / 'onnx' / 'model_int8.onnx').exists()
    assert mod._is_complete(mdir)
    meta = json.loads((mdir / '.dp_install.json').read_text())
    assert meta['sha256'] == hashlib.sha256(fake_net['blob']).hexdigest()
    assert meta['archive_root'] == 'bge_v24_tuned'
    assert not (fake_net['dir'] / 'dpx-reranker.tar.gz.part').exists()


def test_pull_sha_mismatch_rejects(fake_net, monkeypatch):
    monkeypatch.setattr(mod, '_MODEL_SHA256', '0' * 64)
    r = _invoke('model', 'pull')
    assert r.exit_code == 1
    out = json.loads(r.output)
    assert out['status'] == 'error'
    assert out['code'] == 'MODEL_SHA_MISMATCH'
    # 临时文件删除 + 不落地安装
    assert not (fake_net['dir'] / 'dpx-reranker.tar.gz.part').exists()
    assert not (fake_net['dir'] / 'bge_model').exists()


def test_pull_idempotent_skip(fake_net):
    _pull_ok(fake_net)
    assert len(fake_net['calls']) == 1
    out = _pull_ok(fake_net)                    # 第二次——幂等
    assert out['data']['skipped'] is True
    assert len(fake_net['calls']) == 1          # 完整即不再发请求


def test_pull_resume_range(fake_net):
    part = fake_net['dir'] / 'dpx-reranker.tar.gz.part'
    fake_net['dir'].mkdir(parents=True, exist_ok=True)
    part.write_bytes(fake_net['blob'][:100])    # 已下 100B
    _pull_ok(fake_net)
    assert fake_net['calls'] == ['bytes=100-']  # Range 续传
    assert (fake_net['dir'] / 'bge_model' / 'config.json').exists()


def test_pull_200_restart_when_range_ignored(fake_net, monkeypatch):
    """服务端忽略 Range 回 200——从头覆写仍能完整安装。"""
    part = fake_net['dir'] / 'dpx-reranker.tar.gz.part'
    fake_net['dir'].mkdir(parents=True, exist_ok=True)
    part.write_bytes(b'GARBAGE')

    def _no_range(req, timeout=60):
        return _Resp(fake_net['blob'], status=200)

    monkeypatch.setattr(mod, '_urlopen', _no_range)
    _pull_ok(fake_net)
    assert (fake_net['dir'] / 'bge_model' / 'config.json').exists()


def test_pull_incomplete_backup(fake_net):
    mdir = fake_net['dir'] / 'bge_model'
    mdir.mkdir(parents=True)
    (mdir / 'config.json').write_text('{}')     # 残缺：缺 tokenizer/权重
    out = _pull_ok(fake_net)
    assert out['data']['installed'] is True
    backups = list(fake_net['dir'].glob('bge_model.bak-*'))
    assert backups, '残缺安装应改名备份'
    assert mod._is_complete(mdir)


def test_pull_download_failure_keeps_part(fake_net, monkeypatch):
    def _boom(req, timeout=60):
        raise ConnectionError('net down')

    monkeypatch.setattr(mod, '_urlopen', _boom)
    r = _invoke('model', 'pull')
    out = json.loads(r.output)
    assert out['status'] == 'error'
    assert out['code'] == 'MODEL_DOWNLOAD_FAILED'


# ── model-status ─────────────────────────────────────────────────────────────

def test_status_after_pull(fake_net):
    _pull_ok(fake_net)
    r = _invoke('model-status')
    assert r.exit_code == 0, r.output
    d = json.loads(r.output)['data']
    bge = d['bge_model']
    assert bge['exists'] and bge['complete']
    assert bge['files'] > 0 and bge['size_bytes'] > 0
    assert bge['sha256_verified'] is True
    eng = d['engine']
    assert eng['family'] == 'bge'
    assert eng['selected'] == str(fake_net['dir'] / 'bge_model')
    assert eng['probe_chain'][0]['loadable'] is True
    assert 'decide_model' in d and d['decide_model']['exists'] is False


def test_status_empty(fake_net):
    r = _invoke('model-status')
    assert r.exit_code == 0
    d = json.loads(r.output)['data']
    assert d['bge_model']['exists'] is False
    assert d['bge_model']['complete'] is False
    assert d['bge_model']['sha256_verified'] is None
    assert d['engine']['selected'] is None
    assert d['engine']['family'] == 'minilm'    # 无 bge → MiniLM 兜底族
    assert d['engine']['probe_chain'][0]['exists'] is False


def test_status_probe_chain_echoes_all_candidates(tmp_path, monkeypatch):
    dirs = [tmp_path / 'a', tmp_path / 'b']
    dirs[1].mkdir()
    (dirs[1] / 'config.json').write_text('{}')
    (dirs[1] / 'tokenizer.json').write_text('{}')
    import dp_cli.agent as agent_mod
    monkeypatch.setattr(agent_mod, '_bge_dirs', lambda: dirs)
    monkeypatch.setattr(agent_mod, '_ort', lambda: None)
    monkeypatch.setattr(mod, '_dp_dir', lambda: tmp_path / '.dp_cli')
    r = _invoke('model-status')
    d = json.loads(r.output)['data']
    assert [c['path'] for c in d['engine']['probe_chain']] == \
        [str(dirs[0]), str(dirs[1])]
    assert d['engine']['selected'] == str(dirs[1])


def test_bge_dirs_user_deploy_outranks_dev_paths(tmp_path, monkeypatch):
    """pull 落位 ~/.dp_cli/bge_model 优先于开发机遗留路径（v19/v24/tuned）。"""
    import dp_cli.agent as agent_mod
    monkeypatch.setattr(agent_mod.Path, 'home', lambda: tmp_path)
    monkeypatch.delenv('DPX_BGE_MODEL', raising=False)
    dirs = agent_mod._bge_dirs()
    user_dir = str(tmp_path / '.dp_cli' / 'bge_model')
    assert dirs[0] == Path(user_dir)
    assert all(str(d).startswith('/media/yan/Data/') for d in dirs[1:])
    monkeypatch.setenv('DPX_BGE_MODEL', '/tmp/custom_model')
    assert agent_mod._bge_dirs()[0] == Path('/tmp/custom_model')
