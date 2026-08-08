# coding: utf-8

import argparse
import copy
import json
import logging

_stop_signaled = False
import os
import signal
import sys
import threading
import time
import yaml
import concurrent.futures
from concurrent.futures import ThreadPoolExecutor, as_completed
try:
    import colorama
    colorama.init()
    USE_COLORAMA = True
except ImportError:
    USE_COLORAMA = False
from datetime import datetime

import os
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

VERSION = "0.0.2"
DEFAULT_CONFIG_PATH = "breeding-config.yaml"

from core.zsans_engine import (
    Asset, DomainAsset, IPAsset, URLAsset, PortAsset, JSAsset,
    ASSET_TYPE_DOMAIN, ASSET_TYPE_IP, ASSET_TYPE_URL, ASSET_TYPE_PORT, ASSET_TYPE_JS,
    AssetFactory, AssetGraph, PriorityBreedingQueue
)
from core.breeders.breeders import BreederFactory
from core.tools.tools import ToolOrchestrator
from core.output import OutputHandler


class BreedingEngine:
    def __init__(self, config=None):
        self.config = config or {}
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
        
        self.seed_domains = set()
        self.seed_ips = set()
        self.seed_ip_ranges = set()
        
        self.tool_orchestrator = ToolOrchestrator(self.config, engine=self)
        self.breeder_factory = BreederFactory()
        self.output_handler = OutputHandler(self, self.config.get("output", {}))
        self._setup_signal_handlers()
        
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
            self.state = "stopped"
            logger.info(_("Received stop signal, shutting down gracefully..."))
            if self._executor is not None:
                try:
                    self._executor.shutdown(wait=False, cancel_futures=True)
                except Exception:
                    pass

    
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
                    domain = parsed_url.netloc
                    if domain:
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
        return True
    
    def stop(self):
        if self._finalized:
            return
        self._finalized = True
        global _stop_signaled
        _stop_signaled = True
        self.state = "stopped"
        if self._executor is not None:
            try:
                self._executor.shutdown(wait=False, cancel_futures=True)
            except Exception:
                pass
        
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
            logger.debug(_("Asset {uid} exceeds max depth {depth}, skipping").format(uid=asset.uid, depth=max_depth))
            return True
        
        asset.state = "scanning"
        
        if not self._check_resource_limits(asset):
            logger.warning(_("Asset {uid} exceeds resource limits, skipping").format(uid=asset.uid))
            asset.state = "excluded"
            return True
        
        if self._is_excluded(asset):
            logger.debug(_("Asset {uid} matches exclusion rules, skipping").format(uid=asset.uid))
            asset.state = "excluded"
            return True
        
        breeder = self.breeder_factory.get_breeder(asset.type, self.config, self)
        if not breeder:
            logger.warning(_("No suitable breeder found for asset type {type}, skipping").format(type=asset.type))
            asset.state = "excluded"
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
            
            for new_asset in new_assets:
                if self.asset_graph.add_asset(new_asset):
                    if self.queue.add(new_asset):
                        self.asset_graph.add_edge(asset, new_asset, "discovered")
            
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
            return True
    
    def _concurrent_breed(self):
        workers = max(1, self.config.get("concurrency", {}).get("max_tasks", 1))
        strategy = self.config.get("strategy", "priority_based")
        in_flight = 0
        futures = set()
        
        executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="zsans")
        self._executor = executor
        try:
            while self.state == "running" and not _stop_signaled:
                while self.state == "running" and not _stop_signaled and in_flight < workers and not self.queue.is_empty():
                    asset = self.queue.get_next(strategy)
                    if not asset:
                        break
                    in_flight += 1
                    futures.add(executor.submit(self._process_asset, asset))
                
                if in_flight == 0:
                    break
                
                try:
                    for future in as_completed(futures, timeout=1.0):
                        futures.discard(future)
                        in_flight -= 1
                        break
                except (concurrent.futures.TimeoutError, TimeoutError):
                    pass
                
                if _stop_signaled or self.state != "running":
                    break
            
            if self.state == "running" and not _stop_signaled and in_flight > 0:
                for future in as_completed(futures):
                    futures.discard(future)
                    in_flight -= 1
        finally:
            self._executor = None
            executor.shutdown(wait=False, cancel_futures=True)
        
        if self.state == "running" and not _stop_signaled:
            self.state = "completed"
    
    def _check_resource_limits(self, asset):
        asset_type_config = self.config.get("asset_types", {}).get(asset.type, {})
        depth_limit = asset_type_config.get("depth_limit", self.config.get("max_depth", 3))
        if asset.depth > depth_limit:
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
        
        return current < limit
    
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


def load_config(config_path):
    from core.zsans_engine import DEFAULT_CONFIG
    
    if not os.path.exists(config_path):
        logger.warning(_("Config file {path} does not exist, using default configuration").format(path=config_path))
        return DEFAULT_CONFIG
    
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f)
        
        merged_config = copy.deepcopy(DEFAULT_CONFIG)
        # 确保external_tools配置被正确合并
        if 'external_tools' in config:
            if 'external_tools' not in merged_config:
                merged_config['external_tools'] = {}
            # 合并external_tools配置
            for ext_key, ext_value in config['external_tools'].items():
                if isinstance(ext_value, dict) and ext_key in merged_config['external_tools'] and isinstance(merged_config['external_tools'][ext_key], dict):
                    merged_config['external_tools'][ext_key].update(ext_value)
                else:
                    merged_config['external_tools'][ext_key] = ext_value
        
        for key, value in config.items():
            if key == 'external_tools':
                continue  # 已经单独处理
            if isinstance(value, dict) and key in merged_config and isinstance(merged_config[key], dict):
                merged_config[key].update(value)
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
        with open(config_path, 'w', encoding='utf-8') as f:
            yaml.dump(DEFAULT_CONFIG, f, default_flow_style=False, sort_keys=False)
        
        logger.info(_("Created default config file: {path}").format(path=config_path))
        return True
    
    except Exception as e:
        logger.error(_("Failed to create default config file: {error}").format(error=str(e)))
        return False


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
  
    args = parser.parse_args()
    
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
    
    engine = BreedingEngine(config)
    
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