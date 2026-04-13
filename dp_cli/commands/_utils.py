# -*- coding:utf-8 -*-
"""所有命令模块共享的工具函数和装饰器"""
import io
import csv
import click

from dp_cli.session import get_browser, load_refs
from dp_cli.output import error


def session_option(f):
    return click.option('-s', '--session', default='default',
                        help='会话名称，默认 default', show_default=True)(f)


def _get_page(session: str):
    """获取页面对象，失败则 error 退出"""
    try:
        return get_browser(session)
    except Exception as e:
        error(f'无法连接浏览器会话 [{session}]，请先执行 dp open',
              code='SESSION_NOT_FOUND', detail=str(e))


def resolve_locator(locator: str, session: str = 'default') -> str:
    """解析定位器，支持 ref:N 语法。

    如果 locator 以 'ref:' 开头，从 session 的 refs 映射中查找真实定位器。
    否则原样返回。
    """
    if not locator.startswith('ref:'):
        return locator

    ref_id = locator[4:]
    refs = load_refs(session)
    if not refs:
        error(f'没有可用的 ref 映射，请先执行 dp snapshot',
              code='NO_REFS')
        raise SystemExit(1)

    ref_data = refs.get(ref_id)
    if not ref_data:
        available = sorted(refs.keys(), key=lambda x: int(x) if x.isdigit() else 0)
        hint = f"可用范围: ref:1 ~ ref:{available[-1]}" if available else ""
        error(f'ref:{ref_id} 不存在。{hint}',
              code='REF_NOT_FOUND')
        raise SystemExit(1)

    real_loc = ref_data.get('locator')
    if real_loc and not real_loc.startswith('t:'):
        return real_loc

    # locator 不可用时（如 t:p），尝试用 name 作为 text 定位器
    name = ref_data.get('name', '')
    if name and len(name) <= 50:
        return f'text:{name}'

    error(f'ref:{ref_id} 无法解析为有效定位器 (role={ref_data.get("role")})',
          code='REF_UNRESOLVABLE')
    raise SystemExit(1)


def records_to_csv(records: list) -> str:
    """将记录列表转为 CSV 字符串（含 BOM，Excel 直接打开不乱码）"""
    if not records:
        return ''
    fields = list(records[0].keys())
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fields, extrasaction='ignore',
                            lineterminator='\n')
    writer.writeheader()
    for row in records:
        clean = {k: ('|'.join(str(i) for i in v) if isinstance(v, list) else v)
                 for k, v in row.items()}
        writer.writerow(clean)
    return buf.getvalue()
