---
name: dp-cli
description: 使用 dp-cli 控制浏览器、提取网页数据、自动化操作。当用户需要打开网页、点击元素、填写表单、截图、抓取列表数据、监听网络请求、操作 Cookie/Storage，或者需要连接自己已登录的浏览器进行操作时，使用这个技能。特别适合：批量提取结构化数据（商品/职位/新闻列表）、需要保留登录状态的自动化、需要穿透 shadow-root/iframe 的复杂页面操作。
---

# dp-cli

基于 DrissionPage 的命令行浏览器自动化工具。天然反检测、描述性定位语法、直接穿透 shadow-root/iframe。

**查看所有命令和参数：`dp --help` / `dp <command> --help`**

---

## 第一步：连接用户浏览器

**dp-cli 默认会启动一个全新的临时浏览器实例，没有登录态、没有 Cookie、没有历史记录。绝大多数实际任务都需要连接用户已打开的浏览器。**

使用前，先确认用户是否已用调试端口启动浏览器：

```bash
# 用户需要先执行（只需一次，浏览器保持打开即可）：
google-chrome --remote-debugging-port=9222

# 然后 dp-cli 连接用户浏览器：
dp open <url> --port 9222
# 后续所有命令自动复用此连接，无需再加 --port
```

**如果用户没有打开调试端口，你应该提示用户执行上面的 chrome 启动命令。**

只有以下情况可以不加 `--port`（使用临时实例）：
- 用户明确说不需要登录态
- 纯公开页面抓取，不涉及任何账户数据

---

## 核心思维：先看再做

**任何浏览器任务的第一步都是 `dp snapshot`，不要盲目操作。**

快照会自动分析页面结构，告诉你：
- 页面类型（列表页 / 内容页 / 含搜索）
- 各功能区域（导航栏 / 搜索区 / 筛选区 / 列表区 / 内容区 / 页脚）
- 每个区域的可交互元素及其定位器
- 检测到的重复模式（列表卡片），并提供 `dp extract` 命令提示
- 主体内容文本（文章正文等）

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

```
dp open <url> --port 9222
dp snapshot             → 快照会自动检测重复模式，给出 extract 命令提示
                          例如：💡 批量提取: dp extract "css:.card-area" '{...}'
dp query <selector>     → 验证字段选择器是否正确（先 --limit 1 小量验证）
dp extract <container> <fields_json>  → 批量提取，保存 CSV/JSON
```

**快照输出里的 📊 和 💡 提示就是你的提取起点，不需要手动分析 DOM。**

### 场景三：读取页面内容（文章/详情）

```
dp open <url> --port 9222
dp snapshot             → default 模式自带主体内容（markdown 格式）
                          如果内容被截断，用 --mode content 单独看
```

### 场景四：监听网络请求（抓接口数据）

```
dp listen --filter "api/xxx"   → 开始监听
dp click "text:加载更多"        → 触发请求
dp listen-stop                  → 获取捕获的请求+响应体
```

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

1. **先连接用户浏览器** — `dp open <url> --port 9222`，没有端口就提示用户启动
2. **先 snapshot，后操作** — 不要猜页面结构
3. **信任快照的定位器** — 它已经选了最稳定的（id > aria > text > css）
4. **利用快照的自动化提示** — 📊 重复模式 + 💡 extract 命令直接用
5. **操作后再 snapshot 确认** — 验证结果而非假设成功
6. **小量验证再批量** — extract 先 `--limit 1`，确认字段对了再放大
7. **动态页面先等待** — `dp wait --loaded` 或 `dp wait --selector "css:.target"`

---

## 故障排查

- **元素找不到** → `dp snapshot` 确认元素存在 → `dp wait --selector` 等动态加载
- **浏览器连不上** → 确认 `--remote-debugging-port=9222` 启动
- **提取为空** → `dp query <selector>` 验证选择器 → 确认内容已加载
- **具体命令用法** → `dp <command> --help`
