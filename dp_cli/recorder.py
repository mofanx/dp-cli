# -*- coding:utf-8 -*-
"""浏览器操作录制器：记录点击、输入、选择、按键与滚动。"""
import json
import re
import shlex
import time

from dp_cli.logs import debug

_RECORDER_JS = r"""
(function () {
  if (window.__dpRecorder && window.__dpRecorder.version) {
    window.__dpRecorder.start();
    recordPageEntry();
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
      const nameData = JSON.parse(window.name || '{}');
      if (nameData && Array.isArray(nameData.__dpRecorderActions)) return nameData.__dpRecorderActions;
    } catch (e) {
    }
    try {
      const stored = localStorage.getItem(GLOBAL_STORE_KEY) || sessionStorage.getItem(STORE_KEY);
      if (stored) return JSON.parse(stored);
    } catch (e) {
    }
    return [];
  }

  function saveActions(actions) {
    try {
      sessionStorage.setItem(STORE_KEY, JSON.stringify(actions));
    } catch (e) {}
    try {
      localStorage.setItem(GLOBAL_STORE_KEY, JSON.stringify(actions));
    } catch (e) {}
    try {
      const nameData = JSON.parse(window.name || '{}');
      nameData.__dpRecorderActions = actions;
      window.name = JSON.stringify(nameData);
    } catch (e) {
      try { window.name = JSON.stringify({__dpRecorderActions: actions}); } catch (err) {}
    }
  }

  function readState() {
    try {
      const nameData = JSON.parse(window.name || '{}');
      if (nameData && nameData.__dpRecorderState) return nameData.__dpRecorderState;
    } catch (e) {
    }
    try {
      const stored = sessionStorage.getItem(STATE_KEY);
      if (stored) return JSON.parse(stored);
    } catch (e) {
    }
    return {};
  }

  function saveState(state) {
    try {
      sessionStorage.setItem(STATE_KEY, JSON.stringify(state));
    } catch (e) {}
    try {
      const nameData = JSON.parse(window.name || '{}');
      nameData.__dpRecorderState = state;
      window.name = JSON.stringify(nameData);
    } catch (e) {
      try { window.name = JSON.stringify({__dpRecorderState: state}); } catch (err) {}
    }
  }

  function isRecording() {
    return readState().recording === true;
  }

  function setRecording(recording) {
    const state = readState();
    state.recording = recording;
    if (recording && !state.sessionId) state.sessionId = String(now()) + '-' + Math.random().toString(16).slice(2);
    if (recording && !state.startedAt) state.startedAt = now();
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

  function recordPageEntry() {
    // 简化版本：只初始化/恢复录制状态，不检测导航
    const state = readState();
    console.log('[dp-recorder] recordPageEntry: recording=' + state.recording);
    
    // 更新当前页面信息
    state.lastUrl = location.href;
    state.lastTitle = document.title;
    state.lastEntryAt = now();
    state.updatedAt = now();
    // 确保 recording 标志持续
    if (!state.recording && state.recording !== false) {
      state.recording = true;
    }
    saveState(state);
  }

  function findScrollable(el) {
    let cur = (el && el.nodeType === 1) ? el : (el && el.parentElement);
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

  // 导航事件监听已移除，专注于页面内操作录制
  document.addEventListener('click', onClick, true);
  document.addEventListener('input', onInput, true);
  document.addEventListener('change', onInput, true);
  document.addEventListener('keydown', onKeyDown, true);
  document.addEventListener('wheel', onWheel, true);

  window.__dpRecorder = {
    version: VERSION,
    start: function () { setRecording(true); },
    stop: function () { setRecording(false); return readActions(); },
    clear: function () {
      saveActions([]);
      try { sessionStorage.removeItem(STORE_KEY); } catch (e) {}
      try { localStorage.removeItem(GLOBAL_STORE_KEY); } catch (e) {}
      try { sessionStorage.removeItem(STATE_KEY); } catch (e) {}
      try {
        const nameData = JSON.parse(window.name || '{}');
        nameData.__dpRecorderActions = [];
        delete nameData.__dpRecorderState;
        window.name = JSON.stringify(nameData);
      } catch (e) {}
    },
    actions: readActions,
    status: function () { return {recording: isRecording(), count: readActions().length, version: VERSION}; }
  };

  window.__dpRecorder.start();
  recordPageEntry();
  return window.__dpRecorder.status();
})();
"""


def inject_recorder(page) -> dict:
    try:
        page.run_cdp('Page.addScriptToEvaluateOnNewDocument',
                     source=_RECORDER_JS)
    except Exception as e:
        debug('录制器 init_js 注册失败（仅当前页面内生效）', e)
    return page.run_js(_RECORDER_JS)


def stop_recorder(page) -> list:
    script = """
    if (window.__dpRecorder) {
      return window.__dpRecorder.stop();
    }
    try {
      const nameData = JSON.parse(window.name || '{}');
      const state = nameData.__dpRecorderState || {};
      state.recording = false;
      state.updatedAt = Date.now();
      delete state.pendingNavigation;
      nameData.__dpRecorderState = state;
      window.name = JSON.stringify(nameData);
    } catch (e) {}
    try {
      const state = JSON.parse(sessionStorage.getItem('__dp_recorder_state__') || '{}');
      state.recording = false;
      state.updatedAt = Date.now();
      delete state.pendingNavigation;
      sessionStorage.setItem('__dp_recorder_state__', JSON.stringify(state));
    } catch (e) {}
    try {
      const nameData = JSON.parse(window.name || '{}');
      if (nameData && Array.isArray(nameData.__dpRecorderActions)) return nameData.__dpRecorderActions;
    } catch (e) {}
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
      const nameData = JSON.parse(window.name || '{}');
      if (nameData && Array.isArray(nameData.__dpRecorderActions)) return nameData.__dpRecorderActions;
    } catch (e) {}
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
    try { sessionStorage.removeItem('__dp_recorder_state__'); } catch (e) {}
    try {
      const nameData = JSON.parse(window.name || '{}');
      nameData.__dpRecorderActions = [];
      delete nameData.__dpRecorderState;
      window.name = JSON.stringify(nameData);
    } catch (e) {}
    """
    page.run_js(script)


def get_recorder_status(page) -> dict:
    script = """
    if (window.__dpRecorder) return window.__dpRecorder.status();
    try {
      const nameData = JSON.parse(window.name || '{}');
      if (nameData && Array.isArray(nameData.__dpRecorderActions)) {
        const state = nameData.__dpRecorderState || {};
        return {recording: state.recording === true, count: nameData.__dpRecorderActions.length, version: null};
      }
    } catch (e) {}
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
        if typ == '_final':
            continue                # 伪动作：终态 url 标记
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


def export_actions(actions: list, fmt: str) -> str:
    if fmt == 'json':
        # _final 是 playwright 导出专用的伪动作标记，不进程序化消费面
        return json.dumps([a for a in actions if a.get('type') != '_final'],
                          ensure_ascii=False, indent=2)
    if fmt == 'dp':
        return _export_dp_script(actions)
    if fmt == 'playwright':
        return _export_playwright_sync_script(actions)
    if fmt == 'playwright-async':
        return _export_playwright_async_script(actions)
    if fmt == 'playwright-ts':
        return _export_playwright_ts_script(actions, standalone=False)
    if fmt == 'playwright-ts-async':
        return _export_playwright_ts_script(actions, standalone=True)
    if fmt == 'selenium':
        return _export_selenium_script(actions)
    raise ValueError(f'不支持的导出格式: {fmt}')


def _fallback_locators(action: dict, primary: str) -> list:
    """备用定位器链——best_locator 失效时 batch '||' 回退。"""
    element = action.get('element') or {}
    locs = element.get('locators') or {}
    alts = []
    for key in ('css', 'xpath'):
        v = locs.get(key) or ''
        if v and v != primary:
            alts.append(v)
    return alts


def _export_dp_script(actions: list) -> str:
    lines = ['#!/usr/bin/env bash',
             '# dpx 录制回放脚本——可直接 `dpx batch 本文件` 重放',
             '# "a || b" 是定位器回退链：首选失效自动降级',
             '']
    for action in actions:
        typ = action.get('type')
        if typ == '_final':
            continue                # 伪动作：终态 url 标记
        locator = _action_locator(action)
        fall = _fallback_locators(action, locator)

        def _with_fb(cmd):
            """附备用定位器回退链——'cmd || cmd2' 由 batch 逐段尝试。"""
            parts = [cmd]
            for alt in fall:
                parts.append(cmd.replace(shlex.quote(locator),
                                         shlex.quote(alt)))
            return ' || '.join(parts)

        if typ == 'click' and locator:
            lines.append(_with_fb(f'dpx click {shlex.quote(locator)}'))
        elif typ == 'fill' and locator:
            lines.append(_with_fb(
                f'dpx fill {shlex.quote(locator)} '
                f'{shlex.quote(action.get("value", ""))}'))
        elif typ == 'select' and locator:
            lines.append(_with_fb(
                f'dpx select {shlex.quote(locator)} '
                f'{shlex.quote(action.get("value", ""))}'))
        elif typ == 'check' and locator:
            lines.append(f'# check: {locator} -> {action.get("checked")}')
            lines.append(_with_fb(f'dpx click {shlex.quote(locator)}'))
        elif typ == 'press':
            key = action.get('key') or ''
            lines.append(f'dpx press {shlex.quote(key)}')
        elif typ == 'scroll':
            delta = action.get('delta') or {}
            mouse = action.get('mouse') or {}
            cmd = f'dpx scroll --x {int(delta.get("x", 0) or 0)} --y {int(delta.get("y", 0) or 0)}'
            if locator:
                cmd += f' --locator {shlex.quote(locator)}'
            if mouse.get('x') is not None and mouse.get('y') is not None:
                cmd += f' --mouse-x {int(mouse.get("x"))} --mouse-y {int(mouse.get("y"))}'
            lines.append(cmd)
        else:
            lines.append(f'# unsupported action: {json.dumps(action, ensure_ascii=False)}')
    return '\n'.join(lines).rstrip() + '\n'


def _export_playwright_script(actions: list, is_async: bool = False) -> str:
    # ponytail: 提取公共逻辑，减少80%重复代码
    prefix = 'async ' if is_async else ''
    await_kw = 'await ' if is_async else ''
    api_module = 'async_api' if is_async else 'sync_api'
    playwright_func = 'async_playwright' if is_async else 'sync_playwright'
    context_mgr = f'async with {playwright_func}() as p:' if is_async else f'with {playwright_func}() as p:'
    launch_line = f'        browser = {await_kw}p.chromium.launch(headless=False)'
    new_page_line = f'        page = {await_kw}browser.new_page()'
    
    lines = [
        f'from playwright.{api_module} import {playwright_func}, expect',
        '',
        '',
        f'{prefix}def run():',
        f'    {context_mgr}',
        launch_line,
        new_page_line,
    ]
    first_url = _first_url(actions)
    if first_url:
        lines.append(f'        {await_kw}page.goto({repr(str(first_url))})')
    for i, action in enumerate(actions):
        typ = action.get('type')
        loc = _pw_locator(action)   # 完整 page 定位器表达式（含 page. 前缀）
        if typ == 'click' and loc:
            lines.append(f'        {await_kw}{loc}.click()')
        elif typ == 'fill' and loc:
            lines.append(f'        {await_kw}{loc}.fill({repr(str(action.get("value", "")))})')
        elif typ == 'select' and loc:
            lines.append(f'        {await_kw}{loc}.select_option({repr(str(action.get("value", "")))})')
        elif typ == 'check' and loc:
            method = 'check' if action.get('checked') else 'uncheck'
            lines.append(f'        {await_kw}{loc}.{method}()')
        elif typ == 'press':
            key = action.get('key') or ''
            if loc:
                lines.append(f'        {await_kw}{loc}.press({repr(str(key))})')
            else:
                lines.append(f'        {await_kw}page.keyboard.press({repr(str(key))})')
        elif typ == 'scroll':
            delta = action.get('delta') or {}
            mouse = action.get('mouse') or {}
            if mouse.get('x') is not None and mouse.get('y') is not None:
                lines.append(f'        {await_kw}page.mouse.move({mouse.get("x")}, {mouse.get("y")})')
            lines.append(f'        {await_kw}page.mouse.wheel({delta.get("x", 0)}, {delta.get("y", 0)})')
        # 导航同步点：下一个动作的 page.url ≠ 本动作 url → 本动作
        # 触发了导航 → 断言新 URL（消除竞态）。末次导航由 stop 时
        # 补录的 _final pseudo-action 捕获。
        if i + 1 < len(actions):
            nxt_url = (actions[i + 1].get('page') or {}).get('url')
            cur_url = (action.get('page') or {}).get('url')
            if nxt_url and cur_url and nxt_url != cur_url:
                lines.append(
                    f'        {await_kw}expect(page).to_have_url('
                    f'{repr(str(nxt_url))})')
    if is_async:
        lines.extend([
            '',
            '',
            '# import asyncio',
            '# asyncio.run(run())',
        ])
    else:
        lines.extend([
            '',
            '',
            'if __name__ == "__main__":',
            '    run()',
        ])
    return '\n'.join(lines) + '\n'


def _export_playwright_sync_script(actions: list) -> str:
    return _export_playwright_script(actions, is_async=False)


def _export_playwright_async_script(actions: list) -> str:
    return _export_playwright_script(actions, is_async=True)


def _export_playwright_ts_script(actions: list, standalone: bool) -> str:
    """导出 TypeScript Playwright 脚本（驼峰 API + async/await）。

    standalone=False → Playwright Test 风格（.spec.ts）：
      import { test, expect } from '@playwright/test'
    standalone=True → 独立库脚本：
      import { chromium, expect } from 'playwright' + async function run()
    定位器语义复用 _pw_locator，仅经 py_loc_to_ts 做 API 层映射。
    """
    from dp_cli.pw_ts import py_loc_to_ts, ts_str
    ind = '  '
    if standalone:
        lines = [
            "import { chromium, expect } from 'playwright';",
            '',
            'async function run() {',
            f'{ind}const browser = await chromium.launch({{ headless: false }});',
            f'{ind}const page = await browser.newPage();',
        ]
    else:
        lines = [
            "import { test, expect } from '@playwright/test';",
            '',
            "test('dp-cli recording', async ({ page }) => {",
        ]
    first_url = _first_url(actions)
    if first_url:
        lines.append(f'{ind}await page.goto({ts_str(first_url)});')
    for i, action in enumerate(actions):
        typ = action.get('type')
        py_loc = _pw_locator(action)
        loc = py_loc_to_ts(py_loc) if py_loc else ''
        if typ == 'click' and loc:
            lines.append(f'{ind}await {loc}.click();')
        elif typ == 'fill' and loc:
            lines.append(
                f'{ind}await {loc}.fill({ts_str(action.get("value", ""))});')
        elif typ == 'select' and loc:
            lines.append(
                f'{ind}await {loc}.selectOption({ts_str(action.get("value", ""))});')
        elif typ == 'check' and loc:
            method = 'check' if action.get('checked') else 'uncheck'
            lines.append(f'{ind}await {loc}.{method}();')
        elif typ == 'press':
            key = action.get('key') or ''
            if loc:
                lines.append(f'{ind}await {loc}.press({ts_str(key)});')
            else:
                lines.append(f'{ind}await page.keyboard.press({ts_str(key)});')
        elif typ == 'scroll':
            delta = action.get('delta') or {}
            mouse = action.get('mouse') or {}
            if mouse.get('x') is not None and mouse.get('y') is not None:
                lines.append(
                    f'{ind}await page.mouse.move({mouse.get("x")}, {mouse.get("y")});')
            lines.append(
                f'{ind}await page.mouse.wheel({delta.get("x", 0)}, {delta.get("y", 0)});')
        # 导航同步点：与 py 版同规则——下一动作 page.url 变化 →
        # 断言着陆 URL；末次导航由 _final pseudo-action 捕获
        if i + 1 < len(actions):
            nxt_url = (actions[i + 1].get('page') or {}).get('url')
            cur_url = (action.get('page') or {}).get('url')
            if nxt_url and cur_url and nxt_url != cur_url:
                lines.append(
                    f'{ind}await expect(page).toHaveURL({ts_str(nxt_url)});')
    if standalone:
        lines.extend(['}', '', 'run();'])
    else:
        lines.append('});')
    return '\n'.join(lines) + '\n'


def _export_selenium_script(actions: list) -> str:
    lines = [
        'from selenium import webdriver',
        'from selenium.webdriver.common.by import By',
        'from selenium.webdriver.common.action_chains import ActionChains',
        'from selenium.webdriver.support.ui import Select',
        'import time',
        '',
        '',
        'def find(driver, locator):',
        '    if locator.startswith("xpath:"):',
        '        return driver.find_element(By.XPATH, locator[6:])',
        '    if locator.startswith("text:"):',
        '        text = locator[5:]',
        '        return driver.find_element(By.XPATH, f"//*[normalize-space()={text!r}]")',
        '    return driver.find_element(By.CSS_SELECTOR, locator)',
        '',
        '',
        'def run():',
        '    driver = webdriver.Chrome()',
    ]
    first_url = _first_url(actions)
    if first_url:
        lines.append(f'    driver.get({repr(str(first_url))})')
    lines.append('    actions = ActionChains(driver)')
    for action in actions:
        typ = action.get('type')
        if typ == '_final':
            continue                # 伪动作：终态 url 标记
        locator = _action_locator(action)
        if typ == 'click' and locator:
            lines.append(f'    find(driver, {repr(str(locator))}).click()')
        elif typ == 'fill' and locator:
            lines.append(f'    el = find(driver, {repr(str(locator))})')
            lines.append('    el.clear()')
            lines.append(f'    el.send_keys({repr(str(action.get("value", "")))})')
        elif typ == 'select' and locator:
            lines.append(f'    Select(find(driver, {repr(str(locator))})).select_by_value({repr(str(action.get("value", "")))})')
        elif typ == 'check' and locator:
            lines.append(f'    el = find(driver, {repr(str(locator))})')
            if action.get('checked'):
                lines.append('    if not el.is_selected():')
                lines.append('        el.click()')
            else:
                lines.append('    if el.is_selected():')
                lines.append('        el.click()')
        elif typ == 'press':
            key = action.get('key') or ''
            key_map = {'Enter': 'Keys.ENTER', 'Escape': 'Keys.ESCAPE', 'Tab': 'Keys.TAB'}
            selenium_key = key_map.get(key, repr(key))
            locator = locator or ''
            if locator:
                lines.append(f'    find(driver, {repr(str(locator))}).send_keys({selenium_key})')
            else:
                lines.append(f'    actions.send_keys({selenium_key}).perform()')
        elif typ == 'scroll':
            delta = action.get('delta') or {}
            mouse = action.get('mouse') or {}
            if mouse.get('x') is not None and mouse.get('y') is not None:
                lines.append(f'    actions.move_by_offset({mouse.get("x")}, {mouse.get("y")}).perform()')
            lines.append(f'    driver.execute_script("window.scrollBy(arguments[0], arguments[1]);", {delta.get("x", 0)}, {delta.get("y", 0)})')
    lines.extend([
        '',
        '',
        'if __name__ == "__main__":',
        '    run()',
    ])
    return '\n'.join(lines) + '\n'


def _action_locator(action: dict) -> str:
    element = action.get('element') or {}
    return action.get('best_locator') or element.get('best_locator') or ''


# ── Playwright 语义定位器 ────────────────────────────────────────────────
# tag → ARIA role 映射（显式 role 属性优先；input 按 type 细分）
_PW_TAG_ROLE = {
    'button': 'button', 'select': 'combobox', 'textarea': 'textbox',
    'h1': 'heading', 'h2': 'heading', 'h3': 'heading', 'h4': 'heading',
    'h5': 'heading', 'h6': 'heading',
    'img': 'img', 'nav': 'navigation', 'main': 'main',
    'header': 'banner', 'footer': 'contentinfo', 'form': 'form',
    'table': 'table', 'li': 'listitem', 'ul': 'list', 'ol': 'list',
    'article': 'article', 'aside': 'complementary', 'section': 'region',
    'dialog': 'dialog', 'progress': 'progressbar',
}
_PW_INPUT_ROLE = {
    'text': 'textbox', 'email': 'textbox', 'password': 'textbox',
    'tel': 'textbox', 'url': 'textbox', 'search': 'searchbox',
    'checkbox': 'checkbox', 'radio': 'radio', 'number': 'spinbutton',
    'range': 'slider', 'button': 'button', 'submit': 'button',
    'reset': 'button',
}


def _pw_role(element: dict) -> str:
    """element.tag/attrs → Playwright ARIA role（无语义时返回 ''）。"""
    attrs = element.get('attrs') or {}
    if attrs.get('role'):
        return attrs['role']                        # 显式 role 属性
    tag = element.get('tag') or ''
    if tag == 'input':
        return _PW_INPUT_ROLE.get((attrs.get('type') or 'text').lower(), '')
    if tag == 'a':
        return 'link' if attrs.get('href') else ''  # 无 href 的 a 不是 link
    return _PW_TAG_ROLE.get(tag, '')


def _pw_locator(action: dict) -> str:
    """action → 完整 page 定位器表达式（get_by_* 优先，page.locator 兜底）。

    优先级对齐 Playwright 官方推荐：testid > role+name > label >
    placeholder > name(表单) > id > text > css。无语义元素回退
    page.locator(css)——与旧版输出一致（向后兼容）。
    """
    el = action.get('element') or {}
    attrs = el.get('attrs') or {}
    tag = el.get('tag') or ''
    text = (el.get('text') or '').strip()[:60]
    # accessible name：aria-label > innerText
    name = (attrs.get('aria-label') or '').strip() or text[:50]

    if attrs.get('data-testid'):
        return f"page.get_by_test_id({attrs['data-testid']!r})"
    if attrs.get('data-test'):
        return f"page.locator({('[data-test=' + json.dumps(attrs['data-test'], ensure_ascii=False) + ']')!r})"
    role = _pw_role(el)
    # role+accessible_name 最具语义；无 name 的纯 role 降为兜底
    # （get_by_role('textbox') 在多输入框页不唯一，让位 label/placeholder）
    if role and name:
        return f"page.get_by_role({role!r}, name={name!r})"
    if attrs.get('placeholder') and tag == 'input':
        return f"page.get_by_placeholder({attrs['placeholder']!r})"
    if attrs.get('name') and tag in ('input', 'select', 'textarea'):
        return f"page.locator({('[name=' + json.dumps(attrs['name'], ensure_ascii=False) + ']')!r})"
    if attrs.get('id'):
        # 合法 CSS 标识符用 #id（惯用可读）；含元字符（JSF form:field 型）
        # 落 [id="..."] 引号形式，否则选择器破裂
        eid = attrs['id']
        if re.fullmatch(r'[A-Za-z_][\w-]*', eid):
            return f"page.locator('#{eid}')"
        return f"page.locator({('[id=' + json.dumps(eid, ensure_ascii=False) + ']')!r})"
    if text and len(text) <= 40:
        return f"page.get_by_text({text!r})"
    if role:
        return f"page.get_by_role({role!r})"
    sel = _playwright_selector(action)
    return f"page.locator({sel!r})" if sel else ''


def _playwright_selector(action: dict) -> str:
    locator = _action_locator(action)
    if locator.startswith('css:'):
        return locator[4:]
    if locator.startswith('text:'):
        return f'text={locator[5:]}'
    element = action.get('element') or {}
    locators = element.get('locators') or {}
    css = locators.get('css') or ''
    if css.startswith('css:'):
        return css[4:]
    return locator


def _first_url(actions: list) -> str:
    for action in actions:
        page = action.get('page') or {}
        url = page.get('url')
        if url:
            return url
    return ''


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
