# -*- coding:utf-8 -*-
"""所有命令模块共享的工具函数和装饰器"""
import io
import csv
import click

from dp_cli.session import get_browser
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
