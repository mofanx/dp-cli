# dp-cli

A powerful CLI for [DrissionPage](https://github.com/g1879/DrissionPage) — browser automation, structured data extraction, network listening and more.

## Features

- **Anti-detection by default** — not based on webdriver, `navigator.webdriver` is `false`
- **Reuse your own browser** — connect to a running Chrome via `--port`, keeping login state and cookies
- **Powerful locator syntax** — descriptive strings stable across navigation (no ephemeral refs)
- **Structured data extraction** — `extract` + `query` + `snapshot --mode content` for scraping list pages
- **Network listening** — capture XHR/Fetch requests and response bodies
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

## Documentation

See [`skills/SKILL.md`](skills/SKILL.md) for full workflow guide and [`skills/references/commands.md`](skills/references/commands.md) for complete command reference.

## License

BSD-3-Clause
