# dp-cli 测试指南

本文档说明如何运行、编写和维护 dp-cli 的测试。

## 1. 环境准备

```bash
# 安装项目 + 测试依赖(test 组在 pyproject.toml 的 optional-dependencies)
pip install -e ".[test]"
```

测试依赖(`[project.optional-dependencies].test`):
`pytest`、`pytest-cov`、`pytest-timeout`、`pytest-mock`。

## 2. 运行测试

```bash
# 运行全部单元测试
pytest

# 只跑单元测试(排除需要浏览器的集成测试)—— 与 CI 一致
pytest -m "not integration"

# 跑单个文件 / 单个用例
pytest tests/test_session.py
pytest tests/test_session.py::test_save_and_load_session

# 带覆盖率(覆盖率参数不在默认 addopts 中,需手动加)
pytest --cov=dp_cli --cov-report=term-missing

# 生成 HTML 覆盖率报告(输出到 htmlcov/)
pytest --cov=dp_cli --cov-report=html
```

### pytest markers

| marker | 含义 |
|--------|------|
| `unit` | 单元测试,不依赖浏览器(当前测试基本都属于此类) |
| `integration` | 集成测试,需要真实浏览器(CI 中单独 job,默认不随单测运行) |
| `slow` | 慢速测试 |
| `network` | 需要网络访问 |

```bash
pytest -m unit            # 只跑单元测试
pytest -m "not slow"      # 跳过慢速测试
```

## 3. 测试结构

```
tests/
├── conftest.py              # 全局 fixtures(tmp_work_dir / isolated_home / write_json)
├── test_session.py          # 会话管理(session.py)
├── test_recorder.py         # 录制器导出/格式化(recorder.py)
├── test_a11y.py             # a11y 树构建与渲染(snapshot/a11y.py)
├── test_snapshot_small.py   # snapshot 小模块(utils/clickable/extract)
├── test_commands.py         # 命令工具与 CLI 入口(commands/_utils.py、output.py、CliRunner)
├── test_clickable.py        # 可点击元素检测(已有)
├── test_pw_locator.py       # Playwright 风格定位器(已有)
├── test_resolve_locator.py  # 定位器解析(已有)
├── test_bridge_manager.py   # 桥接管理器(已有)
└── test_bridge_integration.py
```

## 4. 编写测试的约定

1. **标记类型**:每个测试加 `@pytest.mark.unit`(或相应 marker)。
2. **不依赖真实浏览器/网络**:用假对象 mock DrissionPage 的 `page` / 元素。
   - 浏览器函数内部多为延迟 `import`(如 `from DrissionPage import ChromiumPage`),
     patch 时打到**源模块属性**:`monkeypatch.setattr("DrissionPage.ChromiumPage", fake)`。
   - `requests` / `socket` 同理 patch 到被调用处。
3. **行为验证优先**:断言函数的返回值/副作用/输出子串,**不要**对内嵌的 JS 字符串
   (如 `recorder._RECORDER_JS`、`a11y` 的 fallback JS)做内容断言 —— 那是脆弱反模式。
4. **文件隔离**:涉及会话/文件读写的测试,patch 模块级目录常量
   (如 `session._SESSION_DIR`),不要依赖真实用户目录。conftest 提供了相关 fixture。
5. **CLI 测试**:用 `click.testing.CliRunner` invoke 命令;mock `commands._utils.get_browser`
   与 `load_session` 来避免真实浏览器,断言退出码与 JSON 输出。

### 一个最小示例
```python
import pytest
from dp_cli import session

@pytest.mark.unit
def test_load_session_missing_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(session, "_SESSION_DIR", tmp_path / "sessions")
    assert session.load_session("nope") == {}
```

## 5. 覆盖率现状(参考)

| 模块 | 覆盖率 |
|------|--------|
| session.py | ~92% |
| recorder.py | ~91% |
| snapshot/utils.py | ~96% |
| snapshot/clickable.py | ~95% |
| snapshot/a11y.py | ~77% |
| snapshot/extract.py | ~76% |
| output.py | ~84% |
| commands/_utils.py | ~63% |
| **整体** | **~50%** |

> 目标为方向性:核心模块(session/recorder/snapshot)较高;命令模块覆盖关键路径即可。
> 浏览器交互的深层分支不强求覆盖。

## 6. CI

`.github/workflows/test.yml`:
- **test job**:Python 3.10–3.13 矩阵,`pytest -m "not integration"` + 覆盖率,
  Python 3.12 上传 Codecov。
- **integration job**:headless 浏览器(`browser-actions/setup-chrome`),
  当前 `if: false` 禁用,待补充 `@pytest.mark.integration` 用例后启用。

## 7. 维护原则

1. **新功能**必须带相应单元测试。
2. **Bug 修复**应附回归测试(例:`test_a11y.py::test_take_a11y_snapshot_total_failure`
   即为修复 `cdp_err` 作用域 bug 的回归测试)。
3. **重构**前先保证测试通过,再改代码。
4. 定期运行测试套件,关注执行时间,及时更新失效测试与依赖。
