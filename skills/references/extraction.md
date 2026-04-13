# 数据提取参考

> 完整参数：`dp extract --help` / `dp query --help`

## 提取流程（最佳实践）

```
1. dp snapshot                         → 识别列表结构，找到一个列表项的 ref 编号
2. dp dom "ref:21" -d parent --depth 3 → 追溯父节点链，找到最佳容器类名
3. dp query "css:.card" --fields "text,loc" --limit 2  → 小量验证选择器
4. dp extract "css:.card" '{"title":"css:.name",...}'   → 批量提取
```

**关键：用 dom 命令向上追溯找容器，比猜 CSS 更准确。**

## 字段映射格式

```json
{
  "标题": "css:.title",
  "链接": {"selector": "css:a", "attr": "href"},
  "标签": {"selector": "css:.tag", "multi": true},
  "描述": {"selector": "css:.desc", "default": "暂无"}
}
```

| 参数 | 说明 |
|------|------|
| `selector` | 子元素定位器（相对于容器） |
| `attr` | 取属性值（href/src/data-id 等） |
| `multi` | `true` 返回匹配列表 |
| `default` | 元素缺失时的回退值 |

## 完整示例

```bash
# 1. 快照 → 找到职位名的 ref 编号（如 ref:21）
dp snapshot --mode brief

# 2. 追溯容器
dp dom "ref:21" -d parent --depth 5
# 输出: a.job-name → div.job-title → div.job-info → li.job-card-box → div.card-area

# 3. 小量验证
dp query "css:.card-area .job-name" --fields "text,loc" --limit 2

# 4. 批量提取
dp extract "css:.card-area" \
  '{"title":"css:.job-name",
    "salary":"css:.job-salary",
    "company":"css:.company-location",
    "url":{"selector":"css:.job-name","attr":"href"}}' \
  --limit 50 --output csv --filename jobs.csv
```

## 分页提取

```bash
for page in 1 2 3:
  dp extract "css:.card" '{"title":"css:.title"}' --filename p${page}.csv
  dp click "css:.next-page"    # 或 dp click "ref:N"
  dp wait --loaded
```

## 无限滚动提取

```bash
for i in range(max_rounds):
  dp extract "css:.item" '{"title":"css:.title"}' --filename batch_${i}.csv
  dp scroll --y 3000
  dp wait --loaded
```
