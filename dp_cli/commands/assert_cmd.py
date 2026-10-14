# -*- coding:utf-8 -*-
"""断言命令: assert-text / assert-url / assert-visible / assert-count

面向脚本与 agent 自检闭环：断言失败输出 JSON error 并以非零码退出，
可直接用于 `&&` 串联或 CI 检查。
"""
import click

from dp_cli.output import ok, error
from dp_cli.commands._utils import (session_option, frame_option,
                                    _get_page, resolve_locator, frame_ctx)


def _is_missing(ele) -> bool:
    return not ele or ele.__class__.__name__ == 'NoneElement'


def register(cli):

    @cli.command('assert-text')
    @click.argument('text')
    @session_option
    @click.option('--locator', default=None, help='限定断言范围到指定元素')
    @click.option('--exact', is_flag=True, help='精确匹配（默认包含匹配；建议配合 --locator）')
    @frame_option
    @click.option('--timeout', default=5, type=float, help='元素等待超时秒数', show_default=True)
    def assert_text(text, session, locator, exact, frame, timeout):
        """断言页面或指定元素包含文本。

        \b
        示例:
          dp assert-text "登录成功"
          dp assert-text "订单已提交" --locator "#msg"
          dp assert-text "Hello" --locator "#title" --exact
        """
        page = _get_page(session)
        ctx = frame_ctx(page, frame, session)
        try:
            if locator:
                real_loc = resolve_locator(locator, session)
                ele = ctx.ele(real_loc, timeout=timeout)
                if _is_missing(ele):
                    error(f'断言失败：元素不存在 {real_loc}', code='ASSERT_FAILED')
                    return
                haystack = (ele.raw_text or '').strip()
                scope = f'元素 {real_loc}'
            else:
                haystack = page.html or ''
                scope = '页面'
            matched = haystack == text if exact else text in haystack
            if matched:
                ok({'text': text, 'scope': scope, 'exact': exact}, msg='断言通过')
            else:
                error(f'断言失败：{scope}不包含文本 "{text}"', code='ASSERT_FAILED',
                      detail=(haystack[:200] if haystack else '(空)'))
        except Exception as e:
            error('断言执行失败', code='ASSERT_FAILED', detail=str(e))

    @cli.command('assert-url')
    @click.argument('text')
    @session_option
    @click.option('--exact', is_flag=True, help='精确匹配（默认包含匹配）')
    @click.option('--title', 'check_title', is_flag=True, help='断言标题而非 URL')
    def assert_url(text, session, exact, check_title):
        """断言当前 URL（或 --title 断言标题）包含文本。

        \b
        示例:
          dp assert-url "dashboard"
          dp assert-url "https://example.com/home" --exact
          dp assert-url "搜索结果" --title
        """
        page = _get_page(session)
        try:
            field = 'title' if check_title else 'url'
            actual = page.title if check_title else page.url
            actual = actual or ''
            matched = actual == text if exact else text in actual
            if matched:
                ok({'field': field, 'actual': actual, 'exact': exact}, msg='断言通过')
            else:
                error(f'断言失败：{field} 不包含 "{text}"', code='ASSERT_FAILED',
                      detail=f'实际值: {actual[:200]}')
        except Exception as e:
            error('断言执行失败', code='ASSERT_FAILED', detail=str(e))

    @cli.command('assert-visible')
    @click.argument('locator')
    @session_option
    @click.option('--hidden', 'expect_hidden', is_flag=True,
                  help='断言元素不可见或不存在')
    @frame_option
    @click.option('--timeout', default=5, type=float, help='元素等待超时秒数', show_default=True)
    def assert_visible(locator, session, expect_hidden, frame, timeout):
        """断言元素可见（--hidden 断言不可见/不存在）。

        \b
        示例:
          dp assert-visible "#result"
          dp assert-visible "ref:5"
          dp assert-visible ".loading" --hidden   # 等待加载指示器消失后断言
        """
        page = _get_page(session)
        ctx = frame_ctx(page, frame, session)
        try:
            real_loc = resolve_locator(locator, session)
            ele = ctx.ele(real_loc, timeout=timeout)
            missing = _is_missing(ele)
            visible = False if missing else bool(ele.states.is_displayed)
            if expect_hidden:
                if visible:
                    error(f'断言失败：元素仍可见 {real_loc}', code='ASSERT_FAILED')
                else:
                    ok({'locator': real_loc, 'hidden': True}, msg='断言通过：元素不可见')
            elif missing:
                error(f'断言失败：元素不存在 {real_loc}', code='ASSERT_FAILED')
            elif not visible:
                error(f'断言失败：元素存在但不可见 {real_loc}', code='ASSERT_FAILED')
            else:
                ok({'locator': real_loc}, msg='断言通过：元素可见')
        except Exception as e:
            error('断言执行失败', code='ASSERT_FAILED', detail=str(e))

    @cli.command('assert-count')
    @click.argument('locator')
    @click.argument('expected', required=False, type=int, default=None)
    @session_option
    @click.option('--min', 'min_count', type=int, default=None, help='最小数量（含）')
    @click.option('--max', 'max_count', type=int, default=None, help='最大数量（含）')
    @frame_option
    @click.option('--timeout', default=0, type=float,
                  help='等待元素出现的超时秒数（默认 0 即时返回）', show_default=True)
    def assert_count(locator, expected, session, min_count, max_count, frame, timeout):
        """断言匹配元素的数量。

        \b
        示例:
          dp assert-count ".item" 20          # 恰好 20 个
          dp assert-count ".item" --min 1     # 至少 1 个
          dp assert-count ".error" 0          # 无错误元素
          dp assert-count "li" --min 3 --max 10
        """
        if expected is not None and (min_count is not None
                                     or max_count is not None):
            error('位置参数 expected 与 --min/--max 互斥，请二选一',
                  code='INVALID_ARGS')
            return
        if expected is None and min_count is None and max_count is None:
            error('请指定期望数量或 --min/--max', code='INVALID_ARGS')
            return
        page = _get_page(session)
        try:
            ctx = frame_ctx(page, frame, session)
            real_loc = resolve_locator(locator, session)
            eles = ctx.eles(real_loc, timeout=timeout)
            n = len(eles)
            if expected is not None:
                passed = n == expected
                desc = f'期望 {expected} 个，实际 {n} 个'
            else:
                lo = min_count if min_count is not None else 0
                hi = max_count if max_count is not None else n
                passed = lo <= n <= hi
                lo_s = str(min_count) if min_count is not None else '-∞'
                hi_s = str(max_count) if max_count is not None else '+∞'
                desc = f'期望 [{lo_s}, {hi_s}]，实际 {n} 个'
            if passed:
                ok({'locator': real_loc, 'count': n}, msg='断言通过')
            else:
                error(f'断言失败：{desc}', code='ASSERT_FAILED')
        except Exception as e:
            error('断言执行失败', code='ASSERT_FAILED', detail=str(e))
