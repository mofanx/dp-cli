# -*- coding:utf-8 -*-
"""resolve_locator 的鲁棒解析测试（不依赖真实浏览器）。"""
import json
import pytest
from unittest.mock import patch, MagicMock

from dp_cli.commands import _utils


# ─────────────────────────────────────────────────────────────────────────────
# _mark_element_by_backend_id：CDP 现场打标
# ─────────────────────────────────────────────────────────────────────────────

def test_mark_element_success_returns_marker():
    """正常路径：DOM.resolveNode → objectId → setAttribute 成功，返回 marker。"""
    page = MagicMock()
    page.run_cdp.side_effect = [
        {'object': {'objectId': 'obj-123'}},  # DOM.resolveNode
        {'result': {'type': 'undefined'}},     # Runtime.callFunctionOn
    ]
    marker = _utils._mark_element_by_backend_id(page, 42)
    assert marker is not None
    assert marker.startswith('dp')
    assert len(marker) >= 10

    # 确认调了两个 CDP 命令
    assert page.run_cdp.call_count == 2
    # 第一个必须是 DOM.resolveNode + backendNodeId=42
    first = page.run_cdp.call_args_list[0]
    assert first.args[0] == 'DOM.resolveNode'
    assert first.kwargs.get('backendNodeId') == 42
    # 第二个必须是 Runtime.callFunctionOn + objectId=obj-123
    second = page.run_cdp.call_args_list[1]
    assert second.args[0] == 'Runtime.callFunctionOn'
    assert second.kwargs.get('objectId') == 'obj-123'
    # setAttribute 调用里的 marker 值必须跟返回的一致
    args_arg = second.kwargs.get('arguments', [])
    assert args_arg and args_arg[0].get('value') == marker


def test_mark_element_resolve_node_failure_returns_none():
    """DOM.resolveNode 抛异常 → 返回 None（上层会走 fallback）。"""
    page = MagicMock()
    page.run_cdp.side_effect = RuntimeError('node not found')
    assert _utils._mark_element_by_backend_id(page, 42) is None


def test_mark_element_no_objectid_returns_none():
    """resolveNode 返回里没 objectId（已 GC）→ 返回 None。"""
    page = MagicMock()
    page.run_cdp.return_value = {'object': {}}
    assert _utils._mark_element_by_backend_id(page, 42) is None


def test_mark_element_setattr_failure_returns_none():
    """setAttribute 抛异常 → 返回 None。"""
    page = MagicMock()
    page.run_cdp.side_effect = [
        {'object': {'objectId': 'obj-1'}},
        RuntimeError('runtime disconnected'),
    ]
    assert _utils._mark_element_by_backend_id(page, 42) is None


def test_mark_element_markers_are_unique():
    """多次调用产生不同 marker，避免冲突。"""
    page = MagicMock()
    page.run_cdp.side_effect = [
        {'object': {'objectId': 'o1'}}, {},
        {'object': {'objectId': 'o2'}}, {},
    ]
    m1 = _utils._mark_element_by_backend_id(page, 1)
    m2 = _utils._mark_element_by_backend_id(page, 2)
    assert m1 and m2 and m1 != m2


# ─────────────────────────────────────────────────────────────────────────────
# resolve_locator：ref 解析流程
# ─────────────────────────────────────────────────────────────────────────────

def test_resolve_locator_non_ref_passes_through():
    """非 ref: 直接走 normalize_locator，不查 refs。"""
    with patch.object(_utils, 'load_refs') as mock_load:
        assert _utils.resolve_locator('#submit') == 'css:#submit'
        assert _utils.resolve_locator('css:.btn') == 'css:.btn'
        assert _utils.resolve_locator('text:Login') == 'text:Login'
        mock_load.assert_not_called()


def test_resolve_locator_ref_uses_backend_id_when_available(tmp_path):
    """ref 有 backendNodeId → 优先走 CDP 打标，返回 @data-dp-ref=<marker>。"""
    refs = {'7': {'locator': '.stale-class', 'backendNodeId': 99,
                  'name': 'Hi'}}
    page = MagicMock()
    page.run_cdp.side_effect = [
        {'object': {'objectId': 'x'}},
        {},
    ]
    with patch.object(_utils, 'load_refs', return_value=refs):
        result = _utils.resolve_locator('ref:7', session='x', page=page)
    assert result.startswith('@data-dp-ref=dp')
    # 没有回落到陈旧的 .stale-class
    assert '.stale-class' not in result


def test_resolve_locator_ref_falls_back_to_locator_when_marking_fails():
    """CDP 打标失败 → 回落到保存的 locator 字符串。"""
    refs = {'3': {'locator': 'css:#voice-input-button', 'backendNodeId': 42}}
    page = MagicMock()
    page.run_cdp.side_effect = RuntimeError('resolve failed')
    with patch.object(_utils, 'load_refs', return_value=refs):
        result = _utils.resolve_locator('ref:3', session='x', page=page)
    assert result == 'css:#voice-input-button'


def test_resolve_locator_ref_falls_back_to_name_when_no_locator():
    """既没 backendNodeId、locator 也不可用 → 用 name 走 text: 定位。"""
    refs = {'5': {'locator': 't:p', 'name': 'Submit', 'backendNodeId': None}}
    with patch.object(_utils, 'load_refs', return_value=refs):
        result = _utils.resolve_locator('ref:5', session='x', page=None)
    assert result == 'text:Submit'


def test_resolve_locator_ref_not_found_exits():
    refs = {'1': {'locator': '#a', 'backendNodeId': None}}
    with patch.object(_utils, 'load_refs', return_value=refs), \
         patch.object(_utils, 'error') as mock_err:
        mock_err.side_effect = SystemExit(1)
        with pytest.raises(SystemExit):
            _utils.resolve_locator('ref:999', session='x', page=None)
        mock_err.assert_called_once()
        assert 'REF_NOT_FOUND' in str(mock_err.call_args)


def test_resolve_locator_no_refs_file_exits():
    with patch.object(_utils, 'load_refs', return_value={}), \
         patch.object(_utils, 'error') as mock_err:
        mock_err.side_effect = SystemExit(1)
        with pytest.raises(SystemExit):
            _utils.resolve_locator('ref:1', session='x', page=None)
        assert 'NO_REFS' in str(mock_err.call_args)
