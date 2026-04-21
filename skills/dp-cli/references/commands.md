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
| 提取 | `extract`, `query`, `find`, `inspect`, `dom` | 数据提取和元素查询 |
| 交互 | `click`, `dblclick`, `fill`, `clear`, `select`, `check`, `hover`, `scroll`, `scroll-to`, `drag`, `upload` | 元素操控 |
| 键盘 | `press`, `type` | 键盘输入 |
| 等待 | `wait` | `--loaded` / `--locator` / `--text` / `--locator-gone` / `--url` |
| 监听 | `listen`, `listen-stop` | 网络请求捕获 |
| 标签页 | `tab-list`, `tab-new`, `tab-select`, `tab-close` | 多标签页管理（支持标签页绑定/分离自动化与手动浏览） |
| 截图 | `screenshot`, `pdf` | 页面截图/PDF（支持全页截图、元素截图） |
| JS | `eval`, `add-init-js` | 执行 JavaScript |
| HTTP | `http-get`, `http-post` | 纯 HTTP 请求（无需浏览器） |
| 对话框 | `dialog-accept`, `dialog-dismiss` | alert/confirm/prompt 处理 |
| 状态 | `state-save`, `state-load` | Cookie + localStorage 保存/恢复 |
| Cookie | `cookie-list`, `cookie-get`, `cookie-set`, `cookie-delete`, `cookie-clear` | Cookie 细粒度操作 |
| Storage | `localstorage-*`, `sessionstorage-*` | localStorage/sessionStorage 操作 |
| 窗口 | `resize`, `maximize` | 窗口控制 |
| 配置 | `config-set`, `delete-data` | 浏览器路径/数据目录 |

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
