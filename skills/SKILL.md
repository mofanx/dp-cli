---
name: dp-cli
description: 使用 dp-cli 控制浏览器、提取网页数据、自动化操作。当用户需要打开网页、点击元素、填写表单、截图、抓取列表数据、监听网络请求、操作 Cookie/Storage，或者需要连接自己已登录的浏览器进行操作时，使用这个技能。特别适合：批量提取结构化数据（商品/职位/新闻列表）、需要保留登录状态的自动化、需要穿透 shadow-root/iframe 的复杂页面操作。
---

# dp-cli

DrissionPage 的命令行浏览器自动化工具。所有命令输出 JSON，便于 AI 解析。

核心优势：天然反检测（`navigator.webdriver=false`）、描述性定位语法跨导航稳定、lxml 高效 DOM 快照、内置网络监听、直接穿透 shadow-root/iframe。

> 完整命令参考：`references/commands.md`

---

## 安装

```bash
pip install DrissionPage
dp --help
```

---

## 两种启动模式

### 模式一：接管已登录的浏览器（最常用）

保留 Cookie、登录状态、历史记录，适合需要登录的自动化任务。

```bash
# 第一步：用调试端口启动 Chrome（只需做一次）
google-chrome --remote-debugging-port=9222

# 第二步：dp 接管并导航
dp open https://example.com --port 9222

# 第三步：后续命令自动复用端口，无需再加 --port
dp snapshot
dp click "text:登录"
dp close   # 只断开连接，不关闭浏览器
```

### 模式二：dp 全自动管理

```bash
dp open https://example.com
dp snapshot
dp click "text:登录"
dp close
```

---

## 核心工作流

### 页面操控（登录/表单/导航）

```bash
dp open https://example.com
dp snapshot                          # 查看可交互元素及定位器
dp fill "@name=username" admin
dp fill "@name=password" mypassword
dp press Enter
dp wait --text "欢迎"
dp screenshot --filename result.png
```

### 批量数据提取（列表页三步法）

适用于商品、职位、新闻等任何重复卡片结构：

```bash
# 第一步：找 CSS 类名（去噪内容树）
dp snapshot --mode content --max-text 40

# 第二步：验证字段选择器
dp query "css:.item-title" --fields "text,loc"
dp query "css:.item-price" --fields "text"

# 第三步：批量提取，保存 CSV
dp extract "css:.item-card" \
  '{"title":"css:.item-title",
    "price":"css:.item-price",
    "tags":{"selector":"css:.tag","multi":true},
    "url":{"selector":"css:a","attr":"href"}}' \
  --limit 100 --output csv --filename result.csv
```

`extract` 的 fields_json 规范：
- `"field": "css:.cls"` → 取文本
- `"field": {"selector":"css:a","attr":"href"}` → 取属性
- `"field": {"selector":"css:li","multi":true}` → 取列表
- `"field": {"selector":"css:.x","default":""}` → 有默认值

### 网络监听（抓 XHR/Fetch 接口数据）

```bash
dp listen --filter "api/list"
dp click "text:加载更多"
dp listen-stop                       # 返回捕获的请求+响应体
```

### 登录态保存/恢复

```bash
dp state-save auth.json              # 保存 Cookie + localStorage
dp state-load auth.json              # 恢复
```

---

## 定位语法速查

| 语法 | 示例 |
|------|------|
| `text:xxx` | 文本包含 `text:登录` |
| `text=xxx` | 文本精确 `text=提交` |
| `#id` | `#submit` |
| `.class` | `.btn-primary` |
| `@attr=val` | `@name=username` |
| `css:xxx` | `css:form > button` |
| `xpath:xxx` | `xpath://button` |
| `@@A@@B` | 多条件与 `@@t()=button@@text():提交` |

---

## 快照模式

| 模式 | 用途 |
|------|------|
| `interactive`（默认） | 列出可交互元素及最优定位器，AI 操控首选 |
| `content` | 去噪内容树，找数据 CSS 类名，搭配 `extract` 使用 |
| `full` | 完整 DOM 树 |
| `text` | 纯文本 |

```bash
dp snapshot                                    # interactive
dp snapshot --mode content --selector "css:#main"  # 限定区域
dp snapshot --format json --filename snap.json
```

---

## 会话与多浏览器

```bash
dp -s work open https://work-site.com
dp -s personal open https://personal-site.com
dp -s work snapshot
dp list
dp close-all
```

---

## JSON 输出格式

```json
{"status": "ok", "message": "操作成功", "data": {...}}
{"status": "error", "code": "ELEMENT_NOT_FOUND", "message": "..."}
```

---

> 详细命令（HTTP模式/Storage/Cookie/PDF/JS执行等）见 `references/commands.md`
