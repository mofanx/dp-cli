# dp-cli 完整命令参考

> 本文件是详细命令手册，日常使用见主文件 `../SKILL.md`

## 目录
- [会话管理](#会话管理)
- [导航](#导航)
- [页面快照](#页面快照)
- [数据提取：extract / query](#数据提取)
- [元素交互](#元素交互)
- [键盘操作](#键盘操作)
- [JavaScript 执行](#javascript-执行)
- [标签页管理](#标签页管理)
- [截图与 PDF](#截图与-pdf)
- [等待](#等待)
- [网络监听](#网络监听)
- [Cookie 管理](#cookie-管理)
- [Storage 管理](#storage-管理)
- [HTTP 模式](#http-模式)
- [状态保存/加载](#状态保存加载)
- [窗口控制](#窗口控制)
- [配置管理](#配置管理)

---

## 会话管理

```bash
# 默认会话（session name = default）
dp open https://example.com

# 命名会话（支持多浏览器并行）
dp -s work open https://github.com
dp -s personal open https://gmail.com

# 列出所有会话
dp list

# 关闭指定会话
dp -s work close

# 关闭所有会话
dp close-all

# 强制创建新实例（不复用已有浏览器）
dp open https://example.com --new

# 无头模式
dp open https://example.com --headless

# 使用自定义 profile
dp open https://example.com --profile /path/to/profile

# 使用代理
dp open https://example.com --proxy http://127.0.0.1:7890
```

---

## 导航

```bash
dp goto https://example.com
dp goto https://example.com --timeout 60
dp reload
dp go-back
dp go-forward
```

---

## 页面快照（核心能力）

```bash
# 可交互元素快照（默认，AI 操控最佳）
dp snapshot

# 去噪内容树：只保留有文本的语义节点，适合找数据 CSS 类名
dp snapshot --mode content
dp snapshot --mode content --max-text 40    # 过滤超长文本节点
dp snapshot --mode content --min-text 5     # 过滤超短文本节点

# 完整 DOM 树
dp snapshot --mode full

# 纯文本内容
dp snapshot --mode text

# 限定范围
dp snapshot --selector "css:#main-content"
dp snapshot --selector "xpath://form[@id='login']"

# JSON 格式输出（适合程序解析）
dp snapshot --format json

# 保存到文件
dp snapshot --filename snapshot.txt
dp snapshot --format json --filename snapshot.json
```

### 快照输出示例

```
### Page
- URL: https://example.com/login
- Title: 登录 - Example

### Interactive Elements (4 found)

[0] <input type="text" name="username" placeholder="用户名">
     loc: @name=username
[1] <input type="password" name="password" placeholder="密码">
     loc: @name=password
[2] <input type="checkbox" name="remember">
     loc: @name=remember
[3] <button type="submit"> "登录"
     loc: text:登录
```

---

## 数据提取

### extract — 批量结构化提取

```bash
# 基本用法
dp extract "css:.card" '{"title":"css:.title","price":"css:.price"}'

# 取属性值
dp extract "css:.item" '{"url":{"selector":"css:a","attr":"href"}}'

# 取多值列表（multi）
dp extract "css:.item" '{"tags":{"selector":"css:.tag","multi":true}}'

# 缺失时的默认值
dp extract "css:.item" '{"desc":{"selector":"css:.desc","default":"暂无"}}'

# 保存 CSV（utf-8-sig，Excel 直接打开不乱码）
dp extract "css:.card" '{"title":"css:.title"}' --output csv --filename result.csv

# 保存 JSON
dp extract "css:.card" '{"title":"css:.title"}' --filename result.json

# 限制条数
dp extract "css:.card" '{"title":"css:.title"}' --limit 50
```

### query — 按选择器批量查询元素属性

```bash
# 提取文本
dp query "css:.title" --fields "text"

# 提取多个字段（逗号分隔）
dp query "css:a" --fields "text,href"

# 支持的字段：text, tag, loc, href, src, id, class, 任意 HTML 属性名
dp query "css:.item" --fields "text,id,class,loc"

# 限制条数、保存文件
dp query "css:.title" --fields "text" --limit 100 --filename titles.json
```

---

## 定位语法（DrissionPage 特有，比 e15 ref 更强大）

```bash
# 按文本（模糊）
dp click "text:登录"

# 按文本（精确）
dp click "text=精确文本"

# 按 ID（快捷）
dp click "#submit-btn"

# 按 class（快捷）
dp click ".btn-primary"

# 按属性
dp click "@data-action=submit"
dp click "@name=username"
dp click "@placeholder=请输入用户名"

# CSS 选择器
dp click "css:.btn-primary"
dp click "css:form > button[type=submit]"

# XPath
dp click "xpath://button[@type='submit']"

# tag 快捷
dp click "t:button"

# 多条件与（同时满足）
dp click "@@tag()=button@@text():提交"

# 多条件或（满足其一）
dp click "@|class=btn@|class=button"

# 排除条件
dp click "@!class=disabled"

# 属性存在检查
dp click "@data-active"

# 模糊/精确/前缀/后缀
dp click "@class:active"    # 包含
dp click "@class=active"    # 精确
dp click "@class^=btn"      # 前缀
dp click "@class$=large"    # 后缀
```

---

## 元素交互

```bash
# 点击
dp click "text:登录"
dp click "#btn" --by-js          # JavaScript 点击
dp click "css:li" --index 3      # 第3个匹配

# 双击
dp dblclick "#editable-cell"

# 填入
dp fill "@name=username" admin
dp fill "#search" "关键词"
dp fill "css:textarea" "多行内容"

# 清空
dp clear "#input"

# 下拉选择
dp select "@name=city" beijing           # 按 value
dp select "#role" 管理员 --by-text       # 按文本
dp select "#size" "" --by-index 2        # 按位置

# 悬停
dp hover "css:.dropdown-trigger"
dp hover "#tooltip" --offset-x 10 --offset-y 5

# 拖拽
dp drag "#draggable" "#drop-zone"
dp drag "#item" "#target" --duration 1.0

# checkbox/radio
dp check "@name=agree"
dp check "@name=remember" --uncheck

# 文件上传
dp upload "css:input[type=file]" /path/to/file.pdf

# 滚动
dp scroll --y 300              # 向下
dp scroll --y -200             # 向上
dp scroll-to "#footer"         # 滚动到元素
dp scroll --locator "css:.list" --y 100  # 元素内滚动
```

---

## 键盘操作

```bash
dp press Enter
dp press Tab
dp press Escape
dp press "Control+A"
dp press "Control+C"
dp press "Shift+Enter"
dp press ArrowDown
dp press F5

# 输入文本（当前焦点）
dp type "search query"
```

---

## 元素查询与检查

```bash
# 查找元素
dp find "css:a"
dp find "css:a" --all      # 返回所有匹配

# 详细检查（DrissionPage 独有：位置/尺寸/样式/状态）
dp inspect "#submit-btn"
dp inspect "#submit-btn" --include-rect      # 含位置尺寸
dp inspect "css:input" --include-style       # 含计算样式
```

---

## JavaScript 执行

```bash
# 表达式求值
dp eval "document.title"
dp eval "window.innerWidth"

# 函数（return 值）
dp eval "return Array.from(document.links).map(l=>l.href)"

# 在元素上执行（this 指向元素）
dp eval "el => el.textContent" --locator "#header"
dp eval "return this.value" --locator "#input"

# 添加初始化脚本（每个新页面执行）
dp add-init-js "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})"
dp add-init-js "delete navigator.__proto__.webdriver"
```

---

## 标签页管理

```bash
dp tab-list
dp tab-new
dp tab-new https://example.com
dp tab-new https://example.com --background
dp tab-select 0          # 切到第1个
dp tab-select 2          # 切到第3个
dp tab-close             # 关闭当前
dp tab-close 1           # 关闭第2个
```

---

## 截图与 PDF

```bash
# 截图（当前视口）
dp screenshot
dp screenshot --filename page.png

# 完整页面截图（含视口外，DrissionPage 独有）
dp screenshot --full-page

# 元素截图
dp screenshot --locator "#chart"
dp screenshot --locator "css:.card" --filename card.png

# PDF
dp pdf
dp pdf --filename output.pdf
```

---

## 对话框处理

```bash
dp dialog-accept
dp dialog-accept "确认内容"    # prompt 对话框输入内容
dp dialog-dismiss
```

---

## 等待

```bash
dp wait --loaded                            # 等待页面加载
dp wait --locator "#result"                 # 等待元素出现
dp wait --locator-gone "css:.loading"       # 等待元素消失
dp wait --url "success"                     # 等待 URL 包含字符串
dp wait --text "操作成功"                    # 等待页面包含文本
dp wait --loaded --timeout 60               # 自定义超时
```

---

## 网络监听（DrissionPage 核心独有能力）

```bash
# 开始监听（在执行操作前调用）
dp listen --filter "api/login"
dp listen --count 5 --timeout 10
dp listen                        # 监听所有请求

# 执行触发请求的操作
dp click "text:提交"

# 获取捕获的数据
dp listen-stop
dp listen-stop --timeout 15
```

---

## Cookie 管理

```bash
dp cookie-list
dp cookie-list --domain example.com
dp cookie-get session_id
dp cookie-set token abc123
dp cookie-set session_id xyz --domain example.com --http-only --secure
dp cookie-delete session_id
dp cookie-clear
```

---

## Storage 管理

```bash
# localStorage
dp localstorage-list
dp localstorage-get theme
dp localstorage-set theme dark
dp localstorage-delete theme
dp localstorage-clear

# sessionStorage
dp sessionstorage-list
dp sessionstorage-get step
dp sessionstorage-set step 3
dp sessionstorage-clear
```

---

## 状态保存/加载

```bash
# 保存（Cookie + localStorage）
dp state-save
dp state-save auth.json

# 加载
dp state-load
dp state-load auth.json
```

---

## HTTP 模式（无浏览器，高效爬虫）

```bash
# GET 请求
dp http-get https://api.example.com/users
dp http-get https://example.com --output page.html
dp http-get https://api.example.com --headers '{"Authorization":"Bearer token"}'

# POST 请求
dp http-post https://api.example.com/login \
  --data '{"username":"admin","password":"123"}'
dp http-post https://example.com/form \
  --form '{"name":"test","email":"test@example.com"}'
```

---

## 窗口控制

```bash
dp resize 1920 1080
dp resize 375 812      # 移动端模拟
dp maximize
```

---

## 配置管理

```bash
dp config-set --browser-path /usr/bin/google-chrome
dp config-set --user-path /home/user/.chrome-data
dp config-set --copy-config   # 复制配置文件到当前目录
```

---

## 典型工作流示例

### 登录并抓取数据

```bash
dp open https://example.com/login
dp snapshot
dp fill "@name=username" admin
dp fill "@name=password" mypassword
dp listen --filter "api/user/info"
dp click "text:登录"
dp listen-stop
dp state-save auth.json
```

### 反检测模式

```bash
dp open https://protected-site.com
dp add-init-js "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})"
dp goto https://protected-site.com
dp snapshot
```

### 多标签页并行

```bash
dp -s tab1 open https://site-a.com
dp -s tab2 open https://site-b.com
dp -s tab1 snapshot
dp -s tab2 snapshot
```

### 移动端模拟

```bash
dp open https://m.example.com
dp resize 375 812
dp snapshot
```

---

## JSON 输出格式

所有命令输出统一 JSON 格式，便于程序/AI 解析：

```json
{
  "status": "ok",
  "message": "操作成功",
  "data": { ... }
}
```

错误时：

```json
{
  "status": "error",
  "code": "ELEMENT_NOT_FOUND",
  "message": "未找到元素: text:登录",
  "detail": "..."
}
```

---

## 与 playwright-cli 对比

| 特性 | playwright-cli | dp-cli |
|------|---------------|--------|
| 反检测 | 需额外配置 | **天然支持** |
| 元素定位 | a11y ref (e15) | **描述性语法，跨导航稳定** |
| 快照效率 | a11y tree（多次CDP） | **lxml一次解析** |
| 元素信息 | 基础属性 | **位置/CSS/JS属性/状态** |
| shadow-root | 需特殊处理 | **直接穿透** |
| iframe | 需切换 | **直接跨越** |
| 网络监听 | route（拦截） | **listen（捕获+读取响应体）** |
| HTTP模式 | 无 | **内置高效HTTP模式** |
| 多浏览器并行 | 支持 | **支持（命名会话）** |
