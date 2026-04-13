# 数据提取参考

> 完整参数：`dp extract --help` / `dp query --help`

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

## 提取流程

1. `dp snapshot` 查看页面结构，从 a11y tree 中识别列表容器
2. `dp query` 验证选择器和字段
3. `dp extract` 批量提取

```bash
# 1. 验证（小量）
dp query "css:.card-area .job-name" --fields "text,loc" --limit 3

# 2. 提取
dp extract "css:.card-area" \
  '{"title":"css:.job-name",
    "salary":"css:.job-salary",
    "company":"css:.company-location",
    "url":{"selector":"css:.job-name","attr":"href"}}' \
  --limit 50 --output csv --filename jobs.csv
```

## 分页提取

```bash
dp extract "css:.card" '{"title":"css:.title"}' --limit 100 --filename p1.csv
dp click "css:.next-page"
dp wait --loaded
dp extract "css:.card" '{"title":"css:.title"}' --limit 100 --filename p2.csv
```
