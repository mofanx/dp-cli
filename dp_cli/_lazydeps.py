# -*- coding:utf-8 -*-
"""DataRecorder/DrissionRecord 懒代理——削减 `import DrissionPage` 的 openpyxl 税。

实测 `import DrissionPage` ≈325ms，其中 ~200ms 是记录器包→
openpyxl→numpy 链（Excel 写出），而 DrissionPage/DownloadKit 只用到
tools.py 里两个纯 stdlib 函数（make_valid_name / get_usable_path）；
Recorder/ByteRecorder 等记录器类仅在实际下载任务时才实例化。

上游 4.1.1.3+ 将 ``DataRecorder`` 包改名为 ``DrissionRecord``，
结构与 API 相同——本模块按候选名集合处理，装哪个桩哪个。

机制（install() 在 dp_cli/__init__.py 以 µs 级成本安装）：

1. ``sys.modules['<pkg>']`` 放桩包，``__path__`` 指向真实目录
   ——``from <pkg>.xxx import y`` 仍按需解析真文件；
2. ``sys.modules['<pkg>.tools']`` 放 vendored 模块，覆盖
   make_valid_name/get_usable_path；未覆盖的名字经 ``__getattr__``
   触发自愈回载真模块；
3. 桩包 ``__getattr__`` 对 Recorder/ByteRecorder/Filler/DBRecorder
   等导出返回懒代理类，实例化/取类属性时才 heal 回载真实现；
4. 首个 ``import DrissionPage`` 由 meta_path finder 套自愈 loader：
   exec 抛错 → 撤销桩 → 重试真实 import（上游升级最坏退回慢路径）。

逃生通道：``DP_CLI_NO_STUB=1`` 完全禁用，走原生 import。
"""
import os
import sys
from importlib import import_module
from importlib.util import find_spec
from pathlib import Path
from re import search, sub
from types import ModuleType

# 上游包名候选：4.1.1.3+ 为 DrissionRecord，更早为 DataRecorder
_CANDIDATES = ('DrissionRecord', 'DataRecorder')
_PROXY_NAMES = frozenset({
    'Recorder', 'ByteRecorder', 'Filler', 'DBRecorder', 'MapRecorder'})
# 已安装的桩名（含 <pkg>.tools），heal 时统一撤销
_STUBBED = set()


def _heal():
    """撤销全部桩，后续 import 走真实模块。"""
    for name in _STUBBED:
        sys.modules.pop(name, None)
    _STUBBED.clear()


def _real_attr(pkg_name, name):
    _heal()
    mod = import_module(pkg_name)
    return getattr(mod, name)


class _LazyMeta(type):
    def __getattr__(cls, item):
        return getattr(_real_attr(cls._pkg_name, cls.__name__), item)


def _lazy_class(pkg_name, name):
    """懒代理类：仅在被实例化或访问类属性时才回载真实现。"""
    def _new(cls, *a, **kw):
        return _real_attr(pkg_name, name)(*a, **kw)
    return _LazyMeta(name, (), {
        '__new__': staticmethod(_new), '_pkg_name': pkg_name})


def _make_pkg_getattr(pkg_name):
    def _getattr(name):
        if name.startswith('_'):
            # dunder 探测（__all__/__path__ 等）是 import 机制的例行检查，
            # 绝不触发回载，否则桩形同虚设
            raise AttributeError(name)
        if name in _PROXY_NAMES:
            return _lazy_class(pkg_name, name)
        # 其他名字（__version__、未来新增导出等）按需回载真模块
        return _real_attr(pkg_name, name)
    return _getattr


# ── vendored 自 <pkg>.tools（纯 stdlib 实现，行为需与原版一致）──

def get_long(txt):
    """返回字符串中字符个数（一个汉字算 2 个字符）"""
    txt_len = len(txt)
    return int((len(txt.encode('utf-8')) - txt_len) / 2 + txt_len)


def make_valid_name(full_name):
    """获取有效的文件名"""
    full_name = full_name.strip()
    if search(r'[<>/\\|:*?\n"]', full_name):
        full_name = sub(r'<', '＜', full_name)
        full_name = sub(r'>', '＞', full_name)
        full_name = sub(r'/', '／', full_name)
        full_name = sub(r'\\', '＼', full_name)
        full_name = sub(r'\|', '｜', full_name)
        full_name = sub(r':', '：', full_name)
        full_name = sub(r'\*', '＊', full_name)
        full_name = sub(r'\?', '？', full_name)
        full_name = sub(r'\n', '', full_name)
        full_name = sub(r'"(.*?)"', r'“\1”', full_name)
        full_name = sub(r'"', '“', full_name)

    r = search(r'(.*)(\.[^.]+$)', full_name)
    if r:
        name, ext = r.group(1), r.group(2)
        ext_long = len(ext)
    else:
        name, ext = full_name, ''
        ext_long = 0

    while get_long(name) > 255 - ext_long:
        name = name[:-1]

    return f'{name}{ext}'.rstrip('.')


def get_usable_path(path, is_file=True, parents=True):
    """检查文件或文件夹是否有重名，并返回可以使用的路径"""
    path = Path(path)
    parent = path.parent
    if parents:
        parent.mkdir(parents=True, exist_ok=True)
    path = parent / make_valid_name(path.name)
    name = path.stem if path.is_file() else path.name
    ext = path.suffix if path.is_file() else ''

    first_time = True
    while path.exists() and path.is_file() == is_file:
        r = search(r'(.*)_(\d+)$', name)
        if not r or (r and first_time):
            src_name, num = name, '1'
        else:
            src_name, num = r.group(1), int(r.group(2)) + 1
        name = f'{src_name}_{num}'
        path = parent / f'{name}{ext}'
        first_time = None
    return path


def _build_tools_stub(pkg_name):
    tools = ModuleType(f'{pkg_name}.tools')
    tools.make_valid_name = make_valid_name
    tools.get_usable_path = get_usable_path
    tools.get_long = get_long

    def _missing(name):
        if name.startswith('_'):
            raise AttributeError(name)
        _heal()
        real_tools = import_module(f'{pkg_name}.tools')
        return getattr(real_tools, name)
    tools.__getattr__ = _missing
    return tools


class _HealLoader:
    """包裹真实 loader：exec 失败→撤销桩后重试真实 import。"""

    def __init__(self, real_loader):
        self._real = real_loader

    def create_module(self, spec):
        fn = getattr(self._real, 'create_module', None)
        return fn(spec) if fn else None

    def exec_module(self, module):
        try:
            self._real.exec_module(module)
        except Exception:
            _heal()
            self._real.exec_module(module)


class _HealFinder:
    """拦截首个顶层 `import DrissionPage`（仅包本体），套自愈 loader。"""

    def find_spec(self, fullname, path=None, target=None):
        if fullname != 'DrissionPage' or path is not None:
            return None
        try:
            sys.meta_path.remove(self)
        except ValueError:
            pass
        spec = find_spec(fullname)
        if spec is None or spec.loader is None:
            return None
        spec.loader = _HealLoader(spec.loader)
        return spec


def install():
    """安装记录器包桩与 DrissionPage 自愈 finder。幂等、零副作用。

    遍历候选包名（DrissionRecord/DataRecorder），存在的都装桩。
    条件不满足（真模块已加载/未安装/环境变量禁用）时静默跳过。
    """
    if os.environ.get('DP_CLI_NO_STUB'):
        return
    installed = False
    for pkg_name in _CANDIDATES:
        if pkg_name in sys.modules:
            continue
        spec = find_spec(pkg_name)
        if spec is None or not spec.submodule_search_locations:
            continue
        pkg = ModuleType(pkg_name)
        pkg.__path__ = list(spec.submodule_search_locations)
        pkg.__spec__ = spec
        pkg.tools = _build_tools_stub(pkg_name)
        pkg.__getattr__ = _make_pkg_getattr(pkg_name)
        sys.modules[pkg_name] = pkg
        sys.modules[f'{pkg_name}.tools'] = pkg.tools
        _STUBBED.update((pkg_name, f'{pkg_name}.tools'))
        installed = True
    if installed:
        # meta_path 头部插入——append 会被默认 PathFinder 抢先命中
        sys.meta_path.insert(0, _HealFinder())
