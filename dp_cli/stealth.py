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

_JS_MARKER_HEAD = r"""
// 调试 marker，用 `dp eval "return window.__stealth__"` 验证注入是否生效
(() => {
  window.__stealth__ = window.__stealth__ || { applied: [], errors: {}, at: Date.now() };
})();
"""


def _mark(feature):
    """生成每个特性执行后的 marker 记录代码。"""
    return (f"try {{ window.__stealth__.applied.push('{feature}'); }} catch(e) {{}}\n")


_JS_WEBDRIVER = r"""
// 删除 navigator.webdriver
(() => {
  try {
    Object.defineProperty(Navigator.prototype, 'webdriver', {
      get: () => undefined, configurable: true,
    });
    delete Object.getPrototypeOf(navigator).webdriver;
  } catch (e) { window.__stealth__.errors.webdriver = String(e); }
})();
"""

_JS_CHROME_RUNTIME = r"""
// 补 window.chrome.runtime（headless 下常缺失；直接赋值常被冻结）
(() => {
  try {
    if (!window.chrome) {
      Object.defineProperty(window, 'chrome', {
        value: {}, configurable: true, writable: true, enumerable: true,
      });
    }
    if (!window.chrome.runtime) {
      Object.defineProperty(window.chrome, 'runtime', {
        value: {
          OnInstalledReason: { CHROME_UPDATE: 'chrome_update', INSTALL: 'install', SHARED_MODULE_UPDATE: 'shared_module_update', UPDATE: 'update' },
          OnRestartRequiredReason: { APP_UPDATE: 'app_update', OS_UPDATE: 'os_update', PERIODIC: 'periodic' },
          PlatformArch: { ARM: 'arm', ARM64: 'arm64', MIPS: 'mips', MIPS64: 'mips64', X86_32: 'x86-32', X86_64: 'x86-64' },
          PlatformNaclArch: { ARM: 'arm', MIPS: 'mips', MIPS64: 'mips64', X86_32: 'x86-32', X86_64: 'x86-64' },
          PlatformOs: { ANDROID: 'android', CROS: 'cros', LINUX: 'linux', MAC: 'mac', OPENBSD: 'openbsd', WIN: 'win' },
          RequestUpdateCheckStatus: { NO_UPDATE: 'no_update', THROTTLED: 'throttled', UPDATE_AVAILABLE: 'update_available' },
        },
        configurable: true, writable: true, enumerable: true,
      });
    }
  } catch (e) { window.__stealth__.errors.chrome_runtime = String(e); }
})();
"""

_JS_PERMISSIONS = r"""
// 修正 Notification.permission 和 permissions.query 返回合理值
// headless 下 Notification.permission === 'denied' 是强 bot 信号，改成 'default'
(() => {
  try {
    // 1) 覆盖 Notification.permission
    try {
      Object.defineProperty(Notification, 'permission', {
        get: () => 'default', configurable: true,
      });
    } catch (e1) {}
    // 2) permissions.query 返回一致的结果
    const orig = window.navigator.permissions && window.navigator.permissions.query;
    if (!orig) return;
    window.navigator.permissions.query = (parameters) => (
      parameters && parameters.name === 'notifications'
        ? Promise.resolve({ state: 'default', onchange: null })
        : orig.call(window.navigator.permissions, parameters)
    );
  } catch (e) { window.__stealth__.errors.permissions = String(e); }
})();
"""

_JS_HARDWARE = r"""
// 伪造硬件信息：核心数 / 内存（VPS 只有 2-4 核，真实桌面 >=8）
(() => {
  try {
    Object.defineProperty(Navigator.prototype, 'hardwareConcurrency', {
      get: () => __HW_CORES__, configurable: true,
    });
    Object.defineProperty(Navigator.prototype, 'deviceMemory', {
      get: () => __HW_MEMORY__, configurable: true,
    });
  } catch (e) { window.__stealth__.errors.hardware = String(e); }
})();
"""

_JS_UA_DATA = r"""
// 补 navigator.userAgentData（Client Hints）
// 当 UA 声称是 Chrome 147 但 userAgentData 缺失时，是明显的矛盾信号
(() => {
  try {
    const brands = __UA_BRANDS__;
    const fake = {
      brands: brands,
      mobile: false,
      platform: '__UA_PLATFORM__',
      getHighEntropyValues: function(hints) {
        return Promise.resolve({
          architecture: 'x86', bitness: '64', model: '',
          platform: '__UA_PLATFORM__', platformVersion: '__UA_PLATFORM_VER__',
          uaFullVersion: '__UA_FULL_VER__', wow64: false,
          fullVersionList: brands.map(b => ({brand: b.brand, version: b.version + '.0.0.0'})),
          brands: brands,
        });
      },
      toJSON: function() { return {brands, mobile: false, platform: '__UA_PLATFORM__'}; },
    };
    Object.defineProperty(Navigator.prototype, 'userAgentData', {
      get: () => fake, configurable: true,
    });
  } catch (e) { window.__stealth__.errors.ua_data = String(e); }
})();
"""

_JS_PLUGINS = r"""
// 伪造 navigator.plugins（Chrome 147 原生已有 5 个 PDF 插件，这里保留代码以防以后变化）
(() => {
  try {
    const fakePlugin = (name, filename, desc) => {
      const p = Object.create(Plugin.prototype);
      Object.defineProperties(p, {
        name: { value: name }, filename: { value: filename },
        description: { value: desc }, length: { value: 1 },
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
  } catch (e) { window.__stealth__.errors.plugins = String(e); }
})();
"""

_JS_LANGUAGES = r"""
// 覆盖 navigator.languages（双重保险：实例 + 原型）
(() => {
  try {
    const val = __LANGS__;
    const def = { get: () => val, configurable: true };
    // 实例优先（避免原型被忽略的情况）
    try { Object.defineProperty(navigator, 'languages', def); } catch (e1) {}
    // 原型兜底
    try { Object.defineProperty(Navigator.prototype, 'languages', def); } catch (e2) {}
    // language 单数也补一下
    try { Object.defineProperty(navigator, 'language', { get: () => val[0], configurable: true }); } catch (e3) {}
  } catch (e) { window.__stealth__.errors.languages = String(e); }
})();
"""

_JS_WEBGL = r"""
// Hook WebGL getParameter 伪造 VENDOR/RENDERER。用 defineProperty 强制替换
(() => {
  try {
    const patch = (Ctor) => {
      if (!Ctor) return;
      const proto = Ctor.prototype;
      const orig = proto.getParameter;
      const fakeGet = function(p) {
        // UNMASKED_VENDOR_WEBGL=37445, UNMASKED_RENDERER_WEBGL=37446
        // RENDERER=7937, VENDOR=7936, VERSION=7938, SHADING_LANG=35724
        if (p === 37445 || p === 7936) return '__VENDOR__';
        if (p === 37446 || p === 7937) return '__RENDERER__';
        return orig.apply(this, arguments);
      };
      Object.defineProperty(proto, 'getParameter', {
        value: fakeGet, configurable: true, writable: true, enumerable: false,
      });
      // 同时 hook getExtension，确保 WEBGL_debug_renderer_info 可用
      const origGetExt = proto.getExtension;
      Object.defineProperty(proto, 'getExtension', {
        value: function(name) {
          const r = origGetExt.apply(this, arguments);
          if (r || name !== 'WEBGL_debug_renderer_info') return r;
          return { UNMASKED_VENDOR_WEBGL: 37445, UNMASKED_RENDERER_WEBGL: 37446 };
        },
        configurable: true, writable: true,
      });
    };
    patch(window.WebGLRenderingContext);
    patch(window.WebGL2RenderingContext);
  } catch (e) { window.__stealth__.errors.webgl = String(e); }
})();
"""

_JS_WINDOW_DIMS = r"""
// headless 下 outerWidth/outerHeight 为 0，伪造为 innerWidth/innerHeight
(() => {
  try {
    if (window.outerWidth === 0 || window.outerHeight === 0) {
      Object.defineProperty(window, 'outerWidth',  { get: () => window.innerWidth,  configurable: true });
      Object.defineProperty(window, 'outerHeight', { get: () => window.innerHeight, configurable: true });
    }
  } catch (e) { window.__stealth__.errors.window_dims = String(e); }
})();
"""


# ===== 预设 =====

PRESETS = {
    # 仅修 webdriver + UA（最小改动，性能最好）
    'mild':   {'webdriver', 'ua', 'window_dims'},
    # 常用全套 + 硬件/userAgentData（推荐默认，应对一般反爬）
    'full':   {'webdriver', 'ua', 'ua_data', 'chrome_runtime', 'permissions',
               'plugins', 'languages', 'webgl', 'window_dims', 'hardware'},
}

DEFAULT_UA = ('Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
              '(KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36')
DEFAULT_LANGS = ['zh-CN', 'zh', 'en-US', 'en']
DEFAULT_WEBGL_VENDOR = 'Intel Inc.'
DEFAULT_WEBGL_RENDERER = 'Intel Iris OpenGL Engine'
DEFAULT_HW_CORES = 8
DEFAULT_HW_MEMORY = 8
# 默认 Chrome 147 品牌信息
DEFAULT_UA_BRANDS = [
    {'brand': 'Google Chrome', 'version': '147'},
    {'brand': 'Chromium', 'version': '147'},
    {'brand': 'Not)A;Brand', 'version': '24'},
]
DEFAULT_UA_PLATFORM = 'Linux'  # 与 UA 里的 X11; Linux 对齐
DEFAULT_UA_PLATFORM_VER = '6.5.0'
DEFAULT_UA_FULL_VER = '147.0.0.0'


def build_init_script(
    features: set,
    langs: list[str] | None = None,
    webgl_vendor: str | None = None,
    webgl_renderer: str | None = None,
) -> str:
    """根据启用的 features 构建初始化脚本（不含 UA，UA 由 CDP 直接覆盖）。

    生成脚本在最开头创建 window.__stealth__ marker，每个 feature 执行后
    push 到 applied 列表；异常时 errors[feature] 记录错误。
    可用 `dp eval "return window.__stealth__"` 验证是否生效。
    """
    parts = [_JS_MARKER_HEAD]

    def add(feature, js):
        parts.append(js)
        parts.append(_mark(feature))

    if 'webdriver' in features:
        add('webdriver', _JS_WEBDRIVER)
    if 'chrome_runtime' in features:
        add('chrome_runtime', _JS_CHROME_RUNTIME)
    if 'permissions' in features:
        add('permissions', _JS_PERMISSIONS)
    if 'plugins' in features:
        add('plugins', _JS_PLUGINS)
    if 'languages' in features:
        langs = langs or DEFAULT_LANGS
        add('languages', _JS_LANGUAGES.replace(
            '__LANGS__', '[' + ','.join(f'"{x}"' for x in langs) + ']'))
    if 'webgl' in features:
        add('webgl', _JS_WEBGL
            .replace('__VENDOR__', (webgl_vendor or DEFAULT_WEBGL_VENDOR))
            .replace('__RENDERER__', (webgl_renderer or DEFAULT_WEBGL_RENDERER)))
    if 'window_dims' in features:
        add('window_dims', _JS_WINDOW_DIMS)
    if 'hardware' in features:
        add('hardware', _JS_HARDWARE
            .replace('__HW_CORES__', str(DEFAULT_HW_CORES))
            .replace('__HW_MEMORY__', str(DEFAULT_HW_MEMORY)))
    if 'ua_data' in features:
        import json as _json
        add('ua_data', _JS_UA_DATA
            .replace('__UA_BRANDS__', _json.dumps(DEFAULT_UA_BRANDS))
            .replace('__UA_PLATFORM_VER__', DEFAULT_UA_PLATFORM_VER)
            .replace('__UA_PLATFORM__', DEFAULT_UA_PLATFORM)
            .replace('__UA_FULL_VER__', DEFAULT_UA_FULL_VER))
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

    # 1) UA 覆盖（通过 CDP，作用于所有请求头 + navigator.userAgent）
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
    if not script:
        return applied

    # 2a) 直接用 CDP 注册，避开 DrissionPage add_init_js 的潜在封装问题
    try:
        page.run_cdp('Page.enable')
    except Exception:
        pass
    try:
        result = page.run_cdp('Page.addScriptToEvaluateOnNewDocument',
                              source=script)
        applied['init_js_id'] = result.get('identifier') if isinstance(result, dict) else result
    except Exception as e:
        applied['init_js_error'] = str(e)

    # 2b) 立刻对当前文档注入一次
    # 关键改动：用 as_expr=True 走 Runtime.evaluate 路径，避免被包装为
    # callFunctionOn(function(){...}) 导致的 objectId 失效 / this 绑定问题
    try:
        page.run_js(script, as_expr=True)
        applied['applied_to_current'] = True
    except Exception as e:
        applied['applied_to_current'] = False
        applied['current_error'] = str(e)

    return applied
