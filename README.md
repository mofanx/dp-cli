# dp-cli

**English** | [中文](README_zh.md)

A powerful CLI for [DrissionPage](https://github.com/g1879/DrissionPage) — browser automation, structured data extraction, network listening and more.

## Features

- **Anti-detection by default** — not based on webdriver, `navigator.webdriver` is `false`
- **Reuse your own browser** — `--auto-connect` (Chrome/Edge 144+, no CLI flag needed) or `--port`
- **Hybrid snapshot** — a11y tree + Vimium-style clickable detection, catches icon-only buttons
  and custom menu items the a11y tree misses; every element gets an `[N]` ref with
  confidence markers (`⚡` medium, `?` low)
- **`dpx scan`** — fast Vimium-style listing of interactive elements (viewport-only mode available)
- **Powerful locator syntax** — descriptive strings stable across navigation, plus
  Playwright-style `pw:role=button[name="Submit"] >> nth=2` chains
- **Structured data extraction** — `extract` + `query` + `snapshot` for scraping list pages
- **Network listening** — capture XHR/Fetch requests and response bodies
- **Stealth patches** — `dpx stealth` bypasses common automation detections
- **Dual mode** — browser control + pure HTTP requests
- **Shadow-root / iframe** — traverse directly without switching context
- **Actionable auto-wait** — click/fill wait for visible+stable+enabled+unoccluded, reports the blocking element on failure; `dialog-policy` for unattended dialog handling; `batch --screenshot-on-fail` for evidence
- **Fine-grained input** — `keydown/keyup` + real `mousemove/mousedown/mouseup/mousewheel` CDP events; `click --button/--modifiers/--click-count/--force`
- **JSON output** — all commands output JSON, AI-friendly

## Installation

```bash
pip install dp-cli
dpx --help
```

> **入口冲突提示**：DrissionPage 4.1.1.4+ 也注册了 `dp` 命令（浏览器路径配置工具）。若 `dp` 被抢占（装包/升级顺序所致），用主入口 **`dpx`**（独占命名）或 `python -m dp_cli.main`；`pip install --force-reinstall dp-cli` 可夺回 `dp`。归属检测：`dpx install --skills --check` 输出的 `entrypoint` 字段。

### Agent skill（可选，推荐）

为 AI agent 安装完整使用指南（全部命令参考 + 工作流 playbook）：

```bash
dpx install --skills                        # 装到 agent 的 skill 目录
npx skills add mofanx/dp-cli               # 或走 skills CLI 渠道
claude plugin marketplace add mofanx/dp-cli  # 或 Claude Code 插件市场
```

未安装也能用——skill 只是文档层；agent 可直接读 `dpx --help` 或 `python -m dp_cli.catalog` 自举。

> **升级后请刷新 skill**：`pip install -U dp-cli` 后已装 skill 版本戳落后于 CLI——
> 每条命令 stderr 会提示 `运行 'dpx install --skills' 更新`（不污染 stdout JSON）。
> 执行 `dpx install --skills` 或 `dpx install --skills --check` 校验即可对齐。
> 版本戳相同但内容被篡改/过期时，`--check` 同样检出差异文件清单。

## Quick Start

```bash
# Auto-managed browser
dpx open https://example.com
dpx snapshot
dpx click "text:Login"
dpx fill "@name=username" admin
dpx press Enter
dpx close

# Connect to your own logged-in browser
google-chrome --remote-debugging-port=9222
dpx open https://example.com --port 9222
dpx snapshot
```

## Connect to a Normally-Launched Chrome/Edge (Chrome/Edge 144+)

No `--remote-debugging-port` required. Chrome/Edge 144+ exposes opt-in remote debugging
via `chrome://inspect` (Chrome) or `edge://inspect/#devices` (Edge):

1. Open your Chrome or Edge as usual (no special flags)
2. Visit `chrome://inspect/#remote-debugging` (Chrome) or `edge://inspect/#devices` (Edge)
3. Check **"Allow remote debugging for this browser instance"**
4. Run `dpx open --auto-connect`

```bash
dpx open --auto-connect                              # auto channel: sniffs Chrome stable → Edge stable
dpx open --auto-connect --channel edge              # force Edge stable
dpx open --auto-connect --channel beta               # Chrome beta
dpx open --auto-connect --probe-dir ~/my-profile     # custom user-data-dir
```

### How it works

Chrome/Edge 144+ in this mode exposes **only** a browser-level WebSocket and omits the HTTP
REST API (`/json`, `/json/version`, ...) that DrissionPage / puppeteer / Playwright
depend on. `dp-cli` transparently handles this:

1. Reads `DevToolsActivePort` from the user-data-dir → real CDP port
2. Probes the port — if `/json/version` is missing, identifies this as inspect mode
3. Spawns a local bridge (`python -m dp_cli.bridge`) that:
   - Synthesizes the missing HTTP endpoints from CDP calls
   - Multiplexes page-level CDP traffic over a single browser-level WebSocket
     via `Target.attachToTarget(flatten=True)`
4. Points DrissionPage at the bridge. Subsequent `dpx` commands reuse the same bridge.

The bridge subprocess is registered in a shared registry (`~/.dp_cli/bridges/`) so all
sessions on the same profile reuse one authorized connection — `dpx close` **keeps** it
by default (only detaches your session). Use `dpx close --stop-bridge` or
`DPX_CLOSE_STOPS_BRIDGE=1` for the old stop-on-close behavior, and `dpx bridge list` /
`dpx bridge stop` to inspect or terminate registered bridges directly.
`dpx close` never quits your browser (it's your browser, not dp's).

### Caveats

- Chrome/Edge always shows an **"Allow remote debugging"** dialog per new WebSocket client.
  Since bridge maintains one WebSocket and dpx commands share it, you confirm at most
  once per `dpx open --auto-connect`.
- Works with whatever profile Chrome/Edge is actually using — same cookies, logins, history.
- Classic `--remote-debugging-port=9222` mode still works unchanged via `dpx open --port 9222`.

## Hybrid Snapshot (a11y + Vimium-style)

The default `dpx snapshot` fuses two element-discovery paths:

1. **Browser a11y tree** via CDP — the structural skeleton (headings, lists, form roles,
   explicit `<a>`/`<button>`, any `role="..."` element).
2. **Vimium-style clickable detection** — a JS probe that flags icon-only buttons,
   `<div onclick>`, `[tabindex>=0]`, `aria-selected`, `cursor:pointer` elements, etc.
   that the a11y tree misses.

Results are deduplicated by `backendNodeId` and rendered with confidence markers:

| Marker | Confidence | Triggers |
|--------|-----------|----------|
| none   | **high**   | `<a href>`, `<button>`, `<input>`, `role=button/link/...`, `contenteditable` |
| `⚡`   | **medium** | `onclick` / `jsaction` / `tabindex>=0` / `aria-selected` / `<audio>/<video>`, or `cursor:pointer` + heuristic (aria-label / icon child / small square / class keyword) — **catches most React/Vue icon buttons** |
| `?`    | **low**    | bare `cursor:pointer` / class keyword only (no other signals); hidden unless `--include-low` |

Output includes helpful context:
- `@top-left`, `@top-right`, `@center`, `@bottom` … — position in the 9-region viewport grid
- `(icon)` — icon-only button (no visible label, has `<svg>` / `<img>` child)
- Shadow DOM is traversed automatically (open shadow roots)

Every element gets an `[N]` ref usable in any command: `dpx click "ref:5"`.

```bash
dpx snapshot                     # a11y + clickable (default); high + medium markers
dpx snapshot -i                  # interactive mode: only interactive elements
dpx snapshot -s ".main"          # limit to specific area
dpx snapshot -p "data-test"      # custom locator priority
dpx scan --viewport              # only elements currently in viewport
dpx scan --confidence all        # include low-confidence heuristics
dpx scan --confidence high       # only the sure-thing clickables
```

### `dpx scan` — fast clickable-only listing

When you only need "what can I click next?" without the full a11y tree:

```bash
dpx scan                         # full page, high+medium
dpx scan --viewport              # only elements currently in viewport
dpx scan --confidence all        # include low-confidence heuristics
dpx scan --confidence high       # only the sure-thing clickables
```

Both `snapshot` and `scan` share the same `[N]` ref numbering per session, so
`dpx click "ref:N"` works regardless of which one produced the snapshot.

## Playwright-style locators (`pw:` prefix)

Need semantic, role-based targeting on a fresh page (no snapshot required)?
Use the `pw:` prefix. Syntax mirrors Playwright, and chains with `>>`:

```bash
# By ARIA role (with accessible name — exact / substring / regex)
dpx click 'pw:role=button[name="Submit"]'
dpx click 'pw:role=button[name=/^Sign/i]'
dpx click 'pw:role=link[name=More]'         # substring

# By visible text (exact / substring / regex)
dpx click 'pw:text="Login"'                 # exact
dpx click 'pw:text=Login'                   # substring (case-insensitive)
dpx click 'pw:text=/^log/i'                 # regex

# By form affordances
dpx fill  'pw:placeholder=Search…' "chatgpt"
dpx fill  'pw:label="Email"' "a@b.com"
dpx click 'pw:alt="Logo"'
dpx click 'pw:title="Close"'
dpx click 'pw:testid=submit-btn'            # data-testid / data-test-id / data-test

# Chain with >> (each step narrows the scope)
dpx click 'pw:css=.sidebar >> role=listitem[name="Chat"] >> nth=2'
dpx click 'pw:css=li >> has-text="Python"'
dpx click 'pw:role=list >> nth=-1'          # negative index = from end

# Raw css/xpath chunks mix freely
dpx click 'pw:xpath=//nav >> role=link[name=Docs]'
```

**Matchers**: `role` / `text` / `label` / `placeholder` / `alt` / `title` /
`testid` / `css` / `xpath` / `nth` / `has-text` / `visible`

**Value forms**: `bare` = substring, `"quoted"` = exact, `/pattern/flags` = regex

**Visibility**: `role` / `text` / `has-text` automatically skip elements hidden
via `display:none`, `visibility:hidden`, `hidden` attribute, or
`aria-hidden="true"` (matches Playwright semantics).

**Shadow DOM**: open shadow roots are traversed automatically.

Under the hood the matcher chain is evaluated in-page as JS, the target element
is tagged with a one-shot `data-dp-ref` attribute, and DrissionPage resolves it
by that attribute — bypassing stale classes / CSS Modules / dynamic XPath.

## Anti-Detection (stealth)

Bypass `navigator.webdriver`, `HeadlessChrome` UA, empty `plugins`, SwiftShader WebGL,
`chrome.runtime` missing, and other common automation fingerprints.

```bash
# One-shot: connect + apply full stealth patches
dpx open --port 9322 --stealth
dpx goto https://bot.sannysoft.com/

# Or apply manually on an existing session (full preset by default)
dpx stealth
dpx stealth --preset mild                       # webdriver + UA only
dpx stealth --ua "Mozilla/5.0 ..."              # custom UA
dpx stealth --feature webdriver --feature webgl # fine-grained
```

### Recommended VPS Chrome flags (when connecting via SSH tunnel)

```bash
google-chrome --headless=new --remote-debugging-port=9222 \
  --no-sandbox --disable-dev-shm-usage \
  --disable-blink-features=AutomationControlled \
  --user-data-dir=~/.config/google-chrome
# Then on local:
ssh -NL 9322:127.0.0.1:9222 vps
dpx open --port 9322 --stealth
```

Patched features (full preset): `webdriver`, `UA`, `chrome.runtime`, `permissions`,
`plugins`, `languages`, `WebGL VENDOR/RENDERER`, `window.outerWidth/Height`.

Patches are injected via `Page.addScriptToEvaluateOnNewDocument` — they persist across
navigations and frames. Advanced fingerprints (Canvas/Audio/font list) require a real
GPU or Xvfb environment.

## Data Extraction (3-step workflow)

```bash
# 1. Discover CSS class names via noise-filtered content tree
dpx snapshot -i -s ".main"

# 2. Verify field selectors
dpx query "css:.item-title" --fields "text,loc"

# 3. Batch extract to CSV
dpx extract "css:.item-card" \
  '{"title":"css:.item-title",
    "price":"css:.item-price",
    "tags":{"selector":"css:.tag","multi":true},
    "url":{"selector":"css:a","attr":"href"}}' \
  --limit 100 -o csv -f result.csv
```

## Project Structure

```
dp_cli/
├── main.py              # CLI entry point (~47 lines)
├── session.py           # Browser session management + auto-connect bridge glue
├── bridge.py            # chrome://inspect mode CDP bridge (python -m dp_cli.bridge)
├── bridge_manager.py    # Bridge subprocess lifecycle + inspect-mode detection
├── stealth.py           # Anti-detection JS patches (applied via CDP)
├── snapshot/            # a11y-tree snapshot & data extraction engine
├── output.py            # JSON output helpers
└── commands/
    ├── _utils.py        # Shared decorators & helpers
    ├── browser.py       # open / goto / reload / close / list / stealth
    ├── snapshot_cmd.py  # snapshot / extract / query / find / inspect
    ├── element.py       # click / fill / select / hover / drag / check / upload / count
    ├── keyboard.py      # press / type / scroll / scroll-to / autoscroll
    ├── page.py          # screenshot / pdf / eval / wait / console / dialog
    ├── assert_cmd.py    # assert-text / assert-url / assert-visible / assert-count
    ├── tab.py           # tab-list / tab-new / tab-select / tab-close
    ├── storage.py       # cookie-* / localstorage-* / sessionstorage-*
    ├── network.py       # listen / listen-stop / http-get / http-post
    └── misc.py          # resize / maximize / state-save / state-load / config-set
```

## Documentation

See [`skills/dp-cli/SKILL.md`](skills/dp-cli/SKILL.md) for full workflow guide and [`skills/dp-cli/references/commands.md`](skills/dp-cli/references/commands.md) for complete command reference. Exit-code contract: [`ERROR_CODES.md`](ERROR_CODES.md).

## License

BSD-3-Clause
