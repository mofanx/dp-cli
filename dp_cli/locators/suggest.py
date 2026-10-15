# -*- coding:utf-8 -*-
"""pw 候选定位器生成（语义化优先，可直接固化到 Playwright 用例）。

输入元素信息 dict（tag/input_type/attrs/text/value/accname/label…），
输出按稳定性排序的候选列表：

    role+name > testid > label > placeholder > alt > title
    > text > role(无名) > css

每项 {'kind', 'expr', 'py'}：
  - expr：pw: 表达式体（可回喂 dp 命令验证，如 dp click "pw:<expr>"）
  - py：playwright python 片段（可直接贴进用例/POM）
"""
import json

# 与 snapshot/utils.py suggest_locator 同源的测试属性优先级
_TESTID_ATTRS = ('data-testid', 'data-test-id', 'data-test',
                 'data-qa', 'data-cy')

# input[type] → 隐式 ARIA role（与 pw_js.ROLE_NATIVE 反向对应）
_INPUT_ROLES = {
    'button': 'button', 'submit': 'button', 'reset': 'button',
    'image': 'button',
    'checkbox': 'checkbox', 'radio': 'radio',
    'range': 'slider', 'number': 'spinbutton',
    'search': 'searchbox',
    'text': 'textbox', 'email': 'textbox', 'tel': 'textbox',
    'url': 'textbox', 'password': 'textbox',
}

_TAG_ROLES = {
    'button': 'button', 'summary': 'button',
    'textarea': 'textbox', 'select': 'combobox', 'option': 'option',
    'datalist': 'listbox',
    'img': 'img', 'svg': 'img',
    'h1': 'heading', 'h2': 'heading', 'h3': 'heading',
    'h4': 'heading', 'h5': 'heading', 'h6': 'heading',
    'ul': 'list', 'ol': 'list', 'menu': 'list', 'li': 'listitem',
    'table': 'table', 'tr': 'row', 'td': 'cell',
    'dialog': 'dialog', 'nav': 'navigation', 'main': 'main',
    'header': 'banner', 'footer': 'contentinfo', 'aside': 'complementary',
    'article': 'article', 'hr': 'separator', 'progress': 'progressbar',
    'details': 'group',
}


def _ps(s) -> str:
    """Python 字符串字面量（双引号 + 转义，json.dumps 输出兼容）。"""
    return json.dumps(str(s), ensure_ascii=False)


def implicit_role(tag: str, input_type: str = '', attrs: dict = None):
    """tag/type/attrs → 隐式 ARIA role；无对应 role 返回 None。"""
    attrs = attrs or {}
    explicit = (attrs.get('role') or '').split()
    if explicit:
        return explicit[0]
    if tag in ('a', 'area'):
        return 'link' if attrs.get('href') else None
    if tag == 'input':
        return _INPUT_ROLES.get((input_type or 'text').lower(), 'textbox')
    if tag == 'th':
        scope = attrs.get('scope')
        return 'rowheader' if scope == 'row' else 'columnheader'
    if tag in ('form', 'section'):
        # 需 aria-label/labelledby 才具备 landmark role
        if attrs.get('aria-label') or attrs.get('aria-labelledby'):
            return 'form' if tag == 'form' else 'region'
        return None
    return _TAG_ROLES.get(tag)


def _best_css(info: dict) -> str:
    """语义候选都不可用时生成相对稳定的 CSS 兜底。"""
    attrs = info.get('attrs') or {}
    tag = info.get('tag') or ''
    for a in _TESTID_ATTRS:
        if attrs.get(a):
            return f'[{a}={_ps(attrs[a])}]'
    if attrs.get('id'):
        return f'#{attrs["id"]}'
    if attrs.get('name'):
        return f'{tag}[name={_ps(attrs["name"])}]' if tag else f'[name={_ps(attrs["name"])}]'
    cls = (attrs.get('class') or '').split()
    meaningful = [c for c in cls if len(c) >= 2 and ('-' in c or '_' in c
                                                   or c.islower())]
    if meaningful:
        return f'{tag}.{meaningful[0]}' if tag else f'.{meaningful[0]}'
    return tag or 'body'


def pw_candidates(info: dict) -> list:
    """元素信息 → 按稳定性排序的 pw 候选定位器列表。

    :param info: {'tag','input_type','attrs','text','value','accname','label'}
    :return: [{'kind','expr','py'}]，末位始终是 css 兜底
    """
    attrs = info.get('attrs') or {}
    tag = info.get('tag') or ''
    out = []

    role = info.get('role') or implicit_role(
        tag, info.get('input_type') or attrs.get('type', ''), attrs)
    name = (info.get('accname') or '').strip()
    if len(name) > 200:
        name = name[:200]

    # 1. role+name（最稳定，Playwright 首推）
    if role and name:
        out.append({
            'kind': 'role',
            'expr': f'role={role}[name={_ps(name)}]',
            'py': f'page.get_by_role({_ps(role)}, name={_ps(name)})'})

    # 2. testid 族：data-testid → get_by_test_id；
    #    其他 attr（data-test/data-test-id/data-qa/data-cy）降级
    #    为 css attr 选择器——Playwright get_by_test_id 默认只查
    #    data-testid，非默认 attr 需 selectors.set_test_id_attribute
    #    配置，导出脚本独立运行不会配置 → css 形式直接命中。
    for a in _TESTID_ATTRS:
        if attrs.get(a):
            if a == 'data-testid':
                py = f'page.get_by_test_id({_ps(attrs[a])})'
                expr = f'testid={_ps(attrs[a])}'
            else:
                css = f'[{a}={_ps(attrs[a])}]'
                py = f'page.locator({_ps(css)})'
                # pw 引擎 testid 只查 data-testid/-test-id/-test；
                # data-qa/data-cy 走 css 表达式才可在 dp 侧回验
                expr = (f'testid={_ps(attrs[a])}'
                        if a in ('data-test-id', 'data-test')
                        else f'css={css}')
            out.append({'kind': 'testid', 'expr': expr, 'py': py})
            break

    # 3. label / placeholder / alt / title
    label = (info.get('label') or '').strip()
    if label:
        out.append({
            'kind': 'label',
            'expr': f'label={_ps(label)}',
            'py': f'page.get_by_label({_ps(label)})'})
    for attr, api in (('placeholder', 'get_by_placeholder'),
                      ('alt', 'get_by_alt_text'),
                      ('title', 'get_by_title')):
        v = (attrs.get(attr) or '').strip()
        if v:
            out.append({'kind': attr,
                        'expr': f'{attr}={_ps(v)}',
                        'py': f'page.{api}({_ps(v)})'})

    # 4. text（短文本精确、长文本截取子串）
    text = (info.get('text') or '').strip()
    if text:
        if len(text) <= 50:
            out.append({'kind': 'text',
                        'expr': f'text={_ps(text)}',
                        'py': f'page.get_by_text({_ps(text)}, exact=True)'})
        else:
            sub = text[:50]
            out.append({'kind': 'text',
                        'expr': f'text={sub}',
                        'py': f'page.get_by_text({_ps(sub)})'})

    # 5. role 无名（弱候选，配合 nth 才稳）
    if role and not name:
        out.append({'kind': 'role',
                    'expr': f'role={role}',
                    'py': f'page.get_by_role({_ps(role)})'})

    # 6. css 兜底
    css = _best_css(info)
    out.append({'kind': 'css',
                'expr': f'css={css}',
                'py': f'page.locator({_ps(css)})'})
    return out


def dp_loc_to_pw(loc: str):
    """DrissionPage 风格定位串 → {'expr','py'}；无法映射返回 None。

    覆盖 suggest_locator / snapshot loc 的常见形态：
    @attr=v、#id、.cls、text:x、t:tag、css:、xpath:、裸 css。
    """
    if not loc:
        return None
    s = loc.strip()
    if not s:
        return None
    if s.startswith('pw:'):
        return {'expr': s[3:], 'py': None}
    if s.startswith('text:'):
        v = s[5:]
        return {'expr': f'text={_ps(v)}',
                'py': f'page.get_by_text({_ps(v)})'}
    if s.startswith('xpath:') or s.startswith('xpath='):
        v = s[6:]
        return {'expr': f'xpath={v}',
                'py': f'page.locator({_ps("xpath=" + v)})'}
    if s.startswith('css:') or s.startswith('css='):
        v = s[4:]
        return {'expr': f'css={v}', 'py': f'page.locator({_ps(v)})'}
    if s.startswith('@') and '=' in s:
        a, v = s[1:].split('=', 1)
        css = f'[{a}={_ps(v)}]'
        return {'expr': f'css={css}', 'py': f'page.locator({_ps(css)})'}
    if s.startswith('t:'):
        v = s[2:]
        return {'expr': f'css={v}', 'py': f'page.locator({_ps(v)})'}
    if s.startswith('tag:'):
        v = s[4:]
        return {'expr': f'css={v}', 'py': f'page.locator({_ps(v)})'}
    # 裸值按 css 处理（#id/.cls/tag[attr]/...）
    return {'expr': f'css={s}', 'py': f'page.locator({_ps(s)})'}
