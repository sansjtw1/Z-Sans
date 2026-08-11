# Z-Sans 插件开发文档

> 面向安全研究员 / 开发者的插件编写指南。读完本文,你将能写出接入 Shodan、漏洞扫描、Webhook 通知、自定义报告等任意功能的插件。

---

## 目录

1. [插件机制总览](#一-插件机制总览)
2. [事件参考表](#二-事件参考表)
3. [5 分钟上手写一个插件](#三-5-分钟上手写一个插件)
4. [插件清单 `__manifest__`](#四-插件清单-__manifest__)
5. [平台能力:能拿到什么](#五-平台能力能拿到什么)
6. [常见插件模式](#六-常见插件模式)
7. [多线程与可靠性](#七-多线程与可靠性)
8. [插件管理](#八-插件管理)
9. [密钥与安全建议](#九-密钥与安全建议)

---

## 一、插件机制总览

- **插件就是 Python 文件或目录**:把任意 `.py` 放进插件目录(默认 `plugins/`),或放入含入口文件的目录(`<目录名>.py` / `plugin.py` / `main.py` / `__init__.py`),引擎启动时自动加载,并在扫描生命周期中派发事件给它。
- **识别规则**:插件模块里所有顶层、以 `on_` 开头且可调用的函数,会被自动注册为对应事件的处理器;其余函数不会被注册,可以当作普通工具函数。
- **无侵入**:插件模块加载在独立命名空间,一个插件写崩溃不会拖垮扫描。
- **CLI 集成(可选)**:插件可定义 `register_cli(parser)`,向 `main.py` 追加自己的命令行参数;**仅当插件被加载时**该参数才存在(见 8.2)。
- **配置开关**:`breeding-config.yaml` 的 `plugins.dir` 指定插件目录、`plugins.disabled` 禁用个别插件(按插件名,文件与目录通用)。
- **冲突处理**:同名插件(如 `foo.py` + `foo/`)只保留排序首位,失败者记入 `engine.plugin_conflicts`;插件也可声明互斥(见 四 / 8.4)。

```text
plugins/                 # 或 plugins.dir 指定的任意目录
├── shodan_scan.py       # 单文件插件
└── report/              # 目录插件
    ├── plugin.py        # 入口(plugin.py / main.py / __init__.py / report.py 任一)
    ├── config.yaml      # 可选:插件自带默认配置
    └── README.md        # 可选:--plugin-info 展示的说明文档
```

---

## 二、事件参考表

所有事件通过 `emit_hook(event, **kwargs)` 派发。处理器签名写上该事件的参数名即可(兼容 `**kwargs`)。

| 事件 | 触发时机 | 载荷 |
|------|----------|------|
| `on_scan_started` | 引擎 `start()`,扫描开始 | `engine` |
| `on_scan_completed` | 队列处理完毕,即将导出 | `engine` |
| `on_scan_stopped` | `stop()`(正常结束 / Ctrl+C / 异常) | `engine`, `finalize` |
| `on_asset_scanned` | 一个资产繁殖完成 | `asset`, `new_assets` |
| `on_asset_discovered` | 发现并入库一个新资产 | `asset`, `source`(父资产) |
| `on_asset_excluded` | 资产被跳过(超深度/资源上限/排除规则/无繁殖器) | `asset` |
| `on_asset_eliminated` | 资产被繁殖器判定“已淘汰” | `asset` |
| `on_asset_failed` | 资产处理抛异常 | `asset`, `error` |

处理器内部异常只记日志,不会中断扫描。

---

## 三、5 分钟上手写一个插件

```python
# plugins/hello.py —— 放进去即生效,无需任何配置
import logging

logger = logging.getLogger('zsans.plugin.hello')

__manifest__ = {
    "name": "hello",
    "version": "0.1.0",
    "description": "示例:打印扫描与资产事件",
    "author": "你",
}

def on_scan_started(engine):
    logger.info("插件看到种子队列 size = %s", engine.queue.size())

def on_asset_discovered(asset, source):
    print(f"[hello] 发现 {asset.type}:{asset.value} ← {source.uid}")

def on_scan_completed(engine):
    logger.info("Hello 插件统计到 %s 个资产", len(engine.asset_graph.nodes))
```

启动:

```bash
python main.py -d example.com
```

`--list-plugins` 可确认插件进入驻留清单,`--plugin-info hello` 查看详情。

---

## 四、插件清单 `__manifest__`

在模块顶层放一个 `__manifest__` 字典,`--list-plugins` / `--plugin-info` 就会展示它:

```python
__manifest__ = {
    "name": "shodan_scan",     # 唯一标识,建议与文件名一致
    "version": "0.1.0",
    "description": "通过 Shodan 补充 IP 资产指纹",
    "author": "你的名字",
}
```

也可用旧常量:`PLUGIN_NAME` / `PLUGIN_VERSION` / `PLUGIN_DESCRIPTION` / `PLUGIN_AUTHOR`。

`__plugin__` 已作为 `__manifest__` 的别名被支持。

**互斥声明**:在清单中声明两个插件不能同时加载(模式支持 `*` / `?` 通配):

```python
__manifest__ = {
    "name": "shodan_scan",
    "version": "0.1.0",
    "description": "通过 Shodan 补充 IP 资产指纹",
    "author": "你的名字",
    "conflicts": ["other_scan", "scanner_*"],   # fnmatch(*) 通配
}
```

等同键:`conflicts` / `conflict_with` / `incompatible`(清单内),或模块级
`PLUGIN_CONFLICTS` 列表。检测到冲突时只加载胜者,败者被跳过并记入
`engine.plugin_conflicts`。

---

## 五、平台能力:能拿到什么

| 对象 | 说明 |
|------|------|
| `engine.config` | 合并后完整配置 |
| `engine.asset_graph` | 图谱,`.nodes` / `.edges`,自带锁,线程安全 |
| `engine.asset_graph.add_asset(a)` | 向图谱注册新资产 |
| `engine.asset_graph.add_edge(a, b, rel)` | 添加关系边 |
| `engine.queue` | 待繁殖队列,`.add(a)` / `.size()` / `.is_empty()` |
| `engine.metrics` | `assets_processed` / `new_assets_found` / `depth_reached` / `errors` |
| `engine.output_handler.run_dir` | **本次运行的时间戳子目录**(推荐写入位置,引擎启动时已创建,主输出与插件产物都放这里) |
| `engine.output_handler.output_dir` | 输出根目录(所有时间戳子目录的父目录) |
| `engine.save_checkpoint()` | 手动存检查点 |
| `engine.register_hook / emit_hook` | 自定义事件(见 6.4) |
| `engine.plugins` | 已加载插件信息 dict(名称 -> 信息),与 `--list-plugins` 一致 |
| `engine.plugin_conflicts` | 插件冲突记录列表(`reason` / `winner` / `loser`) |

**Asset 对象**:

| 字段 | 含义 |
|------|------|
| `asset.uid` | 唯一标识,如 `domain:example.com` / `ip:203.0.113.9` |
| `asset.type` | `domain` / `ip` / `url` / `port` / `js` 等 |
| `asset.value` | 值,如 `example.com` |
| `asset.depth` | 繁殖深度 |
| `asset.state` | `new`(初始) / `scanning` / `scanned` / `eliminated` / `excluded` / `failed` |
| `asset.properties` | **dict,引擎与插件都可自由读写,最终进导出 JSON** |
| `asset.to_dict()` | JSON 友好的资产表示 |

**导出包含插件数据的两条通道**:

1. 写 `asset.properties["mysql_tmp"] = {...}`;导出 JSON 时 `to_dict()` 会原样带上。
2. 用 `engine.asset_graph.add_asset()` 生成新节点(如“漏洞节点”),同样进入导出;若想让它参与繁殖,再 `engine.queue.add()`。

插件生成的独立文件(如 `events.jsonl`)不会合并进 JSON/CSV/GraphML,请把它们写到 **`engine.output_handler.run_dir`**(本次运行的时间戳子目录)里,与主输出文件放一起:

```python
handler = getattr(engine, 'output_handler', None)
outdir = getattr(handler, 'run_dir', None) or getattr(handler, 'output_dir', None)
os.makedirs(outdir, exist_ok=True)
path = os.path.join(outdir, "my_plugin_report.txt")
```

> `run_dir` 在引擎 `start()` 时已预创建并缓存,单次扫描内多次写入会进**同一个**时间戳目录;插件不需要自行生成时间戳。若引擎未启动(如单独调试插件),`run_dir` 可能为 `None`,此时回退到 `output_dir`。

---

## 六、常见插件模式

### 6.1 外部情报探测(Shodan)

```python
# plugins/shodan_scan.py
import logging
import os
import requests

logger = logging.getLogger('zsans.plugin.shodan')

__manifest__ = {"name": "shodan_scan", "version": "0.1.0",
                "description": "用 Shodan 补充 IP 资产指纹", "author": "你"}

_engine = None

def _api_key():
    # 密钥走环境变量或配置,严禁硬编码进插件
    if _engine is None:
        return None
    return (os.environ.get("SHODAN_API_KEY")
            or _engine.config.get("plugins", {}).get("shodan_api_key"))

def on_scan_started(engine):
    global _engine; _engine = engine

def on_asset_scanned(asset, new_assets):
    # 每个被扫描过的资产都判断一次并补指纹
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
        logger.debug("shodan 查询 %s 失败: %s", asset.value, e)
```

效果:每个 IP 资产在导出的 JSON 里带上 `properties._shodan`,可直接进下游分析。

### 6.2 漏洞扫描,并向图谱 / 导出注入结果

```python
def _notify_scan(engine):
    # 对发现的端口/域名做一次轻量检查,产出“vuln”节点
    for uid, asset in list(engine.asset_graph.nodes.items()):
        if asset.type != "port" or "vuln" in asset.properties:
            continue
        # 假冒示例:真实场景换成调 Nessus / nuclei / NVD
        cves = ["CVE-2026-0000"]
        asset.properties["vulns"] = cves              # 通道1:进导出
        if cves:
            vuln_asset = AssetFactory.create_asset(f"{asset.value}:发现漏洞", ASSET_TYPE_URL)
            vuln_asset.depth = asset.depth + 1
            vuln_asset.properties["cves"] = cves
            engine.asset_graph.add_asset(vuln_asset)  # 通道2:新节点,进导出
            engine.asset_graph.add_edge(asset, vuln_asset, "affected")
```

### 6.3 聚合报告

在 `on_asset_discovered` 里汇总数据,`on_scan_completed` 里一次性把 Markdown / 报告导出到 `run_dir`。在事件间持久数据用**模块级全局**:

```python
_seen = []

def on_asset_discovered(asset, source):
    _seen.append(asset.uid)

def on_scan_started(engine):
    _seen.clear()
```

> 每次 `on_scan_started` 先清空,防止跨轮次(W 监控模式)串数据。

### 6.4 自定义事件

插件既当“消费者”,也能造“新事件”给别的插件消费:

```python
def on_asset_scanned(asset, new_assets):
    engine = _engine                       # 6.1 里已缓存
    for na in new_assets:
        if na.type == "port":
            engine.emit_hook("on_port_reported", asset=na)   # 自定义事件
```

其它插件注册 `on_port_reported` 即可收到;自定义事件不会干扰引擎内置流程。

---

## 七、多线程与可靠性

- 引擎用线程池并行处理资产,`_process_asset` 在**多个 worker 线程**中执行,所以 `on_asset_*` 处理器可能被并发调用。
  - 只读 `asset_graph` / `metrics`:自带锁,安全。
  - 改**模块级共享变量**(如 `_seen` 列表):需自己加 `threading.Lock()`,或用 `engine.metrics` 这类已加锁的结构。
- 处理器抛异常:仅记日志,不影响扫描。
- 插件 import 阶段(模块顶层代码)尽量只做“常量 + 函数定义”,把耗时/带副作用的工作放进处理器;否则每个引擎实例一启动就被执行。

---

## 八、插件管理

### 8.1 命令

| 命令 | 作用 |
|------|------|
| `python main.py --list-plugins` | 列出插件:版本、处理器数、类型、状态、订阅事件 |
| `python main.py --plugin-info <name>` | 查看某个插件完整信息(含目录插件文件/文档及其 `plugin_help()`) |
| `python main.py --web` | Web 控制台 -> **插件**页面列出插件并可启停(`/api/plugins`) |
| 配置文件 | 控制加载行为(见下) |

```yaml
plugins:
  dir: plugins              # 插件目录(默认 plugins/)
  disabled:                 # 禁用名单,值为插件名(不带 .py)
    - my_plugin
```

`plugins.disabled` 对单文件**和**目录插件均生效。插件状态:`loaded` /
`disabled` / `failed`(import 异常时显示原因)。

### 8.2 CLI 集成

插件可定义 `register_cli(parser)`;插件加载期间,`main.py` 在解析参数时会
调用它,其参数会出现在 `--help` 中。禁用插件(或改掉 `plugins.dir`)后参数消失。

```python
def register_cli(parser):
    parser.add_argument("--my-feature", action="store_true",
                        help="由 my_plugin 提供(仅在加载时存在)")
```

### 8.3 目录插件

目录插件导入时其所在目录会前置进 `sys.path`,可自由 `import` 同目录的辅助
模块、配置与资源。`--plugin-info` 会展示目录内文件清单及 `README.md` /
`说明.md` 的摘要。必须含入口文件——纯资源目录不算插件。

### 8.4 冲突与互斥

- 同名插件来自两个来源(如 `foo.py` + `foo/`):排序首个胜出,其余跳过并记日志。
- 两个插件在清单中声明相同 `name`:先加载者胜。
- `conflicts` 互斥声明(见 四章)命中已加载插件:先加载者胜。
- 所有败者都会以 `{name, reason, winner, loser}` 记入
  `engine.plugin_conflicts`。

---

## 九、密钥与安全建议

- API key / 令牌一律放环境变量或 `breeding-config.yaml`(已声明为配置文件),**严禁**写死进插件代码再提交。
- 插件代码与配置不提交 git;`breeding-config.yaml` 提交前先做脱敏。
- 调用外部服务(**比如 Shodan**)时设置 `timeout`,异常要 `try/except` 吞掉并记日志,避免拖慢扫描。
- 恶意插件概念:引擎会执行 `plugins/` 下所有代码,别随意放第三方 .py;放一份清单审计 `--list-plugins` 也不错。

---

> 从 `plugins/` 目录放一个简单的插件(如只打印事件的 `hello.py`)开始修改学习即可。