---
name: dp-cli
description: 使用 dp-cli 控制浏览器、提取网页数据、自动化操作。当用户需要打开网页、点击元素、填写表单、截图、抓取列表数据、监听网络请求、操作 Cookie/Storage，或者需要连接自己已登录的浏览器进行操作时，使用这个技能。特别适合：批量提取结构化数据（商品/职位/新闻列表）、需要保留登录状态的自动化、需要穿透 shadow-root/iframe 的复杂页面操作。
---

# dp-cli

基于 DrissionPage 的命令行浏览器自动化工具。天然反检测、描述性定位语法、直接穿透 shadow-root/iframe。

**查看所有命令和参数：`dp --help` / `dp <command> --help`**

---

## 第一步：连接用户浏览器

dp-cli 支持两种方式连接用户自己的 Chrome。**默认首选 `--auto-connect`**：无需命令行启动参数，对非技术用户最友好。

**不要询问用户用哪种方式，按下面决策流程自动选择即可。**

### 推荐路径：--auto-connect（Chrome 144+）

```bash
# 尝试连接（首次使用会让用户做一次性配置）
dp open --auto-connect
# 成功后后续命令自动复用，无需再加 --auto-connect：
dp open <url>
dp snapshot
```

**首次使用时若报错 `AUTOCONNECT_FAILED`，指引用户做一次性配置：**

> 请在 Chrome 中打开 `chrome://inspect/#remote-debugging`，勾选顶部的
> **"Allow remote debugging for this browser instance"** 复选框，然后重试。

配置完成后，该设置持久化；以后任何时候都可直接用 `dp open --auto-connect`。

**⚠ 关键提示：执行 `dp open --auto-connect` 后，Chrome 可能弹出 "Allow remote debugging" 授权对话框。必须主动告诉用户：**

> 请切到 Chrome 窗口，点击弹出的 "Allow remote debugging" 对话框中的 **Allow** 按钮。后续命令会复用同一个连接，不会再弹框。

命令会阻塞最多 90 秒等待授权。一次会话内只需点一次。

### 备选路径：--port 9222（经典模式）

当 `--auto-connect` 无法用（Chrome 版本低于 144、用户不愿打开 chrome://inspect 等）时使用：

```bash
# 1) 指引用户执行一次（窗口保持打开）：
google-chrome --remote-debugging-port=9222

# 2) dp 连接：
dp open --port 9222
```

### 临时实例（不连用户浏览器）

仅以下情况使用：用户明确说不需要登录态 / 纯公开页面 / 用户明确要求临时实例。

```bash
dp open <url>    # 不加任何 --auto-connect 或 --port
```

### URL 自动补全

`https://` 可省略：
- `dp open example.com` → `https://example.com`
- `dp goto baidu.com` → `https://baidu.com`
- `dp tab-new zhipin.com --new-window` → `https://zhipin.com`

### 多会话隔离（可选）

用 `-s <name>` 启用不同会话，互不干扰（各自独立的 ChromiumPage 实例 / 可绑定不同 tab）：

```bash
dp -s work open --auto-connect
dp -s scrape open --port 9222
dp -s work snapshot     # 只作用于 work 会话
```

### 决策流程（给 AI 的执行规则）

1. 默认先跑 `dp open --auto-connect`
   - 立即告诉用户："请留意 Chrome 窗口是否弹出 Allow 对话框并点击 Allow"
   - 若返回 `status: ok` → 连接成功，继续执行任务
   - 若返回 `code: AUTOCONNECT_FAILED` → 指引用户在 `chrome://inspect/#remote-debugging` 勾选 Allow 复选框后重试
   - 若返回 `code: BROWSER_START_FAILED` 且 detail 含 "timed out" → 用户没点 Allow → 提示用户点击 Allow 后重试
2. 若用户明确说 Chrome 版本低 / 不想用 --auto-connect → 备选 `--port 9222`
3. 后续命令**不要**再加 `--auto-connect` 或 `--port`（会话已复用）

---

## 核心思维：先看再做

**任何浏览器任务的第一步都是 `dp snapshot`，不要盲目操作。**

快照基于浏览器原生 a11y tree，输出：
- 页面完整结构（标题层级、区域划分、列表）
- 所有可交互元素及其定位器（link/button/input 等）
- 内容文本（段落、代码块、列表项）
- **每个元素都有 `[N]` 编号**，可直接用 `ref:N` 引用操作

三种模式：
- `dp snapshot` — **full**（默认），完整内容，首次调用用这个
- `dp snapshot --mode brief` — 精简模式，截断长文本保留结构+交互，省 token
- `dp snapshot --mode text` — 纯文本，按阅读顺序输出

**快照是你理解页面的唯一入口，拿到快照后直接用 `ref:N` 操作元素。**

**重要：每次 `dp snapshot` 后编号会重新分配。操作导致页面变化后，需要重新 snapshot 获取新编号。**

---

## 定位语法

| 语法 | 说明 | 示例 |
|------|------|------|
| **`ref:N`** | **快照编号（推荐）** | **`ref:5`** |
| `text:xxx` | 文本包含 | `text:登录` |
| `text=xxx` | 文本精确 | `text=提交` |
| `#id` | ID | `#submit` |
| `@attr=val` | 属性 | `@name=username` |
| `.class` | 类名 | `.submit-btn` |
| `css:xxx` | CSS 选择器 | `css:form > button` |
| `xpath:xxx` | XPath | `xpath://button` |
| `t:tag` | 标签名 | `t:button` |
| `@@A@@B` | 多条件与 | `@@tag()=button@@text():提交` |

**定位器优先级**：`ref:N` > `#id` > `@data-testid` / `@aria-label` > `text:` > `.class` > `css:` > `xpath:`

属性匹配：`@class:active`(包含) `@class=active`(精确) `@class^=btn`(前缀) `@class$=large`(后缀)

**快照输出的每个元素都自带推荐定位器（`→` 后面的部分），直接复制使用即可。**

---

## 通用调用逻辑

### 场景一：页面操控（登录/填表/点击/导航）

```
dp open --auto-connect     → 连接用户浏览器（首次需点 Allow）
dp goto <url>              → 导航到目标页（复用已建立的连接）
dp snapshot                → 看页面结构，每个元素有 [N] 编号
dp fill "ref:15" <val>     → 用编号填写输入框
dp click "ref:19"          → 用编号点击按钮/链接
dp wait --text "xxx"       → 等待预期结果出现
dp snapshot                → 确认操作结果（编号会刷新）
```

**如果点击后出现弹窗（alert/confirm/prompt），用 `dp dialog-accept` 或 `dp dialog-dismiss` 处理。**

### 场景二：批量数据提取（列表页）

**优先使用批量提取，避免逐个点击的低效方式。**

```
dp snapshot                → 识别列表容器和字段结构
dp dom "ref:21" -d parent --depth 3  → 追溯父节点，找到最佳容器选择器
dp query "css:.card" --fields "text,loc" --limit 2  → 小量验证选择器
dp extract "css:.card" '{"title":"css:.name","url":{"selector":"css:a","attr":"href"}}' --output csv --filename data.csv
```

**定位流程：snapshot 找元素 → dom 查上下文 → query 验证 → extract 批量提取。**

### 场景三：读取页面内容（文章/详情）

```
dp snapshot                → full 模式包含完整页面内容
dp snapshot --mode brief   → 只看结构和交互，省 token
dp snapshot --selector "css:.article-body"  → 只看指定区域
dp query "ref:57" --fields "text,html"      → 提取特定内容块的文本或 HTML
```

### 场景四：监听网络请求（抓接口数据）

**当页面数据通过 API 异步加载时，监听比 DOM 提取更高效。**

```
dp listen --filter "api/xxx"   → 开始监听（必须在触发操作之前）
dp click "text:加载更多"        → 触发请求
dp listen-stop                  → 获取捕获的请求+响应体（JSON 格式）
```

### 场景五：列表+详情页面

**特点：页面分为列表区和详情区，点击列表项时详情内容动态更新。**

```
dp snapshot             → 分析页面结构

# 方案A：列表卡片已含所需信息 → 优先批量提取
dp extract "css:.list-item" '{"title":"css:.title","desc":"css:.desc"}'

# 方案B：需要完整详情 → 逐个点击
for i in range(n):
  dp click "css:.list-item" --index {i+1}
  dp wait --loaded
  dp snapshot --mode brief   → 获取详情（用 brief 省 token）
  dp query "css:.detail" --fields "text"
```

### 场景六：无限滚动/分页加载

```
# 无限滚动
for page in range(max_pages):
  dp extract "css:.item" '{...}' --filename page_{page}.csv
  dp scroll --y 3000
  dp wait --loaded               → 等待新内容加载
  dp wait --locator "css:.item:nth-child({count})"  → 或等待新元素出现

# 翻页
for page in range(max_pages):
  dp extract "css:.item" '{...}'
  dp click "css:.next-page"      → 或 dp click "ref:N"
  dp wait --loaded
```

### 场景七：纯 API 数据获取（不需浏览器）

**当目标是公开 API 且不需要浏览器渲染时，用 HTTP 模式最高效。**

```
dp http-get "https://api.example.com/data?page=1" --output data.json
dp http-post "https://api.example.com/search" --data '{"keyword":"test"}' --output result.json
```

### 场景八：状态保存与恢复

**登录态跨会话复用，避免重复登录。**

```
# 登录后保存
dp state-save --filename my-site.json    → 保存 Cookie + localStorage

# 下次直接恢复
dp open --auto-connect
dp state-load --filename my-site.json
dp goto "https://my-site.com/dashboard"  → 已登录状态
```

### 场景九：多标签页操作（标签页绑定）

**标签页绑定（Tab Pinning）：自动化任务与手动浏览分离**

```
dp tab-list                           → 查看所有标签页（显示 [pinned] 标记）
dp tab-new "example.com" --new-window  → 新窗口创建标签页（自动化专用），自动绑定
dp tab-select zhipin                   → 按 URL 关键词绑定标签页
dp tab-select 1                        → 按序号绑定（从 0 开始）
dp tab-select none                     → 解除绑定，恢复默认行为
dp snapshot                           → 操作绑定的标签页（无需激活）
dp tab-close                          → 关闭绑定的标签页
```

**关键特性：**
- 绑定后所有 `dp` 命令只在指定标签页执行，无需标签页处于激活/前台状态
- 支持按序号/tab_id/URL关键词/title关键词/none 解绑
- URL 可省略 `https://`（如 `dp tab-new baidu.com`）

**典型场景：**
```bash
dp open --auto-connect
dp tab-new https://www.zhipin.com --new-window   # 新窗口 + 自动绑定
dp snapshot                                        # 只操作绑定的标签页
dp click "ref:5"                                   # 同时你可在原窗口自由浏览
```

### 场景十：反自动化检测（遇到网站阻止/验证码/空白）

目标站点返回空白、验证码、"检测到自动化工具"等提示时，先加反检测补丁再重试：

```bash
dp stealth                    # 默认 full 预设：webdriver / UA / plugins / WebGL 等全套
dp stealth --preset mild      # 保守：只改 webdriver + UA
dp stealth --feature webdriver --feature plugins   # 精细粒度

# 或一步到位：连接时就启用
dp open --auto-connect --stealth
dp goto https://bot.sannysoft.com/
```

**注意 stealth 的边界：**
- 修 `navigator.webdriver`、UA 去 `HeadlessChrome`、`plugins`、WebGL VENDOR 等常见检测
- **不覆盖** Canvas / Audio / 字体指纹（这些需要真实 GPU 或 Xvfb 环境）
- **不覆盖** TLS JA3/JA4 / IP 信誉（需要更底层方案）
- 对 Cloudflare / Akamai / 阿里云 Anti-Bot 等重指纹检测效果有限

---

## 元素定位进阶

### 用 `dp dom` 查看 DOM 上下文

当需要精确定位容器或了解元素结构时：

```
dp dom "ref:21"                     → 查看父/子/兄弟全部
dp dom "ref:21" -d parent --depth 5 → 向上追溯 5 层，找到最佳容器
dp dom "ref:21" -d children         → 查看子节点结构
dp dom "ref:21" -d siblings         → 查看兄弟节点
```

### 用 `dp query --fields` 获取精确路径

```
dp query "ref:21" --fields "text,css,tag"       → 获取精确 CSS 路径
dp query "css:.item" --fields "text,loc,xpath"   → 获取 XPath
dp query "ref:57" --fields "text,html"           → 获取 innerHTML
dp query "ref:57" --fields "outer_html"          → 获取完整 outerHTML
```

可用字段：`text` `tag` `loc` `css` `xpath` `html` `outer_html` `href` `src` `id` `class` 及任意 HTML 属性名。

### 用 `dp inspect` 获取元素状态

```
dp inspect "ref:5" --include-rect    → 位置、尺寸
dp inspect "ref:5" --include-style   → 计算样式（display/visibility/color等）
```

### 用 `dp eval` 执行自定义 JS（最后手段）

```
dp eval "document.title"
dp eval "return document.querySelectorAll('.item').length"
dp eval "el => el.getBoundingClientRect()" --locator "ref:5"
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

## 关键原则

1. **先连接用户浏览器** — 默认 `dp open --auto-connect`，失败再降级到 `--port 9222`
2. **提示用户点 Allow** — 执行 `--auto-connect` 后，主动告诉用户留意 Chrome 的 Allow 授权框
3. **先 snapshot，后操作** — 不要猜页面结构
4. **用 ref:N 引用元素** — `dp click "ref:5"` 最高效，每次 snapshot 后编号刷新
5. **善用 brief 模式省 token** — 循环操作中用 `--mode brief`
6. **操作后再 snapshot 确认** — 验证结果而非假设成功
7. **小量验证再批量** — `dp query ... --limit 2` 确认后再 `dp extract`
8. **动态页面先等待** — `dp wait --loaded` / `--locator` / `--text` / `--locator-gone`
9. **严禁编造数据** — 所有数据必须从页面实际提取
10. **优先批量提取** — 列表页优先 `dp extract`，避免逐个点击
11. **注意反爬机制** — 加密数据如实说明 `[无法解密]`，不得猜测；遇可疑网站先 `dp stealth`
12. **善用 dom 辅助定位** — `dp dom "ref:N" -d parent` 找容器比猜 CSS 更准
13. **截图验证** — 操作结果不确定时用 `dp screenshot` 确认视觉效果

---

## 数据完整性（防止编造数据）

- 所有数据必须来自 `dp snapshot` / `dp query` / `dp extract` 的实际输出
- 遇到字体加密、乱码等特殊字符，标记为 `[无法解密]`，不得猜测
- 保存前对比原始输出，确保数据一致

---

## 故障排查

- **元素找不到** → `dp snapshot` 确认元素存在 → `dp wait --locator` 等动态加载
- **`AUTOCONNECT_FAILED`（找不到 DevToolsActivePort）** → 指引用户在 `chrome://inspect/#remote-debugging` 勾选 **Allow remote debugging for this browser instance**
- **`dp open --auto-connect` 卡住不返回** → 用户没点 Allow → 提示用户切到 Chrome 窗口点 **Allow** 授权对话框
- **`BROWSER_START_FAILED` 带 "timed out"** → 同上，Allow 未点；90 秒超时会返回
- **`--auto-connect` 反复失败** → 降级到经典模式：`google-chrome --remote-debugging-port=9222` + `dp open --port 9222`
- **被网站检测（空白页/验证码/反爬）** → 先 `dp stealth`，或打开时加 `--stealth`
- **提取为空** → `dp query <selector> --limit 1` 验证选择器 → 确认内容已加载
- **弹窗阻塞** → `dp dialog-accept` 或 `dp dialog-dismiss`
- **iframe 内容** → DrissionPage 天然穿透 iframe，用 `dp snapshot` 直接可见
- **定位不准** → `dp dom "ref:N" -d parent --depth 3` 查看上下文 → 用 `css` 字段获取精确路径
- **需要 JS 兜底** → `dp eval "..."` 执行自定义 JavaScript
- **会话状态混乱** → `dp close` 清理当前会话；`dp list` 查看所有会话；`dp close-all` 清空
- **具体命令用法** → `dp <command> --help`
