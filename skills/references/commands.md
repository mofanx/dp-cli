# dp-cli 命令速查

> 所有命令的完整参数：`dp --help` / `dp <command> --help`
>
> 使用逻辑和工作流见主文件 `../SKILL.md`

## 命令一览

| 类别 | 命令 | 说明 |
|------|------|------|
| 浏览器 | `open`, `close`, `close-all`, `list` | 启动/关闭/列出会话 |
| 导航 | `goto`, `reload`, `go-back`, `go-forward` | 页面跳转 |
| 快照 | `snapshot` | 页面结构分析（核心，输出带 `[N]` 编号） |
| 提取 | `extract`, `query`, `find`, `inspect`, `dom` | 数据提取和元素查询 |
| 交互 | `click`, `dblclick`, `fill`, `clear`, `select`, `check`, `hover`, `scroll`, `scroll-to`, `drag`, `upload` | 元素操控 |
| 键盘 | `press`, `type` | 键盘输入 |
| 等待 | `wait` | `--loaded` / `--locator` / `--text` / `--locator-gone` / `--url` |
| 监听 | `listen`, `listen-stop` | 网络请求捕获 |
| 标签页 | `tab-list`, `tab-new`, `tab-select`, `tab-close` | 多标签页管理 |
| 截图 | `screenshot`, `pdf` | 页面截图/PDF（支持全页截图、元素截图） |
| JS | `eval`, `add-init-js` | 执行 JavaScript |
| HTTP | `http-get`, `http-post` | 纯 HTTP 请求（无需浏览器） |
| 对话框 | `dialog-accept`, `dialog-dismiss` | alert/confirm/prompt 处理 |
| 状态 | `state-save`, `state-load` | Cookie + localStorage 保存/恢复 |
| Cookie | `cookie-list`, `cookie-get`, `cookie-set`, `cookie-delete`, `cookie-clear` | Cookie 细粒度操作 |
| Storage | `localstorage-*`, `sessionstorage-*` | localStorage/sessionStorage 操作 |
| 窗口 | `resize`, `maximize` | 窗口控制 |
| 配置 | `config-set`, `delete-data` | 浏览器路径/数据目录 |

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
- **brief**：截断长文本，跳过正文细节，保留结构+交互，省 token
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
