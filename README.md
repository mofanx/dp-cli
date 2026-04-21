# dp-cli

A powerful CLI for [DrissionPage](https://github.com/g1879/DrissionPage) — browser automation, structured data extraction, network listening and more.

## Features

- **Anti-detection by default** — not based on webdriver, `navigator.webdriver` is `false`
- **Reuse your own browser** — `--auto-connect` (Chrome 144+, no CLI flag needed) or `--port`
- **Hybrid snapshot** — a11y tree + Vimium-style clickable detection, catches icon-only buttons
  and custom menu items the a11y tree misses; every element gets an `[N]` ref with
  confidence markers (`⚡` medium, `?` low)
- **`dp scan`** — fast Vimium-style listing of interactive elements (viewport-only mode available)
- **Powerful locator syntax** — descriptive strings stable across navigation
- **Structured data extraction** — `extract` + `query` + `snapshot` for scraping list pages
- **Network listening** — capture XHR/Fetch requests and response bodies
- **Stealth patches** — `dp stealth` bypasses common automation detections
- **Dual mode** — browser control + pure HTTP requests
- **Shadow-root / iframe** — traverse directly without switching context
- **JSON output** — all commands output JSON, AI-friendly

## Installation

```bash
pip install dp-cli
dp --help
```

## Quick Start

```bash
# Auto-managed browser
dp open https://example.com
dp snapshot
dp click "text:Login"
dp fill "@name=username" admin
dp press Enter
dp close

# Connect to your own logged-in browser
google-chrome --remote-debugging-port=9222
dp open https://example.com --port 9222
dp snapshot
```

## Connect to a Normally-Launched Chrome (Chrome 144+)

No `--remote-debugging-port` required. Chrome 144+ exposes opt-in remote debugging
via `chrome://inspect`:

1. Open your Chrome as usual (no special flags)
2. Visit `chrome://inspect/#remote-debugging`
3. Check **"Allow remote debugging for this browser instance"**
4. Run `dp open --auto-connect`

```bash
dp open --auto-connect                              # stable channel, default profile
dp open --auto-connect --channel beta               # pick a different channel
dp open --auto-connect --probe-dir ~/my-profile     # custom user-data-dir
```

### How it works

Chrome 144+ in this mode exposes **only** a browser-level WebSocket and omits the HTTP
REST API (`/json`, `/json/version`, ...) that DrissionPage / puppeteer / Playwright
depend on. `dp-cli` transparently handles this:

1. Reads `DevToolsActivePort` from the user-data-dir → real CDP port
2. Probes the port — if `/json/version` is missing, identifies this as inspect mode
3. Spawns a local bridge (`python -m dp_cli.bridge`) that:
   - Synthesizes the missing HTTP endpoints from CDP calls
   - Multiplexes page-level CDP traffic over a single browser-level WebSocket
     via `Target.attachToTarget(flatten=True)`
4. Points DrissionPage at the bridge. Subsequent `dp` commands reuse the same bridge.

The bridge subprocess and its port are tracked in the session file; `dp close` stops
the bridge automatically and never quits your Chrome (it's your browser, not dp's).

### Caveats

- Chrome always shows an **"Allow remote debugging"** dialog per new WebSocket client.
  Since bridge maintains one WebSocket and dp commands share it, you confirm at most
  once per `dp open --auto-connect`.
- Works with whatever profile Chrome is actually using — same cookies, logins, history.
- Classic `--remote-debugging-port=9222` mode still works unchanged via `dp open --port 9222`.

## Hybrid Snapshot (a11y + Vimium-style)

The default `dp snapshot` fuses two element-discovery paths:

1. **Browser a11y tree** via CDP — the structural skeleton (headings, lists, form roles,
   explicit `<a>`/`<button>`, any `role="..."` element).
2. **Vimium-style clickable detection** — a JS probe that flags icon-only buttons,
   `<div onclick>`, `[tabindex>=0]`, `aria-selected`, `cursor:pointer` elements, etc.
   that the a11y tree misses.

Results are deduplicated by `backendNodeId` and rendered with confidence markers:

| Marker | Confidence | Triggers |
|--------|-----------|----------|
| none   | **high**   | `<a href>`, `<button>`, `<input>`, `role=button/link/...`, `contenteditable` |
| `⚡`   | **medium** | `onclick` / `jsaction` / `tabindex>=0` / `aria-selected` / `<audio>/<video>` |
| `?`    | **low**    | `cursor:pointer` / class keyword match (`btn` / `click` / `toggle` / …) |

Every element gets an `[N]` ref usable in any command: `dp click "ref:5"`.

```bash
dp snapshot                     # a11y + clickable (default); high + medium markers
dp snapshot --viewport-only     # clickable probe limited to viewport (faster)
dp snapshot --include-low       # also surface `?` low-confidence heuristics
dp snapshot --no-clickables     # a11y tree only (legacy behavior)
```

### `dp scan` — fast clickable-only listing

When you only need "what can I click next?" without the full a11y tree:

```bash
dp scan                         # full page, high+medium
dp scan --viewport              # only elements currently in viewport
dp scan --confidence all        # include low-confidence heuristics
dp scan --confidence high       # only the sure-thing clickables
```

Both `snapshot` and `scan` share the same `[N]` ref numbering per session, so
`dp click "ref:N"` works regardless of which one produced the snapshot.

## Anti-Detection (stealth)

Bypass `navigator.webdriver`, `HeadlessChrome` UA, empty `plugins`, SwiftShader WebGL,
`chrome.runtime` missing, and other common automation fingerprints.

```bash
# One-shot: connect + apply full stealth patches
dp open --port 9322 --stealth
dp goto https://bot.sannysoft.com/

# Or apply manually on an existing session (full preset by default)
dp stealth
dp stealth --preset mild                       # webdriver + UA only
dp stealth --ua "Mozilla/5.0 ..."              # custom UA
dp stealth --feature webdriver --feature webgl # fine-grained
```

### Recommended VPS Chrome flags (when connecting via SSH tunnel)

```bash
google-chrome --headless=new --remote-debugging-port=9222 \
  --no-sandbox --disable-dev-shm-usage \
  --disable-blink-features=AutomationControlled \
  --user-data-dir=~/.config/google-chrome
# Then on local:
ssh -NL 9322:127.0.0.1:9222 vps
dp open --port 9322 --stealth
```

Patched features (full preset): `webdriver`, `UA`, `chrome.runtime`, `permissions`,
`plugins`, `languages`, `WebGL VENDOR/RENDERER`, `window.outerWidth/Height`.

Patches are injected via `Page.addScriptToEvaluateOnNewDocument` — they persist across
navigations and frames. Advanced fingerprints (Canvas/Audio/font list) require a real
GPU or Xvfb environment.

## Data Extraction (3-step workflow)

```bash
# 1. Discover CSS class names via noise-filtered content tree
dp snapshot --mode content --max-text 40

# 2. Verify field selectors
dp query "css:.item-title" --fields "text,loc"

# 3. Batch extract to CSV
dp extract "css:.item-card" \
  '{"title":"css:.item-title",
    "price":"css:.item-price",
    "tags":{"selector":"css:.tag","multi":true},
    "url":{"selector":"css:a","attr":"href"}}' \
  --limit 100 --output csv --filename result.csv
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
    ├── page.py          # screenshot / pdf / eval / wait (idle/loaded/url/title) / dialog
    ├── tab.py           # tab-list / tab-new / tab-select / tab-close
    ├── storage.py       # cookie-* / localstorage-* / sessionstorage-*
    ├── network.py       # listen / listen-stop / http-get / http-post
    └── misc.py          # resize / maximize / state-save / state-load / config-set
```

## Documentation

See [`skills/SKILL.md`](skills/SKILL.md) for full workflow guide and [`skills/references/commands.md`](skills/references/commands.md) for complete command reference.

## License

BSD-3-Clause
