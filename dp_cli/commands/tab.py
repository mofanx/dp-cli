# -*- coding:utf-8 -*-
"""标签页管理命令: tab-list / tab-new / tab-select / tab-close
及等价子命令组 dpx tab list|new|select|close"""
import click

from dp_cli.output import ok, error, debug
from dp_cli.session import load_session, save_session
from dp_cli.commands._utils import (
    session_option, _get_page, normalize_url, _bind_tab,
    _rebind_from_history)


def _impl_tab_list(session):
    """列出所有标签页。绑定的标签页会显示 [pinned] 标记，
    顶层 pinned 输出 index（可直接喂 tab-select N），pinned_id 保留全量 id。"""
    page = _get_page(session, raw=True)
    if not page:
        return
    try:
        sess = load_session(session)
        pinned_id = sess.get('active_tab')
        tabs = []
        pinned_index = None
        for i, tab_id in enumerate(page.tab_ids):
            tab = page.get_tab(tab_id)
            entry = {
                'index': i,
                'id': tab_id,
                'url': tab.url,
                'title': tab.title,
            }
            if tab_id == pinned_id:
                entry['pinned'] = True
                pinned_index = i
            tabs.append(entry)
        ok({'tabs': tabs, 'count': len(tabs),
            'pinned': pinned_index,
            'pinned_id': pinned_id})
    except Exception as e:
        error(f'获取标签页列表失败', code='TAB_FAILED', detail=str(e))


def _impl_tab_new(url, session, background, foreground, new_window):
    """新建标签页并自动绑定。

    默认后台打开（不抢占用户前台）并自动绑定，后续 dpx 命令在
    该标签页中执行——自动化操作不干扰用户后续手动浏览的页面。
    --foreground 显式要求前台激活；--background 后台打开且不绑定；
    --new-window 新建浏览器窗口（仅用户明确要求隔离时使用）。
    """
    if background and foreground:
        error('--background 与 --foreground 互斥', code='INVALID_ARGS')
        return
    page = _get_page(session, raw=True)
    if not page:
        return
    try:
        new_tab = page.new_tab(url=normalize_url(url) if url else '',
                               new_window=new_window,
                               background=not foreground)
        new_tid = new_tab.tab_id
        msg = '新标签页已创建'

        if not background:
            sess = load_session(session)
            _bind_tab(sess, new_tid)
            save_session(session, sess)
            msg += '（已绑定，dpx 命令将在此标签页执行）'

        ok({'id': new_tid, 'url': new_tab.url,
            'title': new_tab.title, 'pinned': not background},
           msg=msg)
    except Exception as e:
        error(f'创建标签页失败', code='TAB_FAILED', detail=str(e))


def _impl_tab_select(target, index, session):
    """绑定到指定标签页（无参=浏览器当前激活页）。"""
    # --index 是序号位置参数别名——用户/agent 自然猜旗形态
    # （真机 'dpx tab-select --index 1' Usage 报错归因）。
    # 两者同给必须一致；target 非纯数字时与 --index 语义冲突
    if index is not None:
        if target is None:
            target = str(index)
        else:
            try:
                consistent = int(target) == index
            except ValueError:
                consistent = False
            if not consistent:
                error(f'TARGET ({target!r}) 与 --index {index} 不一致——'
                      '同义参数二选一即可',
                      code='INVALID_ARGS')
                return
    # 无参 → 绑定浏览器当前激活页（latest_tab）
    bind_current = target is None

    # 解除绑定
    if target is not None and target.lower() == 'none':
        sess = load_session(session)
        old = sess.pop('active_tab', None)
        save_session(session, sess)
        if old:
            ok(msg='已解除标签页绑定，后续命令将在浏览器活跃标签页执行')
        else:
            ok(msg='当前没有绑定的标签页')
        return

    page = _get_page(session, raw=True)
    if not page:
        return
    try:
        if bind_current:
            lt = getattr(page, 'latest_tab', None)
            tab_id = getattr(lt, 'tab_id', None) if lt else None
            if not tab_id:
                error('浏览器无可绑定的标签页', code='TAB_NOT_FOUND')
                return
        else:
            tab_id = _resolve_tab_target(page, target)
            if not tab_id:
                return

        tab = page.get_tab(tab_id)
        tab.set.activate()
        sess = load_session(session)
        _bind_tab(sess, tab_id)
        save_session(session, sess)
        if sess.get('recording'):
            try:
                from dp_cli.recorder import inject_recorder
                inject_recorder(tab)
            except Exception as e:
                debug('新标签页注入录制器失败', e)
        ok({'id': tab_id, 'url': tab.url, 'title': tab.title},
           msg='已绑定，dpx 命令将在此标签页执行')
    except Exception as e:
        error(f'切换标签页失败', code='TAB_FAILED', detail=str(e))


def _impl_tab_close(index_or_id, session):
    """关闭标签页（默认关闭绑定的标签页，无绑定则关闭当前页）。"""
    page = _get_page(session, raw=True)
    if not page:
        return
    try:
        sess = load_session(session)
        pinned_id = sess.get('active_tab')

        if index_or_id is None:
            if pinned_id:
                tab = page.get_tab(pinned_id)
                tab.close()
                _rebind_from_history(page, sess)
                save_session(session, sess)
                ok({'id': pinned_id}, msg='绑定的标签页已关闭（绑定已解除）')
            else:
                page.close()
                ok(msg='当前标签页已关闭')
        else:
            tab_id = _resolve_tab_target(page, index_or_id)
            if not tab_id:
                return
            tab = page.get_tab(tab_id)
            tab.close()
            if tab_id == pinned_id:
                _rebind_from_history(page, sess)
                save_session(session, sess)
            ok({'id': tab_id}, msg='标签页已关闭')
    except Exception as e:
        error(f'关闭标签页失败', code='TAB_FAILED', detail=str(e))


_LIST_HELP = '列出所有标签页（pinned 输出 index，可直接喂 select N）'
_NEW_HELP = ('新建标签页并后台打开+自动绑定；--foreground 前台激活，'
             '--background 不绑定，--new-window 新窗口')
_SELECT_HELP = '绑定标签页（无参=当前激活页；序号/tab_id/URL/none）'
_CLOSE_HELP = '关闭标签页（默认关绑定页，无绑定关当前页）'


def _mk_flat(cli):
    """平级命令注册（tab-list 等）——旧接口全保留。"""

    @cli.command('tab-list', help=_LIST_HELP)
    @session_option
    def tab_list(session):
        _impl_tab_list(session)

    @cli.command('tab-new', help=_NEW_HELP)
    @click.argument('url', required=False)
    @session_option
    @click.option('--background', is_flag=True,
                  help='后台打开且不绑定（默认后台打开+自动绑定）')
    @click.option('--foreground', is_flag=True,
                  help='在前台打开并激活（打断用户当前页面，慎用）')
    @click.option('--new-window', is_flag=True, help='在新窗口中打开')
    def tab_new(url, session, background, foreground, new_window):
        _impl_tab_new(url, session, background, foreground, new_window)

    @cli.command('tab-select', help=_SELECT_HELP)
    @click.argument('target', required=False)
    @click.option('--index', 'index', type=int, default=None,
                  help='按标签页序号绑定（TARGET=序号 的等价旗）')
    @session_option
    def tab_select(target, index, session):
        _impl_tab_select(target, index, session)

    @cli.command('tab-close', help=_CLOSE_HELP)
    @click.argument('index_or_id', required=False)
    @session_option
    def tab_close(index_or_id, session):
        _impl_tab_close(index_or_id, session)


def _mk_group(cli):
    """tab 子命令组——dpx tab list|new|select|close，与平级命令等价。"""
    grp = click.Group('tab', help='标签页管理（list/new/select/close）')

    @grp.command('list', help=_LIST_HELP)
    @session_option
    def _g_list(session):
        _impl_tab_list(session)

    @grp.command('new', help=_NEW_HELP)
    @click.argument('url', required=False)
    @session_option
    @click.option('--background', is_flag=True,
                  help='后台打开且不绑定（默认后台打开+自动绑定）')
    @click.option('--foreground', is_flag=True,
                  help='在前台打开并激活（打断用户当前页面，慎用）')
    @click.option('--new-window', is_flag=True, help='在新窗口中打开')
    def _g_new(url, session, background, foreground, new_window):
        _impl_tab_new(url, session, background, foreground, new_window)

    @grp.command('select', help=_SELECT_HELP)
    @click.argument('target', required=False)
    @click.option('--index', 'index', type=int, default=None,
                  help='按标签页序号绑定（TARGET=序号 的等价旗）')
    @session_option
    def _g_select(target, index, session):
        _impl_tab_select(target, index, session)

    @grp.command('close', help=_CLOSE_HELP)
    @click.argument('index_or_id', required=False)
    @session_option
    def _g_close(index_or_id, session):
        _impl_tab_close(index_or_id, session)

    cli.add_command(grp)


def register(cli):
    _mk_flat(cli)
    _mk_group(cli)


def _resolve_tab_target(page, target: str) -> str:
    """将 target（序号/tab_id/URL关键词）解析为 tab_id"""
    tab_ids = page.tab_ids

    # 1. 尝试按序号
    try:
        idx = int(target)
        if 0 <= idx < len(tab_ids):
            return tab_ids[idx]
        error(f'标签页序号越界: {idx}（共 {len(tab_ids)} 个）',
              code='TAB_NOT_FOUND')
        return ''
    except ValueError:
        debug(f'target 不是序号，按 tab_id/url 匹配: {target}')

    # 2. 尝试按 tab_id 精确匹配
    if target in tab_ids:
        return target

    # 3. 按 URL 关键词模糊匹配
    for tid in tab_ids:
        try:
            tab = page.get_tab(tid)
            if target.lower() in (tab.url or '').lower():
                return tid
        except Exception:
            continue

    # 4. 按 title 关键词模糊匹配
    for tid in tab_ids:
        try:
            tab = page.get_tab(tid)
            if target.lower() in (tab.title or '').lower():
                return tid
        except Exception:
            continue

    error(f'未找到匹配 "{target}" 的标签页', code='TAB_NOT_FOUND')
    return ''
