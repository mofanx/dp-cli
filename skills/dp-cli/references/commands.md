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
| 浏览器 | `open`, `close`, `close-all`, `list`, `stealth`, `delete-data` | 启动/关闭/列出会话，反检测补丁 |
| 导航 | `goto`, `reload`, `go-back`, `go-forward` | 页面跳转 |
| 快照 | `snapshot`, `scan` | 页面结构分析（snapshot=全页，scan=仅可点）；输出带 `[N]` 编号 |
| 提取 | `extract`, `query`, `find`, `inspect`, `dom`, `count` | 数据提取和元素查询 |
| 交互 | `click`, `dblclick`, `fill`, `clear`, `select`, `check`, `hover`, `drag`, `upload` | 元素操控 |
| 键盘 | `press`, `type`, `scroll`, `scroll-to`, `autoscroll` | 键盘输入与滚动 |
| 等待 | `wait` | `--loaded` / `--idle` / `--locator` / `--text` / `--locator-gone` / `--url` / `--title` / `--downloads-done` |
| 监听 | `listen`, `listen-stop` | 网络请求捕获 |
| 标签页 | `tab-list`, `tab-new`, `tab-select`, `tab-close` | 多标签页管理（支持标签页绑定/分离自动化与手动浏览） |
| 截图 | `screenshot`, `pdf` | 页面截图/PDF（支持全页截图、元素截图） |
| JS | `eval`, `add-init-js` | 执行 JavaScript / 持久注入脚本 |
| HTTP | `http-get`, `http-post` | 纯 HTTP 请求（无需浏览器） |
| 对话框 | `dialog-accept`, `dialog-dismiss` | alert/confirm/prompt 处理 |
| 录制 | `record-start`, `record-stop`, `record-show`, `record-clear`, `record-status`, `record-export` | 操作录制与导出 |
| 状态 | `state-save`, `state-load` | Cookie + localStorage 保存/恢复 |
| Cookie | `cookie-list`, `cookie-get`, `cookie-set`, `cookie-delete`, `cookie-clear` | Cookie 细粒度操作 |
| Storage | `localstorage-*`, `sessionstorage-*` | localStorage/sessionStorage 操作 |
| 窗口 | `resize`, `maximize` | 窗口控制 |
| 配置 | `config-set` | 浏览器路径/用户数据路径 |

## snapshot 模式 & 开关

| 选项 | 行为 |
|------|------|
| `--mode full` | 默认；完整内容 + clickable 补充 |
| `--mode interactive` | 精简（省 token），交互元素 + 关键内容（长文本截断） |
| `--mode brief` | `interactive` 的别名（向后兼容） |
| `--mode text` | 纯文本按阅读顺序 |
| `--selector CSS` | 只快照指定子树 |
| `--no-clickables` | 关闭 Vimium 风格补充探测，纯 a11y tree |
| `--include-low` | 启用 low 置信度（`?` 标记，含 `cursor:pointer` 启发式） |
| `--viewport-only` | 补充探测只看视口内（省 token、更快） |
| `--locator-priority`, `-p` | 自定义 locator 属性优先级（逗号分隔），如 `data-testid,data-test-id,id` |
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
dp scan --verbose                    # 显示 detection reason 和像素尺寸（调试用）
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
- **interactive / brief**：交互元素 + 关键内容（paragraph/heading/code/blockquote 长文本截断至 80 字符），跳过非关键内容，省 token
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
| `--channel auto\|stable\|beta\|dev\|canary\|chromium\|edge` | 搭配 `--auto-connect`，定位指定渠道的默认 profile | `auto`=嗅探 Chrome→Edge（默认）；`edge`=强制 Edge |
| `--probe-dir <path>` | 搭配 `--auto-connect`，显式指定 user-data-dir | 自定义 profile 路径 |
| `--stealth` | 连接后立即应用 full 反检测预设 | 目标站点有反爬/检测 |
| `--headless` | 无头模式（仅临时浏览器） | 不需要 GUI 的服务器环境 |
| `--proxy <url>` | 代理服务器（仅临时浏览器） | 需要代理访问目标站点 |
| `--new` | 强制新建会话（删除同名已有会话） | 会话状态混乱时 |
| （无连接参数） | dp 自管新启一个临时浏览器 | 仅纯公开页面、不需登录态 |

## close 命令

```bash
dp close                     # 关闭会话（--port/--auto-connect 默认只断开连接）
dp close --force             # 强制关闭浏览器进程（即使用户连接模式）
dp close --del-data          # 同时删除用户数据目录
dp close-all                 # 关闭所有会话
```

## stealth 命令

```
dp stealth                           → full 预设（推荐）
dp stealth --preset mild             → 只改 webdriver + UA
dp stealth --ua "Mozilla/5.0 ..."    → 自定义 User-Agent
dp stealth --feature webdriver --feature plugins   → 精细选择
dp stealth --langs "zh-CN,zh,en"     → 改 navigator.languages
dp stealth --webgl-vendor "Intel Inc." --webgl-renderer "Intel Iris OpenGL Engine"
```

full 预设修补：`webdriver` / `UA` / `chrome.runtime` / `permissions` / `plugins` / `languages` / `WebGL VENDOR&RENDERER` / `window.outerWidth&Height`

## autoscroll 命令

```
dp autoscroll --locator "css:.item"  # 按元素数量判断，自动滚到底
dp autoscroll                        # 按页面高度判断
dp autoscroll --container "#feed" --idle 3  # 容器内滚动，等网络空闲
dp autoscroll --fast --max 100       # 快速模式
dp autoscroll --step 800 --max 50    # 每轮滚 800px
dp autoscroll --stable 3             # 连续 3 轮无增长才停止（更耐心）
dp autoscroll --idle-timeout 15      # 网络空闲等待超时（默认 10s）
dp autoscroll --fast --fast-delay 0.1  # 快速模式，每轮等 0.1s
```

终止条件：连续 `--stable` 轮（默认 2）无增长 或 达到 `--max` 轮上限（默认 300）。

## count 命令

```
dp count ".item"              → css（自动识别 . 开头）
dp count "#list li"           → css（自动识别 # 开头）
dp count "//ul/li"            → xpath（自动识别 // 开头）
dp count "css:tr"             → 显式 css 前缀
dp count ".item" --timeout 5  → 等待元素出现
```

## listen 命令

```
dp listen --filter "api/xxx"        # URL 过滤关键字
dp listen --filter "api/xxx" --method POST  # 只捕获 POST
dp listen --count 5 --timeout 10    # 最多捕获 5 个，超时 10 秒
dp listen-stop                      # 停止并获取捕获数据
dp listen-stop --count 3 --timeout 5  # 等待 3 个数据包
```

## HTTP 命令（无需浏览器）

```
dp http-get "https://api.example.com/data" --output data.json
dp http-get "https://api.example.com" --headers '{"Authorization":"Bearer xxx"}'
dp http-get "https://example.com" --proxy http://127.0.0.1:7890

dp http-post "https://api.example.com/search" --data '{"keyword":"test"}'
dp http-post "https://example.com/form" --form '{"field":"value"}'
dp http-post "https://api.example.com" --data '{}' --headers '{"Authorization":"Bearer xxx"}'
```

| 选项 | 适用命令 | 说明 |
|------|---------|------|
| `--output PATH` | http-get | 响应体保存到文件 |
| `--headers JSON` | http-get, http-post | JSON 格式请求头 |
| `--proxy URL` | http-get, http-post | 代理地址 |
| `--data JSON` | http-post | JSON 请求体 |
| `--form JSON` | http-post | 表单数据 |
| `--timeout N` | http-get, http-post | 超时秒数（默认 30） |

## scroll 命令

```
dp scroll --y 300                   # 垂直滚动 300px
dp scroll --top                     # 滚动到顶部
dp scroll --bottom                  # 滚动到底部
dp scroll --locator "css:.feed" --y 500  # 在指定容器内滚动
dp scroll --locator "css:.feed" --bottom   # 容器滚到底
dp scroll-to "ref:20"               # 滚动到元素可见
```

## screenshot 命令

```
dp screenshot                       # 截图
dp screenshot --full-page           # 全页截图
dp screenshot --locator "ref:5"     # 元素截图
dp screenshot --filename page.png   # 保存路径
dp screenshot --format jpg          # 格式 png/jpg/jpeg
```

## state-save / state-load 命令

```
dp state-save my-site.json          # 保存 Cookie + localStorage（位置参数）
dp state-save                       # 默认文件名 state.json
dp state-load my-site.json          # 恢复状态
dp state-load                       # 默认文件名 state.json
```

## tab 命令

```
dp tab-list                         # 列出所有标签页（显示 [pinned] 标记）
dp tab-new "example.com"            # 新建标签页并自动绑定
dp tab-new "example.com" --new-window  # 新窗口中打开（自动化与手动浏览分离）
dp tab-new "example.com" --background  # 后台打开（不绑定）
dp tab-select example               # 按 URL 关键词绑定
dp tab-select 1                     # 按序号绑定（从 0 开始）
dp tab-select none                  # 解除绑定
dp tab-close                        # 关闭绑定的标签页
```

## wait 命令完整选项

```
dp wait --loaded               # DOM 加载完成
dp wait --idle                 # 网络空闲 2 秒（默认）
dp wait --idle 3               # 自定义空闲时长
dp wait --locator "#result"    # 等待元素出现
dp wait --locator-gone ".loading"  # 等待元素消失
dp wait --url "success"        # 等待 URL 变化
dp wait --title "搜索结果"     # 等待标题变化
dp wait --text "操作成功"      # 等待页面含文本
dp wait --downloads-done       # 等待下载完成
```

## 交互命令通用选项

| 选项 | 适用命令 | 说明 |
|------|---------|------|
| `--index N` | click, dblclick, fill, clear, select, hover | 第几个匹配元素（默认 1） |
| `--by-js` | click, fill | 使用 JavaScript 执行（绕过遮挡） |
| `--timeout N` | 所有交互命令 | 等待超时秒数（默认 10） |
| `--clear` | fill | 填入前清空（默认开启） |
| `--by-text` | select | 按文本选择（默认按 value） |
| `--by-index N` | select | 按位置索引选择（从 1 开始） |
| `--check/--uncheck` | check | 选中/取消选中 |
| `--duration N` | drag | 拖拽持续时间秒数（默认 0.5） |
| `--offset-x/--offset-y` | hover | 鼠标偏移量（像素） |

## 录制命令

```
dp record-start                     # 开始录制（绑定当前标签页）
dp record-start --append            # 追加到已有记录
dp record-stop                      # 停止，默认输出 dp 脚本格式
dp record-stop -f playwright        # 导出为 Playwright 脚本
dp record-stop -f selenium          # 导出为 Selenium 脚本
dp record-stop -f json -o actions.json  # 保存为 JSON
dp record-show                      # 查看已录制操作
dp record-show --raw                # 完整 JSON 格式
dp record-show --filename rec.json  # 保存到文件
dp record-status                    # 查看录制器状态
dp record-clear                     # 清空录制记录
dp record-export -f playwright-async -o test.py  # 单独导出
```

`record-stop` 格式：`dp` / `playwright` / `playwright-async` / `selenium` / `json` / `text`
`record-export` 格式：`dp` / `playwright` / `playwright-async` / `selenium` / `json`（不含 `text`）

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
| `PW_SYNTAX` | pw: 表达式语法错 | 检查 matcher 语法 |
| `PW_NOT_FOUND` | pw: 未匹配到元素 | 检查 role/text/selector 是否正确 |
| `REF_NOT_FOUND` | ref:N 不存在 | 重新 snapshot 获取新编号 |
| `NO_REFS` | 没有 ref 映射 | 先执行 `dp snapshot` |
| `RECORD_START_FAILED` | 启动录制失败 | 确认页面已连接 |
