# -*- coding:utf-8 -*-
"""模型管理命令: model pull / model-status

bge reranker 走 GitHub Release 资产分发（urllib 直连，不依赖 gh CLI）：
- HTTP Range 断点续传（.part 临时文件，失败重跑自动续传）
- sha256 硬校验——不一致即删 .part 拒绝安装
- 幂等部署：已完整则 skip；存在但残缺则备份后重装
"""
import hashlib
import io
import json
import os
import shutil
import sys
import tarfile
import tempfile
import time
import urllib.request
from pathlib import Path, PurePosixPath

import click

from dp_cli.output import ok, error, debug, _write_err

_MODEL_URL = ('https://github.com/mofanx/dp-cli/releases/download/'
              'model-v28/dpx-reranker-v28.tar.gz')
_MODEL_SHA256 = ('e802da9ef2c34f7eb5ea5fdd4a8a69fb5b49497c5'
                 'c5dc049994a470a38e6a4ae')
# int8-only 变体：同 release 第二 asset，自包含 tokenizer——
# 落位后 onnx/model_int8.onnx 被 _reranker_model ORT 分支首选
_MODEL_URL_INT8 = ('https://github.com/mofanx/dp-cli/releases/download/'
                   'model-v28/dpx-reranker-v28-int8.tar.gz')
_MODEL_SHA256_INT8 = ('af8b6ba1f31d98548e60edca031a5670047b'
                      '9d43ede96ba4b91dfcbb5f98dce2')
_ARCHIVE_ROOT = 'bge_v28_tuned'        # tar 内顶层目录名
_CHUNK = 4 * 1024 * 1024               # 4MB 读块
_HTTP_TIMEOUT = 60                     # 单请求超时（含重定向后的数据流）


# ── 惰性路径 seam（测试可 monkeypatch；同 daemon._dir 教训——import 期不固化）──
def _dp_dir() -> Path:
    return Path.home() / '.dp_cli'


def _model_dir() -> Path:
    return _dp_dir() / 'bge_model'


def _decide_dir() -> Path:
    return _dp_dir() / 'decide_model'


def _variant(int8: bool):
    """(url, sha256, part 文件名)——两变体独立 .part 防交叉续传混字节。"""
    if int8:
        return (_MODEL_URL_INT8, _MODEL_SHA256_INT8,
                'dpx-reranker-int8.tar.gz.part')
    return _MODEL_URL, _MODEL_SHA256, 'dpx-reranker.tar.gz.part'


def _part_file(int8: bool = False) -> Path:
    return _dp_dir() / _variant(int8)[2]


def _meta_file() -> Path:
    return _model_dir() / '.dp_install.json'


def _urlopen(req, timeout=60):
    """urllib.request.urlopen seam——测试注入假响应，真机走 https+重定向。"""
    return urllib.request.urlopen(req, timeout=timeout)


# ── 完整性判据 ────────────────────────────────────────────────────────────────
def _is_complete(d: Path) -> bool:
    """bge 目录完整性：config+tokenizer+至少一个权重载体（ST/ONNX）。"""
    if not d.is_dir():
        return False
    if not (d / 'config.json').exists() or not (d / 'tokenizer.json').exists():
        return False
    if (d / 'model.safetensors').exists():
        return True
    return any((d / 'onnx' / c).exists()
               for c in ('model_int8.onnx', 'model.onnx'))


def _dir_stats(d: Path) -> dict:
    """文件数+总字节（目录不存在时全零）。"""
    files, size = 0, 0
    if d.is_dir():
        for p in d.rglob('*'):
            if p.is_file():
                files += 1
                try:
                    size += p.stat().st_size
                except OSError:
                    pass
    return {'files': files, 'size_bytes': size}


def _sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, 'rb') as f:
        for chunk in iter(lambda: f.read(_CHUNK), b''):
            h.update(chunk)
    return h.hexdigest()


# ── 下载（Range 续传）────────────────────────────────────────────────────────
def _http_get(url: str, range_from: int = 0):
    headers = {'User-Agent': 'dp-cli/model-pull'}
    if range_from > 0:
        headers['Range'] = f'bytes={range_from}-'
    req = urllib.request.Request(url, headers=headers)
    return _urlopen(req, timeout=_HTTP_TIMEOUT)


def _resp_status(resp) -> int:
    s = getattr(resp, 'status', None)
    if s is None:
        try:
            s = resp.getcode()
        except Exception:
            s = None
    return int(s or 0)


def _download(url: str, dest: Path) -> int:
    """下载到 dest（.part 续传），返回总字节数。stderr 打进度。

    续传语义：dest 已存在时发 Range 请求；服务端回 206 追加写，
    回 200（忽略 Range）则从头覆写。非 2xx 抛错由调用方兜。
    """
    pos = dest.stat().st_size if dest.exists() else 0
    resp = _http_get(url, range_from=pos)
    status = _resp_status(resp)
    if status == 416:                          # Range 越界——.part 即完整文件
        return pos
    if status and status >= 400:
        raise RuntimeError(f'HTTP {status}')
    resumed = pos > 0 and status == 206
    mode = 'ab' if resumed else 'wb'
    if pos > 0 and not resumed:
        debug('服务端未接受 Range（status=%s），从头下载', status)
        pos = 0
    total = None
    try:
        cl = resp.headers.get('Content-Length')
        if cl is not None:
            total = int(cl) + pos
    except Exception:
        total = None
    got = pos
    t0 = last = time.monotonic()
    with open(dest, mode) as f:
        while True:
            buf = resp.read(_CHUNK)
            if not buf:
                break
            f.write(buf)
            got += len(buf)
            now = time.monotonic()
            if now - last >= 0.5:               # 进度节流——TTY/pipe 都不刷屏
                last = now
                _write_err(_progress_line(got, total, t0))
    _write_err(_progress_line(got, total, t0, end='\n'))
    return got


def _progress_line(got: int, total, t0: float, end: str = '\r') -> str:
    mb = got / 1048576
    el = max(time.monotonic() - t0, 1e-6)
    rate = mb / el
    if total:
        pct = got / total * 100
        return (f'[dp-cli] 下载模型 {pct:5.1f}% '
                f'({mb:.0f}/{total / 1048576:.0f} MB, {rate:.1f} MB/s){end}')
    return f'[dp-cli] 下载模型 {mb:.0f} MB ({rate:.1f} MB/s){end}'


# ── 解压（防 traversal）───────────────────────────────────────────────────────
def _safe_members(tar: tarfile.TarFile):
    for m in tar.getmembers():
        parts = PurePosixPath(m.name).parts
        if m.name.startswith('/') or '..' in parts:
            raise RuntimeError(f'tar 成员路径越界: {m.name}')
        if m.islnk() or m.issym() or m.isdev():
            continue                          # 链接/设备节点不落地
        yield m


def _extract(tgz: Path, dst: Path) -> None:
    with tarfile.open(tgz, 'r:gz') as tar:
        members = list(_safe_members(tar))
        try:
            tar.extractall(dst, members=members, filter='data')
        except TypeError:                     # py<3.12 无 filter 参数
            tar.extractall(dst, members=members)


def _find_model_src(extract_root: Path) -> Path:
    """定位解压产物中的模型目录——优先约定名，否则取唯一顶层目录。"""
    cand = extract_root / _ARCHIVE_ROOT
    if cand.is_dir():
        return cand
    tops = [p for p in extract_root.iterdir() if p.is_dir()]
    if len(tops) == 1:
        return tops[0]
    raise RuntimeError(f'解压产物中未找到 {_ARCHIVE_ROOT}/ 顶层目录')


# ── 安装（幂等+残缺备份）─────────────────────────────────────────────────────
def _install(src: Path, model_dir: Path, force: bool = False,
             int8: bool = False) -> dict:
    """src mv → model_dir。返回 {installed, skipped, backup?}。

    model_dir 完整且非 force → 幂等 skip；残缺（或 force）→
    改名备份为 bge_model.bak-<ts> 后重装。
    """
    if _is_complete(model_dir) and not force:
        return {'installed': False, 'skipped': True}
    url, sha256, _ = _variant(int8)
    backup = None
    if model_dir.exists():
        backup = model_dir.with_name(
            f'{model_dir.name}.bak-{time.strftime("%Y%m%d-%H%M%S")}')
        i = 1
        while backup.exists():
            backup = backup.with_name(f'{model_dir.name}.bak-{i}')
            i += 1
        shutil.move(str(model_dir), str(backup))
    shutil.move(str(src), str(model_dir))
    st = _dir_stats(model_dir)
    meta = {'url': url, 'archive_root': _ARCHIVE_ROOT,
            'sha256': sha256, 'int8': int8,
            'installed_at': time.strftime('%Y-%m-%dT%H:%M:%S%z'),
            **st}
    try:
        _meta_file().write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding='utf-8')
    except OSError as e:
        debug('安装元信息写盘失败（不影响模型本体）', e)
    out = {'installed': True, 'skipped': False, **st}
    if backup is not None:
        out['backup'] = str(backup)
    return out


# ── 引擎探测链（不实际加载——回显 decide 会命中哪个候选）─────────────────────
def _probe_engine() -> dict:
    from dp_cli import agent
    chain, selected = [], None
    for d in agent._bge_dirs():
        d = Path(d)
        onnx = [c for c in ('model_int8.onnx', 'model.onnx')
                if (d / 'onnx' / c).exists()]
        has_cfg = (d / 'config.json').exists()
        has_tok = (d / 'tokenizer.json').exists()
        # ort 分支需 onnx 权重+tokenizer；ST 分支需 config+tokenizer
        loadable = has_tok and (bool(onnx) or has_cfg)
        chain.append({'path': str(d), 'exists': d.is_dir(),
                      'onnx': onnx, 'config': has_cfg,
                      'loadable': loadable})
        if selected is None and loadable:
            selected = str(d)
    ort = agent._ort()
    fam = 'bge' if selected else 'minilm'
    return {'family': fam, 'selected': selected,
            'ort_available': bool(ort), 'probe_chain': chain}


def _decide_status() -> dict:
    d = _decide_dir()
    st = _dir_stats(d)
    return {'path': str(d), 'exists': d.is_dir(), **st,
            'onnx': any((d / 'onnx' / c).exists()
                        for c in ('model_int8.onnx', 'model.onnx')),
            'config': (d / 'config.json').exists()}


def _bge_status() -> dict:
    d = _model_dir()
    st = _dir_stats(d)
    meta = None
    mf = _meta_file()
    if mf.is_file():
        try:
            meta = json.loads(mf.read_text(encoding='utf-8'))
        except Exception:
            meta = {'_error': 'meta 解析失败'}
    verified = None
    if isinstance(meta, dict) and meta.get('sha256'):
        verified = meta['sha256'] in (_MODEL_SHA256, _MODEL_SHA256_INT8)
    int8 = meta.get('int8') if isinstance(meta, dict) else None
    return {'path': str(d), 'exists': d.is_dir(),
            'complete': _is_complete(d), **st, 'int8': int8,
            'sha256_verified': verified, 'meta': meta}


def register(cli):

    @cli.group('model')
    def model_grp():
        """decide 引擎模型管理（bge reranker 下载/部署）。"""

    @model_grp.command('pull')
    @click.option('--force', is_flag=True, default=False,
                  help='忽略已有完整安装，强制重新下载部署')
    @click.option('--int8/--fp32', 'int8', default=True,
                  help='默认 int8-only 包（~210MB，eval-120 同分、延迟 -71%）；'
                       '--fp32 拉全量权重（~941MB，top1 逐例严格无损）')
    def model_pull(force, int8):
        """从 GitHub Release 下载 bge reranker 并部署到 ~/.dp_cli/bge_model。

        sha256 硬校验+Range 断点续传（.part 文件残留即自动续传）；
        已完整安装则幂等 skip（任一变体在位即 skip，跨变体需 --force），
        残缺安装自动备份后重装。--int8 选自包含 int8-only 资产。

        \b
        示例:
          dpx model pull            # int8（默认）
          dpx model pull --fp32     # 全量 fp32
          dpx model pull --force    # 跨变体换装
        """
        mdir = _model_dir()
        if _is_complete(mdir) and not force:
            ok({'skipped': True, 'path': str(mdir),
                **_dir_stats(mdir)},
               msg='模型已完整安装——跳过下载（--force 强制重装）')
            return
        url, sha256, _ = _variant(int8)
        _dp_dir().mkdir(parents=True, exist_ok=True)
        part = _part_file(int8)
        t0 = time.monotonic()
        try:
            got = _download(url, part)
        except Exception as e:
            error('模型下载失败——.part 已保留可断点续传，重跑 model pull 继续',
                  code='MODEL_DOWNLOAD_FAILED', detail=str(e))
            return
        elapsed = time.monotonic() - t0
        digest = _sha256_file(part)
        if digest != sha256:
            try:
                part.unlink()
            except OSError:
                pass
            error('模型 sha256 校验不一致——已删除临时文件',
                  code='MODEL_SHA_MISMATCH',
                  detail=f'expected {sha256}, got {digest}')
            return
        tmp_root = Path(tempfile.mkdtemp(prefix='.model-extract-',
                                         dir=str(_dp_dir())))
        try:
            _extract(part, tmp_root)
            src = _find_model_src(tmp_root)
            if not _is_complete(src):
                raise RuntimeError(
                    f'解压产物不完整（缺 config/tokenizer/权重）: {src}')
            inst = _install(src, mdir, force=force, int8=int8)
        except Exception as e:
            error('模型解压/安装失败', code='MODEL_INSTALL_FAILED',
                  detail=str(e))
            return
        finally:
            shutil.rmtree(tmp_root, ignore_errors=True)
        try:
            part.unlink()                        # 校验通过才清 .part
        except OSError:
            pass
        ok({'path': str(mdir), 'bytes': got, 'int8': int8,
            'elapsed_sec': round(elapsed, 1), 'sha256': digest, **inst},
           msg='模型已部署' if inst.get('installed') else '模型已存在')

    @cli.command('model-status')
    @click.option('--load', 'do_load', is_flag=True, default=False,
                  help='实际加载 reranker（慢，校验 agent._RERANKER_FAM）')
    def model_status(do_load):
        """输出模型部署状态：bge_model/引擎探测链/decide_model MiniLM。

        \b
        示例:
          dpx model-status           # 轻量探测（不加载模型）
          dpx model-status --load    # 实际加载验证 _RERANKER_FAM
        """
        engine = _probe_engine()
        if do_load:
            from dp_cli import agent
            m = agent._reranker_model()
            if isinstance(m, dict):
                engine['loaded'] = {'error': m.get('_error')}
            else:
                engine['loaded'] = {'kind': m[0],
                                    'family': agent._RERANKER_FAM,
                                    'src': agent._RERANKER_SRC}
        ok({'bge_model': _bge_status(), 'engine': engine,
            'decide_model': _decide_status()})
