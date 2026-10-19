---
name: dp-cli
description: 使用 dp-cli 控制浏览器、提取网页数据、自动化操作。当用户需要打开网页、点击元素、填写表单、截图、抓取列表数据、监听网络请求、操作 Cookie/Storage，或者需要连接自己已登录的浏览器进行操作时，使用这个技能。特别适合：批量提取结构化数据（商品/职位/新闻列表）、需要保留登录状态的自动化、需要穿透 shadow-root/iframe 的复杂页面操作。
allowed-tools: "Bash(dp:*) Bash(python -m dp_cli:*) Bash(python -m dp_cli.catalog:*)"
# dp_cli_version 由 `dpx install --skills` 安装时写入当前包版本；repo 副本为占位
dp_cli_version: "dev"
---

# dp-cli

基于 DrissionPage 的命令行浏览器自动化工具。天然反检测、描述性定位语法、直接穿透 shadow-root/iframe。

**查看所有命令和参数：`dpx --help` / `dpx <command> --help`**

## skill 版本自救

若 `dpx -V` 显示的版本新于本 skill 的 `dp_cli_version`（或本 skill 未安装），以 CLI 实时输出为准：

- `python -m dp_cli.catalog` —— 全量命令 schema（JSON，含全部选项）
- `dpx install --skills` —— 安装/更新本 skill 到 agent 的 skill 目录
- `dpx <command> --help` —— 单命令参数速查

> **`dp` 被抢占时**：DrissionPage 4.1.1.4+ 也注册了 `dp`（浏览器配置工具，非本 CLI）。若 `dp` 行为异常（如无 `snapshot`/`open` 命令），改用主入口 **`dpx`** 或 `python -m dp_cli.main`，或 `pip install --force-reinstall dp-cli` 夺回。归属检测：`dpx install --skills --check` → `entrypoint` 字段。

---

## 第一步：连接用户浏览器

dp-cli 支持两种方式连接用户自己的 Chrome。**默认首选 `--auto-connect`**：无需命令行启动参数，对非技术用户最友好。

**不要询问用户用哪种方式，按下面决策流程自动选择即可。**

### 推荐路径：--auto-connect（Chrome 144+）

```bash
# 尝试连接（首次使用会让用户做一次性配置）
dpx open --auto-connect
# 成功后后续命令自动复用，无需再加 --auto-connect：
dpx open <url>
dpx snapshot
```

**首次使用时若报错 `AUTOCONNECT_FAILED`，指引用户做一次性配置：**

> 请在 Chrome 中打开 `chrome://inspect/#remote-debugging`，勾选顶部的
> **"Allow remote debugging for this browser instance"** 复选框，然后重试。

配置完成后，该设置持久化；以后任何时候都可直接用 `dpx open --auto-connect`。

**⚠ 关键提示：执行 `dpx open --auto-connect` 后，Chrome 可能弹出 "Allow remote debugging" 授权对话框。必须主动告诉用户：**

> 请切到 Chrome 窗口，点击弹出的 "Allow remote debugging" 对话框中的 **Allow** 按钮。后续命令会复用同一个连接，不会再弹框。

命令会阻塞最多 90 秒等待授权。一次会话内只需点一次。

### 备选路径：--port 9222（经典模式）

当 `--auto-connect` 无法用（Chrome 版本低于 144、用户不愿打开 chrome://inspect 等）时使用：

```bash
# 1) 指引用户执行一次（窗口保持打开）：
google-chrome --remote-debugging-port=9222

# 2) dpx 连接：
dpx open --port 9222
```

### 临时实例（不连用户浏览器）

仅以下情况使用：用户明确说不需要登录态 / 纯公开页面 / 用户明确要求临时实例。

```bash
dpx open <url>    # 不加任何 --auto-connect 或 --port
```

### URL 自动补全

`https://` 可省略：
- `dpx open example.com` → `https://example.com`
- `dpx goto baidu.com` → `https://baidu.com`
- `dpx tab-new shop.example.com --new-window` → `https://shop.example.com`

### 多会话隔离（可选）

用 `-s <name>` 启用不同会话，互不干扰（各自独立的 ChromiumPage 实例 / 可绑定不同 tab）：

```bash
dpx -s work open --auto-connect
dpx -s scrape open --port 9222
dpx -s work snapshot     # 只作用于 work 会话
```

### 决策流程（给 AI 的执行规则）

1. 默认先跑 `dpx open --auto-connect`
   - 立即告诉用户："请留意 Chrome 窗口是否弹出 Allow 对话框并点击 Allow"
   - 若返回 `status: ok` → 连接成功，继续执行任务
   - 若返回 `code: AUTOCONNECT_FAILED` → 指引用户在 `chrome://inspect/#remote-debugging` 勾选 Allow 复选框后重试
   - 若返回 `code: BROWSER_START_FAILED` 且 detail 含 "timed out" → 用户没点 Allow → 提示用户点击 Allow 后重试
2. 若用户明确说 Chrome 版本低 / 不想用 --auto-connect → 备选 `--port 9222`
3. 后续命令**不要**再加 `--auto-connect` 或 `--port`（会话已复用）

---

## 核心思维：先看再做

**任何浏览器任务的第一步都是 `dpx snapshot`（理解全页）或 `dpx scan`（只看可点的），不要盲目操作。**

### dpx snapshot：全页快照（大而全）

基于两种来源融合：
1. **浏览器 a11y tree**（骨架）——结构 / 标题 / 列表 / 明确的 `role` / `<a>` / `<button>` 等
2. **Vimium 风格可交互元素补充**（补盲）——a11y tree 漏掉的纯图标按钮、菜单项、`<div onclick>`、有 `tabindex` 的自定义控件、`cursor:pointer` 启发式匹配等

输出末尾会有 `### Additional Interactive Elements` 区块，列出 a11y tree 没收录但可能可点的元素。**每个元素都有 `[N]` 编号**，配合 `⚡` / `?` 置信度标记：

| 标记 | 含义 |
|------|------|
| 无 | **high** 置信度（确定可点，如 `<button>`, `<a href>`, `role=button`）|
| `⚡` | **medium** 置信度（很可能，如 `onclick` / `tabindex>=0` / `aria-selected`，以及带 aria-label / 图标子元素 / 小方形的 `cursor:pointer` 元素——覆盖绝大多数 React/Vue 图标按钮）|
| `?` | **low** 置信度（仅 `cursor:pointer` 或 class 关键词，无其他信号；默认不显示，需 `--include-low`） |

输出附带的上下文信息：
- **`@top-left` / `@top-right` / `@center` …** — 9 宫格位置区域（左上/右上/中心/下方等），便于快速定位
- **`(icon)`** — 仅图标按钮（无可见文本，含 `<svg>`/`<img>` 子元素）
- **Shadow DOM** 自动穿透（open shadow root）—— Web Components / 现代框架组件全覆盖

三种模式：
- `dpx snapshot` — **full**（默认），完整内容 + clickable 补充，首次调用用这个
- `dpx snapshot --mode brief` — 精简模式，省 token，适合循环调用
- `dpx snapshot --mode text` — 纯文本，按阅读顺序

补充探测开关：
- `--viewport-only` 只扫视口，更快（适合长页面）
- `--include-low` 包含 low 置信度（假阳性↑，但能捕获更冷门的 clickable）
- `--no-clickables` 关闭补充探测，回到纯 a11y tree（旧行为）

### dpx scan：快速可交互清单（小而快）

当你只关心"当前页面下一步能点什么"、不需要 a11y tree 的完整结构时，用 `dpx scan` 更省 token：

```
dpx scan                            # 扫全页，high+medium
dpx scan --viewport                 # 只扫视口内
dpx scan --confidence high          # 只要最确定的
dpx scan --confidence all           # 包含 low（含启发式）
```

输出结构同补充元素（`[N]` 编号 + `⚡` / `?` 标记 + locator），`ref:N` 与 `dpx click`/`dpx fill` 无缝配合。

**区别速查**：

| 场景 | 用哪个 |
|------|--------|
| 理解新页面、首次调用 | `dpx snapshot` |
| 读取文章 / 列表内容 / 结构信息 | `dpx snapshot` |
| 循环操作中只看"还能点什么" | `dpx scan` 或 `dpx snapshot --mode brief` |
| 弹窗 / 下拉菜单出现后想找新选项 | `dpx scan --viewport`（很快） |
| 纯图标工具栏（GitHub/VSCode 式 UI） | `dpx snapshot` 或 `dpx scan` 都能覆盖 |

**重要：每次 `dpx snapshot` 或 `dpx scan` 后编号会重新分配。操作导致页面变化后，需要重新执行获取新编号。**

---

## 定位语法

| 语法 | 说明 | 示例 |
|------|------|------|
| **`ref:N`** | **快照编号（推荐）** | **`ref:5`** |
| **`pw:...`** | **Playwright 风格（语义化，支持 `>>` 链式）** | **`pw:role=button[name="Submit"]`** |
| `text:xxx` | 文本包含 | `text:登录` |
| `text=xxx` | 文本精确 | `text=提交` |
| `#id` | ID | `#submit` |
| `@attr=val` | 属性 | `@name=username` |
| `.class` | 类名 | `.submit-btn` |
| `css:xxx` | CSS 选择器 | `css:form > button` |
| `xpath:xxx` | XPath | `xpath://button` |
| `t:tag` | 标签名 | `t:button` |
| `@@A@@B` | 多条件与 | `@@tag()=button@@text():提交` |

**定位器优先级**：`ref:N`（快照后最快）> `pw:role/testid/label`（语义最稳，无需快照）> `#id` > `@data-testid` / `@aria-label` > `text:` > `.class` > `css:` > `xpath:`

属性匹配：`@class:active`(包含) `@class=active`(精确) `@class^=btn`(前缀) `@class$=large`(后缀)

**快照输出的每个元素都自带推荐定位器（`→` 后面的部分），直接复制使用即可。**

### `pw:` Playwright 风格（语义化定位，推荐跨站点通用）

不想每次都 snapshot 的话，用 `pw:` 直接按语义定位：

```bash
# role + accessible name（最语义化，跨 CSS 重构稳定）
dpx click 'pw:role=button[name="Submit"]'
dpx click 'pw:role=button[name=/^Sign/i]'        # 正则，i=忽略大小写
dpx click 'pw:role=link[name=More]'               # 裸值=substring

# 文本（精确/含/正则）
dpx click 'pw:text="登录"'                        # 精确
dpx click 'pw:text=登录'                          # 包含
dpx click 'pw:text=/^log/i'                       # 正则

# 表单语义
dpx fill  'pw:placeholder=搜索' "chatgpt"
dpx fill  'pw:label="邮箱"' "a@b.com"
dpx click 'pw:testid=submit-btn'                  # data-testid / data-test-id / data-test

# 链式 >>（每步缩小作用域）
dpx click 'pw:css=.sidebar >> role=listitem[name="Chat"] >> nth=2'
dpx click 'pw:css=li >> has-text="Python"'
dpx click 'pw:role=list >> nth=-1'                # -1 = 最后一个
dpx click 'pw:xpath=//nav >> role=link[name=Docs]'
```

支持的 matcher：`role` / `text` / `label` / `placeholder` / `alt` / `title` / `testid` / `css` / `xpath` / `nth` / `has-text` / `visible`

值的形式：`裸值`=子串 · `"引号"`=精确 · `/regex/flags`=正则

**默认过滤隐藏元素**（`display:none` 链 / `hidden` / `aria-hidden=true`），Shadow DOM 自动穿透。失败会报 `PW_NOT_FOUND`。

---

## 通用调用逻辑

### 场景一：页面操控（登录/填表/点击/导航）

```
dpx open --auto-connect     → 连接用户浏览器（首次需点 Allow）
dpx goto <url>              → 导航到目标页（复用已建立的连接）
dpx snapshot                → 看页面结构，每个元素有 [N] 编号
dpx fill "ref:15" <val>     → 用编号填写输入框
dpx click "ref:19"          → 用编号点击按钮/链接
dpx wait --text "xxx"       → 等待预期结果出现
dpx snapshot                → 确认操作结果（编号会刷新）
```

**如果点击后出现弹窗（alert/confirm/prompt），用 `dpx dialog-accept` 或 `dpx dialog-dismiss` 处理。**

**交互可靠性（0.12+ 默认开启）**：`click`/`fill`/`hover`/`check`/`upload`/`clear`/`select`/`fill-form` 执行前自动等待元素可见+可用+位置稳定（两帧）+未被遮挡。被遮挡时报 `CLICK_FAILED` 并返回遮挡元素明细（通常是遮罩/consent banner——先处理它再重试）。跳过等待加 `--no-wait`，强制点击加 `--force`。

```bash
dpx click "ref:19" --button right          # 右键（contextmenu）
dpx click "ref:19" --modifiers "Shift"     # 组合键点击
dpx click "ref:19" --click-count 2         # 双击
dpx click "ref:19" --position 10,20        # 元素内相对坐标
dpx dialog-policy --accept                 # 预设会话级自动应答（无人值守必备）
```

**精细键鼠**（canvas/游戏/vision 场景）：`mousemove`/`mousedown`/`mouseup`/`mousewheel`（CDP 真实输入事件，光标位置会话持久）、`keydown`/`keyup`（按住修饰键）、`press`/`type` 作用当前焦点。

**批量自愈**：`dpx batch --screenshot-on-fail <dir>`——任一命令失败自动截图留证后再中断。

### 场景二：批量数据提取（列表页）

**优先使用批量提取，避免逐个点击的低效方式。**

```
dpx snapshot                → 识别列表容器和字段结构
dpx dom "ref:21" -d parent --depth 3  → 追溯父节点，找到最佳容器选择器
dpx query "css:.card" --fields "text,loc" --limit 2  → 小量验证选择器
dpx extract "css:.card" '{"title":"css:.name","url":{"selector":"css:a","attr":"href"}}' --output csv --filename data.csv
```

**定位流程：snapshot 找元素 → dom 查上下文 → query 验证 → extract 批量提取。**

### 场景三：读取页面内容（文章/详情）

```
dpx snapshot                → full 模式包含完整页面内容
dpx snapshot --mode brief   → 只看结构和交互，省 token
dpx snapshot --selector "css:.article-body"  → 只看指定区域
dpx query "ref:57" --fields "text,html"      → 提取特定内容块的文本或 HTML
```

### 场景四：监听网络请求（抓接口数据）

**当页面数据通过 API 异步加载时，监听比 DOM 提取更高效。**

```
dpx listen --filter "api/xxx"   → 开始监听（必须在触发操作之前）
dpx click "text:加载更多"        → 触发请求
dpx listen-stop                  → 获取捕获的请求+响应体（JSON 格式）
```

需要完整录制归档时用 `dpx har-dump --seconds 10 --output trace.har`（HAR 1.2，支持 `--filter`/`--body`）；需要屏蔽或 mock 请求时用 `dpx intercept`（见 references/commands.md）。

### 场景补充：断言自检与观测

**断言**（脚本自检，失败 exit 4 可 `&&` 串联；0.12+ 条件自动轮询到 `--timeout`，适合"等待结果出现再断言"）：

```bash
dpx assert-text "登录成功"          # 页面含文本
dpx assert-url "dashboard"          # URL 包含
dpx assert-visible "#result"        # 元素可见（--hidden 反向）
dpx assert-count ".item" --min 1    # 数量区间
```

**Console 捕获**：`dpx console-listen` 开始捕获 → 触发操作 → `dpx console-dump` 读回（console/error/日志三源）。

**观测**：`dpx perf`（性能指标+Navigation Timing）、`dpx trace --duration 10`（CDP Tracing→Perfetto）、`dpx screencast --dir ./frames`（帧序列）。

**iframe 内操作**：交互命令统一支持 `--frame <locator>` 把作用域切进指定 iframe（`dpx click css:.btn --frame "css:iframe"`）。

### 场景五：列表+详情页面

**特点：页面分为列表区和详情区，点击列表项时详情内容动态更新。**

```
dpx snapshot             → 分析页面结构

# 方案A：列表卡片已含所需信息 → 优先批量提取
dpx extract "css:.list-item" '{"title":"css:.title","desc":"css:.desc"}'

# 方案B：需要完整详情 → 逐个点击
for i in range(n):
  dpx click "css:.list-item" --index {i+1}
  dpx wait --loaded
  dpx snapshot --mode brief   → 获取详情（用 brief 省 token）
  dpx query "css:.detail" --fields "text"
```

### 场景六：无限滚动/分页加载

```
# 无限滚动
for page in range(max_pages):
  dpx extract "css:.item" '{...}' --filename page_{page}.csv
  dpx scroll --y 3000
  dpx wait --loaded               → 等待新内容加载
  dpx wait --locator "css:.item:nth-child({count})"  → 或等待新元素出现

# 翻页
for page in range(max_pages):
  dpx extract "css:.item" '{...}'
  dpx click "css:.next-page"      → 或 dpx click "ref:N"
  dpx wait --loaded
```

### 场景七：纯 API 数据获取（不需浏览器）

**当目标是公开 API 且不需要浏览器渲染时，用 HTTP 模式最高效。**

```
dpx http-get "https://api.example.com/data?page=1" --output data.json
dpx http-post "https://api.example.com/search" --data '{"keyword":"test"}' --output result.json
```

### 场景八：状态保存与恢复

**登录态跨会话复用，避免重复登录。**

```
# 登录后保存
dpx state-save --filename my-site.json    → 保存 Cookie + localStorage

# 下次直接恢复
dpx open --auto-connect
dpx state-load --filename my-site.json
dpx goto "https://my-site.com/dashboard"  → 已登录状态
```

`state-load` 兼容 playwright `storage_state` JSON（自动探测 `cookies` 为数组的格式，cookie 全字段恢复）。

### 场景九：多标签页操作（标签页绑定）

**标签页绑定（Tab Pinning）：自动化任务与手动浏览分离**

```
dpx tab-list                           → 查看所有标签页（显示 [pinned] 标记）
dpx tab-new "example.com" --new-window  → 新窗口创建标签页（自动化专用），自动绑定
dpx tab-select example                  → 按 URL 关键词绑定标签页
dpx tab-select 1                        → 按序号绑定（从 0 开始）
dpx tab-select none                     → 解除绑定，恢复默认行为
dpx snapshot                           → 操作绑定的标签页（无需激活）
dpx tab-close                          → 关闭绑定的标签页
```

**关键特性：**
- 绑定后所有 `dp` 命令只在指定标签页执行，无需标签页处于激活/前台状态
- 支持按序号/tab_id/URL关键词/title关键词/none 解绑
- URL 可省略 `https://`（如 `dpx tab-new baidu.com`）

**典型场景：**
```bash
dpx open --auto-connect
dpx tab-new https://shop.example.com --new-window   # 新窗口 + 自动绑定
dpx snapshot                                        # 只操作绑定的标签页
dpx click "ref:5"                                   # 同时你可在原窗口自由浏览
```

### 场景十：反自动化检测（遇到网站阻止/验证码/空白）

目标站点返回空白、验证码、"检测到自动化工具"等提示时，先加反检测补丁再重试：

```bash
dpx stealth                    # 默认 full 预设：webdriver / UA / plugins / WebGL 等全套
dpx stealth --preset mild      # 保守：只改 webdriver + UA
dpx stealth --feature webdriver --feature plugins   # 精细粒度

# 或一步到位：连接时就启用
dpx open --auto-connect --stealth
dpx goto https://bot.sannysoft.com/
```

**注意 stealth 的边界：**
- 修 `navigator.webdriver`、UA 去 `HeadlessChrome`、`plugins`、WebGL VENDOR 等常见检测
- **不覆盖** Canvas / Audio / 字体指纹（这些需要真实 GPU 或 Xvfb 环境）
- **不覆盖** TLS JA3/JA4 / IP 信誉（需要更底层方案）
- 对 Cloudflare / Akamai / 阿里云 Anti-Bot 等重指纹检测效果有限

### 场景十一：探查 → 固化 Playwright 用例

**dp-cli 负责「探查与取证」，Playwright/pwtest 负责「固化与回归」——不要在 dpx 里跑测试，要把探查结果翻译成 playwright 断言与定位器。**

```bash
# 1) 连用户真实浏览器（已登录态是 playwright codegen 拿不到的独占优势）
dpx open --auto-connect

# 2) 语义探查：a11y 快照拿结构，expect-gen 拿即用断言
dpx snapshot --mode brief
dpx expect-gen --ele "ref:5"            # 单元素：稳定性排序定位器候选 + expect() 行
dpx expect-gen --all --limit 30         # 枚举全页可断言点

# 3) 录制人工/agent 操作流，导出 playwright 脚本素材
dpx record-start
dpx click "pw:role=button[name=登录]"
dpx fill "pw:role=textbox[name=密码]" "x"
dpx record-stop -f playwright -o steps.py   # 产出 get_by_* + to_have_url 同步点

# 4) 登录态交接：真人/stealth 过风控登录 → 导出 storageState
dpx state-save auth.json --format playwright
# playwright 侧：browser.new_context(storage_state="auth.json")

# 5) 固化：导出脚本拆进 pwtest Page Object，expect-gen 断言行直接粘贴
```

**dpx ↔ Playwright 映射表**

| dpx 探查/操作 | Playwright 固化等价物 |
|---|---|
| `pw:role=button[name=登录]` | `page.get_by_role("button", name="登录")` |
| `pw:testid=submit` | `page.get_by_test_id("submit")` |
| `pw:text=...` / `pw:placeholder=...` / `pw:label=...` | `get_by_text(...)` / `get_by_placeholder(...)` / `get_by_label(...)` |
| `dpx wait --text X` | `expect(page.get_by_text("X")).to_be_visible()` |
| `dpx wait --locator S` | `expect(loc).to_be_visible()` / `loc.wait_for()` |
| `dpx assert-visible` / `assert-text` / `assert-url` / `assert-count` | `expect(loc).to_be_visible()` / `to_contain_text()` / `expect(page).to_have_url()` / `to_have_count()` |
| `dpx eval "expr"` | `page.evaluate("expr")` |
| `dpx intercept` | `page.route("**/*", handler)` |
| `dpx emulate` | `browser.new_context(viewport=/user_agent=/locale=...)` |
| `dpx throttle` | `context.new_cdp_session(page)` → `Network.emulateNetworkConditions` |
| `dpx listen` + `listen-stop` | `page.expect_response(...)` / `page.on("response")` |
| `dpx hook-set`（探查接口调用链） | `page.on("request")` / `page.expect_request` |
| `dpx state-save --format playwright` | `browser.new_context(storage_state=...)` |
| `dpx record-export -f playwright` | 用例骨架（含 `get_by_*` + `expect().to_have_url()` 同步点） |
| `dpx expect-gen` | 即用 `expect()` 断言行（输出 `code` 字段直接粘贴） |

**要点：**
- 固化用例优先 `pw:`/`get_by_*` 语义定位器，`css:` 仅兜底——脆弱 CSS 路径是用例腐烂主因
- `record-export` 产出是**素材不是成品**：拆进 pwtest Page Object，断言由 `expect-gen` 补
- `expect-gen --ele` 的 `data.target.candidates` 是按稳定性排序的全候选（取第一项）；`matches` 为命中数（1 = 唯一性背书）
- 登录态走 storageState 桥：真人/stealth 登录 → `state-save --format playwright` → 测试 `new_context(storage_state=...)`

---

## 元素定位进阶

### 用 `dpx dom` 查看 DOM 上下文

当需要精确定位容器或了解元素结构时：

```
dpx dom "ref:21"                     → 查看父/子/兄弟全部
dpx dom "ref:21" -d parent --depth 5 → 向上追溯 5 层，找到最佳容器
dpx dom "ref:21" -d children         → 查看子节点结构
dpx dom "ref:21" -d siblings         → 查看兄弟节点
```

### 用 `dpx query --fields` 获取精确路径

```
dpx query "ref:21" --fields "text,css,tag"       → 获取精确 CSS 路径
dpx query "css:.item" --fields "text,loc,xpath"   → 获取 XPath
dpx query "ref:57" --fields "text,html"           → 获取 innerHTML
dpx query "ref:57" --fields "outer_html"          → 获取完整 outerHTML
```

可用字段：`text` `tag` `loc` `css` `xpath` `html` `outer_html` `href` `src` `id` `class` 及任意 HTML 属性名。

### 用 `dpx inspect` 获取元素状态

```
dpx inspect "ref:5" --include-rect    → 位置、尺寸
dpx inspect "ref:5" --include-style   → 计算样式（display/visibility/color等）
```

### 用 `dpx eval` 执行自定义 JS（最后手段）

```
dpx eval "document.title"
dpx eval "return document.querySelectorAll('.item').length"
dpx eval "el => el.getBoundingClientRect()" --locator "ref:5"
```

---

## extract 字段映射

```json
{
  "标题": "css:.title",
  "链接": {"selector": "css:a", "attr": "href"},
  "标签列表": {"selector": "css:.tag", "multi": true},
  "可选字段": {"selector": "css:.x", "default": ""}
}
```

| 参数 | 说明 |
|------|------|
| `selector` | 子元素定位器（相对于容器） |
| `attr` | 取属性值（href/src/data-id 等） |
| `multi` | `true` 返回匹配列表 |
| `default` | 元素缺失时的回退值 |

---

## 性能：循环/批量操作默认 `dpx batch`

每条 `dp` 命令是独立进程，固定开销 ≈0.4-0.5s（Python 启动 + DrissionPage
import + CDP attach）——**单发命令这个延迟属正常预期，不是 bug**。
循环或连续多步操作时逐条调用会把固定税乘上 N 倍，必须改用摊销入口：

| 场景 | 用法 | 开销模型 |
| --- | --- | --- |
| 多步操作（提取/填表/翻页循环） | `dpx batch cmds.txt` 或 `printf ... \| dpx batch -` | 单进程内顺序执行，import+attach 只付一次；实测 3 条 eval 总 0.43s vs 逐条 1.2s+ |
| 交互式探索多步操作 | `dpx sh`（REPL） | 常驻进程，命令边际成本 ~10ms 级 |
| 长驻服务/多 agent 共用 | `dpx serve` | HTTP 常驻，一次 attach 服务所有请求 |
| 需要在浏览器上下文跑自定义逻辑 | `dpx exec 'python 代码'` | 单进程执行，复用同一 attach |

`dpx batch` 输入每行一条子命令（可省略 `dpx ` 前缀），`#` 开头为注释，
逐行输出 JSON 结果；`-S` 会话参数写在每行命令里。

---

## 关键原则

1. **先连接用户浏览器** — 默认 `dpx open --auto-connect`，失败再降级到 `--port 9222`
2. **提示用户点 Allow** — 执行 `--auto-connect` 后，主动告诉用户留意 Chrome 的 Allow 授权框
3. **先 snapshot/scan，后操作** — 不要猜页面结构；snapshot 看全貌，scan 只看可点
4. **用 ref:N 引用元素** — `dpx click "ref:5"` 最高效，每次 snapshot/scan 后编号刷新
5. **善用 brief 模式 / scan 省 token** — 循环操作用 `--mode brief` 或改用 `dpx scan`
6. **操作后再 snapshot 确认** — 验证结果而非假设成功
7. **小量验证再批量** — `dpx query ... --limit 2` 确认后再 `dpx extract`
8. **动态页面先等待** — `dpx wait --loaded` / `--locator` / `--text` / `--locator-gone`
9. **严禁编造数据** — 所有数据必须从页面实际提取
10. **优先批量提取** — 列表页优先 `dpx extract`，避免逐个点击
11. **注意反爬机制** — 加密数据如实说明 `[无法解密]`，不得猜测；遇可疑网站先 `dpx stealth`
12. **善用 dom 辅助定位** — `dpx dom "ref:N" -d parent` 找容器比猜 CSS 更准
13. **截图验证** — 操作结果不确定时用 `dpx screenshot` 确认视觉效果

---

## 数据完整性（防止编造数据）

- 所有数据必须来自 `dpx snapshot` / `dpx query` / `dpx extract` 的实际输出
- 遇到字体加密、乱码等特殊字符，标记为 `[无法解密]`，不得猜测
- 保存前对比原始输出，确保数据一致

---

## 故障排查

- **元素找不到** → `dpx snapshot` 确认元素存在；若 a11y tree 没有，看末尾 `Additional Interactive Elements` 或直接 `dpx scan --confidence all` 找补充元素 → `dpx wait --locator` 等动态加载
- **纯图标按钮 / 自定义菜单项无 ref** → `dpx scan` 或 `dpx snapshot --include-low`（启发式匹配会把 `⚡` medium / `?` low 也列出）
- **`AUTOCONNECT_FAILED`（找不到 DevToolsActivePort）** → 指引用户在 `chrome://inspect/#remote-debugging` 勾选 **Allow remote debugging for this browser instance**
- **`dpx open --auto-connect` 卡住不返回** → 用户没点 Allow → 提示用户切到 Chrome 窗口点 **Allow** 授权对话框
- **`BROWSER_START_FAILED` 带 "timed out"** → 同上，Allow 未点；90 秒超时会返回
- **`--auto-connect` 反复失败** → 降级到经典模式：`google-chrome --remote-debugging-port=9222` + `dpx open --port 9222`
- **被网站检测（空白页/验证码/反爬）** → 先 `dpx stealth`，或打开时加 `--stealth`
- **提取为空** → `dpx query <selector> --limit 1` 验证选择器 → 确认内容已加载
- **弹窗阻塞** → `dpx dialog-accept` 或 `dpx dialog-dismiss`
- **iframe 内容** → `dpx snapshot` 天然穿透可见；iframe 内元素交互用 `--frame` 限定作用域（`dpx click css:.x --frame "css:iframe"`）
- **页面 JS 报错** → `dpx console-listen` + 复现操作 + `dpx console-dump` 查看未捕获异常
- **定位不准** → `dpx dom "ref:N" -d parent --depth 3` 查看上下文 → 用 `css` 字段获取精确路径
- **需要 JS 兜底** → `dpx eval "..."` 执行自定义 JavaScript
- **会话状态混乱** → `dpx close` 清理当前会话；`dpx list` 查看所有会话；`dpx close-all` 清空；`dpx kill-all` 最彻底（关全部会话+停 bridge+强杀 dp-cli 自管浏览器实例，不误伤用户浏览器）
- **具体命令用法** → `dpx <command> --help`

## 维护约定（仓库贡献者）

修改本 skill 文档后必跑 `scripts/sync-skill.sh` 同步到 `~/.claude/skills/dp-cli/`（脚本末尾 diff -r 自检）；新增/改命令选项后重跑 `python -m dp_cli.catalog --md` 刷新 `references/commands.md` 生成段。详见 TESTING.md 第 8 节。


## 新特性速查（0.13）

- `dpx fill-form <pairs...>|--data '<json>'`：批量填表，per-field 明细
- `dpx cookies-import <file|->`：批量导 cookie（Netscape/JSON/Cookie 头）
- `dpx intercept --allow-domain <域>`：白名单外请求 abort（agent 安全护栏）
- `dpx read <url>`：无浏览器直抓 markdown（llms.txt 探测）
- `dpx requests`/`dpx request <id>`：serve 托管被动网络日志
- `dpx daemon-start|daemon-status|daemon-stop`：socket 常驻免启动税（DPX_DAEMON=1 强制路由）
- `dpx sleep N`、`dpx wait --popup`、`dpx wait --download`：定时/新tab/下载等待
