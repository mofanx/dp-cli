---
name: dp-cli
description: 使用 dp-cli 控制浏览器、提取网页数据、自动化操作。当用户需要打开网页、点击元素、填写表单、截图、抓取列表数据、监听网络请求、操作 Cookie/Storage，或者需要连接自己已登录的浏览器进行操作时，使用这个技能。特别适合：批量提取结构化数据（商品/职位/新闻列表）、需要保留登录状态的自动化、需要穿透 shadow-root/iframe 的复杂页面操作。
---

# dp-cli

基于 DrissionPage 的命令行浏览器自动化工具。天然反检测、描述性定位语法、直接穿透 shadow-root/iframe。

**查看所有命令和参数：`dp --help` / `dp <command> --help`**

---

## 第一步：连接用户浏览器

**dp-cli 默认行为：先尝试连接用户浏览器（端口 9222），如果失败则提示用户启动。**

**不要询问用户，直接按以下流程执行：**

```bash
# 1. 先测试连接（不打开页面，只确认端口可用）：
dp open --port 9222

# 2. 如果连接失败，提示用户执行以下命令启动浏览器（只需一次，浏览器保持打开即可）：
google-chrome --remote-debugging-port=9222

# 3. 用户启动后，重新测试连接：
dp open --port 9222

# 4. 连接成功后，打开目标页面（后续命令自动复用连接，无需再加 --port）：
dp open <url>
```

**URL 可省略 `https://`：**
- `dp open example.com` → 自动补全为 `https://example.com`
- `dp goto baidu.com` → 自动补全为 `https://baidu.com`
- `dp tab-new zhipin.com --new-window` → 自动补全为 `https://zhipin.com`
```

**什么时候使用临时实例（不加 --port）？**
- 用户明确说不需要登录态
- 纯公开页面抓取，不涉及任何账户数据
- 用户明确要求使用临时实例

**关键原则：默认尝试连接用户浏览器，连接失败则提示用户启动，不要先询问。**

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
dp open <url> --port 9222  → 连接用户浏览器并打开页面
dp snapshot             → 看页面结构，每个元素有 [N] 编号
dp fill "ref:15" <val>  → 用编号填写输入框
dp click "ref:19"      → 用编号点击按钮/链接
dp wait --text "xxx"    → 等待预期结果出现
dp snapshot             → 确认操作结果（编号会刷新）
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
dp open --port 9222
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
dp open --port 9222
dp tab-new https://www.zhipin.com --new-window   # 新窗口 + 自动绑定
dp snapshot                                        # 只操作绑定的标签页
dp click "ref:5"                                   # 同时你可在原窗口自由浏览
```

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

1. **先连接用户浏览器** — `dp open --port 9222`，连接失败再提示启动
2. **先 snapshot，后操作** — 不要猜页面结构
3. **用 ref:N 引用元素** — `dp click "ref:5"` 最高效，每次 snapshot 后编号刷新
4. **善用 brief 模式省 token** — 循环操作中用 `--mode brief`
5. **操作后再 snapshot 确认** — 验证结果而非假设成功
6. **小量验证再批量** — `dp query ... --limit 2` 确认后再 `dp extract`
7. **动态页面先等待** — `dp wait --loaded` / `--locator` / `--text` / `--locator-gone`
8. **严禁编造数据** — 所有数据必须从页面实际提取
9. **优先批量提取** — 列表页优先 `dp extract`，避免逐个点击
10. **注意反爬机制** — 加密数据如实说明 `[无法解密]`，不得猜测
11. **善用 dom 辅助定位** — `dp dom "ref:N" -d parent` 找容器比猜 CSS 更准
12. **截图验证** — 操作结果不确定时用 `dp screenshot` 确认视觉效果

---

## 数据完整性（防止编造数据）

- 所有数据必须来自 `dp snapshot` / `dp query` / `dp extract` 的实际输出
- 遇到字体加密、乱码等特殊字符，标记为 `[无法解密]`，不得猜测
- 保存前对比原始输出，确保数据一致

---

## 故障排查

- **元素找不到** → `dp snapshot` 确认元素存在 → `dp wait --locator` 等动态加载
- **浏览器连不上** → 确认 `--remote-debugging-port=9222` 启动
- **提取为空** → `dp query <selector> --limit 1` 验证选择器 → 确认内容已加载
- **弹窗阻塞** → `dp dialog-accept` 或 `dp dialog-dismiss`
- **iframe 内容** → DrissionPage 天然穿透 iframe，用 `dp snapshot` 直接可见
- **定位不准** → `dp dom "ref:N" -d parent --depth 3` 查看上下文 → 用 `css` 字段获取精确路径
- **需要 JS 兜底** → `dp eval "..."` 执行自定义 JavaScript
- **具体命令用法** → `dp <command> --help`
