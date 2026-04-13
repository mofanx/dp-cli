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

基于 a11y tree，三种模式：

```
### Page Snapshot (full)
- URL: https://www.zhipin.com/web/geek/jobs
- Title: 「深圳招聘」- BOSS直聘
- Nodes: 957 total, 80 interactive

- RootWebArea "「深圳招聘」- BOSS直聘"
  - link "BOSS直聘" → text:BOSS直聘
  - list
    - listitem "首页" [level=1]
      - link "首页" → text:首页
    - listitem "职位" [level=1]
      - link "职位" → text:职位
  - textbox "搜索职位、公司" → @placeholder=搜索职位、公司
  - link "测试开发(深圳)" → text:测试开发(深圳)
  ...
```

- **full（默认）**：完整内容，零截断
- **brief**：截断长文本，跳过正文细节，保留结构+交互
- **text**：纯文本按阅读顺序输出

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
