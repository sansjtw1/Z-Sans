#!/usr/bin/env python3
# coding: utf-8
import copy
import io
import json
import logging
import os
import re
import threading
import time
import traceback
import yaml
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

logger = logging.getLogger('zsans.web')

# ─────────────────────────────────────────────
# 扫描任务管理器
# ─────────────────────────────────────────────

class ScanManager:

    def __init__(self, base_config, config_path, output_dir):
        self._base_config = base_config
        self._config_path = config_path
        self._output_dir = output_dir
        self._tasks = {}          # task_id -> task dict
        self._lock = threading.Lock()
        self._next_id = 1

    def _new_task(self):
        with self._lock:
            tid = f"task-{self._next_id}"
            self._next_id += 1
            return tid

    def _task_metrics(self, t):
        """返回任务当前 metrics；运行中从引擎实时读取，否则用完成时的快照。"""
        engine = t.get("engine")
        if engine is not None and getattr(engine, "metrics", None):
            try:
                return dict(engine.metrics)
            except Exception:
                pass
        return dict(t["metrics"]) if t.get("metrics") else {}

    def list_tasks(self):
        with self._lock:
            return [
                {
                    "id": t["id"],
                    "seeds": t["seeds"],
                    "status": t["status"],
                    "started_at": t["started_at"],
                    "finished_at": t["finished_at"],
                    "metrics": self._task_metrics(t),
                    "run_dir": t.get("run_dir"),
                }
                for t in sorted(self._tasks.values(), key=lambda x: x["started_at"] or 0, reverse=True)
            ]

    def get_task(self, tid):
        with self._lock:
            t = self._tasks.get(tid)
            if not t:
                return None
            return {
                "id": t["id"],
                "seeds": t["seeds"],
                "status": t["status"],
                "started_at": t["started_at"],
                "finished_at": t["finished_at"],
                "metrics": self._task_metrics(t),
                "run_dir": t.get("run_dir"),
                "error": t.get("error"),
                "traceback": t.get("traceback"),
            }

    def rescan_task(self, tid):
        """用原任务的种子/配置重新发起一次扫描。返回新任务 id 或 None。"""
        with self._lock:
            t = self._tasks.get(tid)
            if not t:
                return None
            seeds = list(t.get("seeds") or [])
            overrides = dict(t.get("config") or {})
        if not seeds:
            return None
        return self.start_scan(seeds, overrides)

    def get_logs(self, tid, tail=500):
        with self._lock:
            t = self._tasks.get(tid)
            if not t:
                return []
            return t["logs"][-tail:]

    def get_logs_since(self, tid, last_index=0):
        """返回自 last_index 之后新增的日志与任务状态，供 SSE/增量轮询使用。

        返回 (new_logs, total_index, status) 或 None(任务不存在)。
        """
        with self._lock:
            t = self._tasks.get(tid)
            if not t:
                return None
            logs = t["logs"]
            total = len(logs)
            if 0 <= last_index <= total:
                new_logs = logs[last_index:]
            else:
                # last_index 超出范围（日志被 _TaskLogHandler 裁剪过）时返回全量追平，
                # 避免 last_index == total 时误返全量导致 SSE 无限重复推送历史日志。
                new_logs = logs
            return new_logs, total, t.get("status")

    def get_live_project(self, run_dir):
        """若 run_dir 对应一个进行中的任务，返回其当前资产图的实时快照。

        任务未结束前 output/ 下还没有导出的 JSON，前端查看项目详情时
        用实时图数据代替，避免"项目数据不存在"。
        """
        run_dir = os.path.normpath(run_dir or '')
        with self._lock:
            for t in self._tasks.values():
                trd = os.path.normpath(t.get("run_dir") or '')
                if trd != run_dir:
                    continue
                engine = t.get("engine")
                if engine is None:
                    return None
                try:
                    nodes = []
                    for uid, a in list(engine.asset_graph.nodes.items()):
                        nodes.append(a.to_dict())
                    edges = [{"source": s, "target": e, "relation": r} for (s, e), r in engine.asset_graph.edges.items()]
                    return {
                        "nodes": nodes,
                        "edges": edges,
                        "metrics": dict(engine.metrics),
                        "status": t["status"],
                        "live": True,
                        "seed_domains": sorted(engine.seed_domains),
                    }
                except Exception as e:
                    logger.debug("live project snapshot failed: %s", e)
                    return None
        return None

    def start_scan(self, seeds, overrides=None):
        """启动一个新扫描任务（后台线程）。"""
        from main import BreedingEngine

        tid = self._new_task()
        config = copy.deepcopy(self._base_config)
        if overrides:
            self._merge_config(config, overrides)

        # 输出目录: 任务级覆盖（兼容 dict 形式 {dir: ...} 或字符串形式）
        if overrides and overrides.get('output'):
            ov_out = overrides['output']
            if isinstance(ov_out, dict) and ov_out.get('dir'):
                config['output']['dir'] = ov_out['dir']
            elif isinstance(ov_out, str):
                config['output']['dir'] = ov_out

        task = {
            "id": tid,
            "seeds": seeds,
            "config": config,
            "status": "running",
            "started_at": time.time(),
            "finished_at": None,
            "metrics": {},
            "run_dir": None,
            "error": None,
            "traceback": None,
            "logs": [],
            "thread": None,
            "engine": None,
        }
        with self._lock:
            self._tasks[tid] = task

        # 任务级日志收集 handler
        handler = _TaskLogHandler(task["logs"])
        handler.setLevel(logging.INFO)
        logging.getLogger('zsans').addHandler(handler)
        task["log_handler"] = handler

        def _run():
            engine = None
            try:
                engine = BreedingEngine(config, register_signals=False)
                task["engine"] = engine

                added = False
                for s in seeds:
                    if s.get("type") == "domain":
                        if engine.add_seed("domain", s.get("value")):
                            added = True
                    elif s.get("type") == "url":
                        if engine.add_seed("url", s.get("value")):
                            added = True
                    elif s.get("type") == "ip":
                        if engine.add_seed("ip", s.get("value")):
                            added = True

                if not added:
                    task["status"] = "failed"
                    task["error"] = "No valid seed assets provided"
                    return

                run_dir = engine.output_handler.ensure_run_dir()
                task["run_dir"] = run_dir

                ok = engine.run()

                task["metrics"] = dict(engine.metrics)
                # 用户主动停止的任务应显示 stopped，而非 failed/completed：
                # engine.stop() 会把状态置为 stopped，此时 run() 返回 False
                # 或 True 均不代表"失败"。
                if task.get("_stopped_by_user"):
                    task["status"] = "stopped"
                else:
                    task["status"] = "completed" if ok else "failed"
                    if not ok and engine.metrics.get("errors"):
                        task["error"] = f"{engine.metrics.get('errors')} errors during scan"
            except Exception as e:
                logger.error("Web task %s failed: %s", tid, e)
                task["status"] = "failed"
                task["error"] = str(e)
                task["traceback"] = traceback.format_exc()
                traceback.print_exc()
            finally:
                task["finished_at"] = time.time()
                if task.get("log_handler"):
                    try:
                        logging.getLogger('zsans').removeHandler(task["log_handler"])
                    except Exception:
                        pass
                task["log_handler"] = None
                task["engine"] = None

        t = threading.Thread(target=_run, daemon=True, name=f"zsans-web-{tid}")
        task["thread"] = t
        t.start()
        return tid

    def stop_scan(self, tid):
        with self._lock:
            t = self._tasks.get(tid)
            if not t:
                return False
            engine = t.get("engine")
            if engine and t["status"] == "running":
                engine.stop()
                t["_stopped_by_user"] = True
                return True
            return False

    def delete_projects(self, ids):
        """删除指定历史扫描项目目录（仅限时间戳格式 id，防路径穿越）。

        返回 (deleted_ids, failed_ids)。
        """
        deleted = []
        failed = []
        for pid in (ids or []):
            if not re.match(r'^\d{8}_\d{6}$', pid or ''):
                failed.append(pid)
                continue
            proj_dir = os.path.join(self._output_dir, pid)
            # 不能删除正在运行任务的 run_dir
            with self._lock:
                running = any(
                    t.get("status") == "running"
                    and os.path.normpath(t.get("run_dir") or '') == os.path.normpath(proj_dir)
                    for t in self._tasks.values()
                )
            if running:
                failed.append(pid)
                continue
            try:
                if os.path.isdir(proj_dir):
                    import shutil
                    shutil.rmtree(proj_dir)
                deleted.append(pid)
            except Exception as e:
                logger.error("delete project %s failed: %s", pid, e)
                failed.append(pid)
        return deleted, failed

    def toggle_plugin(self, plugin_name):
        """启用/禁用插件：读写配置文件中的 plugins.disabled 列表。

        文本级最小化修改（保留配置文件注释），不重建整个 YAML 文档。
        返回 (success, message, disabled_status)。
        """
        if not re.match(r'^[A-Za-z0-9_.-]+$', plugin_name or ''):
            return False, "invalid plugin name", None
        raw, err = read_config_raw(self._config_path)
        if err:
            return False, err, None
        if raw is None:
            raw = ""

        data, _ = read_config_file(self._config_path)
        data = data or {}
        plugins_cfg = data.get('plugins') or {}
        disabled = plugins_cfg.get('disabled') if isinstance(plugins_cfg, dict) else []
        if not isinstance(disabled, list):
            disabled = []
        disabled = [str(x) for x in disabled]

        if plugin_name in disabled:
            new_disabled = [x for x in disabled if x != plugin_name]
            now_disabled = False
        else:
            new_disabled = list(disabled) + [plugin_name]
            now_disabled = True

        new_raw = _set_plugins_disabled_text(raw, new_disabled)
        try:
            with open(self._config_path, 'w', encoding='utf-8') as f:
                f.write(new_raw)
        except Exception as e:
            return False, str(e), None
        return True, f"plugin {plugin_name} {'disabled' if now_disabled else 'enabled'}", now_disabled

    @staticmethod
    def _merge_config(base, overrides):
        """递归合并 overrides 到 base（dict 深合并）。

        None 视为"未填写"跳过，避免前端/配置中的空段落把默认子树抹掉。
        """
        for k, v in overrides.items():
            if v is None:
                continue
            if isinstance(v, dict) and isinstance(base.get(k), dict):
                ScanManager._merge_config(base[k], v)
            else:
                base[k] = v

    # ── 插件前端配置（webui/schema 表单）──────────────
    def plugin_config_path(self, plugin_name):
        """插件 web 配置独立存储路径（不污染主配置文件）。"""
        cfg_dir = os.path.join(self._output_dir, "plugin_config")
        return os.path.join(cfg_dir, re.sub(r'[^A-Za-z0-9_.-]', '_', plugin_name) + ".yaml")

    def get_plugin_config(self, plugin_name):
        """读取插件 web 配置；无则返回 {}。"""
        p = self.plugin_config_path(plugin_name)
        data, err = read_config_file(p) if os.path.exists(p) else (None, None)
        if err or data is None:
            return {}
        return data

    def set_plugin_config(self, plugin_name, data):
        """保存插件 web 配置（整体覆盖）。"""
        if not isinstance(data, dict):
            return False, "config must be an object"
        p = self.plugin_config_path(plugin_name)
        try:
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, 'w', encoding='utf-8') as f:
                yaml.dump(data, f, default_flow_style=False, sort_keys=False, allow_unicode=True)
            return True, None
        except Exception as e:
            return False, str(e)

    def resolve_plugin_webui(self, plugin_name, rel=''):
        """定位插件 webui 文件绝对路径。

        rel 为空返回主 webui；否则返回插件目录下相对路径 rel（用于加载
        同目录 schema/json/css/js 等资源）。返回 (abs_path|None, error|None)。
        """
        if not re.match(r'^[A-Za-z0-9_.-]+$', plugin_name or ''):
            return None, "invalid plugin name"
        try:
            plugins = list_plugins(lambda: self._new_engine())
        except Exception as e:
            return None, str(e)
        for info in plugins:
            if info.get("name") != plugin_name:
                continue
            base = info.get("path") or ""
            if not base:
                return None, "plugin not found"
            base_abs = os.path.abspath(base)
            if rel:
                abs_path = os.path.abspath(os.path.join(base_abs, rel))
            else:
                webui = info.get("webui") or ""
                if not webui:
                    return None, "plugin has no webui"
                abs_path = os.path.abspath(os.path.join(base_abs, webui))
            if not abs_path.startswith(base_abs + os.sep) and abs_path != base_abs:
                return None, "path escapes plugin dir"
            if not os.path.isfile(abs_path):
                return None, "file not found"
            return abs_path, None
        return None, "plugin not found"


class _TaskLogHandler(logging.Handler):
    """把 zsans 日志收集到任务内存队列。"""

    def __init__(self, sink, max_entries=2000):
        super().__init__()
        self._sink = sink
        self._max = max_entries
        self._lock = threading.Lock()

    def emit(self, record):
        try:
            line = self.format(record)
        except Exception:
            return
        with self._lock:
            self._sink.append(line)
            if len(self._sink) > self._max:
                del self._sink[:len(self._sink) - self._max]


# ─────────────────────────────────────────────
# 项目(历史扫描)浏览
# ─────────────────────────────────────────────

def list_projects(output_dir):
    """扫描 output/ 下所有时间戳子目录，返回项目元信息。"""
    projects = []
    if not os.path.isdir(output_dir):
        return projects
    for name in sorted(os.listdir(output_dir), reverse=True):
        d = os.path.join(output_dir, name)
        if not os.path.isdir(d):
            continue
        # 只认时间戳形式目录
        if not re.match(r'^\d{8}_\d{6}$', name):
            continue
        json_file = _find_file(d, '.json', exclude='_report')
        csv_file = _find_file(d, '_assets.csv')
        graphml = _find_file(d, '.graphml')
        projects.append({
            "id": name,
            "path": d,
            "has_json": bool(json_file),
            "has_csv": bool(csv_file),
            "has_graphml": bool(graphml),
            "json_file": os.path.basename(json_file) if json_file else None,
            "csv_file": os.path.basename(csv_file) if csv_file else None,
            "graphml_file": os.path.basename(graphml) if graphml else None,
        })
    return projects


def _find_file(d, suffix, exclude=None):
    try:
        for f in sorted(os.listdir(d)):
            if f.endswith(suffix) and (not exclude or exclude not in f):
                return os.path.join(d, f)
    except Exception:
        pass
    return None


def read_project_json(output_dir, pid):
    """读取某项目导出的 JSON 资产图。"""
    # 安全防护：仅允许时间戳格式目录名，防止路径穿越
    if not re.match(r'^\d{8}_\d{6}$', pid or ''):
        return None
    proj_dir = os.path.join(output_dir, pid)
    if not os.path.isdir(proj_dir):
        return None
    jf = _find_file(proj_dir, '.json', exclude='_report')
    if not jf:
        return None
    try:
        with open(jf, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return None


def compare_projects(output_dir, ids):
    """对比多个扫描项目的资产（按 uid 求交集/差集）。"""
    projects = []
    for pid in ids:
        data = read_project_json(output_dir, pid)
        if not data or not isinstance(data.get('nodes'), list):
            continue
        uid_set = {n.get('uid') for n in data['nodes']}
        projects.append({"id": pid, "uids": uid_set, "nodes": data['nodes']})

    if not projects:
        return {"error": "no valid projects"}

    base = projects[0]
    result = {
        "base": base["id"],
        "others": [p["id"] for p in projects[1:]],
        "only_in_base": sorted(base["uids"] - set().union(*[p["uids"] for p in projects[1:]])) if len(projects) > 1 else sorted(base["uids"]),
        "total_base": len(base["uids"]),
    }
    if len(projects) > 1:
        other_union = set().union(*[p["uids"] for p in projects[1:]])
        result["only_in_others"] = sorted(other_union - base["uids"])
        result["total_others"] = len(other_union)
        result["common"] = sorted(base["uids"] & other_union)
    return result


# ─────────────────────────────────────────────
# 配置 / 插件
# ─────────────────────────────────────────────

def read_config_raw(path):
    """读取配置文件原始文本(YAML)。"""
    if not os.path.exists(path):
        return None, "config file not found"
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return f.read(), None
    except Exception as e:
        return None, str(e)


def _set_plugins_disabled_text(raw, disabled):
    """文本级更新 YAML 中 plugins.disabled 列表，保留其余内容与注释。

    与 yaml round-trip 不同，不重建整个文档，避免把用户注释全部抹掉。
    优先改写 plugins 块内已有的 disabled 项；块内没有则插入；没有 plugins 块则追加。
    """
    list_repr = "[" + ", ".join(disabled) + "]" if disabled else "[]"
    lines = raw.splitlines()

    plugins_indent = None   # plugins 顶层键的缩进
    block_end = None        # plugins 块结束行索引(不含)
    disabled_line_idx = None
    disabled_indent = None
    for idx, line in enumerate(lines):
        m = re.match(r'^(\s*)plugins:\s*(#.*)?$', line)
        if m:
            plugins_indent = m.group(1)
            block_end = len(lines)
            for j in range(idx + 1, len(lines)):
                nxt = lines[j]
                if nxt.strip() and not nxt.startswith(' ') and not nxt.startswith('\t'):
                    block_end = j
                    break
            for j in range(idx + 1, block_end):
                md = re.match(r'^(\s+)disabled:', lines[j])
                if md and len(md.group(1)) > len(plugins_indent):
                    disabled_line_idx = j
                    disabled_indent = len(md.group(1))
                    break
            break  # 只处理第一个 plugins 块

    if disabled_line_idx is not None:
        new_lines = lines[:disabled_line_idx]
        new_lines.append(" " * disabled_indent + f"disabled: {list_repr}")
        # 删除原本属于该列表的块级 "- item" 行
        j = disabled_line_idx + 1
        while j < block_end:
            nxt = lines[j]
            m = re.match(r'^(\s+)-', nxt)
            if m and len(m.group(1)) > disabled_indent:
                j += 1
            else:
                break
        new_lines.extend(lines[j:])
        return "\n".join(new_lines)

    if plugins_indent is not None:
        new_lines = lines[:block_end]
        new_lines.append(f"{plugins_indent}  disabled: {list_repr}")
        new_lines.extend(lines[block_end:])
        return "\n".join(new_lines)

    out = raw.rstrip('\n')
    if out:
        out += "\n"
    out += f"plugins:\n  disabled: {list_repr}\n"
    return out


def _yaml_scalar_repr(value):
    """把值转成单行 YAML 标量表示（供文本级替换）。"""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_yaml_scalar_repr(v) for v in value) + "]"
    if isinstance(value, dict):
        return "{}"
    s = str(value)
    if s == "":
        return "''"
    if any(ch in s for ch in "\n:#"):
        return "'" + s.replace("'", "''") + "'"
    return s


def _yaml_block_line(lines, start, indent, key):
    """在 lines[start:] 中定位 indented key 行的索引；找不到返回 -1。"""
    pat = re.compile(r'^(%s)%s:(?:\s|$)' % (" " * indent, re.escape(key)))
    for i in range(start, len(lines)):
        line = lines[i]
        if not line.strip():
            continue
        m = pat.match(line)
        if m:
            return i
        # 缩进比目标浅的行说明已离开父块，停止搜索，避免误匹配后续兄弟块
        if indent > 0 and not line.startswith(' ') and not line.startswith('\t'):
            break
    return -1


def _yaml_find_parent_block(lines, start, parent_indent):
    """返回 parent 子块的结束索引（不含）：从 start 起，到下一个缩进 <= parent_indent 的行止。"""
    end = len(lines)
    for i in range(start, len(lines)):
        line = lines[i]
        if not line.strip():
            continue
        # 缩进 <= 父缩进的非空行表示离开父块
        m = re.match(r'^(\s*)', line)
        if m and len(m.group(1)) <= parent_indent and not line.strip().startswith('#'):
            end = i
            break
    return end


def _yaml_value_lines(key, value, indent):
    """把 key+value 序列化为多行 YAML 文本行（支持标量/list/dict）。"""
    pad = " " * indent
    if isinstance(value, dict):
        if not value:
            return [f"{pad}{key}: {{}}"]
        out = [f"{pad}{key}:"]
        for k, v in value.items():
            out.extend(_yaml_value_lines(k, v, indent + 2))
        return out
    if isinstance(value, list):
        if not value:
            return [f"{pad}{key}: []"]
        # 使用 flow 风格单行表示整个列表，避免块式缩进复杂度
        flow = yaml.dump(value, default_flow_style=True, allow_unicode=True).strip()
        return [f"{pad}{key}: {flow}"]
    return [f"{pad}{key}: {_yaml_scalar_repr(value)}"]


def _yaml_insert_chain(lines, keys, value, anchor_line, base_indent):
    """在 anchor_line（插入到其之后）处插入 keys 链，最内层为 value。"""
    insert_at = anchor_line + 1
    out = list(lines)
    for depth, key in enumerate(keys[:-1]):
        indent = base_indent + depth * 2
        out.insert(insert_at, " " * indent + f"{key}:")
        insert_at += 1
    leaf_lines = _yaml_value_lines(keys[-1], value, base_indent + (len(keys) - 1) * 2)
    for i, ln in enumerate(leaf_lines):
        out.insert(insert_at + i, ln)
    return out


def _yaml_find_deepest(lines, keys):
    """逐级定位 keys；返回 (found_depth, last_idx, indent)。found_depth 为成功匹配的最大层数。"""
    start = 0
    indent = 0
    last_idx = -1
    found_depth = 0
    for depth, key in enumerate(keys):
        idx = _yaml_block_line(lines, start, indent, key)
        if idx < 0:
            break
        last_idx = idx
        found_depth = depth + 1
        start = idx + 1
        indent = depth * 2 + 2
    return found_depth, last_idx, indent


def _yaml_strip_block_body(lines, idx, key_indent):
    """删除 lines[idx]（某 key 行）之后属于其旧值的子块行，返回新行列表。

    删除规则（从 idx+1 起连续消费）：
      - 缩进 > key_indent 的行（嵌套子键）
      - 缩进 == key_indent 且以 '- ' 开头的块级序列项（YAML 允许与 key 同缩进）
      - 上述行之间的空行
    遇到缩进 <= key_indent 的非序列项行停止。
    """
    j = idx + 1
    while j < len(lines):
        line = lines[j]
        if not line.strip():
            # 空行：向后找下一个非空行判断是否仍在子块内
            k = j
            while k < len(lines) and not lines[k].strip():
                k += 1
            if k >= len(lines):
                break
            m = re.match(r'^(\s*)(-(\s|$)|.)', lines[k])
            nind = len(m.group(1)) if m else 0
            is_seq = bool(m and m.group(2).startswith('-'))
            if nind > key_indent or (nind == key_indent and is_seq):
                j = k
                continue
            break
        m = re.match(r'^(\s*)(-(\s|$)|.*)', line)
        nind = len(m.group(1)) if m else 0
        is_seq = bool(m and m.group(2).startswith('-'))
        if nind > key_indent or (nind == key_indent and is_seq):
            j += 1
        else:
            break
    if j > idx + 1:
        del lines[idx + 1:j]
    return lines


def apply_config_patch(raw, updates):
    """文本级更新 YAML 中若干 key 的值，保留注释与其他内容。

    updates: { "a.b.c": value, "a.x": value } —— 用点路径定位嵌套 key。
    已存在的 key 就地更新（保留行尾注释）；不存在的 key 在父块末尾插入。
    若父块也不存在，则整条链按需创建。
    """
    if not updates:
        return raw, None
    try:
        yaml.safe_load(raw)
    except Exception as e:
        return raw, f"YAML 语法错误: {e}"
    lines = raw.splitlines()

    # 先排序，父路径优先于子路径，避免插入影响后续定位
    ordered = sorted(updates.items(), key=lambda kv: (len(kv[0].split('.')), kv[0]))
    for dotted, value in ordered:
        keys = [k for k in dotted.split('.') if k]
        if not keys:
            continue
        found_depth, last_idx, indent = _yaml_find_deepest(lines, keys)
        if found_depth == len(keys):
            # 全路径存在：就地更新（先删旧子块，再用多行表示覆盖整块）
            line = lines[last_idx]
            comment = ""
            m = re.match(r'^(\s*[^#]*?)(\s+#.*)?$', line)
            if m and m.group(2):
                comment = m.group(2)
            key_indent = indent - 2
            lines = _yaml_strip_block_body(lines, last_idx, key_indent)
            value_lines = _yaml_value_lines(keys[-1], value, key_indent)
            lines[last_idx] = value_lines[0] + (comment if len(value_lines) == 1 else "")
            for bi, bl in enumerate(value_lines[1:], start=1):
                lines.insert(last_idx + bi, bl)
        else:
            # 需插入：以已找到的父位置为锚
            missing = keys[found_depth:]
            if found_depth == 0:
                # 顶层新 key：追加到文件末尾
                if lines and lines[-1].strip():
                    lines.append("")
                if len(missing) == 1:
                    lines.extend(_yaml_value_lines(missing[0], value, 0))
                else:
                    base = []
                    for depth, key in enumerate(missing[:-1]):
                        base.append(f"{'  ' * depth}{key}:")
                    lines.extend(base)
                    lines.extend(_yaml_value_lines(missing[-1], value, (len(missing) - 1) * 2))
            else:
                parent_indent = (found_depth - 1) * 2
                block_end = _yaml_find_parent_block(lines, last_idx + 1, parent_indent)
                anchor = block_end - 1
                # 跳过父块末尾的空行，找到最后一个实际行作为插入锚点
                while anchor >= last_idx and (not lines[anchor].strip() or lines[anchor].strip().startswith('#')):
                    anchor -= 1
                if anchor < last_idx:
                    anchor = last_idx
                lines = _yaml_insert_chain(lines, missing, value, anchor, found_depth * 2)

    return "\n".join(lines), None


def write_config_yaml(path, raw):
    """校验 YAML 合法后写回配置文件。"""
    try:
        yaml.safe_load(raw)  # 校验语法
    except Exception as e:
        return False, f"YAML 语法错误: {e}"
    try:
        with open(path, 'w', encoding='utf-8') as f:
            f.write(raw)
        return True, None
    except Exception as e:
        return False, str(e)


def read_config_file(path):
    if not os.path.exists(path):
        return None, "config file not found"
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = yaml.safe_load(f) or {}
        return data, None
    except Exception as e:
        return None, str(e)


def write_config_file(path, data):
    try:
        with open(path, 'w', encoding='utf-8') as f:
            yaml.dump(data, f, default_flow_style=False, sort_keys=False, allow_unicode=True)
        return True, None
    except Exception as e:
        return False, str(e)


def list_plugins(engine_factory):
    """用独立引擎实例读取插件注册表(不启动扫描)。"""
    try:
        engine = engine_factory()
        return [
            {
                "name": info.get("name"),
                "version": info.get("version"),
                "description": info.get("description"),
                "status": info.get("status"),
                "handlers": info.get("handlers"),
                "events": info.get("events", []),
                "path": info.get("path"),
                "kind": info.get("kind", "file"),
                "webui": info.get("webui", ""),
                "schema": info.get("schema"),
            }
            for info in engine.plugins.values()
        ]
    except Exception as e:
        logger.error("list_plugins failed: %s", e)
        return []


# ─────────────────────────────────────────────
# HTTP 服务
# ─────────────────────────────────────────────

# ─────────────────────────────────────────────
# 前端 i18n 语言包（Web 控制台界面文案）
# ─────────────────────────────────────────────

WEB_I18N_KEYS = [
    "scan_projects","new_task","tasks","config","plugins","history_projects",
    "refresh","delete_selected","compare_selected","output","data","actions",
    "view","back","project_compare","base_assets","other_assets","only_base",
    "only_others","common","no_assets","compare_empty","new_scan_task",
    "domain_seed","url_seed","ip_seed","max_depth","strategy","concurrency",
    "start_scan","starting","task_started","scan_tasks","seed","status",
    "asset_count","detail","stop","rescan","task_detail","view_output",
    "real_time_log","follow_log","no_tasks","no_projects","config_file",
    "reload","save","config_saved","plugin","name","version","handlers",
    "events","description","enable","disable","no_plugins","search_assets","enter",
    "all_types","assets","analysis","topology","raw_json","no_match",
    "type_dist","state_dist","depth_dist","no_data","depth","high_related",
    "seed_domains","nodes","edges","type_count","max_depth_l","props",
    "no_props","source","type","close","scan_running","scan_after_done",
    "data_not_found","task_failed","drag_hint","only_base_has","only_others_has",
    "common_has",
]

_web_gettext_cache = {}


def _web_gettext(lang):
    """按语言加载项目 gettext .mo，返回翻译函数。lang 为完整 locale 名（如 zh_CN/en）。"""
    if lang in _web_gettext_cache:
        return _web_gettext_cache[lang]
    import gettext
    try:
        t = gettext.translation('messages', localedir='i18n', languages=[lang])
    except Exception:
        t = gettext.NullTranslations()
    _web_gettext_cache[lang] = t.gettext
    return _web_gettext_cache[lang]


def web_i18n_dict(lang):
    """返回前端语言包（key -> 当前语言翻译），走项目 gettext 体系。"""
    g = _web_gettext(lang)
    return {k: g(k) for k in WEB_I18N_KEYS}


class ZSansWebHandler(BaseHTTPRequestHandler):
    server_version = "Z-Sans-Web/0.0.5"

    def log_message(self, fmt, *args):
        # 抑制默认的访问日志噪音，只保留在 debug 级别。
        # 注意：parse_request 失败时 self.path 可能尚未赋值（AttributeError），
        # 且 fmt/args 由 http.server 传入，需安全兜底，避免二次异常掩盖原始错误。
        path = getattr(self, 'path', '?') or '?'
        try:
            detail = fmt % args if args else fmt
        except Exception:
            detail = fmt
        logger.debug("web %s %s", path, detail)

    # ---- 工具 ----
    def _send_json(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, html, status=200):
        body = html.encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_static(self, path):
        """提供 web_static/ 下的静态资源（本地化前端依赖，不依赖外网 CDN）。"""
        name = os.path.basename(path)
        if not name or '..' in name:
            self._send_json({"error": "bad static path"}, 400)
            return
        file_path = os.path.join(self.server.static_dir, name)
        if not os.path.isfile(file_path):
            self._send_json({"error": "not found"}, 404)
            return
        ctype = 'application/javascript; charset=utf-8' if name.endswith('.js') else 'application/octet-stream'
        try:
            with open(file_path, 'rb') as f:
                body = f.read()
        except Exception:
            self._send_json({"error": "read failed"}, 500)
            return
        self.send_response(200)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, file_path):
        """发送任意文件，按扩展名决定 Content-Type（供插件 webui 托管）。"""
        ext = os.path.splitext(file_path)[1].lower()
        ctype = {
            '.html': 'text/html; charset=utf-8',
            '.htm': 'text/html; charset=utf-8',
            '.js': 'application/javascript; charset=utf-8',
            '.mjs': 'application/javascript; charset=utf-8',
            '.css': 'text/css; charset=utf-8',
            '.json': 'application/json; charset=utf-8',
            '.png': 'image/png',
            '.jpg': 'image/jpeg',
            '.jpeg': 'image/jpeg',
            '.gif': 'image/gif',
            '.svg': 'image/svg+xml',
            '.ico': 'image/x-icon',
            '.woff2': 'font/woff2',
        }.get(ext, 'application/octet-stream')
        try:
            with open(file_path, 'rb') as f:
                body = f.read()
        except Exception:
            self._send_json({"error": "read failed"}, 500)
            return
        self.send_response(200)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _stream_logs(self, tid):
        """SSE 日志流：长连接，有新日志/状态变化即推送。

        前端用 EventSource 连接；任务结束后发送 event: done 并关闭。
        """
        mgr = self._get_manager()
        try:
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.send_header('Cache-Control', 'no-cache')
            self.send_header('Connection', 'keep-alive')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()

            last_idx = 0
            # 先推送一次现有日志(全量)作为初始
            snap = mgr.get_logs_since(tid, 0)
            if snap is None:
                self.wfile.write(b'event: done\ndata: {"error":"task not found"}\n\n')
                self.wfile.flush()
                return
            new_logs, last_idx, status = snap
            if new_logs:
                payload = json.dumps({"logs": new_logs, "status": status}, ensure_ascii=False)
                self.wfile.write(f"data: {payload}\n\n".encode('utf-8'))
                self.wfile.flush()

            # 循环增量推送
            while True:
                snap = mgr.get_logs_since(tid, last_idx)
                if snap is None:
                    break
                new_logs, last_idx, status = snap
                if new_logs:
                    payload = json.dumps({"logs": new_logs, "status": status}, ensure_ascii=False)
                    self.wfile.write(f"data: {payload}\n\n".encode('utf-8'))
                    self.wfile.flush()
                # 任务已结束且无新日志 → 完成
                if status != 'running':
                    self.wfile.write(b'event: done\ndata: {"status":"' + status.encode() + b'"}\n\n')
                    self.wfile.flush()
                    break
                time.sleep(0.5)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:
            try:
                self.wfile.write(b'event: done\ndata: {"error":"stream closed"}\n\n')
                self.wfile.flush()
            except Exception:
                pass

    def _read_body(self):
        length = int(self.headers.get('Content-Length') or 0)
        if length <= 0:
            return b''
        return self.rfile.read(length)

    def _json_body(self):
        raw = self._read_body()
        if not raw:
            return {}
        try:
            return json.loads(raw.decode('utf-8'))
        except Exception:
            return {}

    def _get_manager(self):
        return self.server.manager  # type: ignore[attr-defined]

    # ---- 路由 ----
    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)

        try:
            if path == '/' or path == '/index.html':
                self._send_html(INDEX_HTML)
            elif path.startswith('/static/'):
                self._serve_static(path)
            elif path == '/api/health':
                self._send_json({"ok": True, "version": self.server.zs_version, "lang": self.server.lang})
            elif path == '/api/i18n':
                # 语言由配置文件决定，不随前端切换
                lang = self.server.lang
                self._send_json({"lang": lang, "dict": web_i18n_dict(lang)})
            elif path == '/api/projects':
                self._send_json(list_projects(self._get_manager()._output_dir))
            elif path.startswith('/api/projects/'):
                pid = path.rsplit('/', 1)[-1]
                data = read_project_json(self._get_manager()._output_dir, pid)
                if data is None:
                    # 可能是进行中的任务：run_dir 已建但 JSON 尚未导出，尝试实时快照
                    live = self._get_manager().get_live_project(
                        os.path.join(self._get_manager()._output_dir, pid))
                    if live is not None:
                        self._send_json(live)
                    else:
                        # 目录存在但无数据 vs 目录不存在
                        proj_dir = os.path.join(self._get_manager()._output_dir, pid)
                        if os.path.isdir(proj_dir):
                            self._send_json({"status": "pending", "message": "scan in progress or no output yet"})
                        else:
                            self._send_json({"error": "project not found"}, 404)
                else:
                    self._send_json(data)
            elif path == '/api/compare':
                ids = qs.get('ids', [''])[0].split(',')
                ids = [i for i in ids if re.match(r'^\d{8}_\d{6}$', i or '')][:10]
                result = compare_projects(self._get_manager()._output_dir, ids)
                self._send_json(result)
            elif path == '/api/tasks':
                self._send_json(self._get_manager().list_tasks())
            elif path.startswith('/api/tasks/') and path.endswith('/logs/stream'):
                tid = path.split('/')[3]
                self._stream_logs(tid)
            elif path.startswith('/api/tasks/') and path.endswith('/logs'):
                tid = path.split('/')[3]
                tail = int(qs.get('tail', ['500'])[0])
                self._send_json({"id": tid, "logs": self._get_manager().get_logs(tid, tail)})
            elif path.startswith('/api/tasks/'):
                tid = path.rsplit('/', 1)[-1]
                t = self._get_manager().get_task(tid)
                if t is None:
                    self._send_json({"error": "task not found"}, 404)
                else:
                    self._send_json(t)
            elif path == '/api/config':
                data, err = read_config_raw(self._get_manager()._config_path)
                if err:
                    self._send_json({"error": err}, 500)
                else:
                    self._send_json({"yaml": data})
            elif path == '/api/plugins':
                mgr = self._get_manager()
                plugins = list_plugins(lambda: mgr._new_engine())
                self._send_json({"plugins": plugins})
            elif path.startswith('/api/plugins/') and '/webui' in path:
                parts = path.split('/')
                name = parts[3]
                # /api/plugins/<name>/webui[/<sub>...]
                sub = ''
                if len(parts) > 5:
                    sub = '/'.join(parts[5:])
                mgr = self._get_manager()
                abs_path, err = mgr.resolve_plugin_webui(name, rel=sub)
                if err:
                    self._send_json({"error": err}, 404)
                else:
                    self._send_file(abs_path)
            elif path.startswith('/api/plugins/') and path.endswith('/config'):
                name = path.split('/')[3]
                cfg = self._get_manager().get_plugin_config(name)
                self._send_json({"name": name, "config": cfg})
            elif path == '/api/config/json':
                data, err = read_config_file(self._get_manager()._config_path)
                if err:
                    self._send_json({"error": err}, 500)
                else:
                    self._send_json({"config": data})
            elif path == '/api/config/schema':
                self._send_json(self._get_manager()._base_config)
            else:
                self._send_json({"error": "not found", "path": path}, 404)
        except Exception as e:
            logger.error("GET %s error: %s", path, e)
            traceback.print_exc()
            self._send_json({"error": str(e)}, 500)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path

        try:
            if path == '/api/scan/start':
                body = self._json_body()
                seeds = body.get('seeds') or []
                overrides = body.get('config') or {}
                tid = self._get_manager().start_scan(seeds, overrides)
                self._send_json({"id": tid, "status": "started"})
            elif path == '/api/projects/delete':
                body = self._json_body()
                ids = body.get('ids') or []
                deleted, failed = self._get_manager().delete_projects(ids)
                self._send_json({"ok": True, "deleted": deleted, "failed": failed})
            elif path == '/api/tasks/stop':
                body = self._json_body()
                tid = body.get('id')
                ok = self._get_manager().stop_scan(tid)
                self._send_json({"ok": ok})
            elif path == '/api/tasks/rescan':
                body = self._json_body()
                tid = body.get('id')
                new_id = self._get_manager().rescan_task(tid)
                if new_id:
                    self._send_json({"ok": True, "id": new_id})
                else:
                    self._send_json({"error": "task not found or no seeds"}, 404)
            elif path == '/api/config':
                raw = self._read_body().decode('utf-8')
                ok, err = write_config_yaml(self._get_manager()._config_path, raw)
                if ok:
                    self._send_json({"ok": True})
                else:
                    self._send_json({"error": err}, 500)
            elif path == '/api/config/patch':
                body = self._json_body()
                updates = body.get('updates')
                if not isinstance(updates, dict):
                    self._send_json({"error": "updates must be an object"}, 400)
                    return
                cur_raw, err = read_config_raw(self._get_manager()._config_path)
                if err:
                    self._send_json({"error": err}, 500)
                    return
                new_raw, perr = apply_config_patch(cur_raw, updates)
                if perr:
                    self._send_json({"error": perr}, 500)
                    return
                ok, werr = write_config_yaml(self._get_manager()._config_path, new_raw)
                if ok:
                    self._send_json({"ok": True, "applied": list(updates.keys())})
                else:
                    self._send_json({"error": werr}, 500)
            elif path.startswith('/api/plugins/') and path.endswith('/toggle'):
                name = path.split('/')[3]
                ok, msg, now = self._get_manager().toggle_plugin(name)
                if ok:
                    self._send_json({"ok": True, "message": msg, "disabled": now})
                else:
                    self._send_json({"error": msg}, 500)
            elif path.startswith('/api/plugins/') and path.endswith('/config'):
                name = path.split('/')[3]
                body = self._json_body()
                ok, err = self._get_manager().set_plugin_config(name, body.get('config') or {})
                if ok:
                    self._send_json({"ok": True})
                else:
                    self._send_json({"error": err}, 500)
            else:
                self._send_json({"error": "not found"}, 404)
        except Exception as e:
            logger.error("POST %s error: %s", path, e)
            traceback.print_exc()
            self._send_json({"error": str(e)}, 500)


class ZSansWebServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, addr, handler_cls, manager, zs_version, static_dir=None, lang='zh'):
        super().__init__(addr, handler_cls)
        self.manager = manager
        self.zs_version = zs_version
        self.lang = lang
        self.static_dir = static_dir or os.path.join(os.path.dirname(os.path.abspath(__file__)), 'web_static')


def start_web_server(base_config, config_path, output_dir, port=8050, host='0.0.0.0'):
    """启动 web 控制台。阻塞运行。"""
    from main import BreedingEngine

    manager = ScanManager(base_config, config_path, output_dir)

    def _fresh_engine():
        # 每次从配置文件重新读取，使插件启停等配置改动即时生效。
        # 原始 YAML 必须经 _normalize_config 合并默认值，否则用户省略的
        # 段落（或空段落解析出的 None）会让引擎内部 .get 链拿到 None。
        from main import _normalize_config
        fresh, _err = read_config_file(config_path)
        cfg = _normalize_config(fresh if isinstance(fresh, dict) else {})
        return BreedingEngine(cfg, register_signals=False)

    manager._new_engine = _fresh_engine

    # 语言由配置文件 language.default_language 决定
    lang = 'zh_CN'
    try:
        from core.i18n import get_current_language
        cur = get_current_language()
        if cur:
            lang = cur
        # 配置文件优先：直接读 language.default_language
        cfg_data, _ = read_config_file(config_path)
        if cfg_data and isinstance(cfg_data.get('language'), dict):
            dl = cfg_data['language'].get('default_language')
            if dl:
                lang = dl
    except Exception:
        pass

    try:
        from main import VERSION as _zs_version
        zs_version = _zs_version
    except Exception:
        zs_version = "0.0.6"
    server = ZSansWebServer((host, port), ZSansWebHandler, manager, zs_version, lang=lang)
    logger.info("Z-Sans web console started on http://%s:%d", host, port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


# 前端模板占位(在 web_frontend.py 注入)
INDEX_HTML = None
try:
    from web_frontend import INDEX_HTML as _FRONTEND
    if _FRONTEND:
        INDEX_HTML = _FRONTEND
except ImportError:
    INDEX_HTML = None


def get_index_html():
    if INDEX_HTML:
        return INDEX_HTML
    # 兜底: 找不到前端模板时给提示
    return (
        "<!DOCTYPE html><html><head><title>Z-Sans Web</title></head><body>"
        "<h1>Z-Sans Web Console</h1>"
        "<p>Frontend template not found. API endpoints are available.</p>"
        "</body></html>"
    )


INDEX_HTML = get_index_html()
