# -*- coding:utf-8 -*-
"""浏览器操作录制器：记录点击、输入、选择、按键与滚动。"""
import json
import time

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

  // 导航事件监听已移除，专注于页面内操作录制
  document.addEventListener('click', onClick, true);
  document.addEventListener('input', onInput, true);
  document.addEventListener('change', onInput, true);
  document.addEventListener('keydown', onKeyDown, true);
  document.addEventListener('wheel', onWheel, true);

  console.log('[dp-recorder] initialized, version:', VERSION);

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
    except Exception:
        pass
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
        return json.dumps(actions, ensure_ascii=False, indent=2)
    if fmt == 'dp':
        return _export_dp_script(actions)
    if fmt == 'playwright':
        return _export_playwright_sync_script(actions)
    if fmt == 'playwright-async':
        return _export_playwright_async_script(actions)
    if fmt == 'selenium':
        return _export_selenium_script(actions)
    raise ValueError(f'不支持的导出格式: {fmt}')


def _export_dp_script(actions: list) -> str:
    lines = ['#!/usr/bin/env bash', 'set -e', '']
    for action in actions:
        typ = action.get('type')
        locator = _action_locator(action)
        if typ == 'click' and locator:
            lines.append(f'dp click {_shell_quote(locator)}')
        elif typ == 'fill' and locator:
            lines.append(f'dp fill {_shell_quote(locator)} {_shell_quote(action.get("value", ""))}')
        elif typ == 'select' and locator:
            lines.append(f'dp select {_shell_quote(locator)} {_shell_quote(action.get("value", ""))}')
        elif typ == 'check' and locator:
            lines.append(f'# check: {locator} -> {action.get("checked")}')
            lines.append(f'dp click {_shell_quote(locator)}')
        elif typ == 'press':
            key = action.get('key') or ''
            lines.append(f'dp press {_shell_quote(key)}')
        elif typ == 'scroll':
            delta = action.get('delta') or {}
            mouse = action.get('mouse') or {}
            cmd = f'dp scroll --x {int(delta.get("x", 0) or 0)} --y {int(delta.get("y", 0) or 0)}'
            if locator:
                cmd += f' --locator {_shell_quote(locator)}'
            if mouse.get('x') is not None and mouse.get('y') is not None:
                cmd += f' --mouse-x {int(mouse.get("x"))} --mouse-y {int(mouse.get("y"))}'
            lines.append(cmd)
        else:
            lines.append(f'# unsupported action: {json.dumps(action, ensure_ascii=False)}')
    return '\n'.join(lines).rstrip() + '\n'


def _export_playwright_sync_script(actions: list) -> str:
    lines = [
        'from playwright.sync_api import sync_playwright',
        '',
        '',
        'def run():',
        '    with sync_playwright() as p:',
        '        browser = p.chromium.launch(headless=False)',
        '        page = browser.new_page()',
    ]
    first_url = _first_url(actions)
    if first_url:
        lines.append(f'        page.goto({_py_quote(first_url)})')
    for action in actions:
        typ = action.get('type')
        selector = _playwright_selector(action)
        if typ == 'click' and selector:
            lines.append(f'        page.locator({_py_quote(selector)}).click()')
        elif typ == 'fill' and selector:
            lines.append(f'        page.locator({_py_quote(selector)}).fill({_py_quote(action.get("value", ""))})')
        elif typ == 'select' and selector:
            lines.append(f'        page.locator({_py_quote(selector)}).select_option({_py_quote(action.get("value", ""))})')
        elif typ == 'check' and selector:
            method = 'check' if action.get('checked') else 'uncheck'
            lines.append(f'        page.locator({_py_quote(selector)}).{method}()')
        elif typ == 'press':
            key = action.get('key') or ''
            if selector:
                lines.append(f'        page.locator({_py_quote(selector)}).press({_py_quote(key)})')
            else:
                lines.append(f'        page.keyboard.press({_py_quote(key)})')
        elif typ == 'scroll':
            delta = action.get('delta') or {}
            mouse = action.get('mouse') or {}
            if mouse.get('x') is not None and mouse.get('y') is not None:
                lines.append(f'        page.mouse.move({mouse.get("x")}, {mouse.get("y")})')
            lines.append(f'        page.mouse.wheel({delta.get("x", 0)}, {delta.get("y", 0)})')
    lines.extend([
        '',
        '',
        'if __name__ == "__main__":',
        '    run()',
    ])
    return '\n'.join(lines) + '\n'


def _export_playwright_async_script(actions: list) -> str:
    lines = [
        'from playwright.async_api import async_playwright',
        '',
        '',
        'async def run():',
        '    async with async_playwright() as p:',
        '        browser = await p.chromium.launch(headless=False)',
        '        page = await browser.new_page()',
    ]
    first_url = _first_url(actions)
    if first_url:
        lines.append(f'        await page.goto({_py_quote(first_url)})')
    for action in actions:
        typ = action.get('type')
        selector = _playwright_selector(action)
        if typ == 'click' and selector:
            lines.append(f'        await page.locator({_py_quote(selector)}).click()')
        elif typ == 'fill' and selector:
            lines.append(f'        await page.locator({_py_quote(selector)}).fill({_py_quote(action.get("value", ""))})')
        elif typ == 'select' and selector:
            lines.append(f'        await page.locator({_py_quote(selector)}).select_option({_py_quote(action.get("value", ""))})')
        elif typ == 'check' and selector:
            method = 'check' if action.get('checked') else 'uncheck'
            lines.append(f'        await page.locator({_py_quote(selector)}).{method}()')
        elif typ == 'press':
            key = action.get('key') or ''
            if selector:
                lines.append(f'        await page.locator({_py_quote(selector)}).press({_py_quote(key)})')
            else:
                lines.append(f'        await page.keyboard.press({_py_quote(key)})')
        elif typ == 'scroll':
            delta = action.get('delta') or {}
            mouse = action.get('mouse') or {}
            if mouse.get('x') is not None and mouse.get('y') is not None:
                lines.append(f'        await page.mouse.move({mouse.get("x")}, {mouse.get("y")})')
            lines.append(f'        await page.mouse.wheel({delta.get("x", 0)}, {delta.get("y", 0)})')
    lines.extend([
        '',
        '',
        '# import asyncio',
        '# asyncio.run(run())',
    ])
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
        lines.append(f'    driver.get({_py_quote(first_url)})')
    lines.append('    actions = ActionChains(driver)')
    for action in actions:
        typ = action.get('type')
        locator = _action_locator(action)
        if typ == 'click' and locator:
            lines.append(f'    find(driver, {_py_quote(locator)}).click()')
        elif typ == 'fill' and locator:
            lines.append(f'    el = find(driver, {_py_quote(locator)})')
            lines.append('    el.clear()')
            lines.append(f'    el.send_keys({_py_quote(action.get("value", ""))})')
        elif typ == 'select' and locator:
            lines.append(f'    Select(find(driver, {_py_quote(locator)})).select_by_value({_py_quote(action.get("value", ""))})')
        elif typ == 'check' and locator:
            lines.append(f'    el = find(driver, {_py_quote(locator)})')
            if action.get('checked'):
                lines.append('    if not el.is_selected():')
                lines.append('        el.click()')
            else:
                lines.append('    if el.is_selected():')
                lines.append('        el.click()')
        elif typ == 'press':
            key = _selenium_key(action.get('key') or '')
            locator = locator or ''
            if locator:
                lines.append(f'    find(driver, {_py_quote(locator)}).send_keys({key})')
            else:
                lines.append(f'    actions.send_keys({key}).perform()')
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


def _shell_quote(value) -> str:
    value = str(value)
    return "'" + value.replace("'", "'\"'\"'") + "'"


def _py_quote(value) -> str:
    return repr(str(value))


def _selenium_key(key: str) -> str:
    mapping = {
        'Enter': 'Keys.ENTER',
        'Escape': 'Keys.ESCAPE',
        'Tab': 'Keys.TAB',
    }
    return mapping.get(key, repr(key))


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
