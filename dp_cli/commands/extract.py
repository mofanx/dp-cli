# -*- coding:utf-8 -*-
"""页面正文提取命令: read

把当前页面正文提取为 Markdown / 纯文本 / HTML。
trafilatura 优先（正文识别+原生 markdown 输出），readability-lxml
兜底；均为可选依赖（pip install dp-cli[extract]），缺包给明确错误提示。

命名说明：'extract' 已被结构化数据提取命令占用（snapshot_cmd.py），
故取名 read（jina-reader 风格语义：把页面读成文本）。
"""
import re
from html import unescape
from pathlib import Path

import click

from dp_cli.output import ok, error, debug
from dp_cli.commands._utils import session_option, _get_page

_DEP_HINT = ('需要可选提取依赖：pip install "dp-cli[extract]"'
             '（trafilatura 优先，readability-lxml 兜底）')


def _html_to_text(html: str) -> str:
    """简易 HTML→纯文本（readability 兜底路径用）。"""
    s = re.sub(r'(?is)<(script|style|noscript)[^>]*>.*?</\1>', ' ', html)
    s = re.sub(r'(?is)<br\s*/?>', '\n', s)
    s = re.sub(r'(?is)</(p|div|li|tr|h[1-6]|section|article)>', '\n', s)
    s = re.sub(r'(?is)<[^>]+>', '', s)
    lines = [l.strip() for l in unescape(s).splitlines()]
    return '\n'.join(l for l in lines if l)


def _extract_text(html: str, fmt: str, url: str = ''):
    """提取正文。:return: (engine, content) 或 (None, None)。"""
    _TRAF_FMT = {'markdown': 'markdown', 'text': 'txt', 'html': 'html'}
    try:
        import trafilatura
        out = trafilatura.extract(
            html,
            output_format=_TRAF_FMT[fmt],
            url=url or None,
            include_links=(fmt == 'markdown'))
        if out:
            return 'trafilatura', out
    except ImportError:
        pass
    except Exception as e:
        # trafilatura 对畸形输入可能抛非导入异常——降级 readability 再试
        debug('trafilatura 提取异常，尝试 readability 兜底', e)
    try:
        from readability import Document
        summary = Document(html).summary()
        if not summary or not summary.strip():
            return None, None
        if fmt == 'html':
            return 'readability', summary
        return 'readability', _html_to_text(summary)
    except ImportError:
        return 'missing', None


def register(cli):

    @cli.command('read')
    @session_option
    @click.option('--selector', default=None,
                  help='CSS/locator 限定提取区域（默认整页正文）')
    @click.option('--format', 'fmt',
                  type=click.Choice(['markdown', 'text', 'html']),
                  default='markdown', show_default=True, help='输出格式')
    @click.option('--output', default=None,
                  help='内容写入文件（默认内联 data.content）')
    def read(session, selector, fmt, output):
        """把页面正文提取为 Markdown/纯文本（trafilatura 优先，readability 兜底）。

        适合 agent 消费：跳过导航/脚本/样式，只留正文。
        依赖为可选安装：pip install "dp-cli[extract]"

        \b
        示例:
          dp read                          # 整页正文 → markdown
          dp read --selector "css:article" # 只提取指定区域
          dp read --format text --output article.txt
        """
        page = _get_page(session)
        try:
            if selector:
                ele = page.ele(selector)
                if ele is None or ele.__class__.__name__ == 'NoneElement':
                    error(f'元素未找到: {selector}', code='ELEMENT_NOT_FOUND')
                    return
                html = ele.html
            else:
                html = page.html
            if not html:
                error('页面无 HTML 内容', code='EXTRACT_FAILED')
                return

            engine, content = _extract_text(html, fmt,
                                          getattr(page, 'url', '') or '')
            if engine == 'missing':
                error('提取引擎不可用：未安装 trafilatura / readability-lxml。'
                      + _DEP_HINT, code='DEPENDENCY_MISSING')
                return
            if content is None:
                error('正文提取失败（引擎未能识别正文区域）',
                      code='EXTRACT_FAILED')
                return

            result = {'url': getattr(page, 'url', '') or '',
                      'title': getattr(page, 'title', '') or '',
                      'engine': engine, 'format': fmt,
                      'chars': len(content)}
            if output:
                Path(output).write_text(content, encoding='utf-8')
                result['saved_to'] = output
            else:
                result['content'] = content
            ok(result)
        except SystemExit:
            raise
        except Exception as e:
            error('正文提取失败', code='EXTRACT_FAILED', detail=str(e))
