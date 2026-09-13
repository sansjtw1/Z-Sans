#!/usr/bin/env python3
# coding: utf-8

import argparse
import base64
import ipaddress
import json
import logging
import os
import re
import sys
import threading
import time
import yaml
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import nullcontext
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, urlsplit

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from xml.sax.saxutils import escape, quoteattr
from urllib3.exceptions import InsecureRequestWarning
from core.i18n import _
from core.cache import DiskCache

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

# HTTP 会话采用「每线程一个实例」：requests.Session 并非线程安全（cookie jar、
# 连接池、重定向状态都是可变共享状态），多 worker 并发抓取时共享同一个全局
# Session 会产生数据竞争。这里用线程本地存储 + 配置代次号实现：
#   * 每个线程首次取用时按当前配置构建自己的 Session；
#   * 全局配置（UA/代理/SSL）变化时递增代次号，各线程下次取用时自动重建，
#     从而保证配置变更对所有线程生效，同时避免共享可变对象。

# 会话重试策略 - 只对HTTP状态码错误重试，不对连接/代理错误重试
retry_strategy = Retry(
    total=RETRY_COUNT,
    backoff_factor=RETRY_BACKOFF,
    status_forcelist=[429, 500, 502, 503, 504],
    connect=0,        # 不重试连接错误（包括代理错误）
    read=0,           # 不重试读取错误
    redirect=0,       # 不重试重定向错误
    raise_on_status=False,
)

# 全局配置更新锁，确保线程安全
config_lock = threading.RLock()

# 全局配置对象
current_config = None

# 当前生效的 HTTP 参数（受 config_lock 保护）及其代次号
_http_settings = {
    'user_agent': USER_AGENT,
    'proxy': None,
    'verify': False,
}
_http_settings_generation = 0
_thread_local = threading.local()

# 限速器与磁盘缓存（由 init_http_config 依据配置初始化）
_rate_limiter = None
_http_cache = None
_cache_ttls = {}
_cache_bypass = False

# 代理失败计数器
_proxy_failure_count = 0
_PROXY_FAILURE_THRESHOLD = 3  # 连续失败3次后自动禁用代理

def _bump_http_settings():
    """标记 HTTP 配置已变更，令各线程在下次取用时重建会话。"""
    global _http_settings_generation
    _http_settings_generation += 1

def _apply_http_settings(session):
    session.headers.update({'User-Agent': _http_settings['user_agent']})
    session.verify = _http_settings['verify']
    proxy_url = _http_settings['proxy']
    if proxy_url:
        session.proxies = {'http': proxy_url, 'https': proxy_url}
    else:
        session.proxies = {}
    return session

class _RateLimiter:
    """全局令牌桶 + 按 host 并发信号量。

    rps<=0 且 per_host_concurrency<=0 时完全零开销（直接返回 nullcontext）。
    """

    def __init__(self):
        self.rps = 0.0
        self.burst = 1
        self.per_host_concurrency = 0
        self._lock = threading.Lock()
        self._tokens = 1.0
        self._last = time.monotonic()
        self._host_sems = {}

    def configure(self, rps=0.0, burst=1, per_host_concurrency=0):
        with self._lock:
            self.rps = max(0.0, float(rps or 0))
            self.burst = max(1, int(burst or 1))
            self.per_host_concurrency = max(0, int(per_host_concurrency or 0))
            self._tokens = float(self.burst)
            self._last = time.monotonic()
            self._host_sems = {}

    def enabled(self):
        return self.rps > 0 or self.per_host_concurrency > 0

    def acquire_host(self, host):
        if self.per_host_concurrency <= 0:
            return nullcontext()
        with self._lock:
            sem = self._host_sems.get(host)
            if sem is None:
                sem = threading.BoundedSemaphore(self.per_host_concurrency)
                self._host_sems[host] = sem
        sem.acquire()
        return _SemaphoreRelease(sem)

    def wait_token(self):
        if self.rps <= 0:
            return
        while True:
            with self._lock:
                now = time.monotonic()
                self._tokens = min(self.burst, self._tokens + (now - self._last) * self.rps)
                self._last = now
                if self._tokens >= 1:
                    self._tokens -= 1
                    return
                wait = (1 - self._tokens) / self.rps
            time.sleep(max(0.005, wait))


class _SemaphoreRelease:
    def __init__(self, sem):
        self._sem = sem

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self._sem.release()
        return False


def _cache_ttl_for(namespace):
    try:
        return _cache_ttls.get(namespace)
    except Exception:
        return None


def _response_to_cache(response):
    return json.dumps({
        'status': response.status_code,
        'reason': response.reason,
        'url': response.url,
        'headers': dict(response.headers),
        'body': base64.b64encode(response.content or b'').decode('ascii'),
    }).encode('utf-8')


def _response_from_cache(blob, request):
    data = json.loads(blob.decode('utf-8'))
    response = requests.Response()
    response.status_code = data.get('status', 200)
    response.reason = data.get('reason')
    response.url = data.get('url') or request.url
    response._content = base64.b64decode(data.get('body') or b'')
    response.headers = requests.structures.CaseInsensitiveDict(data.get('headers') or {})
    response.request = request
    response.encoding = requests.utils.get_encoding_from_headers(response.headers)
    return response


class ThrottledSession(requests.Session):
    """带全局限速与可选磁盘缓存的 requests.Session。

    重写 send()：先查缓存（仅 GET/HEAD，命中直接返回合成响应）→ 限速
    （按 host 并发信号量 + 全局令牌桶）→ 实际发送 → 回写缓存（仅 200）。
    以子类形式实现，因此 requests.Session 的 request() 以及插件对
    ``http_session.request(...)`` 的调用全部透明生效。
    """

    def send(self, request, **kwargs):
        method = (request.method or 'GET').upper()
        cache = _http_cache
        cacheable = (cache is not None and cache.enabled and not _cache_bypass
                     and method in ('GET', 'HEAD'))
        key = None
        if cacheable:
            key = cache.make_key(method, request.url)
            cached = cache.get('http', key)
            if cached is not None:
                return _response_from_cache(cached, request)

        limiter = _rate_limiter
        try:
            host = urlsplit(request.url).hostname or ''
        except Exception:
            host = ''
        if limiter is not None and limiter.enabled():
            with limiter.acquire_host(host):
                limiter.wait_token()
                response = super().send(request, **kwargs)
        else:
            response = super().send(request, **kwargs)

        if cacheable and response.status_code == 200:
            try:
                cache.set('http', key, _response_to_cache(response), ttl=_cache_ttl_for('http'))
            except Exception as e:
                logger.debug(_("HTTP cache write failed: {error}").format(error=str(e)))
        return response


def _build_http_session():
    session = ThrottledSession()
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return _apply_http_settings(session)

def get_http_session():
    """返回当前线程专属的 requests.Session（线程安全）。

    配置变更通过代次号传播：线程持有的 Session 代次与当前不一致时会重建，
    因此线程不会读到过期的 UA/代理/SSL 配置。
    """
    generation = _http_settings_generation
    if getattr(_thread_local, 'session', None) is None or \
            getattr(_thread_local, 'generation', None) != generation:
        _thread_local.session = _build_http_session()
        _thread_local.generation = generation
    return _thread_local.session


class _ThreadLocalSessionProxy:
    """向后兼容代理：``http_session`` 曾是全局 requests.Session。

    旧代码 / 第三方插件（如 security_headers.py）会直接
    ``from core.zsans_engine import http_session`` 后调用 ``http_session.get(...)``。
    由于 requests.Session 非线程安全，现改为每线程独立实例；本代理把属性读取与
    写入转发到「当前线程」的 Session，使旧代码无需修改即可继续工作。

    新代码请直接使用 ``get_http_session()``。
    """

    __slots__ = ()

    def __getattr__(self, item):
        return getattr(get_http_session(), item)

    def __setattr__(self, key, value):
        setattr(get_http_session(), key, value)


http_session = _ThreadLocalSessionProxy()

# 设置代理的函数

def set_http_proxy(proxy_url):
    global _proxy_failure_count
    with config_lock:
        if proxy_url:
            _http_settings['proxy'] = proxy_url
            _proxy_failure_count = 0  # 重置失败计数
            # 日志中抹掉代理凭据（proxy URL 可能含 user:password）
            safe_url = proxy_url
            try:
                from urllib.parse import urlsplit, urlunsplit
                parts = urlsplit(proxy_url)
                if parts.username or parts.password:
                    host = parts.hostname or ''
                    if parts.port:
                        host += f':{parts.port}'
                    safe_url = urlunsplit((parts.scheme, host, parts.path, parts.query, parts.fragment))
            except Exception:
                pass
            logger.debug(_("HTTP proxy configured: {proxy}").format(proxy=safe_url))
        else:
            _http_settings['proxy'] = None
            _proxy_failure_count = 0
            logger.debug(_("HTTP proxy disabled"))
        _bump_http_settings()

def report_proxy_failure():
    """报告一次代理失败，连续失败达到阈值后自动禁用代理"""
    global _proxy_failure_count
    with config_lock:
        if _http_settings['proxy']:
            _proxy_failure_count += 1
            if _proxy_failure_count >= _PROXY_FAILURE_THRESHOLD:
                logger.warning(
                    f"Proxy has failed {_proxy_failure_count} consecutive times, "
                    f"automatically disabling proxy. All subsequent requests will go direct."
                )
                _http_settings['proxy'] = None
                _proxy_failure_count = 0
                _bump_http_settings()
                return True
            else:
                logger.debug(
                    f"Proxy failure {_proxy_failure_count}/{_PROXY_FAILURE_THRESHOLD}"
                )
    return False

# 初始化全局配置和HTTP会话

def _init_rate_limiter(config):
    global _rate_limiter
    if _rate_limiter is None:
        _rate_limiter = _RateLimiter()
    rl = (config.get('http', {}) or {}).get('rate_limit') or {}
    if rl.get('enabled', False):
        _rate_limiter.configure(
            rps=rl.get('requests_per_second', 0),
            burst=rl.get('burst', 1),
            per_host_concurrency=rl.get('per_host_concurrency', 0),
        )
    else:
        _rate_limiter.configure()


def _init_cache(config):
    global _http_cache, _cache_ttls
    cfg = config.get('cache') or {}
    if not cfg.get('enabled', False):
        _http_cache = None
        return
    directory = cfg.get('dir') or os.path.join('.cache', 'zsans')
    path = os.path.join(directory, 'zsans-cache.sqlite')
    _http_cache = DiskCache(
        path,
        enabled=True,
        max_entries=cfg.get('max_entries', 100000),
        max_mb=cfg.get('max_mb', 200),
    )
    _cache_ttls = dict(cfg.get('ttl') or {})


def init_http_config(config):
    global current_config
    with config_lock:
        current_config = config
        # 更新用户代理与 SSL 校验设置
        _http_settings['user_agent'] = config.get('http', {}).get('user_agent', USER_AGENT)
        _http_settings['verify'] = config.get('http', {}).get('verify_ssl', False)
        # 更新代理设置（内部会递增代次号）
        set_http_proxy(config.get('http', {}).get('proxy'))
        _bump_http_settings()
        # 限速与跨运行缓存
        _init_rate_limiter(config)
        _init_cache(config)


def cache_get_json(namespace, key):
    """从磁盘缓存读取 JSON 值；未启用或未命中返回 None。"""
    cache = _http_cache
    if cache is None or not cache.enabled:
        return None
    raw = cache.get(namespace, key)
    if raw is None:
        return None
    try:
        return json.loads(raw.decode('utf-8'))
    except Exception:
        return None


def cache_set_json(namespace, key, value, ttl=None):
    """把 JSON 值写入磁盘缓存；未启用时静默跳过。"""
    cache = _http_cache
    if cache is None or not cache.enabled:
        return
    try:
        if ttl is None:
            ttl = _cache_ttls.get(namespace)
        cache.set(namespace, key, json.dumps(value).encode('utf-8'), ttl=ttl)
    except Exception:
        pass


def set_cache_bypass(flag):
    """运行时旁路 HTTP 缓存（如 --watch 模式）。"""
    global _cache_bypass
    _cache_bypass = bool(flag)

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

    # 插件配置（目录插件与单文件插件自动加载）
    "plugins": {
        "dir": None,         # 插件目录路径，None 时使用 PLUGINS_DIR
        "disabled": []       # 需要禁用的插件名列表
    },

    # 资产范围配置
    "asset_scope": {
        "restrict_to_seed_domains": True,    # 限制在种子域名范围内
        "restrict_to_seed_ip_ranges": True,  # 限制在种子IP范围内
        "include_subdomains": True,          # 包含子域名
        "include_ip_ranges": True,           # 包含IP范围
        "seed_scope": "registrable",         # 种子作用域: registrable(按公共后缀表扩展到注册域eTLD+1) / exact(仅种子域本身及子域,不拆分)
        "ipv6_prefix": None                  # IPv6 种子按此前缀扩展网段(如 64)；None 表示仅精确匹配
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
                "dnsx": True,         # DNS解析工具
                "dns_brute": True,     # DNS暴力枚举子域名
                "cert_probe": {        # TLS 证书探测（SAN 提取 / 有效期 / 自签名）
                    "enabled": True,
                    "ports": [443],
                    "expiry_warn_days": 30,
                    "self_signed_finding": True
                }
            },
            "wildcard": {          # 泛解析检测：剔除爆破假阳性
                "enabled": True,
                "samples": 2       # 随机标签探测次数
            }
        },

        # IP资产配置
        "ip": {
            "enabled": True,      # 启用IP发现
            "depth_limit": 2,     # IP发现深度限制
            "priority": 8,        # 优先级
            "tools": {
                "naabu": True,         # 端口扫描工具
                "reverse_dns": True,  # 反向DNS解析
                "port_range": "1-1024,3306,3389,5432,5900,6379,7001,8000-8500,8888,9000-9100,9200,27017,11211"  # 内置端口扫描器扫描范围
            }
        },

        # URL资产配置
        "url": {
            "enabled": True,      # 启用URL发现
            "depth_limit": 5,     # URL发现深度限制
            "priority": 10,       # 优先级
            "tools": {
                "jsfinder": True,      # JS文件发现
                "link_extract": True,  # 链接提取
                "fingerprint": False  # 站点指纹识别（依赖 ehole）
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
            "depth_limit": 5,     # 端口发现深度限制
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
        "output_prefix": "zsans",  # 输出文件名前缀
        "graph_format": "json",   # 图形输出格式
        "asset_report": "json",   # 资产报告格式
        "keep_eliminated_assets": True,  # 是否保留被排除的资产
        "formats": {             # 输出格式
            "json": True,         # JSON格式
            "csv": True,          # CSV格式
            "graphml": True,      # GraphML格式
            "html": True,         # HTML格式
            "sarif": True,        # SARIF 2.1.0（findings，对接 CI 安全流水线）
            "neo4j": False        # Neo4j/BloodHound 风格 CSV（节点+关系）
        },
        "auto_open": True      # 自动打开报告
    },

    # 外部工具配置
    "external_tools": {
        "paths": {               # 工具路径配置
            "subfinder": None,    # subfinder工具路径，如：/usr/bin/subfinder
            "naabu": None,        # naabu工具路径，如：D:\naabu\naabu.exe
            "ehole": None,        # EHole指纹识别工具路径
            "whatweb": None       # WhatWeb指纹识别工具路径
        },
        "fingerprint": {       # 指纹识别配置
            "enabled": False,   # 是否启用指纹识别功能
            # 引擎选择：auto（有哪个用哪个，默认）/ ehole / whatweb / both（两者都跑并合并结果）
            "engine": "auto"
        },
        "jsfinder_timeout": 30  # JSFinder 抓取单个域名超时时间（秒）
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

    # 断点续扫配置
    "checkpoint": {
        "enabled": True,     # 是否启用断点续扫
        "interval": 50,      # 每处理多少资产保存一次断点
        "file": None         # 断点文件路径，None 时使用 <output_dir>/checkpoint.json
    },

    # 变更监控配置
    "monitoring": {
        "enabled": False,        # 是否启用 --watch 变更监控
        "interval": 3600,        # 监控轮询间隔(秒)
        "webhook_url": None      # 变更告警 Webhook 地址，POST JSON 通知
    },

    # 历史 URL 采集（Wayback / Common Crawl），默认关闭
    "historical_urls": {
        "enabled": False,
        "wayback": {"enabled": True, "max_results": 2000, "timeout": 30},
        "commoncrawl": {"enabled": False, "max_results": 1000}
    },

    # 子域接管检测（仅检测，不做任何认领/利用）
    "takeover": {
        "enabled": True,
        "http_probe": True,
        "timeout": 8,
        "detect_only": True
    },

    # HTTP请求配置
    "http": {
        "timeout": 10,       # 超时时间(秒)
        "retries": 3,        # 重试次数
        "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",  # 用户代理
        "verify_ssl": True,    # 是否验证SSL证书
        "proxy": None,         # 代理设置
        "follow_redirects": True,  # 是否跟随重定向
        "redirect_as_new_asset": True,  # 将重定向视为新资产
        "max_redirects": 5,    # 最大重定向次数
        "rate_limit": {        # 全局限速（礼貌扫描）
            "enabled": False,          # 是否启用限速
            "requests_per_second": 5,  # 全局平均 QPS 上限（令牌桶）
            "burst": 5,                # 允许的突发请求数
            "per_host_concurrency": 2  # 同一主机的最大并发请求数（0 表示不限制）
        }
    },

    # 跨运行缓存（DNS/证书/HTTP 结果）
    "cache": {
        "enabled": False,          # 默认关闭；开启后重扫/watch 显著提速
        "dir": ".cache/zsans",     # 缓存目录（sqlite 文件存放处）
        "ttl": {                   # 各命名空间的过期时间（秒）
            "http": 3600,
            "dns": 600,
            "cert": 86400
        },
        "max_entries": 100000,     # 最大缓存条目数
        "max_mb": 200,             # 缓存文件大小上限（MB）
        "bypass_in_watch": True    # --watch 模式旁路 HTTP 缓存（观察实时变化）
    }
}


def _normalize_ip(value):
    """归一化 IP 字符串：去掉 IPv6 方括号与 zone id 并压缩写法。

    非法输入原样返回（保持向后兼容，不抛异常）。IPv4 结果不变，
    因此旧 uid（如 ``ip:1.2.3.4``）保持稳定。
    """
    if not isinstance(value, str):
        return value
    text = value.strip()
    if text.startswith('[') and text.endswith(']'):
        text = text[1:-1]
    # 去掉 link-local 的 zone id，如 fe80::1%eth0
    if '%' in text:
        text = text.split('%', 1)[0]
    try:
        return str(ipaddress.ip_address(text))
    except ValueError:
        return value


class Asset:
    """资产基类，表示一个可被发现和关联的实体"""
    def __init__(self, value, asset_type, source="manual", depth=0):
        self.value = self._normalize_value(value, asset_type)
        self.type = asset_type
        self.source = source
        self.depth = depth
        self.state = "new"  # new, scanning, scanned, excluded, eliminated
        self.properties = {}
        self.properties["discovery_time"] = time.time()
        self.uid = self._generate_uid()

    @staticmethod
    def _normalize_value(value, asset_type):
        """统一资产值的大小写。

        - URL 资产只小写 scheme + host 部分，保留 path 大小写（路径区分大小写，
          http://HOST/API/File 与 http://host/api/file 是不同的资源）；
        - 其余类型整体小写。
        """
        if not isinstance(value, str):
            return value
        if asset_type == ASSET_TYPE_URL:
            try:
                from urllib.parse import urlsplit, urlunsplit
                parts = urlsplit(value.strip())
                scheme = parts.scheme.lower()
                netloc = parts.netloc.lower()
                path = parts.path
                # 归一化：无路径与根路径 "/" 视为同一 URL（https://x 与 https://x/）
                if path == '':
                    path = '/'
                return urlunsplit((scheme, netloc, path, parts.query, parts.fragment))
            except Exception:
                return value.lower()
        return value.lower()
    
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
    """IP资产（同时支持 IPv4 与 IPv6，IPv6 统一压缩为规范写法）"""
    def __init__(self, ip, source="manual", depth=0):
        ip = _normalize_ip(ip)
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
            # 去除末尾反斜杠（JS源码/正则抽取的转义残留，如 xxx.html\\\）
            cleaned_url = cleaned_url.rstrip('\\')
        
        super().__init__(cleaned_url, ASSET_TYPE_URL, source, depth)
        self.properties["url"] = cleaned_url
        parsed = urlparse(cleaned_url)
        self.properties["domain"] = parsed.netloc
        self.properties["path"] = parsed.path
        self.properties["scheme"] = parsed.scheme


class PortAsset(Asset):
    """端口资产。

    IPv6 地址用方括号包裹（``[2001:db8::1]:443``），避免与 ``ip:port`` 的
    冒号分隔歧义；IPv4 保持旧格式 ``1.2.3.4:80`` 不变。
    """
    def __init__(self, ip, port, service=None, source="manual", depth=0):
        ip = _normalize_ip(ip)
        host = f"[{ip}]" if isinstance(ip, str) and ':' in ip else ip
        value = f"{host}:{port}"
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
            port = kwargs.get("port")
            ip = value
            if isinstance(value, str):
                text = value.strip()
                if text.startswith('[') and ']' in text:
                    # [ipv6]:port
                    end = text.index(']')
                    ip = text[1:end]
                    rest = text[end + 1:]
                    if rest.startswith(':'):
                        try:
                            port = int(rest[1:])
                        except (ValueError, TypeError):
                            port = kwargs.get("port")
                elif text.count(':') == 1:
                    # ipv4:port；无括号的 IPv6 冒号多于一个，不拆分以免歧义
                    ip, _, port_str = text.rpartition(':')
                    try:
                        port = int(port_str)
                    except (ValueError, TypeError):
                        port = kwargs.get("port")
            return PortAsset(ip, port, kwargs.get("service"), source, depth)
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
        # uid -> 是否已在队列中等待处理（防止同一资产被重复入队）
        self._queued = {}
        
    def add(self, asset):
        """添加资产到队列。已存在(仍在队列中/处理中/已处理)返回 False，避免重复入队。"""
        with self.lock:
            uid = asset.uid
            if uid in self._queued:
                return False
            if asset.state in ("scanned", "eliminated", "excluded"):
                return False
            self.queue.append(asset)
            self._queued[uid] = True
            return True
            
    def get_next(self, strategy="priority_based"):
        """根据策略获取下一个资产。

        注意：出队后 uid 仍保留在 ``_queued`` 中，直到调用 :meth:`done` 才移除。
        这样在「已出队但尚未置为 scanning」的窗口内，重复发现不会把同一资产再次
        入队并交给第二个 worker 并发扫描。
        """
        with self.lock:
            if not self.queue:
                return None
            
            if strategy == "depth_first":
                asset = self._get_deepest()
            elif strategy == "breadth_first":
                asset = self._get_shallowest()
            elif strategy == "priority_based":
                asset = self._get_highest_priority()
            else:
                asset = self._get_oldest()
            return asset

    def done(self, asset):
        """标记资产处理结束（成功/失败/跳过），移出 ``_queued`` 以允许后续重新入队。"""
        with self.lock:
            self._queued.pop(asset.uid, None)
                
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
        self.type_counts = {}  # asset_type -> count of unique assets
    
    def add_asset(self, asset):
        """向后兼容入口：等价于 ``try_add_asset(asset)`` 的布尔结果。

        保留此方法供插件与既有调用方使用（``True`` 表示新增或可重新处理）。
        """
        return self.try_add_asset(asset) in ("added", "updated")

    def try_add_asset(self, asset, max_count=None):
        """原子地把资产加入图谱，可选执行类型配额限制。

        整个检查 + 占位都在同一把锁内完成，从而消除「先查配额、后加资产」在并发
        下最多超发 N 个（N=worker 数）的 TOCTOU 问题。

        Args:
            asset: 待加入的资产。
            max_count: 该资产类型的数量上限；None 表示不限制。

        Returns:
            "added"    新增节点（已占用一个类型配额）
            "updated"  已存在，属性/来源已更新，允许重新处理
            "exists"   已存在且处于 scanned/eliminated/scanning，无需处理
            "rejected" 超过 max_count，未入库；asset 置为 excluded 并写入原因
        """
        with self.lock:
            if asset.uid in self.nodes:
                # 如果资产已存在，检查其状态
                existing_asset = self.nodes[asset.uid]

                # 如果现有资产已经处理完成（scanned或eliminated），则不再处理
                if existing_asset.state in ["scanned", "eliminated"]:
                    return "exists"

                # 如果现有资产正在处理中（scanning），也不再处理
                if existing_asset.state == "scanning":
                    return "exists"

                # 如果现有资产处于初始状态或失败状态，更新节点信息并允许重新处理
                existing_asset.source = asset.source
                existing_asset.depth = asset.depth
                for _k, _v in asset.properties.items():
                    if _k != "discovery_time":
                        existing_asset.properties[_k] = _v
                return "updated"

            # 锁内检查配额，避免并发超发
            if max_count is not None and self.type_counts.get(asset.type, 0) >= max_count:
                asset.state = "excluded"
                asset.properties["excluded_reason"] = _("Exceeds {type} limit {limit}").format(
                    type=asset.type, limit=max_count)
                return "rejected"

            self.nodes[asset.uid] = asset
            self.type_counts[asset.type] = self.type_counts.get(asset.type, 0) + 1
            return "added"
    
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
    
    def export_json(self, metadata=None):
        with self.lock:
            data = dict(metadata or {})
            data.update({
                "nodes": [asset.to_dict() for asset in self.nodes.values()],
                "edges": [{
                    "source": source,
                    "target": target,
                    "relation": relation
                } for (source, target), relation in self.edges.items()]
            })
            return json.dumps(data, indent=2)
    
    def export_graphml(self):
        with self.lock:
            # 简化实现，实际应使用专门的图库如NetworkX
            xml = ['<?xml version="1.0" encoding="UTF-8"?>',
                  '<graphml xmlns="http://graphml.graphdrawing.org/xmlns">',
                  '<graph id="G" edgedefault="directed">',
                  '<!-- Nodes -->']

            # XML 特殊字符转义,避免 & < > 等破坏文件结构
            def _text(value):
                return escape(str(value))

            def _attr(value):
                return quoteattr(str(value))

            for uid, asset in self.nodes.items():
                xml.append(f'<node id={_attr(uid)}>'
                          f'<data key="type">{_text(asset.type)}</data>'
                          f'<data key="value">{_text(asset.value)}</data>'
                          f'</node>')

            xml.append('<!-- Edges -->')
            for (source, target), relation in self.edges.items():
                xml.append(f'<edge source={_attr(source)} target={_attr(target)}>'
                          f'<data key="relation">{_text(relation)}</data>'
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
