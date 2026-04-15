# -*- coding:utf-8 -*-
"""标签页管理命令: tab-list / tab-new / tab-select / tab-close"""
import click

from dp_cli.output import ok, error
from dp_cli.session import load_session, save_session
from dp_cli.commands._utils import session_option, _get_page


def register(cli):

    @cli.command('tab-list')
    @session_option
    def tab_list(session):
        """列出所有标签页。

        \b
        绑定的标签页会显示 [pinned] 标记，后续所有 dp 命令只在该标签页中执行。
        """
        page = _get_page(session, raw=True)
        if not page:
            return
        try:
            sess = load_session(session)
            pinned_id = sess.get('active_tab')
            tabs = []
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
                tabs.append(entry)
            ok({'tabs': tabs, 'count': len(tabs),
                'pinned': pinned_id or '(none)'})
        except Exception as e:
            error(f'获取标签页列表失败', code='TAB_FAILED', detail=str(e))

    @cli.command('tab-new')
    @click.argument('url', required=False)
    @session_option
    @click.option('--background', is_flag=True, help='在后台打开（不绑定）')
    @click.option('--new-window', is_flag=True, help='在新窗口中打开')
    def tab_new(url, session, background, new_window):
        """新建标签页并自动绑定。

        \b
        新标签页会自动绑定到当前会话，后续 dp 命令在该标签页中执行。
        使用 --background 时不绑定。
        使用 --new-window 在独立窗口中打开（适合自动化与手动浏览分离）。

        \b
        示例:
          dp tab-new https://example.com
          dp tab-new https://example.com --new-window
          dp tab-new https://example.com --background
        """
        page = _get_page(session, raw=True)
        if not page:
            return
        try:
            new_tab = page.new_tab(url=url or '',
                                   new_window=new_window,
                                   background=background)
            new_tid = new_tab.tab_id
            msg = '新标签页已创建'

            # 非 background 时自动绑定
            if not background:
                sess = load_session(session)
                sess['active_tab'] = new_tid
                save_session(session, sess)
                msg += '（已绑定，dp 命令将在此标签页执行）'

            ok({'id': new_tid, 'url': new_tab.url,
                'title': new_tab.title, 'pinned': not background},
               msg=msg)
        except Exception as e:
            error(f'创建标签页失败', code='TAB_FAILED', detail=str(e))

    @cli.command('tab-select')
    @click.argument('target')
    @session_option
    def tab_select(target, session):
        """绑定到指定标签页，后续 dp 命令在该标签页中执行。

        \b
        TARGET 支持：
          序号    dp tab-select 0        （按标签页序号）
          tab_id  dp tab-select ABC123   （按标签页 ID）
          URL     dp tab-select zhipin   （按 URL 关键词匹配）
          none    dp tab-select none     （解除绑定，恢复默认行为）
        """
        # 解除绑定
        if target.lower() == 'none':
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
            tab_id = _resolve_tab_target(page, target)
            if not tab_id:
                return

            tab = page.get_tab(tab_id)
            tab.set.activate()
            sess = load_session(session)
            sess['active_tab'] = tab_id
            save_session(session, sess)
            ok({'id': tab_id, 'url': tab.url, 'title': tab.title},
               msg='已绑定，dp 命令将在此标签页执行')
        except Exception as e:
            error(f'切换标签页失败', code='TAB_FAILED', detail=str(e))

    @cli.command('tab-close')
    @click.argument('index_or_id', required=False)
    @session_option
    def tab_close(index_or_id, session):
        """关闭标签页（默认关闭绑定的标签页，无绑定则关闭当前页）。"""
        page = _get_page(session, raw=True)
        if not page:
            return
        try:
            sess = load_session(session)
            pinned_id = sess.get('active_tab')

            if index_or_id is None:
                # 优先关闭绑定的标签页
                if pinned_id:
                    tab = page.get_tab(pinned_id)
                    tab.close()
                    sess.pop('active_tab', None)
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
                # 如果关闭的是绑定的标签页，清除绑定
                if tab_id == pinned_id:
                    sess.pop('active_tab', None)
                    save_session(session, sess)
                ok({'id': tab_id}, msg='标签页已关闭')
        except Exception as e:
            error(f'关闭标签页失败', code='TAB_FAILED', detail=str(e))


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
        pass

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
