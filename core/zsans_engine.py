#!/usr/bin/env python3
# coding: utf-8

import argparse
import json
import logging
import os
import re
import sys
import threading
import time
import yaml
from concurrent.futures import ThreadPoolExecutor, as_completed
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from urllib3.exceptions import InsecureRequestWarning

# 禁用不安全请求警告
requests.packages.urllib3.disable_warnings(InsecureRequestWarning)

# 使用全局的日志记录器，避免重复配置
logger = logging.getLogger('zsans.engine')

# 全局配置
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
DEFAULT_TIMEOUT = 15
MAX_WORKERS = 5
RETRY_COUNT = 2
RETRY_BACKOFF = 1

# 创建全局的requests会话
http_session = requests.Session()

# 配置会话的基本参数 - 只对HTTP状态码错误重试，不对连接/代理错误重试
retry_strategy = Retry(
    total=RETRY_COUNT,
    backoff_factor=RETRY_BACKOFF,
    status_forcelist=[429, 500, 502, 503, 504],
    connect=0,        # 不重试连接错误（包括代理错误）
    read=0,           # 不重试读取错误
    redirect=0,       # 不重试重定向错误
    raise_on_status=False,
)
adapter = HTTPAdapter(max_retries=retry_strategy)
http_session.mount("http://", adapter)
http_session.mount("https://", adapter)
http_session.headers.update({'User-Agent': USER_AGENT})
http_session.timeout = DEFAULT_TIMEOUT

# 全局配置更新锁，确保线程安全
config_lock = threading.RLock()

# 全局配置对象
current_config = None

# 代理失败计数器
_proxy_failure_count = 0
_PROXY_FAILURE_THRESHOLD = 3  # 连续失败3次后自动禁用代理

# 设置代理的函数

def set_http_proxy(proxy_url):
    global _proxy_failure_count
    with config_lock:
        if proxy_url:
            http_session.proxies = {
                'http': proxy_url,
                'https': proxy_url
            }
            _proxy_failure_count = 0  # 重置失败计数
            logger.debug(f"HTTP proxy configured: {proxy_url}")
        else:
            http_session.proxies = {}
            _proxy_failure_count = 0
            logger.debug("HTTP proxy disabled")

def report_proxy_failure():
    """报告一次代理失败，连续失败达到阈值后自动禁用代理"""
    global _proxy_failure_count
    with config_lock:
        if http_session.proxies:
            _proxy_failure_count += 1
            if _proxy_failure_count >= _PROXY_FAILURE_THRESHOLD:
                logger.warning(
                    f"Proxy has failed {_proxy_failure_count} consecutive times, "
                    f"automatically disabling proxy. All subsequent requests will go direct."
                )
                http_session.proxies = {}
                _proxy_failure_count = 0
                return True
            else:
                logger.debug(
                    f"Proxy failure {_proxy_failure_count}/{_PROXY_FAILURE_THRESHOLD}"
                )
    return False

# 初始化全局配置和HTTP会话

def init_http_config(config):
    global current_config
    with config_lock:
        current_config = config
        # 更新超时设置
        http_session.timeout = config.get('http', {}).get('timeout', DEFAULT_TIMEOUT)
        # 更新用户代理
        user_agent = config.get('http', {}).get('user_agent', USER_AGENT)
        http_session.headers.update({'User-Agent': user_agent})
        # 更新代理设置
        proxy_url = config.get('http', {}).get('proxy')
        set_http_proxy(proxy_url)

# 获取当前HTTP配置

def get_http_config():
    with config_lock:
        return current_config.copy() if current_config else None

# 资产类型定义
ASSET_TYPE_DOMAIN = "domain"
ASSET_TYPE_IP = "ip"
ASSET_TYPE_URL = "url"
ASSET_TYPE_PORT = "port"
ASSET_TYPE_CERT = "cert"
ASSET_TYPE_JS = "js"

# 关系类型定义
RELATION_DISCOVERED = "discovered"
RELATION_RESOLVED = "resolved"
RELATION_HOSTED = "hosted"

# 默认配置
DEFAULT_CONFIG = {
    # 繁殖策略: priority_based(优先级), depth_first(深度优先), breadth_first(广度优先), time_based(时间顺序)
    "strategy": "priority_based",  # 基于优先级的策略

    # 资产范围配置
    "asset_scope": {
        "restrict_to_seed_domains": True,    # 限制在种子域名范围内
        "restrict_to_seed_ip_ranges": True,  # 限制在种子IP范围内
        "include_subdomains": True,          # 包含子域名
        "include_ip_ranges": True            # 包含IP范围
    },

    # 并发配置
    "concurrency": {
        "max_tasks": 20,        # 最大并发任务数
        "tools": {               # 各工具并发数
            "subfinder": 2,       # 子域名发现工具并发数
            "naabu": 2,          # 端口扫描工具并发数
            "jsfinder": 2        # JS文件发现工具并发数
        }
    },

    "max_depth": 4,          # 最大发现深度

    # 资产数量限制
    "resource_limits": {
        "max_domains": 2000,    # 最大域名数量
        "max_ips": 2000,        # 最大IP数量
        "max_urls": 5000,       # 最大URL数量
        "max_ports": 5000,      # 最大端口数量
        "max_js": 2000          # 最大JS文件数量
    },

    # 资产类型配置
    "asset_types": {
        # 域名资产配置
        "domain": {
            "enabled": True,      # 启用域名发现
            "depth_limit": 3,     # 域名发现深度限制
            "priority": 10,       # 优先级(越高越优先)
            "tools": {             # 使用的工具
                "subfinder": True,      # subfinder工具
                "free_subfinder": False, # 免费版subfinder
                "crtsh": True,         # 证书透明度查询
                "dnsx": True           # DNS解析工具
            }
        },

        # IP资产配置
        "ip": {
            "enabled": True,      # 启用IP发现
            "depth_limit": 2,     # IP发现深度限制
            "priority": 8,        # 优先级
            "tools": {
                "naabu": True,         # 端口扫描工具
                "reverse_dns": True   # 反向DNS解析
            }
        },

        # URL资产配置
        "url": {
            "enabled": True,      # 启用URL发现
            "depth_limit": 5,     # URL发现深度限制
            "priority": 10,       # 优先级
            "tools": {
                "jsfinder": True,      # JS文件发现
                "link_extract": True  # 链接提取
            },
            "title_extraction": {     # 标题提取配置
                "enabled": True,           # 启用标题提取
                "max_length": 50,         # 最大标题长度
                "show_in_report": True,   # 在报告中显示
                "show_in_csv": True       # 在CSV中显示
            }
        },

        # 端口资产配置
        "port": {
            "enabled": True,      # 启用端口发现
            "depth_limit": 1,     # 端口发现深度限制
            "priority": 7,        # 优先级
            "tools": {
                "service_identify": True # 服务识别
            }
        },

        # JS文件资产配置
        "js": {
            "enabled": True,      # 启用JS文件发现
            "depth_limit": 4,     # JS文件发现深度限制
            "priority": 6,        # 优先级
            "tools": {
                "jsfinder": True      # JS文件发现工具
            }
        }
    },

    # 输出配置
    "output": {
        "dir": "output",          # 输出目录
        "graph_format": "json",   # 图形输出格式
        "asset_report": "json",   # 资产报告格式
        "keep_eliminated_assets": True,  # 是否保留被排除的资产
        "formats": {             # 输出格式
            "json": True,         # JSON格式
            "csv": True,          # CSV格式
            "graphml": True,      # GraphML格式
            "html": True          # HTML格式
        },
        "auto_open": True      # 自动打开报告
    },

    # 外部工具配置
    "external_tools": {
        "paths": {               # 工具路径配置
            "subfinder": None,    # subfinder工具路径，如：/usr/bin/subfinder
            "naabu": None,        # naabu工具路径，如：D:\naabu\naabu.exe
            "ehole": None         # EHole指纹识别工具路径
        },
        "fingerprint": {       # 指纹识别配置
            "enabled": False    # 是否启用指纹识别功能
        }
    },

    # 国际化配置
    "language": {
        "default_language": "en",  # 默认语言 (zh_CN 或 en)
        "supported_languages": [     # 支持的语言列表
            "zh_CN",
            "en"
        ],
        "locale_dir": "i18n"         # 语言文件目录
    },

    # 排除规则
    "exclusions": {
        "domains": [             # 排除的域名
            "example.com",
            "test.local"
        ],
        "ips": [                 # 排除的IP
            "127.0.0.1",
            "0.0.0.0"
        ],
        "urls": [                # 排除的URL关键词
            "login",
            "logout"
        ],
        "ports": [],           # 排除的端口
        "patterns": [           # 排除的正则表达式模式
            r"\.(css|jpg|jpeg|png|gif|svg|woff|woff2|ttf|eot)$"
        ]
    },

    # HTTP请求配置
    "http": {
        "timeout": 10,         # 超时时间(秒)
        "retries": 3,          # 重试次数
        "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",  # 用户代理
        "verify_ssl": False,   # 是否验证SSL证书
        "proxy": None,         # 代理设置
        "follow_redirects": True,  # 是否跟随重定向
        "redirect_as_new_asset": True,  # 将重定向视为新资产
        "max_redirects": 5     # 最大重定向次数
    }
}


class Asset:
    """资产基类，表示一个可被发现和关联的实体"""
    def __init__(self, value, asset_type, source="manual", depth=0):
        self.value = value.lower() if isinstance(value, str) else value
        self.type = asset_type
        self.source = source
        self.depth = depth
        self.state = "new"  # new, scanning, scanned, excluded, eliminated
        self.properties = {}
        self.uid = self._generate_uid()
        
    def _generate_uid(self):
        """生成资产唯一标识符"""
        return f"{self.type}:{self.value}"
    
    def to_dict(self):
        """转换为字典表示"""
        return {
            "uid": self.uid,
            "type": self.type,
            "value": self.value,
            "source": self.source,
            "depth": self.depth,
            "state": self.state,
            "properties": self.properties
        }
    
    @classmethod
    def from_dict(cls, data):
        """从字典创建资产对象"""
        asset = cls(data["value"], data["type"], data["source"], data["depth"])
        asset.state = data["state"]
        asset.properties = data["properties"]
        return asset


class DomainAsset(Asset):
    """域名资产"""
    def __init__(self, domain, source="manual", depth=0):
        super().__init__(domain, ASSET_TYPE_DOMAIN, source, depth)
        self.properties["domain"] = domain


class IPAsset(Asset):
    """IP资产"""
    def __init__(self, ip, source="manual", depth=0):
        super().__init__(ip, ASSET_TYPE_IP, source, depth)
        self.properties["ip"] = ip


class URLAsset(Asset):
    """URL资产"""
    def __init__(self, url, source="manual", depth=0):
        # 清理URL，移除反引号和双引号
        cleaned_url = url
        if isinstance(cleaned_url, str):
            # 循环清理，确保完全移除所有反引号和双引号
            while '`' in cleaned_url or '"' in cleaned_url:
                cleaned_url = cleaned_url.replace('`', '').replace('"', '')
            # 去除前后空格
            cleaned_url = cleaned_url.strip()
        
        super().__init__(cleaned_url, ASSET_TYPE_URL, source, depth)
        self.properties["url"] = cleaned_url
        parsed = urlparse(cleaned_url)
        self.properties["domain"] = parsed.netloc
        self.properties["path"] = parsed.path
        self.properties["scheme"] = parsed.scheme


class PortAsset(Asset):
    """端口资产"""
    def __init__(self, ip, port, service=None, source="manual", depth=0):
        value = f"{ip}:{port}"
        super().__init__(value, ASSET_TYPE_PORT, source, depth)
        self.properties["ip"] = ip
        self.properties["port"] = port
        if service:
            self.properties["service"] = service


class JSAsset(Asset):
    """JavaScript资产"""
    def __init__(self, url, source="manual", depth=0):
        super().__init__(url, ASSET_TYPE_JS, source, depth)
        self.properties["url"] = url
        parsed = urlparse(url)
        self.properties["domain"] = parsed.netloc
        self.properties["path"] = parsed.path


class AssetFactory:
    """资产工厂，用于创建不同类型的资产"""
    @staticmethod
    def create_asset(value, asset_type, source="manual", depth=0, **kwargs):
        if asset_type == ASSET_TYPE_DOMAIN:
            return DomainAsset(value, source, depth)
        elif asset_type == ASSET_TYPE_IP:
            return IPAsset(value, source, depth)
        elif asset_type == ASSET_TYPE_URL:
            return URLAsset(value, source, depth)
        elif asset_type == ASSET_TYPE_PORT:
            return PortAsset(value, kwargs.get("port"), kwargs.get("service"), source, depth)
        elif asset_type == ASSET_TYPE_JS:
            return JSAsset(value, source, depth)
        else:
            return Asset(value, asset_type, source, depth)


class PriorityBreedingQueue:
    """优先级繁殖队列，管理待处理的资产"""
    def __init__(self, config=None):
        self.queue = []
        self.lock = threading.Lock()
        self.config = config or {}
        
    def add(self, asset):
        """添加资产到队列"""
        with self.lock:
            self.queue.append(asset)
            
    def get_next(self, strategy="priority_based"):
        """根据策略获取下一个资产"""
        with self.lock:
            if not self.queue:
                return None
            
            if strategy == "depth_first":
                return self._get_deepest()
            elif strategy == "breadth_first":
                return self._get_shallowest()
            elif strategy == "priority_based":
                return self._get_highest_priority()
            else:
                return self._get_oldest()
                
    def _get_highest_priority(self):
        if not self.queue:
            return None
        
        # 从配置中获取资产类型优先级
        asset_types_config = self.config.get("asset_types", {})
        type_priority = {
            ASSET_TYPE_DOMAIN: asset_types_config.get("domain", {}).get("priority", 5),
            ASSET_TYPE_URL: asset_types_config.get("url", {}).get("priority", 4),
            ASSET_TYPE_IP: asset_types_config.get("ip", {}).get("priority", 3),
            ASSET_TYPE_PORT: asset_types_config.get("port", {}).get("priority", 2),
            ASSET_TYPE_JS: asset_types_config.get("js", {}).get("priority", 1)
        }
        
        highest = max(self.queue, key=lambda x: (type_priority.get(x.type, 0), -x.depth))
        self.queue.remove(highest)
        return highest
    
    def _get_deepest(self):
        if not self.queue:
            return None
        deepest = max(self.queue, key=lambda x: x.depth)
        self.queue.remove(deepest)
        return deepest

    def _get_shallowest(self):
        if not self.queue:
            return None
        shallowest = min(self.queue, key=lambda x: x.depth)
        self.queue.remove(shallowest)
        return shallowest

    def _get_oldest(self):
        if not self.queue:
            return None
        
        oldest = self.queue[0]
        self.queue.remove(oldest)
        return oldest
    
    def size(self):
        with self.lock:
            return len(self.queue)
    
    def is_empty(self):
        with self.lock:
            return len(self.queue) == 0


class AssetGraph:
    def __init__(self):
        self.nodes = {}  # uid -> asset
        self.edges = {}  # (source_uid, target_uid) -> relation
        self.lock = threading.Lock()
    
    def add_asset(self, asset):
        with self.lock:
            if asset.uid in self.nodes:
                # 如果资产已存在，检查其状态
                existing_asset = self.nodes[asset.uid]
                
                # 如果现有资产已经处理完成（scanned或eliminated），则不再处理
                if existing_asset.state in ["scanned", "eliminated"]:
                    return False
                
                # 如果现有资产正在处理中（scanning），也不再处理
                if existing_asset.state == "scanning":
                    return False
                
                # 如果现有资产处于初始状态或失败状态，更新节点信息并允许重新处理
                existing_asset.source = asset.source
                existing_asset.depth = asset.depth
                existing_asset.properties.update(asset.properties)
                return True
            
            self.nodes[asset.uid] = asset
            return True
    
    def add_edge(self, source, target, relation):
        with self.lock:
            if relation not in [RELATION_DISCOVERED, RELATION_RESOLVED, RELATION_HOSTED]:
                return False
            
            if source.uid not in self.nodes or target.uid not in self.nodes:
                return False
            
            edge_key = (source.uid, target.uid)
            self.edges[edge_key] = relation
            return True
    
    def asset_exists(self, asset):
        with self.lock:
            return asset.uid in self.nodes
    
    def get_asset(self, uid):
        with self.lock:
            return self.nodes.get(uid)
    
    def get_discoveries(self, asset):
        with self.lock:
            discoveries = []
            for edge_key, relation in self.edges.items():
                if edge_key[0] == asset.uid and relation == RELATION_DISCOVERED:
                    target_uid = edge_key[1]
                    discoveries.append(self.nodes[target_uid])
            return discoveries
    
    def export_json(self):
        with self.lock:
            data = {
                "nodes": [asset.to_dict() for asset in self.nodes.values()],
                "edges": [{
                    "source": source,
                    "target": target,
                    "relation": relation
                } for (source, target), relation in self.edges.items()]
            }
            return json.dumps(data, indent=2)
    
    def export_graphml(self):
        with self.lock:
            # 简化实现，实际应使用专门的图库如NetworkX
            xml = ['<?xml version="1.0" encoding="UTF-8"?>',
                  '<graphml xmlns="http://graphml.graphdrawing.org/xmlns">',
                  '<graph id="G" edgedefault="directed">',
                  '<!-- Nodes -->']  
            
            for uid, asset in self.nodes.items():
                xml.append(f'<node id="{uid}">'
                          f'<data key="type">{asset.type}</data>'
                          f'<data key="value">{asset.value}</data>'
                          f'</node>')
            
            xml.append('<!-- Edges -->')
            for (source, target), relation in self.edges.items():
                xml.append(f'<edge source="{source}" target="{target}">'
                          f'<data key="relation">{relation}</data>'
                          f'</edge>')
            
            xml.append('</graph>')
            xml.append('</graphml>')
            return '\n'.join(xml)
    
    def stats(self):
        with self.lock:
            asset_types = {}
            for asset in self.nodes.values():
                if asset.type in asset_types:
                    asset_types[asset.type] += 1
                else:
                    asset_types[asset.type] = 1
            
            return {
                "total_assets": len(self.nodes),
                "total_relations": len(self.edges),
                "asset_types": asset_types
            }