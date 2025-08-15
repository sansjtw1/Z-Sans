# coding: utf-8

import argparse
import logging
import os
import signal
import sys
import time
import yaml
try:
    import colorama
    colorama.init()
    USE_COLORAMA = True
except ImportError:
    USE_COLORAMA = False
from datetime import datetime

import os
from core.i18n import setup_i18n, _
setup_i18n(os.path.join(os.path.dirname(__file__), 'breeding-config.yaml'))

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

VERSION = "0.0.1"
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
        if signum in (signal.SIGINT, signal.SIGTERM):
            logger.info(_("Received stop signal, shutting down gracefully..."))
            self.stop()

    
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
        if self.state == "stopped":
            return
        
        try:
            logger.info(_("Saving scan results before stopping engine..."))
            output_files = self.output_handler.generate_output()
            logger.info(_("Scan results saved: {files}").format(files=output_files))
        except Exception as e:
            logger.error(_("Error saving scan results: {error}").format(error=str(e)))
        
        self.state = "stopped"
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
            
            self.metrics["assets_processed"] += 1
            self.metrics["new_assets_found"] += len(new_assets)
            self.metrics["depth_reached"] = max(self.metrics["depth_reached"], asset.depth)
            
            if asset.state != "eliminated":
                asset.state = "scanned"
            else:
                logger.debug(_("Maintaining asset {uid} elimination status").format(uid=asset.uid))
            
            for new_asset in new_assets:
                if self.asset_graph.add_asset(new_asset):
                    self.queue.add(new_asset)
                    self.asset_graph.add_edge(asset, new_asset, "discovered")
            
            logger.info(_("Asset {uid} processed, found {count} new assets").format(uid=asset.uid, count=len(new_assets)))
            return True
        
        except Exception as e:
            logger.error(_("Error processing asset {uid}: {error}").format(uid=asset.uid, error=str(e)))
            asset.state = "failed"
            self.metrics["errors"] += 1
            return True
    
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
    
    def run(self):
        if not self.start():
            return False
        
        try:
            while self.state == "running":
                if not self.auto_breeding_cycle():
                    break
            
            if self.state == "completed":
                logger.info(_("Breeding engine completed all tasks, generating output..."))
                output_files = self.output_handler.generate_output()
                logger.info(_("Output generated: {files}").format(files=output_files))
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
            if self.state != "stopped":
                self.stop()


def load_config(config_path):
    from core.zsans_engine import DEFAULT_CONFIG
    
    if not os.path.exists(config_path):
        logger.warning(_("Config file {path} does not exist, using default configuration").format(path=config_path))
        return DEFAULT_CONFIG
    
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f)
        
        merged_config = DEFAULT_CONFIG.copy()
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

    parser = argparse.ArgumentParser(description=_("Z-Sans Asset Breeding Engine v{VERSION} Help Information").format(VERSION=VERSION))
    parser.add_argument("-c", "--config", help=_("Configuration file path"), default=DEFAULT_CONFIG_PATH)
    parser.add_argument("-d", "--domain", help=_("Add domain seed"), action="append")

    parser.add_argument("-u", "--url", help=_("Add URL seed"), action="append")
    parser.add_argument("-o", "--output", help=_("Output directory"), default="output")
    parser.add_argument("-v", "--verbose", help=_("Verbose output"), action="store_true")
    parser.add_argument("--init", help=_("Create default configuration file"), action="store_true")
    parser.add_argument("--version", help=_("Show version information"), action="store_true")
    parser.add_argument("--depth", help=_("Set maximum scan depth"), type=int)
 
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
        config["output"]["dir"] = args.output
        
    if args.depth is not None:
        config["max_depth"] = args.depth
        logger.info(_("Maximum scan depth set: {depth}").format(depth=args.depth))
    
    engine = BreedingEngine(config)
    
    has_seeds = False
    
    if args.domain:
        for domain in args.domain:
            if engine.add_seed(ASSET_TYPE_DOMAIN, domain):
                has_seeds = True
    
    
    
    if args.url:
        for url in args.url:
            if engine.add_seed(ASSET_TYPE_URL, url):
                has_seeds = True
    
    if not has_seeds:
        logger.error(_("No seed assets provided, unable to start breeding engine"))
        return 1
    
    success = engine.run()
    
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())