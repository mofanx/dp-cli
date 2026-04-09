# 数据提取模式与示例

使用 dp-cli 的 3 步工作流从网页提取结构化数据的指南。

## 3 步数据提取工作流

### 第 1 步：发现页面结构

使用快照了解页面布局并识别容器元素。

```bash
# 完整快照（可交互元素 + 内容）
dp snapshot

# 仅内容快照（专注文本结构）
dp snapshot --mode content

# 限制文本长度以清晰查看结构
dp snapshot --mode content --max-text 40
```

**需要关注的内容：**
- 重复元素（职位卡片、商品项、文章条目）
- 容器类名（`.card`、`.item`、`.job-item`）
- 字段类名（`.title`、`.price`、`.company`、`.location`）

### 第 2 步：验证选择器

在批量提取前使用 `dp query` 测试选择器。

```bash
# 测试容器选择器
dp query "css:.job-card" --fields "text"

# 测试字段选择器
dp query "css:.job-title" --fields "text,loc"
dp query "css:.company-name" --fields "text,loc"
dp query "css:.salary" --fields "text,loc"
```

**需要提取的关键字段：**
- `text` - 可见文本内容
- `loc` - 推荐的定位器（用于后续交互）
- `css_path` - 精确 CSS 路径（用于复杂场景）
- `href` - 链接 URL
- `attr` - 任何属性值

### 第 3 步：批量提取

使用 `dp extract` 配合容器和字段映射。

```bash
dp extract "css:.job-card" \
  '{"title":"css:.job-title",
    "company":"css:.company-name",
    "location":"css:.location",
    "salary":"css:.salary"}' \
  --limit 100 --output csv --filename jobs.csv
```

## 常见提取模式

### 简单文本字段

从子元素提取文本。

```bash
dp extract "css:.card" \
  '{"title":"css:.title",
    "description":"css:.description"}'
```

### 链接 URL

从链接提取 `href` 属性。

```bash
dp extract "css:.article" \
  '{"title":"css:.title",
    "url":{"selector":"css:a","attr":"href"}}'
```

### 多个值（multi: true）

提取多个匹配元素作为列表。

```bash
dp extract "css:.product" \
  '{"name":"css:.name",
    "tags":{"selector":"css:.tag","multi":true}}'
```

输出：
```json
{
  "name": "Product A",
  "tags": ["tag1", "tag2", "tag3"]
}
```

### 嵌套结构

从嵌套容器提取。

```bash
dp extract "css:.job-card" \
  '{"title":"css:.job-title",
    "company":{"selector":"css:.company-info .name"},
    "location":{"selector":"css:.company-info .location"}}'
```

### 默认值

当元素缺失时提供回退值。

```bash
dp extract "css:.product" \
  '{"name":"css:.name",
    "price":{"selector":"css:.price","default":"N/A"},
    "discount":{"selector":"css:.discount","default":"0%"}}'
```

### 图片来源

从图片提取 `src` 属性。

```bash
dp extract "css:.product" \
  '{"name":"css:.name",
    "image":{"selector":"css:img.product-img","attr":"src"}}'
```

### 数据属性

提取自定义 `data-*` 属性。

```bash
dp extract "css:.item" \
  '{"title":"css:.title",
    "id":{"selector":"css:.item","attr":"data-id"},
    "category":{"selector":"css:.item","attr":"data-category"}}'
```

## 真实场景示例

### 职位列表（BOSS直聘风格）

```bash
# 第 1 步：发现
dp snapshot --mode content --max-text 40

# 第 2 步：验证
dp query "css:.job-card" --fields "text"
dp query "css:.job-name" --fields "text,loc"
dp query "css:.company-name" --fields "text,loc"

# 第 3 步：提取
dp extract "css:.job-card" \
  '{"title":"css:.job-name",
    "company":"css:.company-name",
    "location":"css:.location",
    "salary":"css:.salary",
    "url":{"selector":"css:.job-name","attr":"href"}}' \
  --limit 100 --output csv --filename jobs.csv
```

### 电商产品

```bash
# 第 1 步：发现
dp snapshot --mode content

# 第 2 步：验证
dp query "css:.product-card" --fields "text"
dp query "css:.product-title" --fields "text"
dp query "css:.product-price" --fields "text"

# 第 3 步：提取
dp extract "css:.product-card" \
  '{"name":"css:.product-title",
    "price":"css:.product-price",
    "rating":"css:.rating",
    "image":{"selector":"css:img.product-img","attr":"src"},
    "url":{"selector":"css:a.product-link","attr":"href"}}' \
  --limit 200 --output csv --filename products.csv
```

### 新闻文章

```bash
# 第 1 步：发现
dp snapshot --mode content

# 第 2 步：验证
dp query "css:.article-item" --fields "text"
dp query "css:.article-title" --fields "text"
dp query "css:.article-meta" --fields "text"

# 第 3 步：提取
dp extract "css:.article-item" \
  '{"title":"css:.article-title",
    "summary":"css:.article-summary",
    "author":"css:.author-name",
    "date":"css:.publish-date",
    "url":{"selector":"css:a.article-link","attr":"href"}}' \
  --limit 50 --output json --filename articles.json
```

### 社交媒体帖子

```bash
# 第 1 步：发现
dp snapshot --mode content

# 第 2 步：验证
dp query "css:.post" --fields "text"
dp query "css:.post-content" --fields "text"
dp query "css:.post-author" --fields "text"

# 第 3 步：提取
dp extract "css:.post" \
  '{"author":"css:.post-author",
    "content":"css:.post-content",
    "likes":"css:.like-count",
    "comments":"css:.comment-count",
    "timestamp":"css:.post-time"}' \
  --limit 100 --output csv --filename posts.csv
```

### 餐厅菜单项

```bash
# 第 1 步：发现
dp snapshot --mode content

# 第 2 步：验证
dp query "css:.menu-item" --fields "text"
dp query "css:.dish-name" --fields "text"
dp query "css:.dish-price" --fields "text"

# 第 3 步：提取
dp extract "css:.menu-item" \
  '{"name":"css:.dish-name",
    "description":"css:.dish-description",
    "price":"css:.dish-price",
    "category":"css:.category-tag"}' \
  --limit 50 --output csv --filename menu.csv
```

## 高级技巧

### 处理动态内容

对于通过 JavaScript 加载内容的页面：

```bash
# 等待内容加载
dp wait --loaded
dp wait --selector "css:.job-card"

# 然后快照
dp snapshot --mode content
```

### 分页

跨多页提取：

```bash
# 提取第一页
dp extract "css:.job-card" '{"title":"css:.job-title"}' --limit 100 --output csv --filename jobs.csv

# 点击下一页
dp click "css:.next-page"
dp wait --loaded

# 追加到现有文件
dp extract "css:.job-card" '{"title":"css:.job-title"}' --limit 100 --output csv --filename jobs.csv
```

### Shadow DOM

从 shadow DOM 元素提取：

```bash
dp extract "css:my-component::shadow .item" \
  '{"name":"css:.name","value":"css:.value"}'
```

### iframe 内容

从 iframe 内容提取：

```bash
dp extract "css:iframe >> css:.item" \
  '{"name":"css:.name","value":"css:.value"}'
```

## 字段映射参考

### 简单映射

```json
{
  "field_name": "child_locator"
}
```

### 完整映射

```json
{
  "field_name": {
    "selector": "child_locator",
    "attr": "attribute_name",
    "multi": false,
    "default": "fallback_value"
  }
}
```

### 参数

- `selector` - 子元素定位器（相对于容器）
- `attr` - 属性名而非文本（如 "href"、"src"、"data-id"）
- `multi` - `true` 返回所有匹配的数组
- `default` - 元素不存在时的回退值

## 输出格式

### JSON

```bash
dp extract "css:.card" '{"title":"css:.title"}' --output json
```

输出：
```json
{
  "count": 10,
  "records": [
    {"title": "Item 1"},
    {"title": "Item 2"}
  ]
}
```

### CSV

```bash
dp extract "css:.card" '{"title":"css:.title","price":"css:.price"}' --output csv
```

输出（带 BOM 以兼容 Excel）：
```csv
title,price
Item 1,$10.00
Item 2,$20.00
```

## 故障排查

### 未找到结果

1. 验证容器选择器：`dp query "css:.container" --fields "text"`
2. 检查内容是否加载：`dp wait --loaded`
3. 尝试不同选择器：`dp query "css:.card" --fields "text,loc"`

### 字段缺失

1. 测试单个字段：`dp query "css:.field-name" --fields "text,loc"`
2. 检查字段是否可选：添加 `default` 值
3. 验证字段在容器内（使用 inspect）

### 数据类型错误

1. 对 URL/ID 使用 `attr`：`{"url":{"selector":"css:a","attr":"href"}}`
2. 对列表使用 `multi`：`{"tags":{"selector":"css:.tag","multi":true}}`
3. 检查嵌套结构：调整选择器路径

### 性能问题

1. 使用 `--limit` 限制结果
2. 使用特定选择器（避免宽泛的 `div`）
3. 考虑 `--mode content` 快照以加快发现

## 最佳实践

1. **批量提取前验证选择器** 使用 `dp query`
2. **从小限制开始**（如 `--limit 5`）验证输出
3. **使用稳定选择器**（id > data-attributes > class > text）
4. **处理缺失数据** 使用 `default` 值
5. **大数据集保存到文件**（`--filename output.csv`）
6. **电子表格用 CSV**，程序处理用 JSON
7. **动态内容等待** 使用 `dp wait --loaded` 或 `--selector`
8. **扩展前在一页上测试**

## 集成示例

### 完整职位抓取脚本

```bash
#!/bin/bash
# scrape_jobs.sh

# 打开浏览器
dp open https://www.example-jobs.com

# 等待加载
dp wait --loaded

# 提取第 1 页
dp extract "css:.job-card" \
  '{"title":"css:.job-title",
    "company":"css:.company-name",
    "location":"css:.location",
    "salary":"css:.salary",
    "url":{"selector":"css:.job-title","attr":"href"}}' \
  --limit 100 --output csv --filename jobs.csv

# 分页循环
for i in {1..10}; do
  dp click "css:.next-page"
  dp wait --loaded
  dp extract "css:.job-card" \
    '{"title":"css:.job-title",
      "company":"css:.company-name",
      "location":"css:.location",
      "salary":"css:.salary",
      "url":{"selector":"css:.job-title","attr":"href"}}' \
    --limit 100 --output csv --filename jobs.csv
done

# 关闭浏览器
dp close
```

### 带登录状态

```bash
# 连接到已登录浏览器
google-chrome --remote-debugging-port=9222
dp open https://example.com --port 9222

# 提取认证内容
dp extract "css:.private-data" \
  '{"item":"css:.item","value":"css:.value"}' \
  --output json --filename data.json
```
