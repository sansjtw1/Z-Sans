# coding: utf-8

import argparse
import copy
import json
import logging

_stop_signaled = False
import os
import re
import signal
import sys
import threading
import time
import yaml
import concurrent.futures
import fnmatch
from concurrent.futures import ThreadPoolExecutor, as_completed
try:
    import colorama
    colorama.init()
    USE_COLORAMA = True
except ImportError:
    USE_COLORAMA = False
from datetime import datetime

from core.i18n import setup_i18n, _

LOG_CONFIGURED = False

try:
    import colorlog
    USE_COLOR_LOG = True
except ImportError:
    USE_COLOR_LOG = False

def configure_logging():
    global LOG_CONFIGURED
    if LOG_CONFIGURED:
        return logging.getLogger('zsans.main')
    
    LOG_CONFIGURED = True
    
    root_logger = logging.getLogger()
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)
    
    for name in logging.root.manager.loggerDict:
        if name.startswith('zsans'):
            logger = logging.getLogger(name)
            for handler in logger.handlers[:]:
                logger.removeHandler(handler)
    
    root_logger.setLevel(logging.DEBUG)
    
    if USE_COLOR_LOG:
        logger = colorlog.getLogger('zsans.main')
        logger.setLevel(logging.DEBUG)
        
        handler = colorlog.StreamHandler()
        handler.setLevel(logging.INFO)
        handler.setFormatter(colorlog.ColoredFormatter(
            '%(log_color)s%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            log_colors={
                'DEBUG': 'cyan',
                'INFO': 'green',
                'WARNING': 'yellow',
                'ERROR': 'red',
                'CRITICAL': 'bold_red',
            }
        ))
        
        file_handler = logging.FileHandler('zsans.log', encoding='utf-8')
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
        
        root_logger.addHandler(handler)
        root_logger.addHandler(file_handler)
    else:
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.INFO)
        console_handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
        
        file_handler = logging.FileHandler('zsans.log', encoding='utf-8')
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
        
        root_logger.addHandler(console_handler)
        root_logger.addHandler(file_handler)
        logger = logging.getLogger('zsans.main')
    
    return logger

logger = configure_logging()

VERSION = "0.0.7"
DEFAULT_CONFIG_PATH = "breeding-config.yaml"
PLUGINS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'plugins')

# 模块级 CLI 冲突缓冲:插件同名参数扫描期写入,load_plugin_dir 时并入 engine.plugin_conflicts。
_CLI_CONFLICTS = []

from core.zsans_engine import (
    Asset, DomainAsset, IPAsset, URLAsset, PortAsset, JSAsset,
    ASSET_TYPE_DOMAIN, ASSET_TYPE_IP, ASSET_TYPE_URL, ASSET_TYPE_PORT, ASSET_TYPE_JS,
    AssetFactory, AssetGraph, PriorityBreedingQueue
)
from core.breeders.breeders import BreederFactory
from core.tools.tools import ToolOrchestrator
from core.output import OutputHandler


class BreedingEngine:
    def __init__(self, config=None, register_signals=True):
        self.config = config or {}
        self.register_signals = register_signals
        self.asset_graph = AssetGraph()
        self.queue = PriorityBreedingQueue(self.config)
        self.state = "initialized"
        self.start_time = None
        self.metrics = {
            "assets_processed": 0,
            "new_assets_found": 0,
            "depth_reached": 0,
            "errors": 0
        }
        self._metrics_lock = threading.Lock()
        self._finalized = False
        self._executor = None
        self._hooks = {}
        self.plugins = {}
        self._stop_requested = False
        
        plugin_cfg = self.config.get('plugins', {}) if isinstance(self.config.get('plugins', {}), dict) else {}
        self.plugin_dir = plugin_cfg.get('dir') or PLUGINS_DIR
        load_plugin_dir(self, self.plugin_dir, plugin_cfg.get('disabled', []) or [])
        
        self.seed_domains = set()
        self.seed_ips = set()
        self.seed_ip_ranges = set()
        
        self.tool_orchestrator = ToolOrchestrator(self.config, engine=self)
        self.breeder_factory = BreederFactory()
        self.output_handler = OutputHandler(self, self.config.get("output", {}))
        if register_signals:
            self._setup_signal_handlers()
        else:
            # web/后台线程模式下不注册信号处理器（signal.signal 仅主线程可用）
            self._signal_handlers_setup = False
        
        # 初始化HTTP配置和全局会话
        from core.zsans_engine import init_http_config
        init_http_config(self.config)
        
        logger.info(_("Z-Sans Asset Breeding Engine v{VERSION} initialized").format(VERSION=VERSION))
    
    def _setup_signal_handlers(self):
        signal.signal(signal.SIGINT, self._handle_signal)
        signal.signal(signal.SIGTERM, self._handle_signal)
    
    def _handle_signal(self, signum, frame):
        global _stop_signaled
        if signum in (signal.SIGINT, signal.SIGTERM):
            _stop_signaled = True
            self._stop_requested = True
            self.state = "stopped"
            logger.info(_("Received stop signal, shutting down gracefully..."))
            if self._executor is not None:
                try:
                    self._executor.shutdown(wait=False, cancel_futures=True)
                except Exception:
                    pass

    
    def register_hook(self, event, handler):
        self._hooks.setdefault(event, []).append(handler)

    def emit_hook(self, event, **kwargs):
        for handler in list(self._hooks.get(event, [])):
            try:
                handler(**kwargs)
            except Exception as e:
                logger.error(_("Event hook {event} failed: {error}").format(event=event, error=str(e)))

    def get_export_metadata(self):
        import hashlib
        try:
            cfg_hash = hashlib.sha256(
                json.dumps(self.config, sort_keys=True, default=str, ensure_ascii=False).encode('utf-8')
            ).hexdigest()
        except Exception:
            cfg_hash = None
        return {
            "schema_version": 2,
            "zs_version": VERSION,
            "stats": {
                "nodes": len(self.asset_graph.nodes),
                "edges": len(self.asset_graph.edges),
            },
            "metrics": dict(self.metrics),
            "seeds": {
                "domains": sorted(self.seed_domains),
                "ips": sorted(self.seed_ips),
                "ip_ranges": sorted(self.seed_ip_ranges),
            },
            "config_hash": cfg_hash,
        }

    def add_seed(self, asset_type, value):
        asset = AssetFactory.create_asset(value, asset_type)
        if self.asset_graph.add_asset(asset):
            self.queue.add(asset)
            logger.info(_("Added seed asset: {uid}").format(uid=asset.uid))
            
            if asset_type == ASSET_TYPE_DOMAIN:
                self.seed_domains.add(value)
                parts = value.split('.')
                if len(parts) >= 2:
                    tld = '.'.join(parts[-2:])
                    self.seed_domains.add(tld)
            elif asset_type == ASSET_TYPE_URL:
                try:
                    from urllib.parse import urlparse
                    normalized_value = value
                    if not normalized_value.startswith(('http://', 'https://')):
                        normalized_value = f'https://{normalized_value}'
                    parsed_url = urlparse(normalized_value)
                    # 用 hostname 而非 netloc：netloc 含端口(如 example.com:8443)，
                    # 会把带端口的字符串塞进 seed_domains，导致相关性判断全错。
                    domain = parsed_url.hostname
                    if domain:
                        domain = domain.lower()
                        self.seed_domains.add(domain)
                        parts = domain.split('.')
                        if len(parts) >= 2:
                            tld = '.'.join(parts[-2:])
                            self.seed_domains.add(tld)
                        logger.info(_("Extracted domain from URL seed: {domain}").format(domain=domain))
                except Exception as e:
                    logger.error(_("Error extracting domain from URL: {error}").format(error=str(e)))
            elif asset_type == ASSET_TYPE_IP:
                self.seed_ips.add(value)
                try:
                    ip_parts = value.split('.')
                    if len(ip_parts) >= 2:
                        ip_range = f"{ip_parts[0]}.{ip_parts[1]}.0.0"
                        self.seed_ip_ranges.add(ip_range)
                except Exception as e:
                    logger.error(_("Error recording IP range: {error}").format(error=str(e)))
            
            if self.seed_domains:
                logger.info(_("Current seed domain list: {domains}").format(domains=', '.join(self.seed_domains)))
            else:
                logger.warning(_("Seed domain list is empty, this may cause domain relevance judgment errors"))
                
            return True
        return False
    
    def start(self):
        if self.state != "initialized" and self.state != "paused":
            logger.warning(_("Engine is in state {state}, cannot start").format(state=self.state))
            return False
        
        self.state = "running"
        self.start_time = time.time()
        logger.info(_("Breeding engine started"))
        if hasattr(self.output_handler, 'ensure_run_dir'):
            self.output_handler.ensure_run_dir()
        self.emit_hook("on_scan_started", engine=self)
        return True
    
    def stop(self):
        if self._finalized:
            return
        self._finalized = True
        self._stop_requested = True
        # 注意：这里不能设置全局 _stop_signaled。
        # watch 模式每轮循环结束后 run() 的 finally 都会调用 stop()，
        # 若在此置位会导致 run_watch 首轮后误判为"收到信号"而直接退出。
        # 真正的停止信号由 _handle_signal 负责设置。
        self.state = "stopped"
        if self._executor is not None:
            try:
                self._executor.shutdown(wait=False, cancel_futures=True)
            except Exception:
                pass
        
        self.emit_hook("on_scan_stopped", engine=self, finalize=True)
        
        try:
            self.save_checkpoint()
            logger.info(_("Saving scan results before stopping engine..."))
            output_files = self.output_handler.generate_output()
            logger.info(_("Scan results saved: {files}").format(files=output_files))
        except Exception as e:
            logger.error(_("Error saving scan results: {error}").format(error=str(e)))
        
        if hasattr(self, 'tool_orchestrator'):
            self.tool_orchestrator.shutdown()
        logger.info(_("Breeding engine stopped"))
    
    def pause(self):
        if self.state == "running":
            self.state = "paused"
            logger.info(_("Breeding engine paused"))
            return True
        return False
    
    def resume(self):
        if self.state == "paused":
            self.state = "running"
            logger.info(_("Breeding engine resumed"))
            return True
        return False
    
    def auto_breeding_cycle(self):
        if self.state != "running":
            return False
        
        if self.queue.is_empty():
            logger.info(_("Breeding queue is empty, breeding cycle completed"))
            self.state = "completed"
            return False
        
        asset = self.queue.get_next(self.config.get("strategy", "priority_based"))
        if not asset:
            return False
        
        return self._process_asset(asset)
    
    def _process_asset(self, asset):
        max_depth = self.config.get("max_depth", 3)
        if asset.depth > max_depth:
            asset.state = "excluded"
            asset.properties["excluded_reason"] = _("Exceeds max depth {depth}").format(depth=max_depth)
            logger.debug(_("Asset {uid} exceeds max depth {depth}, skipping").format(uid=asset.uid, depth=max_depth))
            self.emit_hook("on_asset_excluded", asset=asset)
            return True
        
        # 尊重 asset_types.<type>.enabled 开关（此前为死配置，从不生效）
        asset_type_cfg = self.config.get('asset_types', {}).get(asset.type, {})
        if not asset_type_cfg.get('enabled', True):
            asset.state = "excluded"
            asset.properties["excluded_reason"] = _("Asset type {type} is disabled").format(type=asset.type)
            logger.debug(_("Asset type {type} disabled, skipping {uid}").format(type=asset.type, uid=asset.uid))
            self.emit_hook("on_asset_excluded", asset=asset)
            return True
        
        asset.state = "scanning"
        
        if not self._check_resource_limits(asset):
            logger.warning(_("Asset {uid} exceeds resource limits, skipping").format(uid=asset.uid))
            asset.state = "excluded"
            self.emit_hook("on_asset_excluded", asset=asset)
            return True
        
        if self._is_excluded(asset):
            logger.debug(_("Asset {uid} matches exclusion rules, skipping").format(uid=asset.uid))
            asset.state = "excluded"
            asset.properties["excluded_reason"] = _("Matches exclusion rules")
            self.emit_hook("on_asset_excluded", asset=asset)
            return True
        
        breeder = self.breeder_factory.get_breeder(asset.type, self.config, self)
        if not breeder:
            logger.warning(_("No suitable breeder found for asset type {type}, skipping").format(type=asset.type))
            asset.state = "excluded"
            asset.properties["excluded_reason"] = _("No breeder for type {type}").format(type=asset.type)
            self.emit_hook("on_asset_excluded", asset=asset)
            return True
        
        try:
            logger.debug(_("Start processing asset: {uid}").format(uid=asset.uid))
            new_assets = breeder.execute(asset, self.tool_orchestrator)
            
            with self._metrics_lock:
                self.metrics["assets_processed"] += 1
                self.metrics["new_assets_found"] += len(new_assets)
                self.metrics["depth_reached"] = max(self.metrics["depth_reached"], asset.depth)
            
            if asset.state != "eliminated":
                asset.state = "scanned"
            else:
                logger.debug(_("Maintaining asset {uid} elimination status").format(uid=asset.uid))
                self.emit_hook("on_asset_eliminated", asset=asset)
            
            for new_asset in new_assets:
                # 资产首次进入图谱或虽已存在但可重新处理时，记录"发现"边并派发事件。
                # 注意：边与事件不应依赖 queue.add 的返回值——同一资产被重复发现时
                # 仍会走 add_asset=True / queue.add=False，此时边和事件同样要记录，
                # 否则拓扑缺边、on_asset_discovered 丢失、统计虚高。
                if self.asset_graph.add_asset(new_asset):
                    self.asset_graph.add_edge(asset, new_asset, "discovered")
                    self.emit_hook("on_asset_discovered", asset=new_asset, source=asset)
                    self.queue.add(new_asset)
            
            self.emit_hook("on_asset_scanned", asset=asset, new_assets=list(new_assets))
            
            cp_cfg = self.config.get('checkpoint', {})
            if cp_cfg.get('enabled', True) and (self.metrics["assets_processed"] % max(1, cp_cfg.get('interval', 50))) == 0:
                self.save_checkpoint()
            
            logger.info(_("Asset {uid} processed, found {count} new assets").format(uid=asset.uid, count=len(new_assets)))
            return True
        
        except Exception as e:
            logger.error(_("Error processing asset {uid}: {error}").format(uid=asset.uid, error=str(e)))
            import traceback
            if logger.isEnabledFor(logging.DEBUG):
                logger.debug("Full traceback while processing asset %s:\n%s",
                             asset.uid, traceback.format_exc())
            asset.state = "failed"
            with self._metrics_lock:
                self.metrics["errors"] += 1
            self.emit_hook("on_asset_failed", asset=asset, error=str(e))
            return True
    
    def _concurrent_breed(self):
        workers = max(1, self.config.get("concurrency", {}).get("max_tasks", 1))
        strategy = self.config.get("strategy", "priority_based")
        in_flight = 0
        futures = set()
        
        executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="zsans")
        self._executor = executor
        try:
            while self.state == "running" and not self._stop_requested:
                while self.state == "running" and not self._stop_requested and in_flight < workers and not self.queue.is_empty():
                    asset = self.queue.get_next(strategy)
                    if not asset:
                        break
                    in_flight += 1
                    futures.add(executor.submit(self._process_asset, asset))
                
                if in_flight == 0:
                    break
                
                try:
                    # 消费所有已完成的 future（而非只取 1 个），避免大量任务
                    # 同时完成时因每次 break 被迫逐个轮询而拖慢吞吐。
                    completed_any = False
                    for future in as_completed(futures, timeout=1.0):
                        futures.discard(future)
                        in_flight -= 1
                        completed_any = True
                        if self._stop_requested or self.state != "running":
                            break
                    if not completed_any and not futures:
                        break
                except (concurrent.futures.TimeoutError, TimeoutError):
                    pass
                
                if self._stop_requested or self.state != "running":
                    break
            
            if self.state == "running" and not self._stop_requested and in_flight > 0:
                for future in as_completed(futures):
                    futures.discard(future)
                    in_flight -= 1
        finally:
            self._executor = None
            executor.shutdown(wait=False, cancel_futures=True)
        
        if self.state == "running" and not self._stop_requested:
            self.state = "completed"
    
    def _check_resource_limits(self, asset):
        asset_type_config = self.config.get("asset_types", {}).get(asset.type, {})
        depth_limit = asset_type_config.get("depth_limit", self.config.get("max_depth", 3))
        if asset.depth > depth_limit:
            asset.properties["excluded_reason"] = _("Exceeds depth limit {limit}").format(limit=depth_limit)
            return False
        
        stats = self.asset_graph.stats()
        asset_types = stats.get("asset_types", {})
        
        if asset.type == ASSET_TYPE_DOMAIN:
            limit = self.config.get("resource_limits", {}).get("max_domains", 1000)
            current = asset_types.get(ASSET_TYPE_DOMAIN, 0)
        elif asset.type == ASSET_TYPE_IP:
            limit = self.config.get("resource_limits", {}).get("max_ips", 1000)
            current = asset_types.get(ASSET_TYPE_IP, 0)
        elif asset.type == ASSET_TYPE_URL:
            limit = self.config.get("resource_limits", {}).get("max_urls", 5000)
            current = asset_types.get(ASSET_TYPE_URL, 0)
        elif asset.type == ASSET_TYPE_PORT:
            limit = self.config.get("resource_limits", {}).get("max_ports", 2000)
            current = asset_types.get(ASSET_TYPE_PORT, 0)
        elif asset.type == ASSET_TYPE_JS:
            limit = self.config.get("resource_limits", {}).get("max_js", 1000)
            current = asset_types.get(ASSET_TYPE_JS, 0)
        else:
            return True
        
        if current >= limit:
            asset.properties["excluded_reason"] = _("Exceeds {type} limit {limit}").format(type=asset.type, limit=limit)
            return False
        return True
    
    def _is_excluded(self, asset):
        exclusions = self.config.get("exclusions", {})
        
        if asset.type == ASSET_TYPE_DOMAIN:
            excluded_domains = exclusions.get("domains", [])
            for excluded in excluded_domains:
                if asset.value == excluded or asset.value.endswith(f".{excluded}"):
                    return True
        
        elif asset.type == ASSET_TYPE_IP:
            excluded_ips = exclusions.get("ips", [])
            for excluded in excluded_ips:
                if asset.value == excluded:
                    return True
        
        elif asset.type == ASSET_TYPE_URL:
            excluded_urls = exclusions.get("urls", [])
            for excluded in excluded_urls:
                if excluded in asset.value:
                    return True
        
        excluded_patterns = exclusions.get("patterns", [])
        for pattern in excluded_patterns:
            import re
            if re.search(pattern, asset.value):
                return True
        
        return False
    
    # ---------- Checkpoint / resume ----------
    def _checkpoint_path(self):
        cfg = self.config.get('checkpoint', {})
        if cfg.get('file'):
            return cfg['file']
        outdir = self.config.get('output', {}).get('dir', 'output')
        return os.path.join(outdir, 'checkpoint.json')
    
    @staticmethod
    def _rebuild_asset(data):
        atype = data.get('type')
        value = data.get('value')
        source = data.get('source', 'manual')
        depth = data.get('depth', 0)
        props = data.get('properties', {})
        
        if atype == ASSET_TYPE_PORT:
            ip = props.get('ip') or (value.rsplit(':', 1)[0] if ':' in value else value)
            asset = PortAsset(ip, props.get('port'), props.get('service'), source, depth)
        elif atype == ASSET_TYPE_DOMAIN:
            asset = DomainAsset(value, source, depth)
        elif atype == ASSET_TYPE_IP:
            asset = IPAsset(value, source, depth)
        elif atype == ASSET_TYPE_URL:
            asset = URLAsset(value, source, depth)
        elif atype == ASSET_TYPE_JS:
            asset = JSAsset(value, source, depth)
        else:
            asset = Asset(value, atype, source, depth)
        asset.state = data.get('state', 'new')
        asset.properties = dict(props)
        return asset
    
    def save_checkpoint(self, path=None):
        if not self.config.get('checkpoint', {}).get('enabled', True):
            return None
        path = path or self._checkpoint_path()
        try:
            os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
            with self.asset_graph.lock, self.queue.lock:
                data = {
                    'version': 1,
                    'saved_at': time.time(),
                    'seed_domains': sorted(self.seed_domains),
                    'seed_ips': sorted(self.seed_ips),
                    'seed_ip_ranges': sorted(self.seed_ip_ranges),
                    'metrics': dict(self.metrics),
                    'nodes': [a.to_dict() for a in self.asset_graph.nodes.values()],
                    'edges': [[s, t, r] for (s, t), r in self.asset_graph.edges.items()],
                    'queue': [a.to_dict() for a in self.queue.queue],
                }
            tmp = path + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as fq:
                json.dump(data, fq, ensure_ascii=False, indent=2)
                fq.flush()
                os.fsync(fq.fileno())
            os.replace(tmp, path)
            logger.debug(_("Checkpoint saved: {path}").format(path=path))
            return path
        except Exception as e:
            logger.error(_("Failed to save checkpoint {path}: {error}").format(path=path, error=str(e)))
            return None
    
    def load_checkpoint(self, path=None):
        path = path or self._checkpoint_path()
        if not os.path.exists(path):
            logger.warning(_("Checkpoint file not found: {path}").format(path=path))
            return False
        try:
            with open(path, 'r', encoding='utf-8') as fq:
                data = json.load(fq)
            self.seed_domains = set(data.get('seed_domains', []))
            self.seed_ips = set(data.get('seed_ips', []))
            self.seed_ip_ranges = set(data.get('seed_ip_ranges', []))
            self.metrics.update(data.get('metrics', {}))
            self.queue.queue = []
            self.queue._queued = {}
            for nd in data.get('nodes', []):
                self.asset_graph.add_asset(self._rebuild_asset(nd))
            with self.asset_graph.lock:
                for s, t, r in data.get('edges', []):
                    self.asset_graph.edges[(s, t)] = r
            for nd in data.get('queue', []):
                asset = self._rebuild_asset(nd)
                if asset.state in ('new', 'failed', 'scanning'):
                    self.queue.add(asset)
            logger.info(_("Checkpoint loaded: {path}, {nodes} nodes, {queue} queued").format(
                path=path, nodes=len(self.asset_graph.nodes), queue=self.queue.size()))
            return True
        except Exception as e:
            logger.error(_("Failed to load checkpoint {path}: {error}").format(path=path, error=str(e)))
            return False
    
    def run(self):
        if not self.start():
            return False
        
        try:
            self._concurrent_breed()
            
            if self.state == "completed":
                logger.info(_("Breeding engine completed all tasks, generating output..."))
                self.emit_hook("on_scan_completed", engine=self)
            return True
        
        except KeyboardInterrupt:
            logger.info(_("Received user interrupt, stopping..."))
            self.stop()
            return False
        
        except Exception as e:
            logger.error(_("Error running breeding engine: {error}").format(error=str(e)))
            self.stop()
            return False
        
        finally:
            self.stop()


def _deep_merge(base, override):
    """递归合并 override 到 base（dict 深合并，列表/标量直接覆盖）。

    修复浅层合并导致的部分配置覆盖会清空整棵默认子树的问题，
    例如用户只写 `asset_types: {domain: {priority: 5}}` 时，
    domain 下的 tools/depth_limit/enabled 等默认值得以保留。
    """
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            base[k] = _deep_merge(base[k], v)
        else:
            base[k] = v
    return base


def load_config(config_path):
    from core.zsans_engine import DEFAULT_CONFIG
    
    if not os.path.exists(config_path):
        logger.warning(_("Config file {path} does not exist, using default configuration").format(path=config_path))
        return DEFAULT_CONFIG
    
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f)
        
        merged_config = copy.deepcopy(DEFAULT_CONFIG)
        for key, value in config.items():
            if isinstance(value, dict) and key in merged_config and isinstance(merged_config[key], dict):
                merged_config[key] = _deep_merge(merged_config[key], value)
            else:
                merged_config[key] = value
        
        logger.debug(_("Loaded config file: {path}").format(path=config_path))
        return merged_config
    
    except Exception as e:
        logger.error(_("Failed to load config file {path}: {error}").format(path=config_path, error=str(e)))
        return DEFAULT_CONFIG


def create_default_config(config_path=DEFAULT_CONFIG_PATH):
    from core.zsans_engine import DEFAULT_CONFIG
    
    if os.path.exists(config_path):
        logger.warning(_("Config file {path} already exists, skipping creation").format(path=config_path))
        return False
    
    try:
        # 优先复制带完整注释的模板文件，避免 yaml.dump 丢失所有注释
        template_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'templates', 'breeding-config.yaml')
        if os.path.exists(template_path):
            with open(template_path, 'r', encoding='utf-8') as src, \
                 open(config_path, 'w', encoding='utf-8') as dst:
                dst.write(src.read())
            logger.info(_("Created default config file from template: {path}").format(path=config_path))
        else:
            with open(config_path, 'w', encoding='utf-8') as f:
                yaml.dump(DEFAULT_CONFIG, f, default_flow_style=False, sort_keys=False)
            logger.info(_("Created default config file: {path}").format(path=config_path))
        
        return True
    
    except Exception as e:
        logger.error(_("Failed to create default config file: {error}").format(error=str(e)))
        return False


def _plugin_dir_entry(folder, name):
    """目录插件入口文件:优先 <目录名>.py,其次 plugin.py/main.py/__init__.py。"""
    first = os.path.join(folder, name + ".py")
    if os.path.isfile(first):
        return first
    for cand in ("plugin.py", "main.py", "__init__.py"):
        p = os.path.join(folder, cand)
        if os.path.isfile(p):
            return p
    return None


def _iter_plugin_entries(plugins_dir):
    """枚举插件目录下所有插件(单文件 .py 与目录插件),按名有序。

    Yields (name, entry_path, kind), kind ∈ {'file','dir'}。
    目录插件必须含入口文件(plugin.py/main.py/<目录名>.py/__init__.py);
    无入口文件的普通资源目录会被跳过,不算作插件。
    """
    for name in sorted(os.listdir(plugins_dir)):
        path = os.path.join(plugins_dir, name)
        if name.endswith('.py'):
            if name == '__init__.py':
                continue
            yield os.path.splitext(name)[0], path, 'file'
        elif os.path.isdir(path):
            entry = _plugin_dir_entry(path, name)
            if entry is not None:
                yield name, entry, 'dir'


def _resolve_winners(plugins_dir, disabled=None):
    """按插件名去重;同名(如 foo.py 与目录 foo/)只保留排序首个为胜者。

    Returns (winners, conflicts)：
      winners:   [(name, entry_path, kind)] 数组,不含被禁止的插件;
      conflicts: [{"name","reason","winner","loser"}] 列表,reason=duplicate_source。
    """
    disabled = set(disabled or [])
    winners = []
    conflicts = []
    seen = {}
    for name, entry, kind in _iter_plugin_entries(plugins_dir):
        if name in disabled:
            continue
        if name in seen:
            conflicts.append({
                "name": name,
                "reason": "duplicate_source",
                "winner": seen[name],
                "loser": entry,
            })
            logger.warning(_("Plugin name conflict: {name} from {a} and {b}, keeping {a}").format(
                name=name, a=seen[name], b=entry))
            continue
        seen[name] = entry
        winners.append((name, entry, kind))
    return winners, conflicts


def _read_plugin_doc(folder):
    """返回插件说明文档(README/说明.md)摘要,无则空字符串。"""
    for cand in ("README_CN.md", "README.md", "readme.md", "说明.md", "INFO.md"):
        p = os.path.join(folder, cand)
        if not os.path.isfile(p):
            continue
        try:
            with open(p, 'r', encoding='utf-8', errors='replace') as f:
                lines = [ln.rstrip() for ln in f if ln.strip()]
            return "\n".join(lines[:8])
        except Exception:
            return ""
    return ""


def _plugin_cli_modules(config_path):
    """扫描已启用插件目录,收集实现了 register_cli(parser) 的插件模块。

    仅在插件被加载(位于 plugins.dir 且未被禁用、且非冲突胜者)时才返回,
    加载规则与 on_scan_started 等钩子一致:插件启用则增加 CLI 参数。
    """
    pdir = PLUGINS_DIR
    disabled = set()
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            cfg = yaml.safe_load(f.read()) or {}
        plugins_cfg = cfg.get('plugins') or {}
        if isinstance(plugins_cfg, dict):
            pdir = plugins_cfg.get('dir') or PLUGINS_DIR
            disabled = set(plugins_cfg.get('disabled') or [])
    except OSError:
        pass
    except Exception:
        pass
    if not os.path.isdir(pdir):
        return []
    items = []
    try:
        winners, _conflicts = _resolve_winners(pdir, disabled)
    except Exception as e:
        logger.debug("解析插件冲突失败: %s", e)
        winners = []
    taken = {}   # 已入选插件名 -> (声明名, 互斥模式)
    for name, entry, kind in winners:
        try:
            module = _import_plugin(entry, module_name="zsans_plugin_{0}".format(name))
            if not callable(getattr(module, 'register_cli', None)):
                continue
            declared = str((getattr(module, '__manifest__', None) or getattr(module, '__plugin__', None) or {}).get('name') or name)
            conflicts = _plugin_conflict_patterns(module)
            if any(declared == prev_declared for prev_declared, _ in taken.values()):
                continue
            mutual = None
            for prev_declared, (_, prev_conflicts) in taken.items():
                if _matches_name(prev_declared, conflicts) or _matches_name(declared, prev_conflicts):
                    mutual = prev_declared
                    break
            if mutual is not None:
                continue
            taken[declared] = (declared, conflicts)
            items.append((name, module))
        except Exception as e:
            logger.debug("收集插件 CLI 失败 %s: %s", name, e)
    return items


def _apply_plugin_cli(parser, config_path):
    """把已启用插件声明的 CLI 参数注册到主解析器。

    同名参数被多个插件注册时(如 toolbox 与 tools_manager 都提供 --tools),
    先注册者生效,后注册者让位;冲突经模块级 _CLI_CONFLICTS 缓冲,最终并入
    engine.plugin_conflicts,在 --list-plugins 中可见。
    """
    global _CLI_CONFLICTS
    _CLI_CONFLICTS = []
    owners = {}   # option_string -> 注册者("core" 或插件名)
    for opt in (parser._option_string_actions or {}):
        owners[opt] = "(core)"

    original_add = parser.add_argument
    current_plugin = "(core)"

    def tracked_add(*args, **kwargs):
        opts = [a for a in args if isinstance(a, str) and a.startswith('-')]
        try:
            action = original_add(*args, **kwargs)
        except Exception:
            for o in opts:
                if o in owners and owners[o] != current_plugin:
                    _CLI_CONFLICTS.append({
                        "name": current_plugin,
                        "reason": "cli_option_conflict",
                        "winner": owners[o],
                        "loser": o,
                    })
            raise
        for o in opts:
            owners.setdefault(o, current_plugin)
        return action

    parser.add_argument = tracked_add
    for pname, module in _plugin_cli_modules(config_path):
        current_plugin = pname
        try:
            module.register_cli(parser)
        except Exception as e:
            logger.debug("插件 %s 注册 CLI 失败: %s", pname, e)
    parser.add_argument = original_add


def _import_plugin(path_or_module, module_name=None):
    """Import a plugin module from a file path or a dotted module name.

    从文件路径导入时,把插件所在目录前置进 sys.path,使目录插件可以
    import 同目录下的辅助模块/配置/资源文件。
    """
    import importlib
    import importlib.util
    if path_or_module.endswith('.py'):
        if module_name is None:
            module_name = "zsans_plugin_" + os.path.splitext(os.path.basename(path_or_module))[0]
        folder = os.path.dirname(os.path.abspath(path_or_module))
        if folder not in sys.path:
            sys.path.insert(0, folder)
        file_spec = importlib.util.spec_from_file_location(module_name, path_or_module)
        module = importlib.util.module_from_spec(file_spec)
        file_spec.loader.exec_module(module)
        return module
    return importlib.import_module(path_or_module)


def _plugin_conflict_patterns(module):
    """从模块 manifest 读取互斥声明:conflicts / conflict_with / PLUGIN_CONFLICTS。

    返回由插件名或 `*` 通配模式组成的列表(任意一个匹配即互斥)。
    """
    patterns = []
    for attr in ("__plugin__", "__manifest__"):
        meta = getattr(module, attr, None)
        if isinstance(meta, dict):
            for key in ("conflicts", "conflict_with", "incompatible"):
                raw = meta.get(key)
                if raw is None:
                    continue
                if isinstance(raw, (list, tuple)):
                    patterns.extend(str(x).strip() for x in raw if str(x).strip())
                elif isinstance(raw, str):
                    patterns.extend(p.strip() for p in raw.split(",") if p.strip())
    raw = getattr(module, "PLUGIN_CONFLICTS", None)
    if isinstance(raw, (list, tuple)):
        patterns.extend(str(x).strip() for x in raw if str(x).strip())
    elif isinstance(raw, str):
        patterns.extend(p.strip() for p in raw.split(",") if p.strip())
    # 去重保序
    seen = set()
    return [p for p in patterns if not (p in seen or seen.add(p))]


def _matches_name(name, patterns):
    """插件名是否命中互斥模式列表;支持 fnmatch 通配(*, ?)。"""
    if not patterns:
        return False
    for pattern in patterns:
        if fnmatch.fnmatchcase(name, pattern):
            return True
    return False


def _plugin_meta(module, fallback_name):
    """Read plugin manifest: __plugin__ or __manifest__ dict, else PLUGIN_NAME/_VERSION/etc.

    额外读取互斥声明(conflicts)并返回。
    """
    meta = getattr(module, '__plugin__', None)
    if meta is None:
        meta = getattr(module, '__manifest__', None)
    if isinstance(meta, dict):
        return {
            "name": str(meta.get('name') or fallback_name),
            "version": str(meta.get('version') or '0.0.0'),
            "description": str(meta.get('description') or ''),
            "author": str(meta.get('author') or ''),
            "webui": str(meta.get('webui') or ''),
            "schema": meta.get('schema'),
        }
    return {
        "name": str(getattr(module, 'PLUGIN_NAME', fallback_name)),
        "version": str(getattr(module, 'PLUGIN_VERSION', '0.0.0')),
        "description": str(getattr(module, 'PLUGIN_DESCRIPTION', '')),
        "author": str(getattr(module, 'PLUGIN_AUTHOR', '')),
        "webui": str(getattr(module, 'PLUGIN_WEBUI', '')),
        "schema": getattr(module, 'PLUGIN_SCHEMA', None),
    }


def _scan_hook_names(module):
    """返回模块中全部 on_* 钩子名称(已排序)。"""
    return sorted(name for name in dir(module)
                  if name.startswith('on_') and callable(getattr(module, name)))


def _register_events(engine, module, events, source):
    """把 events 中的钩子挂载到引擎,并输出日志。"""
    for name in events:
        engine.register_hook(name, getattr(module, name))
    if events:
        logger.info(_("Registered {count} hook handlers from {source}").format(count=len(events), source=source))
    else:
        logger.warning(_("No on_* hook handlers found in {source}").format(source=source))
    return events


def _register_hook_handlers(engine, module, source):
    """兼容入口:扫描并注册模块的全部 on_* 钩子。"""
    return _register_events(engine, module, _scan_hook_names(module), source)


def _prepare_plugin(entry_path, fallback_name, kind='file'):
    """导入插件模块并构建信息(不挂载钩子),供冲突判定后决定是否挂载。

    目录插件额外携带插件目录(信息 files)与说明文档摘要(doc)。
    返回 (info, events);info["module"] 为 None 表示导入/校验失败。
    """
    info = {
        "name": fallback_name,
        "version": "0.0.0",
        "description": "",
        "author": "",
        "handlers": 0,
        "events": [],
        "path": entry_path,
        "kind": kind,
        "status": "loaded",
        "error": None,
        "module": None,
        "conflicts": [],
        "webui": "",
        "schema": None,
    }
    if kind == 'dir':
        folder = os.path.dirname(entry_path)
        info["path"] = folder
        info["entry"] = entry_path
        info["files"] = [f for f in sorted(os.listdir(folder))
                         if not f.startswith(('__', '.')) and not f.endswith('.pyc')]
        # 目录插件自动探测前端资源: webui.html 或 web/index.html
        for cand in ("webui.html", "webui.htm", os.path.join("web", "index.html"), os.path.join("web", "index.htm")):
            if os.path.isfile(os.path.join(folder, cand)):
                info.setdefault("webui", cand)
                break
        info["doc"] = _read_plugin_doc(folder)
        module_name = "zsans_plugin_" + os.path.basename(folder)
    else:
        module_name = None
    try:
        module = _import_plugin(entry_path, module_name=module_name)
        info.update(_plugin_meta(module, fallback_name))
        info["conflicts"] = _plugin_conflict_patterns(module)
        events = _scan_hook_names(module)
        info["module"] = module
        info["handlers"] = len(events)
        info["events"] = sorted(events)
        return info, events
    except Exception as e:
        logger.error(_("Failed to load plugin {file}: {error}").format(file=entry_path, error=str(e)))
        info.update(status="failed", error=str(e))
        return info, []


def load_plugin_file(engine, file_path):
    """Load a single plugin file（.py）并注册其 on_* 钩子。

    Returns a plugin info dict (name/version/description/author/handlers/...)。
    """
    fallback_name = os.path.splitext(os.path.basename(file_path))[0]
    info, events = _prepare_plugin(file_path, fallback_name, 'file')
    if info["module"] is not None:
        _register_events(engine, info["module"], events, file_path)
    return info


def load_plugin_dir(engine, plugins_dir, disabled=None):
    """Auto-load plugins under plugins_dir（单文件与目录插件均支持）。

    - 单文件插件 foo.py 与目录插件 foo/ 都按“插件名”参与 disabled 名单。
      目录插件可把辅助模块、配置、说明文档(README/说明.md)放在目录内,
      import 时该目录会前置进 sys.path,插件内可自由 import 同目录资源。
    - 冲突按插件名识别:同名仅首个胜出(胜者挂载钩子/注册 CLI),其余写入
      engine.plugin_conflicts 并跳过。
    """
    loaded = []
    engine.plugin_conflicts = []
    if not os.path.isdir(plugins_dir):
        logger.warning(_("Plugins directory not found: {dir}").format(dir=plugins_dir))
        return loaded
    disabled = set(disabled or [])

    for name, entry, kind in _iter_plugin_entries(plugins_dir):
        if name not in disabled:
            continue
        engine.plugins[name] = {
            "name": name,
            "version": "-",
            "description": "",
            "author": "",
            "handlers": 0,
            "events": [],
            "path": os.path.join(plugins_dir, name),
            "kind": kind,
            "status": "disabled",
            "error": None,
            "module": None,
        }
        logger.info(_("Plugin {plugin} disabled by configuration").format(plugin=name))

    winners, source_conflicts = _resolve_winners(plugins_dir, disabled)
    engine.plugin_conflicts.extend(source_conflicts)
    engine.plugin_conflicts.extend(_CLI_CONFLICTS)

    taken = {}   # 已挂载的声明名 -> (入口路径, 互斥声明)
    for name, entry, kind in winners:
        info, events = _prepare_plugin(entry, name, kind)
        if info["module"] is None:
            engine.plugins[name] = info
            continue
        declared = info["name"]
        if declared in taken:
            conflict = {
                "name": declared,
                "reason": "declared_name_conflict",
                "winner": taken[declared][0],
                "loser": entry,
            }
            engine.plugin_conflicts.append(conflict)
            logger.warning(
                _("Plugin name conflict: {cur} declares {name} already provided by {prev}, skipped").format(
                    name=declared, cur=entry, prev=taken[declared][0]))
            continue
        conflicts = info.get("conflicts") or []
        mutual = None   # 命中互斥的已挂载插件名
        for loaded_name, (loaded_path, loaded_conflicts) in taken.items():
            if _matches_name(loaded_name, conflicts) or _matches_name(declared, loaded_conflicts):
                mutual = (loaded_name, loaded_path)
                break
        if mutual is not None:
            conflict = {
                "name": declared,
                "reason": "mutual_conflict",
                "winner": mutual[1],
                "loser": entry,
            }
            engine.plugin_conflicts.append(conflict)
            logger.warning(
                _("Plugin {cur} conflicts with {prev} ({name}), skipped").format(
                    cur=entry, prev=mutual[1], name=mutual[0]))
            continue
        taken[declared] = (entry, conflicts)
        _register_events(engine, info["module"], events, entry)
        engine.plugins[declared] = info
        if info["handlers"] and info["status"] == "loaded":
            loaded.append(declared)
    return loaded


def print_plugin_info(info):
    """Print full details of a single plugin info dict.

    若插件提供了 ``plugin_help()``（返回多行文本），其返回值会追加在
    固定字段之后一并展示，便于插件安装/工具变更后帮助信息即时更新。
    """
    print()
    for key in ("name", "version", "author", "description", "handlers", "events", "kind", "path", "status", "error"):
        if key == "events":
            value = ','.join(info.get(key) or []) or '-'
        else:
            value = info.get(key) or '-'
        print("{}: {}".format(key.upper().ljust(12), value))
    conflicts = info.get("conflicts") or []
    if conflicts:
        print("CONFLICTS   {}".format(", ".join(conflicts)))
    if info.get("kind") == "dir":
        files = info.get("files") or []
        if files:
            shown = ", ".join(files[:12]) + (" ..." if len(files) > 12 else "")
            print("FILES       {}".format(shown))
        doc = (info.get("doc") or "").strip()
        if doc:
            print("DOC:")
            for line in doc.splitlines():
                print("  " + line)
    module = info.get("module")
    plugin_help = getattr(module, "plugin_help", None) if module else None
    if callable(plugin_help):
        print()
        try:
            print(plugin_help())
        except Exception as e:
            logger.debug("plugin_help() failed for %s: %s", info.get("name"), e)
    print()


def print_plugin_table(engine):
    """Print a summary of all plugins known to the engine."""
    print()
    print(_("Plugins directory: {dir}").format(dir=engine.plugin_dir))
    print()
    print("{}  {}  {}  {}  {}  {}".format("NAME".ljust(20), "VERSION".ljust(10), "HANDLERS".ljust(8), "KIND".ljust(5), "STATUS".ljust(10), "EVENTS"))
    print("-" * 78)
    for name in sorted(engine.plugins):
        info = engine.plugins[name]
        events = ','.join(e.replace('on_', '') for e in info.get('events', [])) or '-'
        print("{}  {}  {}  {}  {}  {}".format(
            info["name"][:20].ljust(20),
            info.get("version", '-')[:10].ljust(10),
            str(info.get("handlers", 0)).ljust(8),
            (info.get("kind", "file") or "file")[:5].ljust(5),
            str(info.get("status", '?')).ljust(10),
            events,
        ))
    conflicts = getattr(engine, "plugin_conflicts", None) or []
    if conflicts:
        print()
        print("CONFLICTS:")
        for c in conflicts:
            print("  * {}: {}  ↔  {} ({})".format(
                c.get("name", '-'), c.get("winner", '-'), c.get("loser", '-'), c.get("reason", '?')))
    print()


def run_watch(config, domain_seeds, url_seeds):
    mon = config.get('monitoring', {})
    interval = int(mon.get('interval', 3600))
    webhook_url = mon.get('webhook_url')
    outdir = config.get('output', {}).get('dir', 'output')
    
    prev_uids = None
    first = True
    global _stop_signaled
    _stop_signaled = False
    logger.info(_("Watch mode started, interval: {interval}s").format(interval=interval))
    
    while True:
        if _stop_signaled:
            logger.info(_("Stop signal received, exiting watch mode"))
            return 0
        cycle_start = time.time()
        logger.info(_("Watch cycle starting..."))
        engine = BreedingEngine(config)
        for domain in domain_seeds:
            engine.add_seed(ASSET_TYPE_DOMAIN, domain)
        for url in url_seeds:
            engine.add_seed(ASSET_TYPE_URL, url)
        engine.run()
        
        cur_uids = set(engine.asset_graph.nodes.keys())
        
        if prev_uids is None:
            prev_uids = cur_uids
            logger.info(_("Baseline established: {count} assets").format(count=len(cur_uids)))
            if first:
                first = False
            if interval <= 0:
                return 0
            time.sleep(interval)
            continue
        
        added = sorted(cur_uids - prev_uids)
        removed = sorted(prev_uids - cur_uids)
        prev_uids = cur_uids
        
        changes = {
            'timestamp': datetime.now().isoformat(),
            'added': [uid for uid in added],
            'removed': [uid for uid in removed],
        }
        
        if added or removed:
            logger.info(_("Changes detected: +{added} added, -{removed} removed").format(
                added=len(added), removed=len(removed)))
            try:
                os.makedirs(outdir, exist_ok=True)
                change_file = os.path.join(outdir, f"changes_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
                with open(change_file, 'w', encoding='utf-8') as fc:
                    json.dump(changes, fc, ensure_ascii=False, indent=2)
                logger.info(_("Changes saved: {path}").format(path=change_file))
            except Exception as e:
                logger.error(_("Failed to save changes file: {error}").format(error=str(e)))
            
            if webhook_url:
                try:
                    import requests
                    resp = requests.post(webhook_url, json=changes, timeout=10)
                    logger.info(_("Webhook notified, status: {status}").format(status=resp.status_code))
                except Exception as e:
                    logger.error(_("Webhook notification failed: {error}").format(error=str(e)))
        else:
            logger.info(_("No changes detected"))
        
        cycle_duration = time.time() - cycle_start
        sleep_for = max(0, interval - cycle_duration)
        logger.info(_("Sleeping {seconds}s until next cycle").format(seconds=int(sleep_for)))
        try:
            time.sleep(sleep_for)
        except KeyboardInterrupt:
            logger.info(_("Stop signal received, exiting watch mode"))
            return 0
        if _stop_signaled:
            logger.info(_("Stop signal received, exiting watch mode"))
            return 0


def main():
    banner = r"""

{cyan}  (`-')           (`-').-> (`-')  _ <-. (`-')_  (`-').-> 
{green}  ( OO).->        ( OO)_   (OO ).-/    \( OO) ) ( OO)_   
{green},(_/----.(`-')   (_)--\_)  / ,---.  ,--./ ,--/ (_)--\_)  
{green}|__,    |( OO).->/    _ /  | \ /`.\ |   \ |  | /    _ /  
 (_/   /(,------.\_..`--.  '-'|_.' ||  . '|  |)\_..`--.  
 .'  .'_ `------'.-._)   \(|  .-.  ||  |\    | .-._)   \ 
{blue}|       |        \       / |  | |  ||  | \   | \       / 
{blue}`-------'         `-----'  `--' `--'`--'  `--'  `-----'  

        {green}Z-Sans{reset}
        {blue}Version: v{VERSION}{reset}
        {cyan}GitHub: https://github.com/sansjtw1/Z-Sans{reset}
        {magenta}Gitee: https://gitee.com/sansjtw/Z-Sans{reset}

    """.format(
        VERSION=VERSION,
        green=colorama.Fore.GREEN if USE_COLORAMA else '\033[92m',
        blue=colorama.Fore.BLUE if USE_COLORAMA else '\033[94m',
        cyan=colorama.Fore.CYAN if USE_COLORAMA else '\033[96m',
        magenta=colorama.Fore.MAGENTA if USE_COLORAMA else '\033[95m',
        reset=colorama.Style.RESET_ALL if USE_COLORAMA else '\033[0m'
    )
    print(banner)
    time.sleep(1.5)  

    pre_parser = argparse.ArgumentParser(add_help=False)
    pre_parser.add_argument("-c", "--config", default=DEFAULT_CONFIG_PATH)
    pre_args, _ignored = pre_parser.parse_known_args()
    setup_i18n(pre_args.config)
    # 让 argparse 内置文案(如 -h 的说明)也走项目的 gettext 目录
    argparse._ = _

    parser = argparse.ArgumentParser(description=_("Z-Sans Asset Breeding Engine v{VERSION} Help Information").format(VERSION=VERSION))
    parser.add_argument("-c", "--config", help=_("Configuration file path"), default=DEFAULT_CONFIG_PATH)
    parser.add_argument("-d", "--domain", help=_("Add domain seed"), action="append")

    parser.add_argument("-u", "--url", help=_("Add URL seed"), action="append")
    parser.add_argument("-o", "--output", help=_("Output directory"), default="output")
    parser.add_argument("-v", "--verbose", help=_("Verbose output"), action="store_true")
    parser.add_argument("--init", help=_("Create default configuration file"), action="store_true")
    parser.add_argument("--version", help=_("Show version information"), action="store_true")
    parser.add_argument("--depth", help=_("Set maximum scan depth"), type=int)
    parser.add_argument("--resume", help=_("Resume from last checkpoint"), action="store_true")
    parser.add_argument("--watch", help=_("Run in watch mode, rescan periodically and report changes"), action="store_true")
    parser.add_argument("--list-plugins", help=_("List plugins in the plugins directory and exit"), action="store_true")
    parser.add_argument("--plugin-info", metavar="NAME", help=_("Show detailed info about a plugin and exit"))
    parser.add_argument("--web", help=_("Start the web console"), action="store_true")
    parser.add_argument("--port", help=_("Web console port"), type=int, default=8050)

    # 已启用插件可通过 register_cli(parser) 向主解析器追加 CLI 参数。
    # 插件未加载(未配置 plugins.dir 或被禁用)时,其参数不会出现在 --help 中。
    _apply_plugin_cli(parser, pre_args.config)

    args = parser.parse_args()

    cli_entry = getattr(args, "_zplugin_cli", None)
    active_key = getattr(args, "_zplugin_active", None)
    if cli_entry is not None and active_key and getattr(args, active_key, None):
        return cli_entry(args)
    
    if args.version:
        print(_("Z-Sans Asset Breeding Engine v{VERSION}").format(VERSION=VERSION))
        return 0
    
    if args.init:
        create_default_config(args.config)
        return 0
    
    if args.verbose:
        # 设置zsans日志器的级别为DEBUG
        logging.getLogger('zsans').setLevel(logging.DEBUG)
        # 同时将所有控制台处理器的级别设置为DEBUG
        for handler in logging.getLogger().handlers:
            if isinstance(handler, logging.StreamHandler):
                handler.setLevel(logging.DEBUG)
    
    config = load_config(args.config)
    
    if args.output:
        if isinstance(config.get('output'), dict):
            config['output']['dir'] = args.output
        else:
            config['output'] = {'dir': args.output}
        
    if args.depth is not None:
        config["max_depth"] = args.depth
        logger.info(_("Maximum scan depth set: {depth}").format(depth=args.depth))

    # Web 控制台模式：启动常驻服务，不执行命令行扫描
    if args.web:
        from webapp import start_web_server
        output_dir = config.get('output', {}).get('dir', 'output') if isinstance(config.get('output'), dict) else 'output'
        try:
            start_web_server(config, args.config, output_dir, port=args.port)
        except KeyboardInterrupt:
            logger.info(_("Web console stopped"))
        return 0
    
    engine = BreedingEngine(config)
    
    if args.list_plugins:
        print_plugin_table(engine)
        return 0
    
    if args.plugin_info:
        if args.plugin_info not in engine.plugins:
            logger.error(_("Plugin not found: {name}").format(name=args.plugin_info))
            return 1
        print_plugin_info(engine.plugins[args.plugin_info])
        return 0
    
    resumed = False
    if args.resume:
        resumed = engine.load_checkpoint()
    
    has_seeds = False
    domain_seeds = args.domain or []
    url_seeds = args.url or []
    
    if domain_seeds:
        for domain in domain_seeds:
            if engine.add_seed(ASSET_TYPE_DOMAIN, domain):
                has_seeds = True
    
    if url_seeds:
        for url in url_seeds:
            if engine.add_seed(ASSET_TYPE_URL, url):
                has_seeds = True
    
    if not has_seeds and not (resumed and engine.asset_graph.nodes):
        logger.error(_("No seed assets provided, unable to start breeding engine"))
        return 1
    
    if resumed:
        logger.info(_("Resuming scan from checkpoint, {nodes} assets, {queued} queued").format(
            nodes=len(engine.asset_graph.nodes), queued=engine.queue.size()))
    
    if args.watch:
        return run_watch(config, domain_seeds, url_seeds)
    
    success = engine.run()
    
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())