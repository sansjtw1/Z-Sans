# Z-Sans Plugin Development Guide

> A plugin authoring guide for security researchers / developers. After reading it you will be able to write plugins that hook into Shodan, vulnerability scanning, Webhook notifications, custom reporting, and more.

---

## Table of Contents

1. [Plugin Mechanism Overview](#1-plugin-mechanism-overview)
2. [Event Reference](#2-event-reference)
3. [Write a Plugin in 5 Minutes](#3-write-a-plugin-in-5-minutes)
4. [Plugin Manifest `__manifest__`](#4-plugin-manifest-__manifest__)
5. [Platform Capabilities: What You Can Access](#5-platform-capabilities-what-you-can-access)
6. [Common Plugin Patterns](#6-common-plugin-patterns)
7. [Concurrency & Reliability](#7-concurrency--reliability)
8. [Plugin Management](#8-plugin-management)
9. [Secrets & Security Advice](#9-secrets--security-advice)

---

## 1. Plugin Mechanism Overview

- **A plugin is a Python file or directory**: drop a `.py` file into the plugin directory (default `plugins/`), or a directory containing an entry file (`<name>.py` / `plugin.py` / `main.py` / `__init__.py`), and the engine loads it automatically at startup, dispatching scan-lifecycle events to it.
- **Discovery rule**: every top-level callable named with an `on_` prefix in the plugin module is automatically registered as a handler for the corresponding event. Other functions are not registered and can be used as plain helper utilities.
- **Non-intrusive**: plugin modules are loaded in their own namespace; a crashing plugin never breaks the scan.
- **CLI integration (optional)**: a plugin may define `register_cli(parser)` to add its own command-line arguments to `main.py`; the arguments only exist while the plugin is loaded (see 8.2).
- **Config switch**: `plugins.dir` in `breeding-config.yaml` sets the plugin directory; `plugins.disabled` disables individual plugins (by plugin name, file or directory).
- **Conflict handling**: same-name plugins keep only the first one (sorted order), losers are recorded in `engine.plugin_conflicts`; plugins can also declare mutual exclusions (see 4 / 8.4).

```text
plugins/                 # or any other dir configured by plugins.dir
├── shodan_scan.py       # single-file plugin
└── report/              # directory plugin
    ├── plugin.py        # entry (any of plugin.py / main.py / __init__.py / report.py)
    ├── config.yaml      # optional bundled defaults
    └── README.md        # optional docs shown by --plugin-info
```

---

## 2. Event Reference

All events are dispatched via `emit_hook(event, **kwargs)`. Handlers just name the event parameters they need (a `**kwargs` signature is also accepted).

| Event | When it fires | Payload |
|-------|---------------|---------|
| `on_scan_started` | Engine `start()`, scan begins | `engine` |
| `on_scan_completed` | Queue drained, right before export | `engine` |
| `on_scan_stopped` | `stop()` (normal finish / Ctrl+C / exception) | `engine`, `finalize` |
| `on_asset_scanned` | One asset finished breeding | `asset`, `new_assets` |
| `on_asset_discovered` | A new asset was discovered and registered | `asset`, `source` (parent asset) |
| `on_asset_excluded` | Asset skipped (depth/resource-limit/exclusion rules/no breeder) | `asset` |
| `on_asset_eliminated` | Asset judged "eliminated" by a breeder | `asset` |
| `on_asset_failed` | Asset processing raised an exception | `asset`, `error` |

Exceptions raised inside a handler are only logged; they never interrupt the scan.

---

## 3. Write a Plugin in 5 Minutes

```python
# plugins/hello.py — drop in and it works, no config needed
import logging

logger = logging.getLogger('zsans.plugin.hello')

__manifest__ = {
    "name": "hello",
    "version": "0.1.0",
    "description": "Example: print scan and asset events",
    "author": "you",
}

def on_scan_started(engine):
    logger.info("Plugin sees seed queue size = %s", engine.queue.size())

def on_asset_discovered(asset, source):
    print(f"[hello] discovered {asset.type}:{asset.value} <- {source.uid}")

def on_scan_completed(engine):
    logger.info("Hello plugin counted %s assets", len(engine.asset_graph.nodes))
```

Run it:

```bash
python main.py -d example.com
```

`--list-plugins` confirms the plugin entered the resident list; `--plugin-info hello` shows details.

---

## 4. Plugin Manifest `__manifest__`

Put a `__manifest__` dict at the top level of the module; `--list-plugins` / `--plugin-info` will display it:

```python
__manifest__ = {
    "name": "shodan_scan",     # unique id; keep it consistent with the file name
    "version": "0.1.0",
    "description": "Enrich IP asset fingerprints via Shodan",
    "author": "your name",
}
```

Legacy constants are also supported: `PLUGIN_NAME` / `PLUGIN_VERSION` / `PLUGIN_DESCRIPTION` / `PLUGIN_AUTHOR`.

`__plugin__` is accepted as an alias of `__manifest__`.

**Mutual exclusion**: declare in the manifest that two plugins must not run
together (patterns support `*` / `?` fnmatch wildcards):

```python
__manifest__ = {
    "name": "shodan_scan",
    "version": "0.1.0",
    "description": "Enrich IP asset fingerprints via Shodan",
    "author": "your name",
    "conflicts": ["other_scan", "scanner_*"],   # fnmatch(*) patterns
}
```

Equivalent keys: `conflicts` / `conflict_with` / `incompatible` in the
manifest, or a module-level `PLUGIN_CONFLICTS` list. When a conflict is
detected, only the winner is loaded; the loser is skipped and reported in
`engine.plugin_conflicts`.

---

## 5. Platform Capabilities: What You Can Access

| Object | Description |
|--------|-------------|
| `engine.config` | Merged full configuration |
| `engine.asset_graph` | The graph, `.nodes` / `.edges`, with built-in lock, thread-safe |
| `engine.asset_graph.add_asset(a)` | Register a new asset in the graph |
| `engine.asset_graph.add_edge(a, b, rel)` | Add a relation edge |
| `engine.queue` | Pending breeding queue, `.add(a)` / `.size()` / `.is_empty()` |
| `engine.metrics` | `assets_processed` / `new_assets_found` / `depth_reached` / `errors` |
| `engine.output_handler.run_dir` | **Timestamped subdirectory for the current run** (recommended write target; created at engine startup; main output and plugin artifacts both live here) |
| `engine.output_handler.output_dir` | Output root directory (parent of all timestamped subdirectories) |
| `engine.save_checkpoint()` | Manually save a checkpoint |
| `engine.register_hook / emit_hook` | Custom events (see 6.4) |
| `engine.plugins` | dict of loaded plugin info (name -> info), same as `--list-plugins` |
| `engine.plugin_conflicts` | list of conflict records (`reason` / `winner` / `loser`) between plugins |

**Asset object**:

| Field | Meaning |
|-------|---------|
| `asset.uid` | Unique id, e.g. `domain:example.com` / `ip:203.0.113.9` |
| `asset.type` | `domain` / `ip` / `url` / `port` / `js`, etc. |
| `asset.value` | The value, e.g. `example.com` |
| `asset.depth` | Breeding depth |
| `asset.state` | `new` (initial) / `scanning` / `scanned` / `eliminated` / `excluded` / `failed` |
| `asset.properties` | **dict, freely readable/writable by engine and plugins; ends up in the exported JSON** |
| `asset.to_dict()` | JSON-friendly asset representation |

**Two channels to include plugin data in exports**:

1. Write `asset.properties["mysql_tmp"] = {...}`; `to_dict()` carries it through to the JSON export as-is.
2. Use `engine.asset_graph.add_asset()` to create new nodes (e.g. "vuln" nodes), which are also exported; add them to `engine.queue` if you want them to breed.

Plugin-generated standalone files (such as `events.jsonl`) are not merged into JSON/CSV/GraphML. Write them to **`engine.output_handler.run_dir`** (the timestamped subdirectory for the current run) so they sit next to the main output:

```python
handler = getattr(engine, 'output_handler', None)
outdir = getattr(handler, 'run_dir', None) or getattr(handler, 'output_dir', None)
os.makedirs(outdir, exist_ok=True)
path = os.path.join(outdir, "my_plugin_report.txt")
```

> `run_dir` is pre-created and cached when the engine calls `start()`, so multiple writes within a single scan land in the **same** timestamped directory; plugins don't need to generate their own timestamp. If the engine hasn't started (e.g. debugging a plugin standalone), `run_dir` may be `None` — fall back to `output_dir`.

---

## 6. Common Plugin Patterns

### 6.1 External Intel Lookup (Shodan)

```python
# plugins/shodan_scan.py
import logging
import os
import requests

logger = logging.getLogger('zsans.plugin.shodan')

__manifest__ = {"name": "shodan_scan", "version": "0.1.0",
                "description": "Enrich IP assets via Shodan", "author": "you"}

_engine = None

def _api_key():
    # Key goes through env var or config; NEVER hard-code it into the plugin
    if _engine is None:
        return None
    return (os.environ.get("SHODAN_API_KEY")
            or _engine.config.get("plugins", {}).get("shodan_api_key"))

def on_scan_started(engine):
    global _engine; _engine = engine

def on_asset_scanned(asset, new_assets):
    # Judge each scanned asset once and enrich the fingerprint
    if asset.type != "ip" or "_shodan" in asset.properties:
        return
    key = _api_key(_engine)
    if not key:
        return
    try:
        r = requests.get(
            f"https://api.shodan.io/shodan/host/{asset.value}?key={key}",
            timeout=15)
        r.raise_for_status()
        data = r.json()
        asset.properties["_shodan"] = {
            "ports": data.get("ports", []),
            "products": sorted({p.get("product") for p in data.get("data", []) if p.get("product")}),
            "country": data.get("country_name"),
        }
    except Exception as e:
        logger.debug("shodan lookup %s failed: %s", asset.value, e)
```

Effect: every IP asset carries `properties._shodan` in the exported JSON, ready for downstream analysis.

### 6.2 Vulnerability Scan and Inject Results into the Graph / Export

```python
def _notify_scan(engine):
    # Lightweight check on discovered ports/domains, producing "vuln" nodes
    for uid, asset in list(engine.asset_graph.nodes.items()):
        if asset.type != "port" or "vuln" in asset.properties:
            continue
        # Placeholder example: in reality call Nessus / nuclei / NVD
        cves = ["CVE-2026-0000"]
        asset.properties["vulns"] = cves              # channel 1: into export
        if cves:
            vuln_asset = AssetFactory.create_asset(f"{asset.value}:vulnerability", ASSET_TYPE_URL)
            vuln_asset.depth = asset.depth + 1
            vuln_asset.properties["cves"] = cves
            engine.asset_graph.add_asset(vuln_asset)  # channel 2: new node, into export
            engine.asset_graph.add_edge(asset, vuln_asset, "affected")
```

### 6.3 Aggregated Report

Aggregate data in `on_asset_discovered`, then write Markdown / export to `run_dir` once in `on_scan_completed`. Use **module-level globals** to persist state between events:

```python
_seen = []

def on_asset_discovered(asset, source):
    _seen.append(asset.uid)

def on_scan_started(engine):
    _seen.clear()
```

> Always clear on each `on_scan_started` to prevent data leaking across rounds (W watch mode).

### 6.4 Custom Events

Plugins can be both "consumers" and creators of new events consumed by other plugins:

```python
def on_asset_scanned(asset, new_assets):
    engine = _engine                       # cached in 6.1
    for na in new_assets:
        if na.type == "port":
            engine.emit_hook("on_port_reported", asset=na)   # custom event
```

Any other plugin that registers `on_port_reported` will receive it; custom events never disturb the engine's built-in flow.

---

## 7. Concurrency & Reliability

- The engine processes assets in parallel worker threads; `_process_asset` runs in **multiple worker threads**, so `on_asset_*` handlers may be invoked concurrently.
  - Read-only access to `asset_graph` / `metrics`: built-in locks make it safe.
  - Mutating **module-level shared variables** (e.g. a `_seen` list): protect with your own `threading.Lock()`, or use already-locked structures like `engine.metrics`.
- Exceptions in handlers: only logged, never affect the scan.
- Keep plugin import time (top-level module code) to "constants + function definitions" only; put anything slow or side-effect-heavy inside handlers, otherwise it runs for every engine instance at startup.

---

## 8. Plugin Management

### 8.1 Commands

| Command | Purpose |
|---------|---------|
| `python main.py --list-plugins` | List plugins: version, handler count, kind, status, subscribed events |
| `python main.py --plugin-info <name>` | Show full info for one plugin (incl. directory files/doc and its `plugin_help()`) |
| `python main.py --web` | Web console -> **Plugins** page lists plugins and can enable/disable them (`/api/plugins`) |
| Config file | Control loading behavior (below) |

```yaml
plugins:
  dir: plugins              # plugin directory (default: plugins/)
  disabled:                 # disable list; values are plugin names (without .py)
    - my_plugin
```

`plugins.disabled` applies to single-file **and** directory plugins. Plugin
status: `loaded` / `disabled` / `failed` (the reason is shown when import
fails).

### 8.2 CLI integration

A plugin may define `register_cli(parser)`. While the plugin is loaded,
`main.py` calls it during argument parsing, so its flags appear in `--help`.
Disable the plugin (or point `plugins.dir` elsewhere) and its flags disappear.

```python
def register_cli(parser):
    parser.add_argument("--my-feature", action="store_true",
                        help="Provided by my_plugin (only while loaded)")
```

### 8.3 Directory plugins

A directory plugin's folder is prepended to `sys.path` when it is imported, so
it can `import` sibling helpers, configs and resources. `--plugin-info`
displays the folder's file list and a summary of its `README.md` / `说明.md`.
An entry file is required — resource-only folders are not treated as plugins.

### 8.4 Conflicts & mutual exclusion

- Same plugin name from two sources (e.g. `foo.py` + `foo/`): the first in
  sorted order wins; the other is skipped and logged.
- Two plugins declaring the same manifest `name`: the first wins.
- `conflicts` declarations (see section 4) between loaded plugins: the first
  wins.
- Every loser is recorded in `engine.plugin_conflicts` as
  `{name, reason, winner, loser}`.

---

## 9. Secrets & Security Advice

- API keys / tokens always go in environment variables or `breeding-config.yaml` (declared as config); **never** hard-code them into plugin code and commit.
- Do not commit plugin code or configs that contain secrets; sanitize `breeding-config.yaml` before committing.
- When calling external services (**e.g. Shodan**), set a `timeout` and wrap calls in `try/except` to log errors without slowing down the scan.
- Malicious plugin threat model: the engine executes every `.py` under `plugins/`. Don't drop in third-party `.py` files carelessly; keeping an audit list via `--list-plugins` is a good idea.

---

> Start by dropping a simple plugin into `plugins/` (e.g. a `hello.py` that just prints events) and modify from there.
