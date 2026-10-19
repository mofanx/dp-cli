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
from dp_cli.session import load_session

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


def _read_direct(url: str, fmt: str, output, boundary) -> None:
    """无会话轻量抓取：curl_cffi/requests 直取 → llms.txt 探测 → 正文提取。

    顺序：URL 本身（Accept: text/markdown 优先，服务端直接给 md 就用）→
    响应非 md 时逐级祖先路径探 {dir}/llms.txt → 都不行走 _extract_text
    从 HTML 提正文。整条链路不起浏览器。
    """
    from urllib.parse import urlparse, urljoin

    def _fetch(u):
        try:
            from curl_cffi import requests as cffi
            return cffi.get(u, timeout=20, impersonate='chrome',
                            headers={'Accept': 'text/markdown, text/plain;q=0.9,'
                                               ' text/html;q=0.8'})
        except ImportError:
            import requests
            return requests.get(u, timeout=20, headers={
                'Accept': 'text/markdown, text/plain;q=0.9, text/html;q=0.8',
                'User-Agent': 'Mozilla/5.0'})
        except Exception as e:
            return e

    resp = _fetch(url)
    if isinstance(resp, Exception):
        error(f'抓取失败: {url}', code='FETCH_FAILED', detail=str(resp))
        return

    ctype = (resp.headers.get('content-type') or '').lower()
    text = resp.text or ''

    # 服务端直接给 markdown/plain → 原样
    if resp.status_code == 200 and (
            'markdown' in ctype or 'text/plain' in ctype):
        _emit_read(url, text, 'direct-markdown', fmt, output, boundary)
        return

    # llms.txt 祖先路径探测（当前目录逐级向上）
    if resp.status_code >= 400 or not text.strip():
        p = urlparse(url)
        path = p.path.rstrip('/')
        probe = []
        while path and path != '/':
            path = path.rsplit('/', 1)[0] or '/'
            probe.append(urljoin(f'{p.scheme}://{p.netloc}',
                                 f'{path.rstrip("/")}/llms.txt'))
        probe.append(urljoin(f'{p.scheme}://{p.netloc}', '/llms.txt'))
        for pu in dict.fromkeys(probe):
            try:
                r2 = _fetch(pu)
            except Exception:
                continue
            if (not isinstance(r2, Exception) and r2.status_code == 200
                    and (r2.text or '').strip()):
                _emit_read(pu, r2.text, 'llms.txt', fmt, output, boundary)
                return
        error(f'抓取失败: {url} HTTP {resp.status_code}（llms.txt 探测无命中）',
              code='FETCH_FAILED')
        return

    # HTML → 正文提取
    engine, content = _extract_text(text, fmt, url)
    if engine == 'missing':
        error('提取引擎不可用：未安装 trafilatura / readability-lxml。'
              + _DEP_HINT, code='DEPENDENCY_MISSING')
        return
    if content is None:
        error('正文提取失败（引擎未能识别正文区域）',
              code='EXTRACT_FAILED')
        return
    _emit_read(url, content, f'direct-{engine}', fmt, output, boundary)


def _emit_read(url, content, engine, fmt, output, boundary):
    if boundary:
        from dp_cli.commands._utils import wrap_boundary
        content, nonce = wrap_boundary(content)
    else:
        nonce = None
    result = {'url': url, 'engine': engine, 'format': fmt,
              'chars': len(content)}
    if nonce:
        result['boundary'] = nonce
    if output:
        Path(output).write_text(content, encoding='utf-8')
        result['saved_to'] = output
    else:
        result['content'] = content
    ok(result)


def register(cli):

    @cli.command('read')
    @click.argument('url', required=False, default=None)
    @session_option
    @click.option('--selector', default=None,
                  help='CSS/locator 限定提取区域（默认整页正文）')
    @click.option('--format', 'fmt',
                  type=click.Choice(['markdown', 'text', 'html']),
                  default='markdown', show_default=True, help='输出格式')
    @click.option('--output', default=None,
                  help='内容写入文件（默认内联 data.content）')
    @click.option('--boundary', is_flag=True,
                  help='输出内容加不可信边界标记（防 prompt-injection；'
                       'nonce 见 data.boundary）')
    def read(url, session, selector, fmt, output, boundary):
        """把页面正文提取为 Markdown/纯文本（trafilatura 优先，readability 兜底）。

        适合 agent 消费：跳过导航/脚本/样式，只留正文。
        依赖为可选安装：pip install "dp-cli[extract]"

        带 URL 且会话不存在时走轻量路径：curl_cffi 直抓（Accept:
        text/markdown）→ 祖先路径 llms.txt 探测 → HTML 转 markdown。

        \b
        示例:
          dpx read                          # 整页正文 → markdown
          dpx read https://docs.x.com/page  # 无浏览器直抓
          dpx read --selector "css:article" # 只提取指定区域
          dpx read --format text --output article.txt
        """
        if url:
            if not load_session(session):
                _read_direct(url, fmt, output, boundary)
                return
            page = _get_page(session)
            try:
                page.get(url)
            except Exception as e:
                error(f'导航失败: {url}', code='NAVIGATE_FAILED',
                      detail=str(e))
                return
        else:
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

            if boundary:
                from dp_cli.commands._utils import wrap_boundary
                content, nonce = wrap_boundary(content)
            else:
                nonce = None
            result = {'url': getattr(page, 'url', '') or '',
                      'title': getattr(page, 'title', '') or '',
                      'engine': engine, 'format': fmt,
                      'chars': len(content)}
            if nonce:
                result['boundary'] = nonce
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
