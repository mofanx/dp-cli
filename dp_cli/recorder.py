# -*- coding:utf-8 -*-
"""浏览器操作录制器：记录点击、输入、选择、按键与滚动。"""
import json


_RECORDER_JS = r"""
(function () {
  if (window.__dpRecorder && window.__dpRecorder.version) {
    window.__dpRecorder.start();
    return window.__dpRecorder.status();
  }

  const STORE_KEY = '__dp_recorder_actions__';
  const GLOBAL_STORE_KEY = '__dp_recorder_actions_global__';
  const STATE_KEY = '__dp_recorder_state__';
  const VERSION = '1.0.0';
  const INPUT_TAGS = new Set(['INPUT', 'TEXTAREA', 'SELECT']);
  const KEY_EVENTS = new Set(['Enter', 'Escape', 'Tab']);

  function now() {
    return Date.now();
  }

  function safeText(value, limit) {
    value = (value || '').replace(/\s+/g, ' ').trim();
    if (!limit) limit = 160;
    return value.length > limit ? value.slice(0, limit) + '…' : value;
  }

  function cssEscape(value) {
    if (window.CSS && CSS.escape) return CSS.escape(value);
    return String(value).replace(/[^a-zA-Z0-9_-]/g, function (ch) {
      return '\\' + ch;
    });
  }

  function readActions() {
    try {
      return JSON.parse(localStorage.getItem(GLOBAL_STORE_KEY) || sessionStorage.getItem(STORE_KEY) || '[]');
    } catch (e) {
      return [];
    }
  }

  function saveActions(actions) {
    try {
      sessionStorage.setItem(STORE_KEY, JSON.stringify(actions));
    } catch (e) {}
    try {
      localStorage.setItem(GLOBAL_STORE_KEY, JSON.stringify(actions));
    } catch (e) {}
  }

  function readState() {
    try {
      return JSON.parse(sessionStorage.getItem(STATE_KEY) || '{}');
    } catch (e) {
      return {};
    }
  }

  function saveState(state) {
    try {
      sessionStorage.setItem(STATE_KEY, JSON.stringify(state));
    } catch (e) {}
  }

  function isRecording() {
    return readState().recording === true;
  }

  function setRecording(recording) {
    const state = readState();
    state.recording = recording;
    state.updatedAt = now();
    saveState(state);
  }

  function cssPath(el) {
    if (!el || el.nodeType !== 1) return '';
    if (el.id) return '#' + cssEscape(el.id);
    const testAttrs = ['data-testid', 'data-test', 'data-cy', 'name', 'aria-label'];
    for (const attr of testAttrs) {
      const val = el.getAttribute(attr);
      if (val) return el.tagName.toLowerCase() + '[' + attr + '="' + val.replace(/"/g, '\\"') + '"]';
    }
    const parts = [];
    let cur = el;
    while (cur && cur.nodeType === 1 && cur !== document.body && cur !== document.documentElement) {
      let part = cur.tagName.toLowerCase();
      const cls = Array.from(cur.classList || []).filter(Boolean).slice(0, 2);
      if (cls.length) part += '.' + cls.map(cssEscape).join('.');
      const parent = cur.parentElement;
      if (parent) {
        const same = Array.from(parent.children).filter(function (x) {
          return x.tagName === cur.tagName;
        });
        if (same.length > 1) part += ':nth-of-type(' + (same.indexOf(cur) + 1) + ')';
      }
      parts.unshift(part);
      cur = parent;
      if (parts.length >= 5) break;
    }
    return parts.join(' > ');
  }

  function xpath(el) {
    if (!el || el.nodeType !== 1) return '';
    if (el.id) return '//*[@id="' + el.id.replace(/"/g, '\\"') + '"]';
    const parts = [];
    let cur = el;
    while (cur && cur.nodeType === 1) {
      let idx = 1;
      let sib = cur.previousElementSibling;
      while (sib) {
        if (sib.tagName === cur.tagName) idx++;
        sib = sib.previousElementSibling;
      }
      parts.unshift(cur.tagName.toLowerCase() + '[' + idx + ']');
      cur = cur.parentElement;
    }
    return '/' + parts.join('/');
  }

  function bestLocator(el) {
    if (!el || el.nodeType !== 1) return '';
    const attrs = ['data-testid', 'data-test', 'data-cy'];
    for (const attr of attrs) {
      const val = el.getAttribute(attr);
      if (val) return 'css:[' + attr + '="' + val.replace(/"/g, '\\"') + '"]';
    }
    const name = el.getAttribute('name');
    if (name) return 'css:' + el.tagName.toLowerCase() + '[name="' + name.replace(/"/g, '\\"') + '"]';
    if (el.id) return 'css:#' + cssEscape(el.id);
    const aria = el.getAttribute('aria-label');
    if (aria) return 'css:' + el.tagName.toLowerCase() + '[aria-label="' + aria.replace(/"/g, '\\"') + '"]';
    const placeholder = el.getAttribute('placeholder');
    if (placeholder) return 'css:' + el.tagName.toLowerCase() + '[placeholder="' + placeholder.replace(/"/g, '\\"') + '"]';
    const text = safeText(el.innerText || el.textContent || '', 60);
    if ((el.tagName === 'A' || el.tagName === 'BUTTON') && text) return 'text:' + text;
    const css = cssPath(el);
    return css ? 'css:' + css : '';
  }

  function rectOf(el) {
    if (!el || !el.getBoundingClientRect) return null;
    const r = el.getBoundingClientRect();
    return {
      x: Math.round(r.x),
      y: Math.round(r.y),
      width: Math.round(r.width),
      height: Math.round(r.height)
    };
  }

  function describeElement(el) {
    if (!el || el === window || el === document) el = document.scrollingElement || document.documentElement;
    const attrs = {};
    ['id', 'class', 'name', 'type', 'href', 'role', 'aria-label', 'placeholder', 'data-testid', 'data-test'].forEach(function (k) {
      const v = el.getAttribute && el.getAttribute(k);
      if (v) attrs[k] = safeText(v, 120);
    });
    const sensitive = el.tagName === 'INPUT' && (el.getAttribute('type') || '').toLowerCase() === 'password';
    return {
      tag: (el.tagName || '').toLowerCase(),
      text: safeText(el.innerText || el.textContent || '', 160),
      value: sensitive ? '***' : safeText(el.value || '', 160),
      sensitive: sensitive,
      attrs: attrs,
      rect: rectOf(el),
      best_locator: bestLocator(el),
      locators: {
        css: cssPath(el) ? 'css:' + cssPath(el) : '',
        xpath: xpath(el) ? 'xpath:' + xpath(el) : '',
        text: safeText(el.innerText || el.textContent || '', 60)
      }
    };
  }

  function signatureFor(el) {
    const desc = describeElement(el);
    return desc.best_locator || desc.locators.css || desc.locators.xpath || desc.tag;
  }

  function pageInfo() {
    return {url: location.href, title: document.title};
  }

  function pushAction(action) {
    if (!isRecording()) return;
    const actions = readActions();
    const last = actions[actions.length - 1];
    action.timestamp = now();
    action.page = pageInfo();

    if (last && last.type === 'fill' && action.type === 'fill' && last.target_signature === action.target_signature) {
      last.value = action.value;
      last.timestamp = action.timestamp;
      last.page = action.page;
      last.element = action.element;
      saveActions(actions);
      return;
    }

    if (last && last.type === 'scroll' && action.type === 'scroll' && last.target_signature === action.target_signature && action.timestamp - last.timestamp < 900) {
      last.delta.x += action.delta.x;
      last.delta.y += action.delta.y;
      last.position.after = action.position.after;
      last.mouse = action.mouse;
      last.timestamp = action.timestamp;
      last.direction = scrollDirection(last.delta);
      last.page = action.page;
      saveActions(actions);
      return;
    }

    actions.push(action);
    saveActions(actions);
  }

  function findScrollable(el) {
    let cur = el && el.nodeType === 1 ? el : el.parentElement;
    while (cur && cur !== document.body && cur !== document.documentElement) {
      const st = getComputedStyle(cur);
      const canY = /(auto|scroll|overlay)/.test(st.overflowY) && cur.scrollHeight > cur.clientHeight + 1;
      const canX = /(auto|scroll|overlay)/.test(st.overflowX) && cur.scrollWidth > cur.clientWidth + 1;
      if (canY || canX) return cur;
      cur = cur.parentElement;
    }
    return document.scrollingElement || document.documentElement;
  }

  function scrollPosition(el) {
    if (!el || el === document.documentElement || el === document.body || el === document.scrollingElement) {
      const se = document.scrollingElement || document.documentElement;
      return {scrollTop: Math.round(se.scrollTop), scrollLeft: Math.round(se.scrollLeft)};
    }
    return {scrollTop: Math.round(el.scrollTop), scrollLeft: Math.round(el.scrollLeft)};
  }

  function scrollDirection(delta) {
    if (Math.abs(delta.y) >= Math.abs(delta.x)) return delta.y >= 0 ? 'down' : 'up';
    return delta.x >= 0 ? 'right' : 'left';
  }

  function onClick(e) {
    const el = e.target;
    if (!el || INPUT_TAGS.has(el.tagName)) return;
    const element = describeElement(el);
    pushAction({
      type: 'click',
      target_signature: signatureFor(el),
      element: element,
      best_locator: element.best_locator,
      mouse: {x: Math.round(e.clientX), y: Math.round(e.clientY)}
    });
  }

  function onInput(e) {
    const el = e.target;
    if (!el || !INPUT_TAGS.has(el.tagName)) return;
    const type = (el.getAttribute('type') || '').toLowerCase();
    const actionType = el.tagName === 'SELECT' ? 'select' : (type === 'checkbox' || type === 'radio' ? 'check' : 'fill');
    const element = describeElement(el);
    pushAction({
      type: actionType,
      target_signature: signatureFor(el),
      value: element.sensitive ? '***' : (el.value || ''),
      checked: type === 'checkbox' || type === 'radio' ? !!el.checked : undefined,
      element: element,
      best_locator: element.best_locator
    });
  }

  function onKeyDown(e) {
    if (!KEY_EVENTS.has(e.key)) return;
    const el = e.target;
    const element = describeElement(el);
    pushAction({
      type: 'press',
      key: e.key,
      target_signature: signatureFor(el),
      element: element,
      best_locator: element.best_locator
    });
  }

  function onWheel(e) {
    const container = findScrollable(e.target);
    const before = scrollPosition(container);
    const mouse = {x: Math.round(e.clientX), y: Math.round(e.clientY)};
    const delta = {x: Math.round(e.deltaX), y: Math.round(e.deltaY)};
    setTimeout(function () {
      const after = scrollPosition(container);
      const changed = before.scrollTop !== after.scrollTop || before.scrollLeft !== after.scrollLeft || delta.x || delta.y;
      if (!changed) return;
      const element = describeElement(container);
      pushAction({
        type: 'scroll',
        target_signature: signatureFor(container),
        element: element,
        best_locator: element.best_locator,
        delta: delta,
        direction: scrollDirection(delta),
        position: {before: before, after: after},
        mouse: mouse
      });
    }, 80);
  }

  document.addEventListener('click', onClick, true);
  document.addEventListener('input', onInput, true);
  document.addEventListener('change', onInput, true);
  document.addEventListener('keydown', onKeyDown, true);
  document.addEventListener('wheel', onWheel, true);

  window.__dpRecorder = {
    version: VERSION,
    start: function () { setRecording(true); },
    stop: function () { setRecording(false); return readActions(); },
    clear: function () { saveActions([]); },
    actions: readActions,
    status: function () { return {recording: isRecording(), count: readActions().length, version: VERSION}; }
  };

  window.__dpRecorder.start();
  return window.__dpRecorder.status();
})();
"""


def inject_recorder(page) -> dict:
    return page.run_js(_RECORDER_JS)


def stop_recorder(page) -> list:
    script = """
    if (window.__dpRecorder) {
      return window.__dpRecorder.stop();
    }
    try {
      return JSON.parse(localStorage.getItem('__dp_recorder_actions_global__') || sessionStorage.getItem('__dp_recorder_actions__') || '[]');
    } catch (e) {
      return [];
    }
    """
    return page.run_js(script) or []


def get_recorded_actions(page) -> list:
    script = """
    if (window.__dpRecorder) return window.__dpRecorder.actions();
    try {
      return JSON.parse(localStorage.getItem('__dp_recorder_actions_global__') || sessionStorage.getItem('__dp_recorder_actions__') || '[]');
    } catch (e) {
      return [];
    }
    """
    return page.run_js(script) or []


def clear_recorded_actions(page) -> None:
    script = """
    if (window.__dpRecorder) window.__dpRecorder.clear();
    try { sessionStorage.removeItem('__dp_recorder_actions__'); } catch (e) {}
    try { localStorage.removeItem('__dp_recorder_actions_global__'); } catch (e) {}
    """
    page.run_js(script)


def get_recorder_status(page) -> dict:
    script = """
    if (window.__dpRecorder) return window.__dpRecorder.status();
    try {
      const actions = JSON.parse(localStorage.getItem('__dp_recorder_actions_global__') || sessionStorage.getItem('__dp_recorder_actions__') || '[]');
      const state = JSON.parse(sessionStorage.getItem('__dp_recorder_state__') || '{}');
      return {recording: state.recording === true, count: actions.length, version: null};
    } catch (e) {
      return {recording: false, count: 0, version: null};
    }
    """
    return page.run_js(script) or {'recording': False, 'count': 0, 'version': None}


def format_actions_text(actions: list, raw: bool = False) -> str:
    if raw:
        return json.dumps(actions, ensure_ascii=False, indent=2)
    if not actions:
        return '### Recorded Actions\n\n暂无录制操作'
    lines = ['### Recorded Actions', '']
    for i, action in enumerate(actions, start=1):
        typ = action.get('type')
        element = action.get('element') or {}
        label = _element_label(element)
        locator = action.get('best_locator') or element.get('best_locator') or ''
        if typ == 'fill':
            lines.append(f'{i}. fill {label} = "{action.get("value", "")}"')
        elif typ == 'select':
            lines.append(f'{i}. select {label} = "{action.get("value", "")}"')
        elif typ == 'check':
            checked = '选中' if action.get('checked') else '取消选中'
            lines.append(f'{i}. check {label} → {checked}')
        elif typ == 'click':
            lines.append(f'{i}. click {label}')
        elif typ == 'press':
            lines.append(f'{i}. press {action.get("key", "")} on {label}')
        elif typ == 'scroll':
            delta = action.get('delta') or {}
            direction = action.get('direction') or ''
            lines.append(f'{i}. scroll {label} 向{_direction_zh(direction)} dx={delta.get("x", 0)} dy={delta.get("y", 0)}')
            mouse = action.get('mouse') or {}
            if mouse:
                lines.append(f'   mouse: x={mouse.get("x")}, y={mouse.get("y")}')
        else:
            lines.append(f'{i}. {typ or "action"} {label}')
        if locator:
            lines.append(f'   locator: {locator}')
        page = action.get('page') or {}
        if page.get('url'):
            lines.append(f'   url: {page.get("url")}')
        lines.append('')
    return '\n'.join(lines).rstrip()


def _element_label(element: dict) -> str:
    attrs = element.get('attrs') or {}
    for key in ('aria-label', 'placeholder', 'name', 'id'):
        if attrs.get(key):
            return f'「{attrs[key]}」'
    text = element.get('text')
    if text:
        return f'「{text}」'
    tag = element.get('tag') or 'element'
    cls = attrs.get('class')
    if cls:
        return f'{tag}.{cls.split()[0]}'
    return tag


def _direction_zh(direction: str) -> str:
    return {
        'down': '下',
        'up': '上',
        'left': '左',
        'right': '右',
    }.get(direction, direction or '未知')
