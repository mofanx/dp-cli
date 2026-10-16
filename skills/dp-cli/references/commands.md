# dp-cli 命令速查

> 所有命令的完整参数：`dp --help` / `dp <command> --help`
>
> 使用逻辑和工作流见主文件 `../SKILL.md`

## 全局选项

| 选项 | 说明 |
|------|------|
| `-s <name>` / `--session <name>` | 选择会话（不同 session 相互隔离） |

## 命令一览

| 类别 | 命令 | 说明 |
|------|------|------|
| 浏览器 | `open`, `close`, `close-all`, `list`, `stealth` | 启动/关闭/列出会话，反检测补丁 |
| 导航 | `goto`, `reload`, `go-back`, `go-forward` | 页面跳转 |
| 快照 | `snapshot`, `scan` | 页面结构分析（snapshot=全页，scan=仅可点）；输出带 `[N]` 编号 |
| 提取 | `extract`, `query`, `find`, `inspect`, `dom`, `count` | 数据提取和元素查询 |
| 交互 | `click`, `dblclick`, `fill`, `clear`, `select`, `check`, `hover`, `scroll`, `scroll-to`, `drag`, `upload` | 元素操控 |
| 键盘 | `press`, `type`, `autoscroll` | 键盘输入与自动滚动 |
| 等待 | `wait` | `--loaded` / `--locator` / `--text` / `--locator-gone` / `--url` / `--js` / `--response` |
| 监听 | `listen`, `listen-stop`, `har-dump` | 网络请求捕获；har-dump 聚合导出 HAR 1.2 |
| 拦截 | `intercept` | 屏蔽 URL 通配 / 附加请求头 / mock 响应（Fetch domain） |
| 断言 | `assert-text`, `assert-url`, `assert-visible`, `assert-count` | 断言失败 exit 4 + JSON 错误，可 `&&` 串联做脚本自检 |
| Console | `console-listen`, `console-dump` | 捕获 console/异常/日志事件（缓冲 1000 条） |
| 模拟 | `emulate`, `media`, `throttle`, `bypass-csp`, `file-chooser` | viewport/UA/locale/时区/地理位置/mobile 覆盖；媒体特性/网络与 CPU 节流/CSP 旁路/文件选择器拦截，均写入会话自动重放 |
| 下载 | `download` | 设置下载目录并等待任务完成 |
| 标签页 | `tab-list`, `tab-new`, `tab-select`, `tab-close` | 多标签页管理（支持标签页绑定/分离自动化与手动浏览） |
| 截图 | `screenshot`, `pdf` | 页面截图/PDF（支持全页截图、元素截图） |
| 观测 | `perf`, `trace`, `screencast` | 性能指标 / CDP Tracing 落盘 / 帧序列录制 |
| JS | `eval`, `add-init-js` | 执行 JavaScript |
| 剪贴板 | `clipboard-get`, `clipboard-set` | 读写系统剪贴板（需相应权限） |
| 权限 | `permissions` | CDP Browser.grantPermissions：geolocation/notifications/clipboard 等按 origin 授权 |
| HTTP | `http-get`, `http-post` | 纯 HTTP 请求（无需浏览器）；`--impersonate chrome131` 走 curl_cffi 模拟 TLS 指纹（pip install dp-cli[tls]） |
| 对话框 | `dialog-accept`, `dialog-dismiss` | alert/confirm/prompt 处理 |
| 录制 | `record-start`, `record-stop`, `record-show`, `record-export`, `record-clear`, `record-status` | 人工操作录制与脚本导出 |
| 状态 | `state-save`, `state-load` | Cookie + localStorage 保存/恢复 |
| Cookie | `cookie-list`, `cookie-get`, `cookie-set`, `cookie-delete`, `cookie-clear`, `cookie-export` | Cookie 细粒度操作；export 支持 Netscape 格式供 curl/curl_cffi 复用 |
| Storage | `localstorage-*`, `sessionstorage-*` | localStorage/sessionStorage 操作 |
| 窗口 | `resize`, `maximize` | 窗口控制 |
| 配置 | `config-set`, `delete-data` | 浏览器路径/数据目录 |
| 批量 | `batch`, `exec` | 单进程顺序执行多行子命令（文件/stdin，JSONL 输出，`-k` 失败继续，复用 attach） |

## 通用选项（多数元素命令均支持）

| 选项 | 说明 |
|------|------|
| `--frame <locator>` | 在指定 iframe 上下文内执行（click/fill/count/assert-* 等 13+ 命令），如 `--frame "css:iframe"` / `--frame "ref:8"` |
| `-v` / `--verbose` | 输出 debug 日志到 stderr（也可用环境变量 `DP_DEBUG=1`），不污染 stdout JSON |

## 断言命令（脚本自检 / CI 友好）

```
dp assert-text "登录成功"                    # 页面包含文本（exit 0/4）
dp assert-text "订单已提交" --locator "#msg"  # 限定元素范围
dp assert-url "dashboard"                    # URL 包含
dp assert-url "https://x.com/home" --exact   # URL 精确
dp assert-visible "#result"                  # 元素可见
dp assert-visible ".loading" --hidden        # 元素不可见/不存在
dp assert-count ".item" 20                   # 恰好 20 个
dp assert-count "li" --min 3 --max 10        # 区间
```

失败输出 `{"status":"error","code":"ASSERT_FAILED",...}` 并以退出码 4 退出，可直接 `&&` 串联。

## Console / 错误捕获

```
dp console-listen              # 开始捕获 console.* / 未捕获异常 / 日志条目
dp console-dump                # 读取已捕获记录并停止监听
dp console-dump --keep         # 读取后继续保持监听
dp console-dump --clear        # 仅清空缓冲区
```

## har-dump（网络录制 → HAR）

```
dp har-dump --seconds 10 --output trace.har    # 收集窗口内请求→HAR 1.2
dp har-dump --seconds 8 --filter "api/" --body # URL 过滤 + 补抓响应体
```

## intercept（拦截与 mock）

```
dp intercept --block "*.png" --block "*ads*"           # 屏蔽 URL 通配
dp intercept --header "Authorization=Bearer xxx"       # 附加请求头
dp intercept --mock "*api/user*" --mock-body '{"id":1}'        # mock 响应
dp intercept --mock "*api/list*" --mock-status 404 --mock-body "nf"
dp intercept --mock-clear                              # 仅清 mock
dp intercept --off                                     # 全部关闭
```

配置写入会话，后续 dp 命令自动重放。

## emulate（设备/环境模拟）

```
dp emulate --device iphone-15          # 设备预设一键组合（viewport+dpr+mobile+touch+UA）
dp emulate --device pixel-8            # 预设：iphone-15/iphone-se/pixel-8/galaxy-s24/ipad/desktop-chrome
dp emulate --viewport 375x812 --ua "Mozilla/5.0 (iPhone...)"   # 手写覆盖
dp emulate --viewport 1920x1080 --no-mobile                    # 桌面视口
dp emulate --locale zh-CN --timezone Asia/Shanghai
dp emulate --geolocation 39.9042,116.4074
dp emulate --reset                                             # 清除全部覆盖
```

## 环境/系统能力（media / throttle / bypass-csp / file-chooser / permissions / clipboard）

```
dp media --color-scheme dark                  # prefers-color-scheme；--type print 打印预览
dp media --reduced-motion / dp media --off
dp throttle --profile slow-3g                 # 预设：offline/slow-3g/fast-3g/4g
dp throttle --latency 200 --down 1024 --up 512
dp throttle --cpu 4                           # CPU 4x 降速；--off 全清
dp bypass-csp / dp bypass-csp --off           # Page.setBypassCSP
dp file-chooser ~/a.png --locator "css:.btn"  # 点击触发文件对话框→自动喂文件
dp permissions --list                         # 查看支持权限名
dp permissions --grant geolocation,notifications   # 默认取当前页 origin
dp permissions --reset
dp clipboard-set "text" / dp clipboard-get    # 自动尝试授予剪贴板权限
```

media/throttle/bypass-csp 均写入会话（browser_cdp），后续 dp 命令自动重放。

## stealth --check（指纹自检）

```
dp stealth --check     # 只读报告：webdriver/UA↔platform一致性/WebGL/时区/插件等
                       # 每项 status: ok|warn|fail + summary 计数；不打补丁
```

## 观测（perf / trace / screencast）

```
dp perf                            # Performance.getMetrics 关键子集 + Navigation Timing
dp perf --raw                      # 全部 metrics
dp trace --duration 10 --output trace.json   # CDP Tracing → 可导入 chrome://tracing/Perfetto
dp screencast --dir ./frames --duration 10   # 帧序列 jpg 落盘
```

## download

```
dp download --dir ~/Downloads --timeout 60     # 设置目录并等待下载完成
dp download --exists overwrite                 # 同名文件策略 rename/overwrite/skip
```

## wait 扩展条件

```
dp wait --js "window.appReady === true"        # JS 表达式轮询
dp wait --response "api/login"                 # 一步式等待匹配响应出现
dp wait --idle / --idle 3                      # 网络空闲（默认 2s）
```

## 错误码与退出码分级

错误 JSON 不变（`status/code/message/detail`），进程按错误码分级退出：
**0 成功 · 1 通用运行时 · 2 参数/用法（如 INVALID_ARGS）· 3 连接类（如 SESSION_NOT_FOUND）· 4 未找到/断言（如 ELEMENT_NOT_FOUND、ASSERT_FAILED）· 5 超时（如 WAIT_TIMEOUT）**
完整注册表见仓库 `ERROR_CODES.md`。

## 错误码速查

## snapshot 模式 & 开关

| 选项 | 行为 |
|------|------|
| `--mode full` | 默认；完整内容 + clickable 补充 |
| `--mode brief` | 精简（省 token），结构+交互保留 |
| `--mode text` | 纯文本按阅读顺序 |
| `--selector CSS` | 只快照指定子树 |
| `--no-clickables` | 关闭 Vimium 风格补充探测，纯 a11y tree |
| `--include-low` | 启用 low 置信度（`?` 标记，含 `cursor:pointer` 启发式） |
| `--viewport-only` | 补充探测只看视口内（省 token、更快） |
| `--format json` | JSON 原始结构输出 |
| `--filename PATH` | 保存到文件 |

## scan 命令（Vimium 风格，仅可交互元素）

```
dp scan                              # 扫全页，high+medium 置信度
dp scan --viewport                   # 只扫视口内
dp scan --confidence high            # 只要最确定的
dp scan --confidence high,medium     # 默认
dp scan --confidence all             # 包含 low（启发式）
dp scan --max 500                    # 限制最多返回
dp scan --format json                # JSON 输出
```

输出元素标记：
- 无标记 = **high**（`<a href>`, `<button>`, `role=button` 等明确可点）
- `⚡` = **medium**（`onclick` / `tabindex>=0` / `aria-selected` / `<audio>/<video>`）
- `?` = **low**（`cursor:pointer` / class 关键词匹配的启发式，可能假阳性）

## snapshot 输出示例

基于 a11y tree，每个元素有 `[N]` 编号，可用 `ref:N` 引用：

```
### Page Snapshot (full)
- URL: https://example.com/products
- Title: 产品列表
- Nodes: 842 total, 65 interactive, 72 refs — 使用 ref:N 引用元素

- RootWebArea "产品列表"
  - [1] link "首页" → text:首页
  - list
    - listitem "产品" [level=1]
      - [2] link "产品" → text:产品
    - listitem "分类" [level=1]
      - [3] link "分类" → text:分类
  - [17] textbox "搜索产品" → @placeholder=搜索产品
  - [21] link "电子设备" → text:电子设备
  - [56] heading "产品详情" [level=3] → .product-title
  - [57] paragraph "产品描述：这是一款高性能..."
  ...
```

操作时直接用编号：`dp click "ref:21"` / `dp fill "ref:17" "电子设备"` / `dp query "ref:57"`

- **full（默认）**：完整内容，零截断
- **brief**：截断长文本，跳过正文细节，保留结构+交互，省 token
- **text**：纯文本按阅读顺序输出

**每次 snapshot 后编号重新分配，页面变化后需重新 snapshot。**

## query --fields 可用字段

| 字段 | 说明 |
|------|------|
| `text` | 元素可见文本（过滤隐藏反爬文本） |
| `tag` | 标签名 |
| `loc` | 推荐定位器（可直接用于 click/fill） |
| `css` | 精确 CSS 路径（唯一定位） |
| `xpath` | 精确 XPath |
| `html` | innerHTML |
| `outer_html` | 完整 outerHTML |
| `href`/`src`/`id`/`class` | 常用属性 |
| 其他 | 任意 HTML 属性名 |

## dom 命令

```
dp dom "ref:21"                     → 查看父/子/兄弟全部
dp dom "ref:21" -d parent --depth 5 → 向上追溯，找容器
dp dom "ref:21" -d children         → 查看子节点
dp dom "ref:21" -d siblings         → 查看兄弟节点
```

## `pw:` Playwright 风格定位器

无需先 snapshot，直接语义定位，所有交互命令（click/fill/hover/check/...）都支持。

```
dp click 'pw:role=button[name="Submit"]'         # role + accessible name（精确）
dp click 'pw:role=button[name=/^Sign/i]'         # name 用正则，i=忽略大小写
dp click 'pw:role=link[name=More]'               # 裸值=子串匹配

dp click 'pw:text="登录"'                        # 精确文本
dp click 'pw:text=登录'                          # 子串文本
dp click 'pw:text=/^log/i'                       # 正则文本

dp fill  'pw:placeholder=搜索' "chatgpt"         # placeholder 属性
dp fill  'pw:label="邮箱"' "a@b.com"             # <label> 关联的控件
dp click 'pw:alt="Logo"' / 'pw:title="关闭"'
dp click 'pw:testid=submit-btn'                  # data-testid / data-test-id / data-test

# 链式 >>：每段缩小作用域
dp click 'pw:css=.sidebar >> role=listitem[name="Chat"] >> nth=2'
dp click 'pw:css=li >> has-text="Python"'        # has-text 作为过滤器
dp click 'pw:role=list >> nth=-1'                # nth 支持负数（-1=最后一个）
dp click 'pw:xpath=//nav >> role=link[name=Docs]'
```

**Matcher 全集**：`role` · `text` · `label` · `placeholder` · `alt` · `title` · `testid` · `css` · `xpath` · `nth` · `has-text` · `visible`

**值形式**：`裸值`=substring · `"引号"`=exact · `/pattern/flags`=regex（JS 语法，flags ∈ `gimsuy`）

**可见性**：`role` / `text` / `has-text` 默认过滤掉隐藏元素（`display:none` 链 / `hidden` / `aria-hidden=true`）；Shadow DOM 自动穿透。

**失败码**：
- `PW_SYNTAX` — 表达式语法错
- `PW_NOT_FOUND` — 没匹配到元素
- `PW_EVAL_FAILED` — JS 执行异常（极少见）

## open 连接模式速查

| 参数 | 行为 | 使用条件 |
|------|------|---------|
| `--auto-connect` | 自动发现 Chrome 调试端口（Chrome 144+，必要时起 bridge） | **首选**；需用户在 `chrome://inspect/#remote-debugging` 勾选 Allow |
| `--port <N>` | 连接用户用 `--remote-debugging-port=N` 启动的 Chrome | 旧版 Chrome 或用户已手动启动 |
| `--channel beta\|dev\|canary\|chromium` | 搭配 `--auto-connect`，定位非 stable 渠道的默认 profile | 只用非 stable Chrome 时 |
| `--probe-dir <path>` | 搭配 `--auto-connect`，显式指定 user-data-dir | 自定义 profile 路径 |
| `--stealth` | 连接后立即应用 full 反检测预设 | 目标站点有反爬/检测 |
| `--new` | 强制新建会话（删除同名已有会话） | 会话状态混乱时 |
| （无连接参数） | dp 自管新启一个临时浏览器 | 仅纯公开页面、不需登录态 |

## stealth 命令

```
dp stealth                           → full 预设（推荐）
dp stealth --preset mild             → 只改 webdriver + UA
dp stealth --ua "Mozilla/5.0 ..."    → 自定义 User-Agent
dp stealth --feature webdriver --feature plugins   → 精细选择
dp stealth --langs "zh-CN,zh,en"     → 改 navigator.languages
```

full 预设修补：`webdriver` / `UA` / `chrome.runtime` / `permissions` / `plugins` / `languages` / `WebGL VENDOR&RENDERER` / `window.outerWidth&Height`

## 错误码速查

| code | 含义 | 典型处理 |
|------|------|---------|
| `AUTOCONNECT_FAILED` | 读不到 DevToolsActivePort | 指引用户在 `chrome://inspect` 勾选 Allow |
| `BROWSER_START_FAILED` + "timed out" | bridge 等待 Allow 超时 | 提示用户点 Chrome 中的 Allow |
| `SESSION_NOT_FOUND` | `dp close`/`dp dom` 等命令找不到会话 | 先执行 `dp open --auto-connect` |
| `TAB_NOT_FOUND` | `dp tab-select N` 的 N 越界 | 先 `dp tab-list` 看索引 |
| `CONFLICTING_OPTIONS` | `--auto-connect` 与 `--port` 同用 | 二选一 |
| `NAVIGATE_FAILED` | 导航失败（网络/超时/白名单） | 调 `--timeout` 或检查网络/代理 |
| `STEALTH_FAILED` | 注入补丁失败 | 先确认页面已连接；切到空页再重试 |

## 完整命令清单（自动生成，勿手改）

由 `python -m dp_cli.catalog --md` 生成；新增命令后重跑刷新，test_docs 守卫覆盖完整性。

<!-- GENERATED by `python -m dp_cli.catalog --md` —— 勿手改 -->

| 类别 | 命令 | 参数 |
|------|------|------|
| 浏览器 | `bypass-csp` | [session], [off] |
| 浏览器 | `cf-wait` | [session], [timeout], [poll], [force_poll] |
| 浏览器 | `clipboard-get` | [session] |
| 浏览器 | `clipboard-set` | <text>, [session] |
| 浏览器 | `close` | [session], [del_data], [force] |
| 浏览器 | `close-all` |  |
| 浏览器 | `delete-data` | [session] |
| 浏览器 | `file-chooser` | <files>, [session], <loc>, [timeout] |
| 浏览器 | `go-back` | [session] |
| 浏览器 | `go-forward` | [session] |
| 浏览器 | `goto` | <url>, [session], [timeout], [retry] |
| 浏览器 | `list` |  |
| 浏览器 | `media` | [session], [color_scheme], [media_type], [reduced_motion], [off] |
| 浏览器 | `open` | [url], [session], [headless], [browser_path], [user_data_dir], [proxy], [port], [auto_connect], [channel], [probe_dir], [new], [stealth] |
| 浏览器 | `permissions` | [session], [grants], [origin], [reset], [list_names] |
| 浏览器 | `reload` | [session] |
| 浏览器 | `stealth` | [session], [preset], [ua], [langs], [webgl_vendor], [webgl_renderer], [feature], [check] |
| 浏览器 | `throttle` | [session], [profile], [latency], [down], [up], [cpu], [off] |
| 导航/页面 | `config-set` | [browser_path], [user_path], [copy_config] |
| 导航/页面 | `maximize` | [session] |
| 导航/页面 | `resize` | <width>, <height>, [session] |
| 导航/页面 | `state-load` | [filename], [session] |
| 导航/页面 | `state-save` | [filename], [fmt], [session] |
| 快照 | `dom` | <locator>, [session], [direction], [depth], [index], [timeout] |
| 快照 | `dom-snapshot` | [session], [loc], [styles], [no_rects], [no_paint], [output] |
| 快照 | `extract` | [session], <container>, <fields_json>, [limit], [output_fmt], [filename] |
| 快照 | `find` | <locator>, [session], [find_all], [timeout] |
| 快照 | `inspect` | <locator>, [session], [index], [include_rect], [include_style], [timeout] |
| 快照 | `query` | [session], <selector>, [fields], [limit], [filename] |
| 快照 | `scan` | [session], [viewport_only], [confidence], [max_elements], [fmt], [filename], [verbose] |
| 快照 | `snapshot` | [session], [mode], [interactive], [selector], [fmt], [filename], [no_clickables], [include_low], [viewport_only], [locator_priority], [max_depth], [max_nodes], [pw_locators], [since] |
| 提取/交互 | `check` | <locator>, [session], [check], [frame], [timeout] |
| 提取/交互 | `clear` | <locator>, [session], [index], [frame], [timeout] |
| 提取/交互 | `click` | <locator>, [session], [index], [by_js], [human], [human_min], [human_max], [frame], [timeout] |
| 提取/交互 | `count` | <locator>, [session], [frame], [timeout] |
| 提取/交互 | `dblclick` | <locator>, [session], [index], [human], [human_min], [human_max], [frame], [timeout] |
| 提取/交互 | `drag` | <from_locator>, <to_locator>, [session], [duration], [frame], [timeout] |
| 提取/交互 | `fill` | <locator>, <value>, [session], [index], [clear], [by_js], [human], [human_min], [human_max], [frame], [timeout] |
| 提取/交互 | `hover` | <locator>, [session], [index], [offset_x], [offset_y], [frame], [timeout] |
| 提取/交互 | `select` | <locator>, <value>, [session], [index], [by_text], [sel_by_index], [frame], [timeout] |
| 提取/交互 | `upload` | <locator>, <file_path>, [session], [frame], [timeout] |
| 键盘/滚动 | `autoscroll` | [locator], [container], [max_rounds], [stable], [idle], [idle_timeout], [step], [fast], [fast_delay], [mouse_x], [mouse_y], [session] |
| 键盘/滚动 | `press` | <key>, [session], [human], [human_min], [human_max] |
| 键盘/滚动 | `scroll` | [x], [y], [top], [bottom], [locator], [mouse_x], [mouse_y], [session] |
| 键盘/滚动 | `scroll-to` | <locator>, [session] |
| 键盘/滚动 | `type` | <text>, [session], [human], [human_min], [human_max] |
| 网络 | `har-dump` | [session], [seconds], [url_filter], [output], [with_body] |
| 网络 | `http-get` | <url>, [headers], [proxy], [timeout], [output], [impersonate] |
| 网络 | `http-post` | <url>, [data], [form], [headers], [proxy], [timeout], [output], [impersonate] |
| 网络 | `listen` | [session], [url_filter], [count], [timeout], [method] |
| 网络 | `listen-stop` | [session], [count], [timeout], [output], [fields], [head] |
| 断言 | `assert-count` | <locator>, [expected], [session], [min_count], [max_count], [frame], [timeout] |
| 断言 | `assert-text` | <text>, [session], [locator], [exact], [frame], [timeout] |
| 断言 | `assert-url` | <text>, [session], [exact], [check_title] |
| 断言 | `assert-visible` | <locator>, [session], [expect_hidden], [frame], [timeout] |
| 断言 | `expect-gen` | [ele_loc], [gen_all], [limit], [no_count], [lang], [session], [frame] |
| 模拟/下载/拦截 | `download` | [session], [download_dir], [timeout], [when_exists] |
| 模拟/下载/拦截 | `emulate` | [session], [device], [viewport], [mobile], [ua], [locale], [timezone], [geolocation], [reset] |
| 模拟/下载/拦截 | `intercept` | [session], [blocks], [headers], [mocks], [mock_status], [mock_body], [mock_headers], [mock_clear], [off] |
| 观测 | `perf` | [session], [raw] |
| 观测 | `screencast` | [session], <out_dir>, [duration] |
| 观测 | `trace` | [session], [duration], [out_file], [categories] |
| 标签页 | `tab-close` | [index_or_id], [session] |
| 标签页 | `tab-list` | [session] |
| 标签页 | `tab-new` | [url], [session], [background], [new_window] |
| 标签页 | `tab-select` | <target>, [session] |
| 存储 | `cookie-clear` | [session] |
| 存储 | `cookie-delete` | <name>, [session] |
| 存储 | `cookie-export` | [session], [domain], [fmt], [output] |
| 存储 | `cookie-get` | <name>, [session] |
| 存储 | `cookie-list` | [session], [domain], [url], [fields], [head] |
| 存储 | `cookie-set` | <name>, <value>, [session], [domain], [path], [http_only], [secure] |
| 存储 | `localstorage-clear` | [session] |
| 存储 | `localstorage-delete` | <key>, [session] |
| 存储 | `localstorage-get` | <key>, [session] |
| 存储 | `localstorage-list` | [session] |
| 存储 | `localstorage-set` | <key>, <value>, [session] |
| 存储 | `sessionstorage-clear` | [session] |
| 存储 | `sessionstorage-delete` | <key>, [session] |
| 存储 | `sessionstorage-get` | <key>, [session] |
| 存储 | `sessionstorage-list` | [session] |
| 存储 | `sessionstorage-set` | <key>, <value>, [session] |
| 录制 | `record-clear` | [session] |
| 录制 | `record-export` | [session], [fmt], [output], [filename] |
| 录制 | `record-show` | [session], [raw], [filename] |
| 录制 | `record-start` | [session], [clear_first], [append] |
| 录制 | `record-status` | [session] |
| 录制 | `record-stop` | [session], [fmt], [output], [filename], [raw] |
| 页面 | `add-init-js` | <script>, [session] |
| 页面 | `console-dump` | [session], [keep], [clear_buf], [fields], [head] |
| 页面 | `console-listen` | [session] |
| 页面 | `dialog-accept` | [text], [session] |
| 页面 | `dialog-dismiss` | [session] |
| 页面 | `eval` | <script>, [session], [locator], [timeout] |
| 页面 | `pdf` | [session], [filename] |
| 页面 | `screenshot` | [session], [locator], [filename], [full_page], [fmt] |
| 页面 | `wait` | [session], [url], [title], [locator], [locator_gone], [text], [loaded], [idle], [downloads_done], [js_expr], [resp_pattern], [timeout], [force_poll] |
| 批量 | `batch` | [file], [keep_going], [verbose], [no_summary] |
| 批量 | `exec` | [file], [keep_going], [verbose], [no_summary] |
| 批量 | `serve` | [session] |
| 批量 | `sh` | [session] |
| 逆向 | `hook-clear` | [hid], [all_], [buf], [session] |
| 逆向 | `hook-dump` | [out], [clear], [fields], [head], [session] |
| 逆向 | `hook-list` | [session] |
| 逆向 | `hook-set` | <htype>, [path], [name], [url], [method], [when], [hid], [persist], [session] |
| 逆向 | `rpc-call` | <name>, [args], [session] |
| 逆向 | `rpc-list` | [session] |
| 逆向 | `rpc-register` | <name>, <js>, [session], [persist] |
| 逆向 | `rpc-unregister` | <name>, [session] |
| 提取 | `read` | [session], [selector], [fmt], [output] |
| 元信息 | `catalog` | [as_md], [as_json] |
| 元信息 | `install` | [with_skills], [directory], [all_layouts], [check_only], [agents_md] |
