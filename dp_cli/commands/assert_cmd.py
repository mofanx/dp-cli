# -*- coding:utf-8 -*-
"""断言命令: assert-text / assert-url / assert-visible / assert-count / expect-gen

面向脚本与 agent 自检闭环：断言失败输出 JSON error 并以非零码退出，
可直接用于 `&&` 串联或 CI 检查。
"""
import json

import click

from dp_cli.output import ok, error
from dp_cli.commands._utils import (session_option, frame_option,
                                    _get_page, resolve_locator, frame_ctx)
from dp_cli.locators import (parse_pw, PwParseError, build_pw_count_js,
                             pw_candidates, dp_loc_to_pw)


def _is_missing(ele) -> bool:
    return not ele or ele.__class__.__name__ == 'NoneElement'


def _ps(s) -> str:
    return json.dumps(str(s), ensure_ascii=False)


# 元素信息提取 JS（ele.run_js 执行，this=元素；与 locators/pw_js.py
# 的 accName 同策略，精简实现）
_ELE_INFO_JS = r"""
const el = this;
if (!el || el.nodeType !== 1) return null;
const g = n => el.getAttribute ? el.getAttribute(n) : null;
const attrs = {};
for (const n of ['id','name','type','role','class','placeholder','alt','title',
    'aria-label','aria-labelledby','data-testid','data-test-id','data-test',
    'data-qa','data-cy','href','for']) {
  const v = g(n); if (v) attrs[n] = v;
}
const tag = (el.tagName || '').toLowerCase();
let acc = (attrs['aria-label'] || '').trim();
if (!acc && attrs['aria-labelledby']) {
  acc = attrs['aria-labelledby'].split(/\s+/).map(id => {
    const r = document.getElementById(id);
    return r ? (r.innerText || r.textContent || '').trim() : '';
  }).filter(Boolean).join(' ').trim();
}
let label = '';
try {
  if (el.labels && el.labels.length) {
    label = Array.from(el.labels)
      .map(l => (l.innerText || l.textContent || '').trim())
      .filter(Boolean).join(' ').trim();
  }
} catch (e) {}
if (!acc && tag === 'input') {
  const ty = (el.type || 'text').toLowerCase();
  if (ty === 'button' || ty === 'submit' || ty === 'reset' || ty === 'image') {
    acc = (el.value || '').trim();
  }
}
if (!acc && label) acc = label;
if (!acc && (tag === 'img' || tag === 'area')) acc = (attrs['alt'] || '').trim();
const it = ((el.innerText || el.textContent) || '').trim().replace(/\s+/g, ' ');
if (!acc && it) acc = it.length > 200 ? it.slice(0, 200) : it;
if (!acc) acc = (attrs['title'] || '').trim();
const r = el.getBoundingClientRect();
let st = null;
try { st = getComputedStyle(el); } catch (e) {}
const visible = !!(r.width >= 1 && r.height >= 1) && (!st || (
  st.display !== 'none' && st.visibility !== 'hidden'
  && parseFloat(st.opacity || '1') >= 0.01));
return {
  tag: tag, input_type: (el.type || '').toLowerCase(), attrs: attrs,
  text: it.slice(0, 500), value: (el.value != null ? String(el.value) : ''),
  checked: !!el.checked, disabled: !!el.disabled,
  visible: visible, accname: acc, label: label
};
"""


def _ele_info(ele):
    """提取元素信息（ele.run_js 执行 _ELE_INFO_JS）；失败返回 None。"""
    try:
        return ele.run_js(_ELE_INFO_JS)
    except Exception:
        return None


def _walk_tree(node, out):
    """递归收集 a11y 树节点。"""
    if not node:
        return
    out.append(node)
    for c in node.get('children') or []:
        _walk_tree(c, out)


def _element_expects(py_loc: str, info: dict, matches=None) -> list:
    """按元素信息产出 expect() 行列表 [{'code','basis'}]。"""
    items = []
    if info.get('visible'):
        items.append({'code': f'expect({py_loc}).to_be_visible()',
                      'basis': '元素当前可见'})
    else:
        items.append({'code': f'expect({py_loc}).to_be_hidden()',
                      'basis': '元素当前不可见/无尺寸'})
    text = (info.get('text') or '').strip()
    if text:
        sample = text if len(text) <= 40 else text[:40] + '…'
        items.append({
            'code': f'expect({py_loc}).to_contain_text({_ps(text[:80])})',
            'basis': f'元素文本: {sample}'})
    tag = info.get('tag') or ''
    if tag in ('input', 'textarea', 'select'):
        v = info.get('value')
        if v:
            items.append({
                'code': f'expect({py_loc}).to_have_value({_ps(v)})',
                'basis': f'当前值: {str(v)[:40]}'})
        it = info.get('input_type') or (info.get('attrs') or {}).get('type', '')
        if it in ('checkbox', 'radio'):
            items.append({
                'code': (f'expect({py_loc}).to_be_checked()'
                         if info.get('checked')
                         else f'expect({py_loc}).not_to_be_checked()'),
                'basis': f"checked={bool(info.get('checked'))}"})
    if tag in ('input', 'textarea', 'select', 'button', 'option',
               'optgroup', 'fieldset'):
        items.append({
            'code': (f'expect({py_loc}).to_be_disabled()'
                     if info.get('disabled')
                     else f'expect({py_loc}).to_be_enabled()'),
            'basis': f"disabled={bool(info.get('disabled'))}"})
    acc = (info.get('accname') or '').strip()
    if acc:
        items.append({
            'code': f'expect({py_loc}).to_have_accessible_name({_ps(acc)})',
            'basis': f'可访问名: {acc[:40]}'})
    if matches is not None:
        items.append({
            'code': f'expect({py_loc}).to_have_count({int(matches)})',
            'basis': f'定位器当前命中 {matches} 个元素'})
    return items


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
          dpx assert-text "登录成功"
          dpx assert-text "订单已提交" --locator "#msg"
          dpx assert-text "Hello" --locator "#title" --exact
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
          dpx assert-url "dashboard"
          dpx assert-url "https://example.com/home" --exact
          dpx assert-url "搜索结果" --title
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
          dpx assert-visible "#result"
          dpx assert-visible "ref:5"
          dpx assert-visible ".loading" --hidden   # 等待加载指示器消失后断言
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
          dpx assert-count ".item" 20          # 恰好 20 个
          dpx assert-count ".item" --min 1     # 至少 1 个
          dpx assert-count ".error" 0          # 无错误元素
          dpx assert-count "li" --min 3 --max 10
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

    @cli.command('expect-gen')
    @click.option('--ele', 'ele_loc', default=None,
                  help='对指定元素生成断言（任意 dpx 定位器，含 pw:/ref:）')
    @click.option('--all', 'gen_all', is_flag=True,
                  help='枚举页面上全部可断言元素（a11y 交互节点 + clickable 补充）')
    @click.option('--limit', type=int, default=50, show_default=True,
                  help='--all 模式枚举元素上限')
    @click.option('--no-count', is_flag=True,
                  help='--ele 模式不统计定位器命中数（省一次 JS 求值）')
    @click.option('--lang', type=click.Choice(['py', 'ts']), default='py',
                  show_default=True,
                  help='断言语言：py（Python）/ ts（TypeScript 驼峰 + await）')
    @session_option
    @frame_option
    def expect_gen(ele_loc, gen_all, limit, no_count, lang, session, frame):
        """生成可直接贴进 Playwright 用例的 expect() 断言。

        \b
        定位器候选按稳定性排序：role+name > testid > label >
        placeholder > alt > title > text > css。元素模式统计所选
        定位器命中数（to_have_count，唯一性背书）。

        \b
        示例:
          dpx expect-gen                          # 页面级：url + title
          dpx expect-gen --ele "ref:5"            # 元素级断言
          dpx expect-gen --ele "pw:role=button[name=登录]"
          dpx expect-gen --all --limit 30         # 枚举全部可断言点
          dpx expect-gen --lang ts                # TypeScript 断言输出
        """
        if ele_loc and gen_all:
            error('--ele 与 --all 互斥', code='INVALID_ARGS')
            return
        page = _get_page(session)
        try:
            url = page.url or ''
            title = page.title or ''
        except Exception as e:
            error('页面信息获取失败', code='EXPECT_GEN_FAILED', detail=str(e))
            return

        expects = []
        if url:
            expects.append({'code': f'expect(page).to_have_url({_ps(url)})',
                            'basis': f'当前 URL: {url[:80]}'})
        if title:
            expects.append({'code': f'expect(page).to_have_title({_ps(title)})',
                            'basis': f'当前标题: {title[:60]}'})
        data = {'page': {'url': url, 'title': title}}

        if ele_loc:
            ctx = frame_ctx(page, frame, session)
            real_loc = resolve_locator(ele_loc, session)
            ele = ctx.ele(real_loc, timeout=5)
            if _is_missing(ele):
                error(f'元素不存在: {ele_loc}', code='ELEMENT_NOT_FOUND')
                return
            info = _ele_info(ele) or {}
            info.setdefault('tag', getattr(ele, 'tag', '') or '')
            if not info.get('text'):
                info['text'] = (getattr(ele, 'raw_text', '') or '').strip()
            cands = pw_candidates(info)
            chosen = cands[0]
            matches = None
            if not no_count:
                try:
                    matches = page.run_js(
                        build_pw_count_js(parse_pw(chosen['expr'])))
                except Exception:
                    matches = None
            expects.extend(_element_expects(chosen['py'], info, matches))
            data['target'] = {
                'input': ele_loc, 'resolved': real_loc,
                'chosen': chosen, 'matches': matches,
                'candidates': cands,
            }
        elif gen_all:
            from dp_cli.snapshot.a11y import (take_a11y_snapshot,
                                              _INTERACTIVE_ROLES)
            try:
                snap = take_a11y_snapshot(page)
            except Exception as e:
                error('快照获取失败', code='SNAPSHOT_FAILED', detail=str(e))
                return
            nodes = []
            _walk_tree(snap.get('tree'), nodes)
            points = []
            total = 0
            for n in nodes:
                if n.get('role') not in _INTERACTIVE_ROLES:
                    continue
                total += 1
                if len(points) >= limit:
                    continue
                role = n.get('role') or ''
                name = (n.get('name') or '').strip()
                if len(name) > 200:
                    name = name[:200]
                if role and name:
                    py = (f'page.get_by_role({_ps(role)}, '
                          f'name={_ps(name)})')
                    expr = f'role={role}[name={_ps(name)}]'
                elif role:
                    py = f'page.get_by_role({_ps(role)})'
                    expr = f'role={role}'
                else:
                    conv = dp_loc_to_pw(n.get('locator'))
                    if not conv or not conv.get('py'):
                        continue
                    py, expr = conv['py'], conv['expr']
                pts = [{'code': f'expect({py}).to_be_visible()',
                        'basis': f'{role or "元素"} {name[:30]}'}]
                if name:
                    pts.append({
                        'code': f'expect({py}).to_contain_text({_ps(name[:80])})',
                        'basis': '可访问名/文本'})
                points.append({'expr': expr, 'py': py, 'expects': pts})
            # clickable 补充元素（a11y 漏网的可交互元素）
            extras = snap.get('clickable_extras') or []
            for e in extras:
                total += 1
                if len(points) >= limit:
                    continue
                conv = dp_loc_to_pw(e.get('locator'))
                if not conv or not conv.get('py'):
                    continue
                label = (e.get('text') or e.get('label') or '').strip()
                pts = [{'code': f"expect({conv['py']}).to_be_visible()",
                        'basis': f'clickable 补充: {e.get("tag","")} {label[:30]}'}]
                if label:
                    pts.append({
                        'code': f"expect({conv['py']}).to_contain_text({_ps(label[:80])})",
                        'basis': '元素文本'})
                points.append({'expr': conv['expr'], 'py': conv['py'],
                               'expects': pts})
            for p in points:
                expects.extend(p['expects'])
            data['elements'] = points
            data['total_assertable'] = total
            data['truncated'] = total > len(points)

        # ts 模式：expect 行统一转驼峰 + await；定位器语义不变
        # （expects.extend 与 points 共享 dict，原地改 code 两处同步）
        if lang == 'ts':
            from dp_cli.pw_ts import py_expect_to_ts
            for e in expects:
                e['code'] = py_expect_to_ts(e['code'])
        comment = '// ' if lang == 'ts' else '# '
        data['lang'] = lang
        data['expects'] = expects
        data['count'] = len(expects)
        data['script'] = '\n'.join(
            f'{comment}{e["basis"]}\n{e["code"]}' for e in expects)
        ok(data, msg=f'已生成 {len(expects)} 条 expect 断言')
