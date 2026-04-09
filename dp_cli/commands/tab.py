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
        """列出所有标签页。"""
        page = _get_page(session)
        try:
            tabs = []
            for i, tab_id in enumerate(page.browser.tab_ids):
                tab = page.browser.get_tab(tab_id)
                tabs.append({
                    'index': i,
                    'id': tab_id,
                    'url': tab.url,
                    'title': tab.title,
                    'active': tab_id == page.tab_id,
                })
            ok({'tabs': tabs, 'count': len(tabs)})
        except Exception as e:
            error(f'获取标签页列表失败', code='TAB_FAILED', detail=str(e))

    @cli.command('tab-new')
    @click.argument('url', required=False)
    @session_option
    @click.option('--background', is_flag=True, help='在后台打开')
    def tab_new(url, session, background):
        """新建标签页。

        \b
        示例:
          dp tab-new
          dp tab-new https://example.com
          dp tab-new https://example.com --background
        """
        page = _get_page(session)
        try:
            new_tab = page.browser.new_tab(url=url or '', background=background)
            ok({'id': new_tab.tab_id, 'url': new_tab.url,
                'title': new_tab.title}, msg='新标签页已创建')
        except Exception as e:
            error(f'创建标签页失败', code='TAB_FAILED', detail=str(e))

    @cli.command('tab-select')
    @click.argument('index_or_id')
    @session_option
    def tab_select(index_or_id, session):
        """切换到指定标签页（序号从0开始，或传入 tab_id）。

        \b
        示例:
          dp tab-select 0
          dp tab-select 2
        """
        page = _get_page(session)
        try:
            try:
                idx = int(index_or_id)
                tab_ids = page.browser.tab_ids
                if idx < 0 or idx >= len(tab_ids):
                    error(f'标签页序号越界: {idx}', code='TAB_NOT_FOUND')
                    return
                tab_id = tab_ids[idx]
            except ValueError:
                tab_id = index_or_id

            tab = page.browser.get_tab(tab_id)
            tab.set.activate()
            sess = load_session(session)
            sess['active_tab'] = tab_id
            save_session(session, sess)
            ok({'id': tab_id, 'url': tab.url, 'title': tab.title}, msg='标签页已切换')
        except Exception as e:
            error(f'切换标签页失败', code='TAB_FAILED', detail=str(e))

    @cli.command('tab-close')
    @click.argument('index_or_id', required=False)
    @session_option
    def tab_close(index_or_id, session):
        """关闭标签页（默认关闭当前页）。"""
        page = _get_page(session)
        try:
            if index_or_id is None:
                page.close()
                ok(msg='当前标签页已关闭')
            else:
                try:
                    idx = int(index_or_id)
                    tab_id = page.browser.tab_ids[idx]
                except ValueError:
                    tab_id = index_or_id
                tab = page.browser.get_tab(tab_id)
                tab.close()
                ok({'id': tab_id}, msg='标签页已关闭')
        except Exception as e:
            error(f'关闭标签页失败', code='TAB_FAILED', detail=str(e))
