# dp-cli 错误码与退出码契约

所有命令失败时向 stdout 输出统一 JSON（便于 AI/脚本消费）：

```json
{"status": "error", "code": "<CODE>", "message": "...", "detail": "..."}
```

进程按错误码分级退出（`dp_cli/output.py::ERROR_CODES` 为唯一注册表）：

| 退出码 | 类别 | 语义 |
|---|---|---|
| 0 | — | 成功 |
| 1 | runtime | 通用运行时失败（默认；未注册的错误码也归入此类） |
| 2 | usage | 参数/用法错误（与 click 自身的用法错误退出码一致） |
| 3 | connect | 连接/网络类失败（可重试） |
| 4 | not_found | 元素/资源未找到、断言类失败 |
| 5 | timeout | 超时 |

## 错误码注册表

新增错误码时：①在 `dp_cli/output.py::ERROR_CODES` 中登记退出码与说明；
②在本文件对应分组中补一行。

### usage（退出码 2）

| code | 说明 |
|---|---|
| `INVALID_ARGS` | 参数组合/取值无效 |
| `INVALID_CONFIDENCE` | 置信度参数越界 |
| `INVALID_JSON` | JSON 参数解析失败 |
| `CONFLICTING_OPTIONS` | 互斥选项同时使用 |
| `PW_SYNTAX` | pw 定位器语法错误 |
| `FILE_NOT_FOUND` | 指定文件不存在 |

### connect（退出码 3）

| code | 说明 |
|---|---|
| `SESSION_NOT_FOUND` | 无法连接浏览器会话（需先 `dp open`） |
| `BROWSER_START_FAILED` | 浏览器启动/连接失败 |
| `AUTOCONNECT_FAILED` | auto-connect 自动发现失败 |
| `NAVIGATE_FAILED` | 页面导航失败 |
| `HTTP_FAILED` | HTTP 请求失败（http-get/http-post） |

### not_found（退出码 4）

| code | 说明 |
|---|---|
| `ELEMENT_NOT_FOUND` | 元素未找到 |
| `TAB_NOT_FOUND` | 标签页不存在 |
| `REF_NOT_FOUND` | ref 编号不在映射中 |
| `REF_UNRESOLVABLE` | ref 无法解析为有效定位器 |
| `COOKIE_NOT_FOUND` | Cookie 不存在 |
| `PW_NOT_FOUND` | pw 定位器未匹配到元素 |
| `PROFILE_NOT_FOUND` | 指定 profile 不存在 |
| `NO_REFS` | 无可用的 ref 映射（需先 `dp snapshot`） |
| `ASSERT_FAILED` | 断言失败（页面状态不符合预期） |

### timeout（退出码 5）

| code | 说明 |
|---|---|
| `WAIT_TIMEOUT` | 等待超时（wait 系列） |

### runtime（退出码 1）

| code | 说明 |
|---|---|
| `CLICK_FAILED` / `FILL_FAILED` / `TYPE_FAILED` / `KEY_FAILED` | 元素交互/按键失败 |
| `SCROLL_FAILED` / `AUTOSCROLL_FAILED` | 滚动失败 |
| `HOVER_FAILED` / `CHECK_FAILED` / `SELECT_FAILED` / `DRAG_FAILED` / `CLEAR_FAILED` | 元素操作失败 |
| `UPLOAD_FAILED` | 文件上传失败 |
| `COUNT_FAILED` | 计数失败 |
| `CLOSE_FAILED` / `RELOAD_FAILED` | 关闭/刷新失败 |
| `STEALTH_FAILED` | stealth 应用失败 |
| `TAB_FAILED` | 标签页操作失败 |
| `LISTEN_FAILED` | 网络监听数据获取失败 |
| `WAIT_FAILED` | 等待操作失败（wait 系列非超时类失败） |
| `CONSOLE_FAILED` | console 记录读取失败 |
| `JS_FAILED` / `INIT_JS_FAILED` | JS 执行/注入失败 |
| `DIALOG_FAILED` | 对话框操作失败 |
| `SCREENSHOT_FAILED` / `PDF_FAILED` | 截图/PDF 导出失败 |
| `RESIZE_FAILED` / `WINDOW_FAILED` | 窗口操作失败 |
| `STATE_FAILED` | 状态获取/保存失败 |
| `INSPECT_FAILED` | 元素探测失败 |
| `SNAPSHOT_FAILED` / `SCAN_FAILED` | 快照/clickable 扫描失败 |
| `EXTRACT_FAILED` / `FIND_FAILED` / `QUERY_FAILED` / `DOM_FAILED` | 数据提取/DOM 查询失败 |
| `COOKIE_FAILED` / `STORAGE_FAILED` | Cookie/Storage 操作失败 |
| `PW_EVAL_FAILED` | pw 定位器求值失败 |
| `RECORD_*_FAILED`（START/STOP/STATUS/SHOW/EXPORT/CLEAR） | 录制相关失败 |
| `EMULATE_FAILED` / `DOWNLOAD_FAILED` / `INTERCEPT_FAILED` | 环境模拟/下载/拦截失败 |
| `PERF_FAILED` / `TRACE_FAILED` / `SCREENCAST_FAILED` | 性能指标/trace/screencast 失败 |

## 兼容性说明

- 消费方应优先判断 `status`/`code` 字段，而非退出码；退出码分级自 v0.6.3 起生效，
  旧脚本中 `exit code != 0` 的判定不受影响。
- `detail` 字段携带底层异常信息，仅在排障时使用，不保证稳定格式。
