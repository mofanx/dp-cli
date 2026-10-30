# -*- coding:utf-8 -*-
"""model pull / model-status mock 测试。

假 _urlopen 返回小 tar.gz 包——覆盖：下载+sha256+解压+mv 全流程、
sha 不匹配拒绝、幂等 skip、Range 断点续传、残缺备份重装、status JSON 字段。
全程不触网、不动真实 ~/.dp_cli。
"""
import hashlib
import io
import json
import sys
import tarfile
import types
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
        'bge_v28_tuned/config.json': '{}',
        'bge_v28_tuned/tokenizer.json': '{}',
        'bge_v28_tuned/model.safetensors': 'weights',
        'bge_v28_tuned/onnx/model_int8.onnx': 'onnx',
    }
    files.update(extra or {})
    return _make_tgz(files)


def _int8_tgz() -> bytes:
    """int8-only 自包含包：tokenizer+config+onnx/model_int8.onnx，无 safetensors。"""
    return _make_tgz({
        'bge_v28_tuned/config.json': '{}',
        'bge_v28_tuned/tokenizer.json': '{}',
        'bge_v28_tuned/tokenizer_config.json': '{}',
        'bge_v28_tuned/onnx/model_int8.onnx': 'int8onnx',
    })


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
    int8_blob = _int8_tgz()
    monkeypatch.setattr(mod, '_MODEL_SHA256',
                        hashlib.sha256(blob).hexdigest())
    monkeypatch.setattr(mod, '_MODEL_SHA256_INT8',
                        hashlib.sha256(int8_blob).hexdigest())
    calls = []
    urls = []

    def _fake(req, timeout=60):
        urls.append(req.full_url)
        b = int8_blob if 'int8' in req.full_url else blob
        rng = req.get_header('Range')
        calls.append(rng)
        if rng:
            start = int(rng.split('=')[1].split('-')[0])
            return _Resp(b[start:], status=206)
        return _Resp(b, status=200)

    monkeypatch.setattr(mod, '_urlopen', _fake)
    _patch_agent_dirs(monkeypatch, d / 'bge_model')
    return {'dir': d, 'blob': blob, 'int8_blob': int8_blob, 'calls': calls, 'urls': urls}


@pytest.fixture
def fake_net_variants(fake_net, monkeypatch):
    """双变体假 HTTP——URL 含 int8 走 int8 包，其余走 fp32 包。"""
    int8_blob = _int8_tgz()
    monkeypatch.setattr(mod, '_MODEL_SHA256_INT8',
                        hashlib.sha256(int8_blob).hexdigest())

    def _fake(req, timeout=60):
        url = req.full_url
        fake_net['urls'].append(url)
        blob = int8_blob if 'int8' in url else fake_net['blob']
        rng = req.get_header('Range')
        fake_net['calls'].append(rng)
        if rng:
            start = int(rng.split('=')[1].split('-')[0])
            return _Resp(blob[start:], status=206)
        return _Resp(blob, status=200)

    monkeypatch.setattr(mod, '_urlopen', _fake)
    return {**fake_net, 'int8_blob': int8_blob}


def _invoke(*args):
    return CliRunner().invoke(cli, list(args))


def _pull_ok(env, fp32=False):
    r = _invoke('model', 'pull', '--fp32') if fp32 else _invoke('model', 'pull')
    assert r.exit_code == 0, r.output + str(r.exception or '')
    out = json.loads(r.output)
    assert out['status'] == 'ok', out
    return out


# ── pull 全流程 ───────────────────────────────────────────────────────────────

def test_pull_full_flow(fake_net):
    mdir = fake_net['dir'] / 'bge_model'
    out = _pull_ok(fake_net, fp32=True)
    d = out['data']
    assert d['installed'] is True and d['sha256']
    assert (mdir / 'config.json').exists()
    assert (mdir / 'onnx' / 'model_int8.onnx').exists()
    assert mod._is_complete(mdir)
    meta = json.loads((mdir / '.dp_install.json').read_text())
    assert meta['sha256'] == hashlib.sha256(fake_net['blob']).hexdigest()
    assert meta['archive_root'] == 'bge_v28_tuned'
    assert not (fake_net['dir'] / 'dpx-reranker.tar.gz.part').exists()


def test_pull_sha_mismatch_rejects(fake_net, monkeypatch):
    monkeypatch.setattr(mod, '_MODEL_SHA256', '0' * 64)
    r = _invoke('model', 'pull', '--fp32')
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
    _pull_ok(fake_net, fp32=True)
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
    _pull_ok(fake_net, fp32=True)
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


# ── pull --int8 变体 ─────────────────────────────────────────────────────────

def _pull_int8_ok():
    r = _invoke('model', 'pull', '--int8')
    assert r.exit_code == 0, r.output + str(r.exception or '')
    out = json.loads(r.output)
    assert out['status'] == 'ok', out
    return out


def test_pull_int8_uses_int8_asset(fake_net_variants):
    """--int8 走 int8 URL+sha——自包含包落位，meta 记 int8:true。"""
    mdir = fake_net_variants['dir'] / 'bge_model'
    out = _pull_int8_ok()
    d = out['data']
    assert d['installed'] is True and d['int8'] is True
    assert fake_net_variants['urls'] == [mod._MODEL_URL_INT8]
    assert d['sha256'] == hashlib.sha256(
        fake_net_variants['int8_blob']).hexdigest()
    # 自包含落位：onnx int8+tokenizer+config 就位，无 safetensors
    assert (mdir / 'onnx' / 'model_int8.onnx').exists()
    assert (mdir / 'tokenizer.json').exists()
    assert (mdir / 'tokenizer_config.json').exists()
    assert (mdir / 'config.json').exists()
    assert not (mdir / 'model.safetensors').exists()
    assert mod._is_complete(mdir)
    meta = json.loads((mdir / '.dp_install.json').read_text())
    assert meta['int8'] is True
    assert meta['url'] == mod._MODEL_URL_INT8
    assert not (fake_net_variants['dir'] /
                'dpx-reranker-int8.tar.gz.part').exists()


def test_pull_fp32_meta_int8_false(fake_net):
    """--fp32 pull 的 meta 显式记 int8:false——status 可区分变体。"""
    _pull_ok(fake_net, fp32=True)
    meta = json.loads(
        (fake_net['dir'] / 'bge_model' / '.dp_install.json').read_text())
    assert meta['int8'] is False
    assert meta['url'] == mod._MODEL_URL


def test_pull_int8_sha_mismatch_rejects(fake_net_variants, monkeypatch):
    monkeypatch.setattr(mod, '_MODEL_SHA256_INT8', '0' * 64)
    r = _invoke('model', 'pull', '--int8')
    assert r.exit_code == 1
    out = json.loads(r.output)
    assert out['code'] == 'MODEL_SHA_MISMATCH'
    assert not (fake_net_variants['dir'] /
                'dpx-reranker-int8.tar.gz.part').exists()
    assert not (fake_net_variants['dir'] / 'bge_model').exists()


def test_pull_int8_skips_when_fp32_installed(fake_net_variants):
    """fp32 完整在位 → --int8 幂等 skip（跨变体切换需 --force）。"""
    _pull_ok(fake_net_variants, fp32=True)
    assert fake_net_variants['urls'] == [mod._MODEL_URL]
    r = _invoke('model', 'pull', '--int8')
    out = json.loads(r.output)
    assert out['status'] == 'ok' and out['data']['skipped'] is True
    assert len(fake_net_variants['urls']) == 1      # 未发 int8 请求


def test_pull_fp32_skips_when_int8_installed(fake_net_variants):
    """对称：int8 在位 → 裸 pull 也 skip。"""
    _pull_int8_ok()
    r = _invoke('model', 'pull')
    out = json.loads(r.output)
    assert out['data']['skipped'] is True
    assert fake_net_variants['urls'] == [mod._MODEL_URL_INT8]


def test_pull_int8_force_overwrites_fp32(fake_net_variants):
    """--int8 --force：fp32 在位→备份换装 int8 包。"""
    _pull_ok(fake_net_variants, fp32=True)
    mdir = fake_net_variants['dir'] / 'bge_model'
    assert (mdir / 'model.safetensors').exists()
    r = _invoke('model', 'pull', '--int8', '--force')
    assert r.exit_code == 0, r.output
    out = json.loads(r.output)
    assert out['data']['installed'] is True
    assert out['data']['int8'] is True
    assert not (mdir / 'model.safetensors').exists()
    assert (mdir / 'onnx' / 'model_int8.onnx').exists()
    assert list(fake_net_variants['dir'].glob('bge_model.bak-*'))


def test_pull_int8_resume_uses_own_part(fake_net_variants):
    """int8 独立 .part 续传——与 fp32 半成品文件名不冲突。"""
    part = fake_net_variants['dir'] / 'dpx-reranker-int8.tar.gz.part'
    fake_net_variants['dir'].mkdir(parents=True, exist_ok=True)
    part.write_bytes(fake_net_variants['int8_blob'][:64])
    _pull_int8_ok()
    assert fake_net_variants['calls'] == ['bytes=64-']
    assert (fake_net_variants['dir'] / 'bge_model' / 'onnx' /
            'model_int8.onnx').exists()


def test_status_int8_marker_and_verified(fake_net_variants):
    """int8 安装后 status 回显 int8:true 且 sha256_verified 认 int8 sha。"""
    _pull_int8_ok()
    r = _invoke('model-status')
    assert r.exit_code == 0, r.output
    bge = json.loads(r.output)['data']['bge_model']
    assert bge['int8'] is True
    assert bge['sha256_verified'] is True
    assert bge['complete'] is True


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


# ── ort 加载链 / 喂参过滤（mock，不触真模型）────────────────────────────────

class _FakeInput:
    def __init__(self, name):
        self.name = name


class _FakeSess:
    """假 ORT InferenceSession——记录 run 收到的 feed 键。"""

    def __init__(self, inputs=('input_ids', 'attention_mask')):
        self._inputs = [_FakeInput(n) for n in inputs]
        self.feeds = []

    def get_inputs(self):
        return self._inputs

    def run(self, outs, feed):
        import numpy as np
        self.feeds.append(sorted(feed))
        n = len(next(iter(feed.values())))
        return [np.array([[1.0]] * n)]


class _FakeOrt:
    """假 onnxruntime 模块——可按文件名注入加载失败。"""

    def __init__(self, fail=()):
        self.fail = set(fail)
        self.calls = []

    def InferenceSession(self, path, providers=None):
        self.calls.append(Path(path).name)
        if Path(path).name in self.fail:
            raise RuntimeError('bad onnx')
        return _FakeSess()


class _FakeTok:
    def __call__(self, qs, ts, **kw):
        import numpy as np
        n = len(qs)
        return {'input_ids': np.zeros((n, 2)),
                'attention_mask': np.ones((n, 2))}


def _fake_transformers():
    m = types.ModuleType('transformers')

    class _AT:
        @staticmethod
        def from_pretrained(d):
            return _FakeTok()

    m.AutoTokenizer = _AT
    return m


def _fake_ce(calls):
    m = types.ModuleType('sentence_transformers')

    class _CE:
        def __init__(self, path, max_length=None):
            calls.append(path)

        def predict(self, pairs):
            return [0.1] * len(pairs)

    m.CrossEncoder = _CE
    return m


@pytest.fixture
def reset_reranker(monkeypatch):
    """reranker 单例/环境归零——跨测试不串状态。"""
    import dp_cli.agent as agent_mod
    monkeypatch.setattr(agent_mod, '_RERANKER', None)
    monkeypatch.setattr(agent_mod, '_RERANKER_FAM', None)
    monkeypatch.setattr(agent_mod, '_RERANKER_SRC', None)
    monkeypatch.delenv('DPX_BGE_ONNX', raising=False)
    return agent_mod


def _bge_dir(tmp_path, onnx=('model_int8.onnx',)):
    d = tmp_path / 'bge'
    (d / 'onnx').mkdir(parents=True)
    for c in onnx:
        (d / 'onnx' / c).write_text('x')
    (d / 'config.json').write_text('{}')
    (d / 'tokenizer.json').write_text('{}')
    return d


def test_ort_feed_filters_by_session_inputs(reset_reranker, monkeypatch):
    """_engine_reranker ort 分支按 sess.get_inputs() 动态喂参——
    tokenizer 多出 token_type_ids 不喂，session 不需要的键一律剔除
    （XLM-R tokenizer 无 token_type_ids 的反向：旧硬编码必 KeyError）。"""
    import numpy as np
    agent_mod = reset_reranker
    sess = _FakeSess(('input_ids', 'attention_mask'))

    class _Tok(_FakeTok):
        def __call__(self, qs, ts, **kw):
            enc = dict(super().__call__(qs, ts, **kw))
            enc['token_type_ids'] = np.zeros_like(enc['input_ids'])
            return enc

    monkeypatch.setattr(agent_mod, '_reranker_model',
                        lambda: ('ort', sess, _Tok()))
    st = {'options': [{'ref': i, 'role': 'button', 'name': f'b{i}'}
                      for i in range(3)]}
    r = agent_mod._engine_reranker(st, 'goal x')
    assert sess.feeds == [['attention_mask', 'input_ids']]
    assert r['backend'] == 'ort' and r['target'] is not None


def test_ort_feed_includes_token_type_ids_when_required(
        reset_reranker, monkeypatch):
    """session 声明要 token_type_ids（BERT 系导出）→ 照常喂入。"""
    agent_mod = reset_reranker
    sess = _FakeSess(('input_ids', 'attention_mask', 'token_type_ids'))

    class _Tok(_FakeTok):
        def __call__(self, qs, ts, **kw):
            import numpy as np
            enc = dict(super().__call__(qs, ts, **kw))
            enc['token_type_ids'] = np.zeros_like(enc['input_ids'])
            return enc

    monkeypatch.setattr(agent_mod, '_reranker_model',
                        lambda: ('ort', sess, _Tok()))
    st = {'options': [{'ref': i, 'role': 'button', 'name': f'b{i}'}
                      for i in range(3)]}
    agent_mod._engine_reranker(st, 'goal x')
    assert sess.feeds == [['attention_mask', 'input_ids', 'token_type_ids']]


def test_ort_int8_preferred_and_src_marked(reset_reranker, tmp_path,
                                         monkeypatch):
    """int8 优先实载——_RERANKER_SRC 记文件名供 model-status 回显。"""
    agent_mod = reset_reranker
    d = _bge_dir(tmp_path)
    fake_ort = _FakeOrt()
    monkeypatch.setattr(agent_mod, '_bge_dirs', lambda: [d])
    monkeypatch.setattr(agent_mod, '_ORT_MOD', fake_ort)
    monkeypatch.setitem(sys.modules, 'transformers', _fake_transformers())
    m = agent_mod._reranker_model()
    assert m[0] == 'ort'
    assert agent_mod._RERANKER_FAM == 'bge'
    assert agent_mod._RERANKER_SRC == 'model_int8.onnx'
    assert fake_ort.calls == ['model_int8.onnx']


def test_ort_int8_failure_falls_back_fp32(reset_reranker, tmp_path,
                                        monkeypatch):
    """int8 加载失败→同目录 fp32 model.onnx——不跳目录不跳 ST。"""
    agent_mod = reset_reranker
    d = _bge_dir(tmp_path, onnx=('model_int8.onnx', 'model.onnx'))
    fake_ort = _FakeOrt(fail={'model_int8.onnx'})
    monkeypatch.setattr(agent_mod, '_bge_dirs', lambda: [d])
    monkeypatch.setattr(agent_mod, '_ORT_MOD', fake_ort)
    monkeypatch.setitem(sys.modules, 'transformers', _fake_transformers())
    m = agent_mod._reranker_model()
    assert m[0] == 'ort'
    assert agent_mod._RERANKER_SRC == 'model.onnx'
    assert fake_ort.calls == ['model_int8.onnx', 'model.onnx']


def test_ort_all_failure_falls_back_st(reset_reranker, tmp_path,
                                     monkeypatch):
    """int8+fp32 全失败→降级 ST CrossEncoder（fam 仍 'bge'）。"""
    agent_mod = reset_reranker
    d = _bge_dir(tmp_path, onnx=('model_int8.onnx', 'model.onnx'))
    ce_calls = []
    monkeypatch.setattr(agent_mod, '_bge_dirs', lambda: [d])
    monkeypatch.setattr(agent_mod, '_ORT_MOD',
                        _FakeOrt(fail={'model_int8.onnx', 'model.onnx'}))
    monkeypatch.setitem(sys.modules, 'transformers', _fake_transformers())
    monkeypatch.setitem(sys.modules, 'sentence_transformers',
                        _fake_ce(ce_calls))
    m = agent_mod._reranker_model()
    assert m[0] == 'st' and agent_mod._RERANKER_FAM == 'bge'
    assert ce_calls == [str(d)]


def test_dpx_bge_onnx_zero_skips_ort(reset_reranker, tmp_path, monkeypatch):
    """DPX_BGE_ONNX=0 逃生开关——ORT 完全不碰直走 ST
    （对照 DPX_NO_LEXICAL 惯例）。"""
    agent_mod = reset_reranker
    d = _bge_dir(tmp_path)
    fake_ort = _FakeOrt()
    ce_calls = []
    monkeypatch.setattr(agent_mod, '_bge_dirs', lambda: [d])
    monkeypatch.setattr(agent_mod, '_ORT_MOD', fake_ort)
    monkeypatch.setitem(sys.modules, 'sentence_transformers',
                        _fake_ce(ce_calls))
    monkeypatch.setenv('DPX_BGE_ONNX', '0')
    m = agent_mod._reranker_model()
    assert m[0] == 'st' and agent_mod._RERANKER_FAM == 'bge'
    assert fake_ort.calls == []                    # ORT 一次没调
    assert ce_calls == [str(d)]
