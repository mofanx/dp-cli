"""
recorder.py 单元测试
重点：行为/输出验证，不对 JS 字符串内容做断言，不依赖真实浏览器。
"""
import json
import pytest

from dp_cli import recorder


# ─────────────────────────────────────────────────────────────────────────────
# 2.0 样例动作工厂
# ─────────────────────────────────────────────────────────────────────────────

def _click(locator="css:#btn", text="提交"):
    return {"type": "click", "best_locator": locator,
            "element": {"attrs": {"id": "btn"}, "text": text,
                        "locators": {"css": "css:#btn", "xpath": "xpath://button[1]"}},
            "page": {"url": "https://example.com", "title": "demo"}}


def _fill(locator="css:#name", value="hello"):
    return {"type": "fill", "best_locator": locator, "value": value,
            "element": {"attrs": {"name": "name"}, "text": "",
                        "locators": {"css": "css:#name", "xpath": ""}},
            "page": {"url": "https://example.com"}}


def _press(key="Enter", locator="css:#name"):
    return {"type": "press", "key": key, "best_locator": locator,
            "element": {"attrs": {}, "locators": {}}, "page": {}}


def _scroll(dx=0, dy=300):
    return {"type": "scroll", "delta": {"x": dx, "y": dy},
            "mouse": {"x": 10, "y": 20}, "direction": "down",
            "best_locator": "", "element": {}, "page": {}}


# ─────────────────────────────────────────────────────────────────────────────
# 2.1 export_actions 分发与异常
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_export_json():
    """export_actions([_click()], "json") 返回的是合法 JSON, json.loads 后是 list。"""
    actions = [_click()]
    result = recorder.export_actions(actions, "json")
    parsed = json.loads(result)
    assert isinstance(parsed, list)
    assert len(parsed) == 1
    assert parsed[0]["type"] == "click"


@pytest.mark.unit
def test_export_unknown_format_raises():
    """export_actions([], "foobar") 抛 ValueError。"""
    with pytest.raises(ValueError, match="不支持的导出格式"):
        recorder.export_actions([], "foobar")


# ─────────────────────────────────────────────────────────────────────────────
# 2.2 _export_dp_script
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_dp_script_click():
    """含 dp click 'css:#btn',且以 shebang #!/usr/bin/env bash 开头。"""
    actions = [_click()]
    result = recorder._export_dp_script(actions)
    assert "#!/usr/bin/env bash" in result
    assert "dp click 'css:#btn'" in result


@pytest.mark.unit
def test_dp_script_fill():
    """含 dp fill 'css:#name' 'hello'。"""
    actions = [_fill()]
    result = recorder._export_dp_script(actions)
    assert "dp fill 'css:#name' 'hello'" in result


@pytest.mark.unit
def test_dp_script_scroll():
    """scroll 动作输出含 dp scroll --x 0 --y 300,且含 --mouse-x 10 --mouse-y 20。"""
    actions = [_scroll()]
    result = recorder._export_dp_script(actions)
    assert "dp scroll --x 0 --y 300" in result
    assert "--mouse-x 10 --mouse-y 20" in result


@pytest.mark.unit
def test_dp_script_unsupported():
    """给一个 {"type":"weird"},输出含 # unsupported action。"""
    actions = [{"type": "weird", "best_locator": "css:#x"}]
    result = recorder._export_dp_script(actions)
    assert "# unsupported action" in result


@pytest.mark.unit
def test_dp_script_shell_quote_escapes():
    """value 含单引号(如 it's),输出中被正确转义(包含 '"'"'")。"""
    actions = [{"type": "fill", "best_locator": "css:#input", "value": "it's"}]
    result = recorder._export_dp_script(actions)
    assert '"'"'" in result  # 转义后的单引号


@pytest.mark.unit
def test_dp_script_select_and_check():
    """select 和 check 动作的输出。"""
    actions = [
        {"type": "select", "best_locator": "css:#sel", "value": "option1"},
        {"type": "check", "best_locator": "css:#chk", "checked": True}
    ]
    result = recorder._export_dp_script(actions)
    assert "dp select" in result
    assert "dp click" in result
    assert "# check:" in result


@pytest.mark.unit
def test_dp_script_press():
    """press 动作的输出。"""
    actions = [{"type": "press", "key": "Tab"}]
    result = recorder._export_dp_script(actions)
    assert "dp press 'Tab'" in result


# ─────────────────────────────────────────────────────────────────────────────
# 2.3 _export_playwright_sync_script / async
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_playwright_sync_click():
    """含 from playwright.sync_api import sync_playwright、
    含 page.locator('#btn').click()、且因首个 action 有 url → 含 page.goto('https://example.com')。"""
    actions = [_click()]
    result = recorder._export_playwright_sync_script(actions)
    assert "from playwright.sync_api import sync_playwright" in result
    assert "page.locator('#btn').click()" in result
    assert "page.goto('https://example.com')" in result


@pytest.mark.unit
def test_playwright_sync_fill():
    """含 page.locator('#name').fill('hello')。"""
    actions = [_fill()]
    result = recorder._export_playwright_sync_script(actions)
    assert "page.locator('#name').fill('hello')" in result


@pytest.mark.unit
def test_playwright_press_with_selector():
    """press 带 selector → 含 .press('Enter')。"""
    actions = [_press()]
    result = recorder._export_playwright_sync_script(actions)
    assert "page.locator('#name').press('Enter')" in result


@pytest.mark.unit
def test_playwright_press_without_selector():
    """press 无 locator → 含 page.keyboard.press('Enter')。"""
    actions = [{"type": "press", "key": "Enter", "best_locator": "",
            "element": {"locators": {}}}]
    result = recorder._export_playwright_sync_script(actions)
    assert "page.keyboard.press('Enter')" in result


@pytest.mark.unit
def test_playwright_async_click():
    """async 版含 async with async_playwright、await page.locator('#btn').click()。"""
    actions = [_click()]
    result = recorder._export_playwright_async_script(actions)
    assert "from playwright.async_api import async_playwright" in result
    assert "async with async_playwright() as p:" in result
    assert "await page.locator('#btn').click()" in result


@pytest.mark.unit
def test_playwright_sync_select_and_check():
    """select 和 check 动作的输出。"""
    actions = [
        {"type": "select", "best_locator": "css:#sel", "value": "option1"},
        {"type": "check", "best_locator": "css:#chk", "checked": True}
    ]
    result = recorder._export_playwright_sync_script(actions)
    assert "select_option" in result
    assert ".check()" in result


@pytest.mark.unit
def test_playwright_async_select_and_check():
    """async 版的 select 和 check 动作。"""
    actions = [
        {"type": "select", "best_locator": "css:#sel", "value": "option1"},
        {"type": "check", "best_locator": "css:#chk", "checked": False}
    ]
    result = recorder._export_playwright_async_script(actions)
    assert "await page.locator('#sel').select_option('option1')" in result
    assert "await page.locator('#chk').uncheck()" in result


@pytest.mark.unit
def test_playwright_sync_scroll():
    """scroll 动作的输出。"""
    actions = [_scroll()]
    result = recorder._export_playwright_sync_script(actions)
    assert "page.mouse.move(10, 20)" in result
    assert "page.mouse.wheel(0, 300)" in result


# ─────────────────────────────────────────────────────────────────────────────
# 2.4 _export_selenium_script
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_selenium_click():
    """含 from selenium import webdriver、含 find(driver, 'css:#btn').click()。"""
    actions = [_click()]
    result = recorder._export_selenium_script(actions)
    assert "from selenium import webdriver" in result
    assert "find(driver, 'css:#btn').click()" in result


@pytest.mark.unit
def test_selenium_fill():
    """含 el.clear() 与 el.send_keys('hello')。"""
    actions = [_fill()]
    result = recorder._export_selenium_script(actions)
    assert "el.clear()" in result
    assert "el.send_keys('hello')" in result


@pytest.mark.unit
def test_selenium_press_known_key():
    """press Enter → 含 Keys.ENTER(验证 _selenium_key 映射)。"""
    actions = [_press()]
    result = recorder._export_selenium_script(actions)
    assert "Keys.ENTER" in result


@pytest.mark.unit
def test_selenium_scroll():
    """含 driver.execute_script("window.scrollBy...") 与 move_by_offset(10, 20)。"""
    actions = [_scroll()]
    result = recorder._export_selenium_script(actions)
    assert 'driver.execute_script("window.scrollBy(arguments[0], arguments[1]);", 0, 300)' in result
    assert "actions.move_by_offset(10, 20).perform()" in result


@pytest.mark.unit
def test_selenium_select_and_check():
    """select 和 check 动作的输出。"""
    actions = [
        {"type": "select", "best_locator": "css:#sel", "value": "option1"},
        {"type": "check", "best_locator": "css:#chk", "checked": True}
    ]
    result = recorder._export_selenium_script(actions)
    assert "Select(find(driver, 'css:#sel')).select_by_value('option1')" in result
    assert "if not el.is_selected():" in result


@pytest.mark.unit
def test_selenium_press_unknown_key():
    """未知按键使用 repr。"""
    actions = [{"type": "press", "key": "F12"}]
    result = recorder._export_selenium_script(actions)
    assert "'F12'" in result


# ─────────────────────────────────────────────────────────────────────────────
# 2.5 format_actions_text
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_format_text_raw():
    """raw=True → 返回合法 JSON(json.loads 成功)。"""
    actions = [_click()]
    result = recorder.format_actions_text(actions, raw=True)
    parsed = json.loads(result)
    assert isinstance(parsed, list)


@pytest.mark.unit
def test_format_text_empty():
    """空列表 → 含 暂无录制操作。"""
    result = recorder.format_actions_text([])
    assert "暂无录制操作" in result


@pytest.mark.unit
def test_format_text_click_and_fill():
    """混合动作 → 含 1. 序号、含 fill、click 字样、含 locator: 行。"""
    actions = [_click(), _fill()]
    result = recorder.format_actions_text(actions)
    assert "1. click" in result
    assert "2. fill" in result
    assert "locator:" in result


@pytest.mark.unit
def test_format_text_scroll_direction():
    """scroll(direction=down) → 含 向下(验证 _direction_zh)。"""
    actions = [{"type": "scroll", "direction": "down", "delta": {"x": 0, "y": 300},
            "best_locator": "", "element": {}, "page": {}}]
    result = recorder.format_actions_text(actions)
    assert "向下" in result


@pytest.mark.unit
def test_format_text_select_and_check():
    """select 和 check 动作的处理。"""
    actions = [
        {"type": "select", "best_locator": "css:#sel", "value": "option1",
         "element": {"attrs": {}, "locators": {}}, "page": {}},
        {"type": "check", "best_locator": "css:#chk", "checked": True,
         "element": {"attrs": {}, "locators": {}}, "page": {}}
    ]
    result = recorder.format_actions_text(actions)
    assert "select" in result
    assert "check" in result


# ─────────────────────────────────────────────────────────────────────────────
# 2.6 纯 helper 直测
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_action_locator_prefers_best():
    """_action_locator 优先返回 best_locator,缺省回退 element.best_locator。"""
    action = {"best_locator": "css:#btn", "element": {"best_locator": "xpath://button"}}
    assert recorder._action_locator(action) == "css:#btn"
    
    action2 = {"element": {"best_locator": "xpath://button"}}
    assert recorder._action_locator(action2) == "xpath://button"


@pytest.mark.unit
def test_playwright_selector_css_strip():
    """_playwright_selector 对 css:#x 去前缀返回 #x;对 text:登录 返回 text=登录。"""
    assert recorder._playwright_selector({"best_locator": "css:#x"}) == "#x"
    assert recorder._playwright_selector({"best_locator": "text:登录"}) == "text=登录"


@pytest.mark.unit
def test_first_url():
    """_first_url 返回首个含 url 的 action 的 url;全空 → ''。"""
    actions = [_click(), _fill()]
    result = recorder._first_url(actions)
    assert result == "https://example.com"
    
    assert recorder._first_url([]) == ""


@pytest.mark.unit
def test_shell_quote():
    """基本转义行为。"""
    assert recorder._shell_quote("hello") == "'hello'"
    assert recorder._shell_quote("it's") == "'it'\"'\"'s'"


@pytest.mark.unit
def test_py_quote():
    """基本转义行为。"""
    assert recorder._py_quote("hello") == "'hello'"
    assert recorder._py_quote("it's") == "\"it's\""


@pytest.mark.unit
def test_selenium_key_mapping():
    """Enter/Escape/Tab → 对应 Keys.*;未知键 → repr。"""
    assert recorder._selenium_key("Enter") == "Keys.ENTER"
    assert recorder._selenium_key("Escape") == "Keys.ESCAPE"
    assert recorder._selenium_key("Tab") == "Keys.TAB"
    assert recorder._selenium_key("Unknown") == "'Unknown'"


@pytest.mark.unit
def test_element_label_priority():
    """_element_label 按 aria-label > placeholder > name > id > text > tag 优先级。"""
    element = {"attrs": {"aria-label": "Label1", "name": "name1", "id": "id1"},
                "text": "text1", "tag": "button"}
    assert recorder._element_label(element) == "「Label1」"
    
    element2 = {"attrs": {"placeholder": "Placeholder1", "name": "name1", "id": "id1"},
                  "text": "text1", "tag": "button"}
    assert recorder._element_label(element2) == "「Placeholder1」"
    
    element3 = {"attrs": {"name": "name1", "id": "id1"}, "text": "text1", "tag": "button"}
    assert recorder._element_label(element3) == "「name1」"
    
    element4 = {"attrs": {"id": "id1"}, "text": "text1", "tag": "button"}
    assert recorder._element_label(element4) == "「id1」"
    
    element5 = {"text": "text1", "tag": "button"}
    assert recorder._element_label(element5) == "「text1」"
    
    element6 = {"tag": "button"}
    assert recorder._element_label(element6) == "button"


@pytest.mark.unit
def test_direction_zh_unknown():
    """未知方向返回原值或"未知"。"""
    assert recorder._direction_zh("down") == "下"
    assert recorder._direction_zh("up") == "上"
    assert recorder._direction_zh("unknown") == "unknown"
    assert recorder._direction_zh("") == "未知"


@pytest.mark.unit
def test_playwright_selector_text_with_css_fallback():
    """text selector 的转换，以及 css 回退。"""
    action = {"best_locator": "", "element": {"locators": {"css": "css:#x"}}}
    assert recorder._playwright_selector(action) == "#x"
    
    action2 = {"best_locator": "", "element": {"locators": {}}}
    assert recorder._playwright_selector(action2) == ""


@pytest.mark.unit
def test_element_label_with_class():
    """element 有 class 时返回 tag.class 格式。"""
    element = {"attrs": {"class": "btn-primary"}, "tag": "button"}
    assert recorder._element_label(element) == "button.btn-primary"


# ─────────────────────────────────────────────────────────────────────────────
# 2.7 浏览器函数
# ─────────────────────────────────────────────────────────────────────────────

class FakePage:
    """假 page 对象，用于 mock 浏览器函数。"""
    def __init__(self, js_return=None, cdp_raises=False):
        self._js_return = js_return
        self._cdp_raises = cdp_raises
        self.run_js_calls = []
    
    def run_cdp(self, *a, **k):
        if self._cdp_raises:
            raise RuntimeError("cdp not supported")
    
    def run_js(self, script, *a, **k):
        self.run_js_calls.append(script)
        return self._js_return


@pytest.mark.unit
def test_inject_recorder_returns_status():
    """run_js 返回 {"recording": True} → inject_recorder 返回该 dict;
    即使 run_cdp 抛异常也不崩(cdp_raises=True)。"""
    page = FakePage(js_return={"recording": True})
    result = recorder.inject_recorder(page)
    assert result == {"recording": True}
    assert len(page.run_js_calls) == 1
    
    # 测试 cdp_raises=True 时不崩
    page2 = FakePage(js_return={"recording": True}, cdp_raises=True)
    result2 = recorder.inject_recorder(page2)
    assert result2 == {"recording": True}


@pytest.mark.unit
def test_stop_recorder_returns_list():
    """run_js 返回 [{"type":"click"}] → 返回该 list; run_js 返回 None → []。"""
    page = FakePage(js_return=[{"type": "click"}])
    result = recorder.stop_recorder(page)
    assert result == [{"type": "click"}]
    
    page2 = FakePage(js_return=None)
    result2 = recorder.stop_recorder(page2)
    assert result2 == []


@pytest.mark.unit
def test_get_recorded_actions_none_returns_empty():
    """run_js 返回 None → []。"""
    page = FakePage(js_return=None)
    result = recorder.get_recorded_actions(page)
    assert result == []


@pytest.mark.unit
def test_clear_recorded_actions_calls_run_js():
    """调用后 page.run_js_calls 非空。"""
    page = FakePage(js_return=None)
    recorder.clear_recorded_actions(page)
    assert len(page.run_js_calls) == 1


@pytest.mark.unit
def test_get_recorder_status_none_returns_default():
    """run_js 返回 None → 返回 {'recording': False, 'count': 0, 'version': None}。"""
    page = FakePage(js_return=None)
    result = recorder.get_recorder_status(page)
    assert result == {'recording': False, 'count': 0, 'version': None}