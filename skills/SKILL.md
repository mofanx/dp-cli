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
- 所有可交互元素及其定位器（link/button/input 等，可直接用于 `dp click`/`dp fill`）
- 内容文本（段落、代码块、列表项）

三种模式：
- `dp snapshot` — **full**（默认），完整内容，首次调用用这个
- `dp snapshot --mode brief` — 精简模式，截断长文本保留结构+交互，省 token
- `dp snapshot --mode text` — 纯文本，按阅读顺序输出

**快照是你理解页面的唯一入口，拿到快照后再决定下一步操作。**

---

## 通用调用逻辑

### 场景一：页面操控（登录/填表/点击/导航）

```
dp open <url> --port 9222  → 连接用户浏览器并打开页面
dp snapshot             → 看页面结构，找到目标元素的定位器
dp fill <locator> <val> → 填写输入框
dp click <locator>      → 点击按钮/链接
dp wait --text "xxx"    → 等待预期结果出现
dp snapshot             → 确认操作结果
```

**关键：每次操作后用 snapshot 确认结果，不要假设操作一定成功。**

### 场景二：批量数据提取（列表页）

**优先使用批量提取，避免逐个点击的低效方式。**

```
dp open <url> --port 9222
dp snapshot             → 看页面结构，识别列表容器和字段
dp query <selector>     → 验证字段选择器（先 --limit 1 小量验证）
dp extract <container> <fields_json>  → 批量提取，保存 CSV/JSON
```

**从快照中找到列表容器的 CSS 选择器，用 `dp query` 验证后再 `dp extract`。**

**只有在以下情况才需要逐个点击：**
- 需要点击进入详情页获取完整信息
- 数据是动态加载的，滚动才能触发加载

### 场景三：读取页面内容（文章/详情）

```
dp open <url> --port 9222
dp snapshot             → full 模式包含完整页面内容
dp snapshot --mode brief → 只看结构和交互，省 token
dp snapshot --selector "css:.article-body"  → 只看指定区域
```

### 场景四：监听网络请求（抓接口数据）

```
dp open <url> --port 9222
dp listen --filter "api/xxx"   → 开始监听
dp click "text:加载更多"        → 触发请求
dp listen-stop                  → 获取捕获的请求+响应体
```

### 场景五：列表+详情页面

**特点：页面分为列表区和详情区，点击列表项时详情内容动态更新（常见于电商、招聘、内容管理等网站）。**

```
dp open <url> --port 9222
dp snapshot             → 分析页面结构，确认列表选择器和详情容器

# 方案A：如果列表卡片已包含所需信息，优先批量提取
dp extract "css:.list-item" '{字段1, 字段2, 字段3}'  → 一次性获取所有基本信息

# 方案B：如果需要完整详情信息，才逐个点击
# 先通过 dp query 确认列表项索引和定位方式
for i in range(n):
  dp click <locator>  → 点击第i个列表项
  dp wait --loaded
  dp query "css:.detail-container"  → 提取详情
```

**关键判断：先检查列表项是否已包含足够信息，避免不必要的点击。**

---

## 定位语法

| 语法 | 说明 | 示例 |
|------|------|------|
| `text:xxx` | 文本包含 | `text:登录` |
| `text=xxx` | 文本精确 | `text=提交` |
| `#id` | ID | `#submit` |
| `@attr=val` | 属性 | `@name=username` |
| `css:xxx` | CSS 选择器 | `css:form > button` |
| `xpath:xxx` | XPath | `xpath://button` |

**定位器优先级**：`#id` > `@data-testid` / `@aria-label` > `text:` > `css:.class` > xpath

**快照输出的每个元素都自带推荐定位器，直接复制使用。**

---

## extract 字段映射

```json
{
  "字段名": "css:.子元素",
  "链接": {"selector": "css:a", "attr": "href"},
  "标签列表": {"selector": "css:.tag", "multi": true},
  "可选字段": {"selector": "css:.x", "default": ""}
}
```

---

## 关键原则

1. **先尝试连接用户浏览器** — `dp open --port 9222`，连接失败再提示用户启动调试端口
2. **先 snapshot，后操作** — 不要猜页面结构
3. **信任快照的定位器** — 它已经选了最稳定的（id > aria > text > css）
4. **善用 brief 模式省 token** — 循环操作中用 `--mode brief`，需要完整信息时再用 full
5. **操作后再 snapshot 确认** — 验证结果而非假设成功
6. **小量验证再批量** — extract 先 `--limit 1`，确认字段对了再放大
7. **动态页面先等待** — `dp wait --loaded` 或 `dp wait --selector "css:.target"`
8. **严禁编造数据** — 所有数据必须从页面实际提取，不得凭空编造或猜测任何信息
9. **优先批量提取** — 列表页优先用 `dp extract`，避免逐个点击的低效方式
10. **注意反爬机制** — 遇到加密数据（如薪资用特殊字符）、字体加密等，如实说明无法解密，不得猜测

---

## 数据完整性强制验证（防止编造数据）

- 所有数据必须来自 `dp snapshot` / `dp query` / `dp extract` 的实际输出
- 遇到字体加密、乱码等特殊字符，标记为 `[无法解密]`，不得猜测
- 保存前对比原始输出，确保数据一致

---

## 故障排查

- **元素找不到** → `dp snapshot` 确认元素存在 → `dp wait --selector` 等动态加载
- **浏览器连不上** → 确认 `--remote-debugging-port=9222` 启动
- **提取为空** → `dp query <selector>` 验证选择器 → 确认内容已加载
- **具体命令用法** → `dp <command> --help`
