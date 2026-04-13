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

快照已自动完成第一步（检测重复模式 + 给出 extract 命令提示）：

```
#### 📋 列表区 [.job-list-container] (30个交互元素)
  📊 检测到 15 条重复项 (容器: css:.card-area)
  字段: job-name, job-salary, boss-name, company-location
  💡 批量提取: dp extract "css:.card-area" '{...}'
```

基于快照提示：

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
