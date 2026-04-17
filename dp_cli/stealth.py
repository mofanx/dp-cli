# -*- coding:utf-8 -*-
"""反自动化检测补丁（stealth）。

通过 CDP 的 Page.addScriptToEvaluateOnNewDocument + Network.setUserAgentOverride
修补 headless/automation 特征。对当前会话所有 frame 及后续导航生效。

典型被检测的特征：
- navigator.webdriver === true
- User-Agent 包含 'HeadlessChrome'
- navigator.plugins 长度为 0
- navigator.languages 为空或异常
- WebGL VENDOR/RENDERER 暴露 SwiftShader/Google Inc.
- window.chrome 对象缺失
- Notification.permission === 'denied'
- window.outerWidth/outerHeight === 0

参考: puppeteer-extra-plugin-stealth / playwright-stealth
"""
from __future__ import annotations


# ===== JS 补丁脚本 =====

_JS_WEBDRIVER = r"""
// 删除 navigator.webdriver
(() => {
  try {
    Object.defineProperty(Navigator.prototype, 'webdriver', {
      get: () => undefined,
      configurable: true,
    });
    delete Navigator.prototype.webdriver;
  } catch (e) {}
})();
"""

_JS_CHROME_RUNTIME = r"""
// 补 window.chrome.runtime（headless 下通常缺失）
(() => {
  if (!window.chrome) {
    Object.defineProperty(window, 'chrome', {
      value: { runtime: {}, loadTimes: function(){}, csi: function(){}, app: {} },
      configurable: true, writable: true,
    });
  } else if (!window.chrome.runtime) {
    window.chrome.runtime = {};
  }
})();
"""

_JS_PERMISSIONS = r"""
// 修正 Notification.permission 在 headless 下被查询时的返回
(() => {
  try {
    const orig = window.navigator.permissions && window.navigator.permissions.query;
    if (!orig) return;
    window.navigator.permissions.query = (parameters) => (
      parameters && parameters.name === 'notifications'
        ? Promise.resolve({ state: Notification.permission, onchange: null })
        : orig.call(window.navigator.permissions, parameters)
    );
  } catch (e) {}
})();
"""

_JS_PLUGINS = r"""
// 伪造 navigator.plugins / mimeTypes 非空
(() => {
  try {
    const fakePlugin = (name, filename, desc) => {
      const p = Object.create(Plugin.prototype);
      Object.defineProperties(p, {
        name:        { value: name },
        filename:    { value: filename },
        description: { value: desc },
        length:      { value: 1 },
      });
      return p;
    };
    const plugins = [
      fakePlugin('PDF Viewer', 'internal-pdf-viewer', 'Portable Document Format'),
      fakePlugin('Chrome PDF Viewer', 'internal-pdf-viewer', 'Portable Document Format'),
      fakePlugin('Chromium PDF Viewer', 'internal-pdf-viewer', 'Portable Document Format'),
      fakePlugin('Microsoft Edge PDF Viewer', 'internal-pdf-viewer', 'Portable Document Format'),
      fakePlugin('WebKit built-in PDF', 'internal-pdf-viewer', 'Portable Document Format'),
    ];
    const arr = Object.create(PluginArray.prototype);
    plugins.forEach((p, i) => { arr[i] = p; });
    Object.defineProperty(arr, 'length', { value: plugins.length });
    Object.defineProperty(Navigator.prototype, 'plugins', {
      get: () => arr, configurable: true,
    });
  } catch (e) {}
})();
"""

_JS_LANGUAGES = r"""
// 覆盖 navigator.languages
(() => {
  try {
    Object.defineProperty(Navigator.prototype, 'languages', {
      get: () => __LANGS__, configurable: true,
    });
  } catch (e) {}
})();
"""

_JS_WEBGL = r"""
// Hook WebGL getParameter，伪造 VENDOR / RENDERER
(() => {
  try {
    const patch = (proto) => {
      if (!proto) return;
      const orig = proto.getParameter;
      proto.getParameter = function(p) {
        // UNMASKED_VENDOR_WEBGL = 37445, UNMASKED_RENDERER_WEBGL = 37446
        if (p === 37445) return '__VENDOR__';
        if (p === 37446) return '__RENDERER__';
        return orig.call(this, p);
      };
    };
    patch(window.WebGLRenderingContext && WebGLRenderingContext.prototype);
    patch(window.WebGL2RenderingContext && WebGL2RenderingContext.prototype);
  } catch (e) {}
})();
"""

_JS_WINDOW_DIMS = r"""
// headless 下 outerWidth/outerHeight 为 0，伪造为 innerWidth/innerHeight
(() => {
  try {
    if (window.outerWidth === 0) {
      Object.defineProperty(window, 'outerWidth',  { get: () => window.innerWidth,  configurable: true });
      Object.defineProperty(window, 'outerHeight', { get: () => window.innerHeight, configurable: true });
    }
  } catch (e) {}
})();
"""


# ===== 预设 =====

PRESETS = {
    # 仅修 webdriver + UA（最小改动，性能最好）
    'mild':   {'webdriver', 'ua', 'window_dims'},
    # 常用全套（推荐默认）
    'full':   {'webdriver', 'ua', 'chrome_runtime', 'permissions',
               'plugins', 'languages', 'webgl', 'window_dims'},
}

DEFAULT_UA = ('Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
              '(KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36')
DEFAULT_LANGS = ['zh-CN', 'zh', 'en-US', 'en']
DEFAULT_WEBGL_VENDOR = 'Intel Inc.'
DEFAULT_WEBGL_RENDERER = 'Intel Iris OpenGL Engine'


def build_init_script(
    features: set,
    langs: list[str] | None = None,
    webgl_vendor: str | None = None,
    webgl_renderer: str | None = None,
) -> str:
    """根据启用的 features 构建初始化脚本（不含 UA，UA 由 CDP 直接覆盖）。"""
    parts = []
    if 'webdriver' in features:
        parts.append(_JS_WEBDRIVER)
    if 'chrome_runtime' in features:
        parts.append(_JS_CHROME_RUNTIME)
    if 'permissions' in features:
        parts.append(_JS_PERMISSIONS)
    if 'plugins' in features:
        parts.append(_JS_PLUGINS)
    if 'languages' in features:
        langs = langs or DEFAULT_LANGS
        parts.append(_JS_LANGUAGES.replace('__LANGS__',
                     '[' + ','.join(f'"{x}"' for x in langs) + ']'))
    if 'webgl' in features:
        parts.append(_JS_WEBGL
                     .replace('__VENDOR__', (webgl_vendor or DEFAULT_WEBGL_VENDOR))
                     .replace('__RENDERER__', (webgl_renderer or DEFAULT_WEBGL_RENDERER)))
    if 'window_dims' in features:
        parts.append(_JS_WINDOW_DIMS)
    return '\n'.join(parts)


def apply_stealth(
    page,
    features: set | None = None,
    ua: str | None = None,
    langs: list[str] | None = None,
    webgl_vendor: str | None = None,
    webgl_renderer: str | None = None,
) -> dict:
    """对一个 ChromiumPage/ChromiumTab 应用 stealth 补丁。

    :param page: DrissionPage 的 ChromiumPage 或 ChromiumTab
    :param features: 要启用的特性集合。默认 PRESETS['full']
    :param ua: 自定义 UA；为 None 时使用 DEFAULT_UA（已去掉 Headless）
    :return: 实际应用的配置摘要
    """
    features = features or PRESETS['full']
    applied = {'features': sorted(features)}

    # 1) UA 覆盖（通过 CDP，作用于所有请求，最彻底）
    if 'ua' in features:
        ua = ua or DEFAULT_UA
        try:
            page.run_cdp('Network.setUserAgentOverride', userAgent=ua,
                         acceptLanguage=(langs or DEFAULT_LANGS)[0])
            applied['ua'] = ua
        except Exception as e:
            applied['ua_error'] = str(e)

    # 2) JS 初始化脚本（新文档加载前注入）
    script = build_init_script(features, langs=langs,
                               webgl_vendor=webgl_vendor,
                               webgl_renderer=webgl_renderer)
    if script:
        try:
            # DrissionPage 提供的封装
            js_id = page.add_init_js(script)
            applied['init_js_id'] = js_id
        except Exception as e:
            applied['init_js_error'] = str(e)

    # 3) 立刻对当前文档注入一次（避免当前页未刷新时无效果）
    if script:
        try:
            page.run_js(script)
            applied['applied_to_current'] = True
        except Exception:
            applied['applied_to_current'] = False

    return applied
