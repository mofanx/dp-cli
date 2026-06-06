"""
全局 pytest fixtures 与配置。
仅提供通用测试基础设施(临时目录、隔离的 HOME 等),不含具体业务测试。
"""
import json
from pathlib import Path

import pytest


@pytest.fixture
def tmp_work_dir(tmp_path: Path) -> Path:
    """提供一个干净的临时工作目录。"""
    return tmp_path


@pytest.fixture
def isolated_home(tmp_path: Path, monkeypatch) -> Path:
    """
    将 HOME 指向临时目录,隔离会话文件等对真实用户目录的读写。
    需要操作 ~/.dp-cli 之类路径的测试可使用本 fixture。
    """
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    return home


@pytest.fixture
def write_json(tmp_path: Path):
    """便捷写入 JSON 文件并返回路径,供需要样例数据的测试使用。"""
    def _write(name: str, data) -> Path:
        p = tmp_path / name
        p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return p
    return _write