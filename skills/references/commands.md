# dp-cli 命令速查

> 所有命令的完整参数：`dp --help` / `dp <command> --help`
>
> 使用逻辑和工作流见主文件 `../SKILL.md`

## 命令一览

| 类别 | 命令 | 说明 |
|------|------|------|
| 浏览器 | `open`, `close`, `close-all`, `list` | 启动/关闭/列出会话 |
| 导航 | `goto`, `reload`, `go-back`, `go-forward` | 页面跳转 |
| 快照 | `snapshot` | 页面结构分析（核心命令） |
| 提取 | `extract`, `query`, `find`, `inspect` | 数据提取和元素查询 |
| 交互 | `click`, `fill`, `select`, `hover`, `scroll`, `drag` | 元素操控 |
| 键盘 | `press`, `type` | 键盘输入 |
| 等待 | `wait` | 等待加载/元素/文本 |
| 监听 | `listen`, `listen-stop` | 网络请求捕获 |
| 标签页 | `tab-list`, `tab-new`, `tab-select`, `tab-close` | 多标签页管理 |
| 截图 | `screenshot`, `pdf` | 页面截图/PDF |
| JS | `eval`, `add-init-js` | 执行 JavaScript |
| 状态 | `state-save`, `state-load` | Cookie + Storage 保存/恢复 |
| 窗口 | `resize`, `maximize` | 窗口控制 |

## snapshot 输出示例

列表页（BOSS直聘）：

```
### 页面快照
- URL: https://www.zhipin.com/web/geek/jobs
- Title: 深圳招聘 - BOSS直聘

### 页面结构 (6 个区域, 43 个可交互元素) — 列表页 | 含搜索

#### 🧭 导航栏 [#header] (13个元素)
  [0] <a> "首页" → text:首页
  [1] <a> "职位" → text:职位
  ...

#### 🔍 搜索区 [.expect-and-search] (4个元素)
  [0] <input> role=text "搜索职位、公司" ph="搜索职位、公司" → @placeholder=搜索职位、公司

#### 🏷 筛选区 [.filter-condition] (146个交互元素)
  📊 检测到 5 条重复项 (容器: css:.condition-filter-select)
  💡 批量提取: dp extract "css:.condition-filter-select" '{...}'

#### 📋 列表区 [.job-list-container] (30个交互元素)
  📊 检测到 15 条重复项 (容器: css:.card-area)
  字段: job-name, job-salary, boss-name, company-location
  💡 批量提取: dp extract "css:.card-area" '{...}'
```

## 定位语法

| 语法 | 说明 | 示例 |
|------|------|------|
| `text:xxx` | 文本包含 | `text:登录` |
| `text=xxx` | 文本精确 | `text=提交` |
| `#id` | ID | `#submit` |
| `@attr=val` | 属性 | `@name=username` |
| `css:xxx` | CSS 选择器 | `css:form > button` |
| `xpath:xxx` | XPath | `xpath://button` |
| `t:tag` | 标签名 | `t:button` |
| `@@A@@B` | 多条件与 | `@@tag()=button@@text():提交` |

属性匹配支持：`@class:active`(包含) `@class=active`(精确) `@class^=btn`(前缀) `@class$=large`(后缀)
