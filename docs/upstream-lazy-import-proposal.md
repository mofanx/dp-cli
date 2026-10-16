# Proposal: Lazy-import optional data-recording deps in DrissionPage

**Status:** Draft for upstream submission (DrissionPage issue / PR)
**Author:** dp-cli maintainers
**Tested version:** DrissionPage 4.1.1.2, DataRecorder 3.6.2, DownloadKit (bundled dep)

---

## Summary

`import DrissionPage` costs **~260–325 ms** on a typical machine. Roughly
**~60 % of that (~145 ms)** is spent importing `openpyxl` — pulled in
transitively by `DataRecorder` and `DownloadKit` — even though **none of
DrissionPage's core automation paths touch Excel recording**, and the only
symbols actually used from `DataRecorder.tools` are two pure
string/path helpers (`make_valid_name`, `get_usable_path`).

For CLI tools and automation agents that spawn a fresh Python process per
command (our use case: `dp-cli`, a shell-driven browser automation tool),
this is a per-invocation tax of ~130–150 ms with zero benefit. Making the
recording stack lazy would cut cold import to **~125 ms (−52 %)** with no
behavioral change.

## Measured data

Environment: CPython 3.13, `python -X importtime -c "import DrissionPage"`

| Package | Cumulative import cost | Needed at import time? |
|---|---|---|
| `openpyxl` (via `DataRecorder`/`DownloadKit`) | ~145 ms | No — only for xlsx export |
| `requests` | ~83 ms | Yes (CDP HTTP) |
| `numpy` | ~50 ms | No — used inside `screencast` only |
| DrissionPage own code + misc | ~70 ms | Yes |

Reproduce:

```bash
python -X importtime -c "import DrissionPage" 2>&1 | \
    grep -E "openpyxl|DataRecorder|DownloadKit|DrissionPage$"
# openpyxl ≈ 144 ms, DataRecorder ≈ 146 ms cumulative
```

## Root cause (import chain)

**Chain 1 — direct, for a filename helper:**

```
DrissionPage/_pages/chromium_base.py:15
  → from DataRecorder.tools import make_valid_name
      → DataRecorder/__init__.py            # imports .recorder eagerly
          → DataRecorder/recorder.py        # top-level: import openpyxl
      → DataRecorder/tools.py               # top-level: openpyxl imports too
```

`make_valid_name` itself is a ~5-line regex filename sanitizer with **no
dependency on openpyxl** — the cost is paid because `tools.py` imports
openpyxl at module top for *other* functions in the same file, and
`DataRecorder/__init__.py` eagerly imports `recorder.py`.

**Chain 2 — transitive, for a lazily-used class:**

```
DrissionPage/_base/base.py:14
  → from DownloadKit import DownloadKit     # TOP-LEVEL import…
      → DownloadKit/mission.py
          → from DataRecorder import ByteRecorder
              → DataRecorder/__init__.py → openpyxl
```

…but `DownloadKit` is only *instantiated* lazily:

```python
# DrissionPage/_base/base.py (~line 294-298)
@property
def download(self):
    if self._DownloadKit is None:
        self._DownloadKit = DownloadKit(driver=self, ...)
    return self._DownloadKit
```

So a top-level `from DownloadKit import DownloadKit` pays the full
openpyxl bill up front for a feature that is only used when a page's
`.download` property is actually accessed.

## Proposed changes (independent, ordered by ease)

### 1. Move the `DownloadKit` import inside the `download` property

```python
# DrissionPage/_base/base.py
- from DownloadKit import DownloadKit      # remove from module top

  @property
  def download(self):
      if self._DownloadKit is None:
+         from DownloadKit import DownloadKit   # lazy: only on first use
          self._DownloadKit = DownloadKit(driver=self, ...)
      return self._DownloadKit
```

Behavior-preserving: the symbol is never referenced at class level.
Kills chain 2 entirely for non-download usage.

### 2. Defer openpyxl inside `DataRecorder.tools` / `__init__`

The heavier fix lives upstream in `DataRecorder` itself (same author):

- `DataRecorder/tools.py`: move `openpyxl` imports into the functions
  that read/write xlsx (`align_csv`, `make_valid_name` etc. that need
  them) instead of module top.
- `DataRecorder/__init__.py`: either lazy-export `Recorder`/`ByteRecorder`/
  `DBRecorder`/`Filler` via module `__getattr__` (PEP 562), or defer the
  heavy `recorder.py` import.

If changing DataRecorder is out of scope, an alternative **inside
DrissionPage**: the only three symbols consumed are
`make_valid_name` and `get_usable_path` — tiny pure helpers that could be
vendored (~15 LoC) or imported through a guarded wrapper. We prefer the
upstream lazy-import since vendoring creates drift.

### 3. (Optional) Defer `numpy` in `screencast`

`_units/screencast.py` already imports `from numpy import fromfile, uint8`
inside a method (good), yet `numpy` still shows up in the import tree —
worth double-checking which other module pulls it and whether it can also
be deferred. (~50 ms on top.)

## Expected impact

| Metric | Before | After (shim-verified) |
|---|---|---|
| `import DrissionPage` | ~260 ms | **~125 ms (−52 %)** |
| `openpyxl` in `sys.modules` after import | loaded | not loaded until xlsx used |
| Behavior | — | identical (all symbols still resolve on first real use) |

We verified the boundary with a self-healing import shim in our own code:
pre-seeding `sys.modules` for `DataRecorder`/`DownloadKit` and
`DataRecorder.tools` cuts import to 127 ms, and the first real call to
`make_valid_name()` transparently loads the real package — confirming no
DrissionPage code path needs openpyxl at import time.

## Backward compatibility

- All changed symbols remain importable from the same locations — only
  *when* the module executes changes.
- Users importing `DataRecorder`/`DownloadKit` directly are unaffected.
- No API surface changes.

## Offer

Happy to submit this as a PR if the direction sounds reasonable — the
`download` property change (item 1) is ~3 lines; the `DataRecorder.tools`
lazy-import is straightforward function-scoping.
