# Changelog

本项目遵循 [Semantic Versioning](https://semver.org/lang/zh-CN/)。

## [0.7.0] - 2026-10-14

### 新增

- 断言命令：`assert-text` / `assert-url` / `assert-visible` / `assert-count`（`--min`/`--max` 范围），断言失败以 `ASSERT_FAILED`（退出码 4）输出 JSON，可直接用于 `&&` 串联与 CI 检查。
- `wait` 新增 `--js`（JS 表达式轮询）与 `--response`（一步式网络响应等待，自动清理监听器）。
- console/error 捕获：`console-listen` 注册 CDP `Runtime.consoleAPICalled`/`Runtime.exceptionThrown`/`Log.entryAdded` 回调（缓冲上限 1000），`console-dump` 读取记录（`--keep` 保持监听、`--clear` 清缓冲）。
- 对话框应答：`dialog-accept [text]` / `dialog-dismiss`。
- 环境模拟：`emulate`（`--viewport`/`--ua`/`--locale`/`--timezone`/`--geolocation`/`--mobile`/`--reset`，经 CDP Emulation domain，会话持久化重放）。
- 下载与拦截：`download`（下载完成等待 + `--timeout`）、`intercept`（`--block` 资源屏蔽、`--header` 自定义请求头、`--mock`/`--mock-status`/`--mock-body`/`--mock-header` 请求 mock、`--mock-clear`/`--off`）。
- 观测能力：`perf`（Performance.getMetrics + Navigation Timing）、`trace`（Tracing 事件流落盘为 traceEvents JSON）、`screencast`（帧截图序列）。
- `har-dump`：Network 事件聚合导出 HAR 1.2（`--body`/`--output`/`--filter`）。
- `--frame` 作用域：`click`/`fill`/`assert-*` 等 13 个命令支持在指定 iframe 内定位元素。
- `cookie-list` 输出补充 `secure`/`httpOnly`/`sameSite`/`expires` 字段。
- 全局 debug 日志通道：`dp -v/--verbose` 或 `DP_DEBUG=1`，输出至 stderr 不污染 stdout JSON。
- 错误码分级退出：`ERROR_CODES` 注册表与 `ERROR_CODES.md` 契约文档（usage=2 / connect=3 / not_found=4 / timeout=5 / runtime=1），未注册码向后兼容回落 1。
- 新增 `LICENSE`（BSD-3-Clause）、`CHANGELOG.md`、`.github/workflows/release.yml`（tag `v*` → 版本一致性校验 → build → twine upload）。

### 修复

- `frame_ctx` 对真实 `NoneElement` 用 `getattr('_type')` 判定会抛 `ElementNotFoundError`，`--frame` 未命中误以 exit1 退出；改为类名判定，正确走 `ELEMENT_NOT_FOUND`（exit4）。
- `cookie-list --domain` 裸 `localhost`/IPv4/IPv6 曾被误判为顶级后缀拒绝，现加白名单放行。
- `emulate` viewport 不再硬编码 `mobile=True`，支持 `--mobile/--no-mobile`。
- `console` 回调对 `value=0/False/''` 的 falsy 参数改为按键存在判断，不再丢失。
- `download` 依赖的 `_dl_mgr` 私有 API 增加防御性回退；`--timeout 0` 边界修复（循环前先读一次 missions）。
- `assert-count` 位置参数与 `--min/--max` 并用时按声明互斥返回 `INVALID_ARGS`（exit2），不再静默取位置参数。
- 34 处静默 `except: pass` 改为 `debug()` 记录，便于 `DP_DEBUG=1` 排障。

### 测试

- 非集成测试由 333 增至 366；新增 `tests/conftest.py` 共享 fixture（FakePage 工厂、isolated session dir、http server）。
- CI 覆盖率门禁 `--cov-fail-under=58`（实测水位 ~59%，后续逐步上调）。

## [0.6.3] - 2026-10-14

### 新增

- `sessionstorage-delete` 命令，补齐 localStorage/sessionStorage CRUD 对称性。
- `config-set` 无参数时提示使用 `--help`。

### 修复

- `snapshot --brief` 长文本节点不截断（`_CONTENT_ROLES` early return 跳过截断逻辑）。
- iframe 节点定位器生成 `UnboundLocalError` 回退为 text 定位；iframe 分支复用 `describeNode` 结果消除一次冗余 CDP 调用。
- `_build_dom_bid_map` 缺少 `pierce=True` 导致 shadow DOM 元素无法进入映射；`_build_tree` 多根节点（如 iframe）丢失其余子树。
- a11y XPath/属性选择器引号未转义导致的 JS 注入风险（改用 `json.dumps`）。
- `http-get`/`http-post` 在响应为 None 时 `AttributeError`；`--output` 现在写入原始字节。
- `listen-stop` 在 `wait()` 抛异常时监听器残留（改 `try/finally` 保证 `stop`）。
- `cookie-list --url` 未剥离 userinfo；`--domain` 子串匹配误中 `notexample.com`，改为域边界匹配。
- `press` 特殊键名映射错误：`PageUp`→`PAGEUP` 未命中 `Keys.PAGE_UP`，退化为字面输入并在修饰键组合下误触浏览器快捷键；新增别名表与归一化。
- `record-start` 对 `url=None` 直接切片崩溃；active tab 查找改为 `get_tab(targetId)` 直查。
- `recorder` `findScrollable` 空值防御；`js_scripts` 回退快照穿透 open shadow DOM、过滤哈希类名、兼容 `SVGElement.className.baseVal`。

### 测试

- 新增 a11y 转义/iframe/storage/network/keyboard/record 等回归用例，非集成测试由 268 增至 297。
- `test_bridge_integration` 标记 `@pytest.mark.integration`；新增真实浏览器冒烟用例 `test_browser_smoke.py`；CI 启用 integration job。
