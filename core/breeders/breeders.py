#!/usr/bin/env python3
# coding: utf-8

import re
import socket
import ssl
import logging
from urllib.parse import urlparse, urljoin
import requests
from bs4 import BeautifulSoup
from concurrent.futures import ThreadPoolExecutor, as_completed
from core.i18n import _

from core.zsans_engine import (
    Asset, DomainAsset, IPAsset, URLAsset, PortAsset, JSAsset,
    ASSET_TYPE_DOMAIN, ASSET_TYPE_IP, ASSET_TYPE_URL, ASSET_TYPE_PORT, ASSET_TYPE_JS,
    AssetFactory
)

logger = logging.getLogger('zsans.breeders')

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
}

class BreederBase:
    def __init__(self, config=None, engine=None):
        self.config = config or {}
        self.timeout = self.config.get('http', {}).get('timeout', self.config.get('timeout', 15))
        self.engine = engine
    
    def execute(self, asset, tool_manager):
        raise NotImplementedError(_("Subclasses must implement this method"))
    
    def validate_asset(self, asset):
        return True
        
    def _normalize_url(self, url, base_url=None):
        if not url or not isinstance(url, str):
            return None
            
        if re.search(r'[A-Za-z]:\\', url) or url.startswith('//'):
            logger.warning(_("Skipping system path: {path}").format(path=url))
            return None
            
        if url.startswith('/'):
            if base_url:
                try:
                    base_parsed = urlparse(base_url)
                    base_domain = f"{base_parsed.scheme}://{base_parsed.netloc}"
                    return urljoin(base_domain, url)
                except Exception as e:
                    logger.warning(_("Failed to process relative path: {path}, error: {error}").format(path=url, error=str(e)))
                    return None
            else:
                return None
                
        if not url.startswith('http://') and not url.startswith('https://'):
            if '.' in url:
                try:
                    if re.match(r'^[a-zA-Z0-9][-a-zA-Z0-9.]*\.[a-zA-Z]{2,}$', url):
                        last_label = url.rsplit('.', 1)[-1].lower()
                        if last_label not in ('php', 'asp', 'aspx', 'jsp', 'htm', 'html', 'css', 'js', 'json', 'txt', 'png', 'jpg', 'gif', 'ico', 'svg', 'webp', 'xml', 'pdf', 'zip'):
                            return f"https://{url}"
                except Exception:
                    pass
            if base_url:
                try:
                    return urljoin(base_url, url)
                except Exception:
                    return None
            return None
            
        return url
        
    def _is_related_to_seed_domain(self, domain):
        if not self.engine:
            logger.warning(_("Engine instance does not exist, defaulting to False to avoid adding unrelated domains"))
            return False
            
        if not hasattr(self.engine, 'seed_domains'):
            logger.warning(_("Seed domains list attribute not found in engine, defaulting to False"))
            return False
            
        if not self.engine.seed_domains:
            logger.warning(_("Seed domains list is empty, defaulting to False"))
            return False
        
        if not domain or not isinstance(domain, str):
            logger.warning(_("Invalid domain format: {domain}").format(domain=domain))
            return False
            
        if '/' in domain or '\\' in domain or ' ' in domain or ':' in domain:
            logger.warning(_("Domain contains invalid characters: {domain}").format(domain=domain))
            return False
            
        if domain in self.engine.seed_domains:
            logger.info(_("Domain {domain} is a seed domain").format(domain=domain))
            return True
            
        for seed_domain in self.engine.seed_domains:
            domain_parts = domain.split('.')
            seed_parts = seed_domain.split('.')
            
            if len(domain_parts) >= len(seed_parts) and domain_parts[-len(seed_parts):] == seed_parts:
                if domain.endswith('.' + seed_domain):
                    logger.info(_("Domain {domain} is a subdomain of seed domain {seed}").format(domain=domain, seed=seed_domain))
                else:
                    logger.info(_("Domain {domain} contains all parts of seed domain {seed}").format(domain=domain, seed=seed_domain))
                return True
            elif domain.endswith('.' + seed_domain):
                logger.warning(_("Domain {domain} ends with .{seed} but is not a direct subdomain").format(domain=domain, seed=seed_domain))
        
        logger.warning(_("Domain {domain} is not related to any seed domain").format(domain=domain))
        return False
        
    def _is_in_seed_ip_range(self, ip):
        if not self.engine or not hasattr(self.engine, 'seed_ips') or not self.engine.seed_ips:
            return True
            
        if ip in self.engine.seed_ips:
            return True
            
        if hasattr(self.engine, 'seed_ip_ranges') and self.engine.seed_ip_ranges:
            for ip_range in self.engine.seed_ip_ranges:
                if self._ip_in_range(ip, ip_range):
                    return True
                    
        return False
        
    def _ip_in_range(self, ip, ip_range):
        """判断 IP 是否在指定范围/网段内。

        支持两种格式：
        - CIDR：如 `10.0.0.0/8`、`192.168.1.0/24`，按前缀精确匹配；
        - 旧式 /16 简写：如 `192.168.0.0`，仅比较前两段（兼容历史配置）。
        """
        try:
            if '/' in ip_range:
                import ipaddress
                return ipaddress.ip_address(ip) in ipaddress.ip_network(ip_range, strict=False)
            ip_parts = ip.split('.')
            range_parts = ip_range.split('.')
            return ip_parts[0] == range_parts[0] and ip_parts[1] == range_parts[1]
        except Exception as e:
            logger.error(_("IP range check error: {error}").format(error=str(e)))
            return False


class DomainBreeder(BreederBase):
    def execute(self, asset, tool_manager):
        if asset.type != ASSET_TYPE_DOMAIN:
            logger.warning(_("Domain breeder received non-domain asset: {uid}").format(uid=asset.uid))
            return []
        
        domain = asset.value
        new_assets = []
        
        restrict_to_seed_domains = self.config.get('asset_scope', {}).get('restrict_to_seed_domains', True)
        
        # include_subdomains: 关闭时跳过子域名枚举
        include_subdomains = self.config.get('asset_scope', {}).get('include_subdomains', True)
        if not include_subdomains:
            logger.info(_("Subdomain discovery disabled by config, skipping"))
            subdomains = []
        else:
            subdomains = self._discover_subdomains(domain, tool_manager)
        for subdomain in subdomains:
            if restrict_to_seed_domains:
                is_related = self._is_related_to_seed_domain(subdomain)
                logger.info(_("Subdomain relevance check: {subdomain}, related: {related}").format(subdomain=subdomain, related=is_related))
                if not is_related:
                    logger.warning(_("Skipping non-seed-related subdomain: {subdomain}").format(subdomain=subdomain))
                    continue
                else:
                    logger.info(_("Adding seed-related subdomain: {subdomain}").format(subdomain=subdomain))
            else:
                logger.info(_("Domain scope restriction disabled, adding all subdomains: {subdomain}").format(subdomain=subdomain))
                
            new_asset = DomainAsset(subdomain, source=asset.uid, depth=asset.depth+1)
            new_assets.append(new_asset)
        
        # include_ip_ranges: 关闭时跳过域名的 IP 解析与 IP 资产生成
        include_ip_ranges = self.config.get('asset_scope', {}).get('include_ip_ranges', True)
        if not include_ip_ranges:
            logger.info(_("IP range discovery disabled by config, skipping IP resolution"))
            ip_addresses = []
        else:
            ip_addresses = self._resolve_domain(domain)
        for ip in ip_addresses:
            restrict_to_seed_ip_ranges = self.config.get('asset_scope', {}).get('restrict_to_seed_ip_ranges', True)
            if restrict_to_seed_ip_ranges and not self._is_in_seed_ip_range(ip):
                logger.debug(_("Skipping IP outside seed range: {ip}").format(ip=ip))
                continue
                
            new_asset = IPAsset(ip, source=asset.uid, depth=asset.depth+1)
            new_assets.append(new_asset)
        
        cert_domains = self._check_certificate(domain)
        for cert_domain in cert_domains:
            if cert_domain != domain:
                if restrict_to_seed_domains and not self._is_related_to_seed_domain(cert_domain):
                    logger.debug(_("Skipping non-seed-related certificate domain: {domain}").format(domain=cert_domain))
                    continue
                    
                new_asset = DomainAsset(cert_domain, source=asset.uid, depth=asset.depth+1)
                new_assets.append(new_asset)
        
        for protocol in ['http', 'https']:
            url = f"{protocol}://{domain}"
            new_asset = URLAsset(url, source=asset.uid, depth=asset.depth+1)
            new_assets.append(new_asset)
        
        return new_assets
            
    def _discover_subdomains(self, domain, tool_manager):
        subfinder_enabled = self.config.get('asset_types', {}).get('domain', {}).get('tools', {}).get('subfinder', True)
        return self._collect_subdomains(domain, tool_manager, subfinder_enabled)
    
    def _collect_subdomains(self, domain, tool_manager, subfinder_enabled):
        subdomains = set()
        free_subfinder_enabled = self.config.get('asset_types', {}).get('domain', {}).get('tools', {}).get('free_subfinder', True)
        
        if subfinder_enabled and tool_manager and hasattr(tool_manager, 'run_subfinder'):
            logger.info(_("Using subfinder tool to discover subdomains: {domain}").format(domain=domain))
            tool_subdomains = tool_manager.run_subfinder(domain)
            if tool_subdomains:
                logger.info(_("Subfinder found {count} subdomains").format(count=len(tool_subdomains)))
                subdomains.update(tool_subdomains)
            else:
                logger.warning(_("Subfinder found no subdomains"))
        
        if free_subfinder_enabled and tool_manager and hasattr(tool_manager, 'run_free_subfinder'):
            logger.info(_("Using free-subfinder tool to discover subdomains: {domain}").format(domain=domain))
            free_subdomains = tool_manager.run_free_subfinder(domain)
            if free_subdomains:
                logger.info(_("Free-subfinder found {count} subdomains").format(count=len(free_subdomains)))
                subdomains.update(free_subdomains)
            else:
                logger.warning(_("Free-subfinder found no subdomains"))
        
        try:
            crtsh_enabled = self.config.get('asset_types', {}).get('domain', {}).get('tools', {}).get('crtsh', True)
            
            if crtsh_enabled:
                logger.info(_("Querying crt.sh for subdomains: {domain}").format(domain=domain))
                crt_subdomains = self._query_crtsh(domain)
                if crt_subdomains:
                    logger.info(_("crt.sh found {count} subdomains").format(count=len(crt_subdomains)))
                    subdomains.update(crt_subdomains)
            else:
                logger.info(_("crt.sh query disabled"))
            
        except Exception as e:
            logger.error(_("Subdomain discovery failed: {error}").format(error=str(e)))

        # 内置 DNS 暴力枚举：纯本地、无需外部工具，用常见子域词表探测 A 记录
        try:
            dns_brute_enabled = self.config.get('asset_types', {}).get('domain', {}).get('tools', {}).get('dns_brute', True)
            if dns_brute_enabled:
                logger.info(_("Running built-in DNS brute-force for subdomains: {domain}").format(domain=domain))
                brute_subdomains = self._dns_brute_subdomains(domain)
                if brute_subdomains:
                    logger.info(_("DNS brute-force found {count} subdomains").format(count=len(brute_subdomains)))
                    subdomains.update(brute_subdomains)
            else:
                logger.info(_("DNS brute-force disabled"))
        except Exception as e:
            logger.error(_("DNS brute-force failed: {error}").format(error=str(e)))
        
        return self._filter_subdomains(subdomains, domain)

    # 常见子域名字典（内置，免外部依赖）
    _BRUTE_SUBDOMAINS = [
        'www', 'mail', 'smtp', 'pop', 'pop3', 'imap', 'webmail', 'mx',
        'ftp', 'sftp', 'ssh', 'vpn', 'remote', 'portal', 'login', 'auth',
        'api', 'api2', 'apis', 'rest', 'gateway', 'mobile', 'm', 'app',
        'apps', 'admin', 'administrator', 'manage', 'manager', 'console',
        'dev', 'development', 'test', 'testing', 'staging', 'stage', 'qa',
        'beta', 'demo', 'preview', 'sandbox', 'uat', 'prod', 'production',
        'intranet', 'internal', 'office', 'crm', 'erp', 'oa', 'hrm', 'hr',
        'wiki', 'docs', 'documentation', 'help', 'support', 'status', 'health',
        'blog', 'news', 'forum', 'bbs', 'community', 'chat', 'im', 'media',
        'img', 'image', 'images', 'static', 'assets', 'cdn', 'download',
        'uploads', 'files', 'file', 'data', 'db', 'database', 'mysql', 'redis',
        'git', 'gitlab', 'github', 'svn', 'jenkins', 'ci', 'cd', 'build',
        'monitor', 'monitoring', 'grafana', 'zabbix', 'prometheus', 'metrics',
        'log', 'logs', 'logging', 'kibana', 'elastic', 'es', 'elk',
        'docker', 'k8s', 'kubernetes', 'registry', 'proxy', 'lb', 'nlb',
        'dns', 'ns1', 'ns2', 'ns3', 'mail1', 'mail2', 'mx1', 'mx2',
        'shop', 'store', 'pay', 'payment', 'order', 'cart', 'mobi', 'wap',
        'game', 'games', 'video', 'live', 'tv', 'radio', 'music', 'newsletter',
        'security', 'sso', 'oauth', 'saml', 'keycloak', 'cas', 'ldap', 'radius',
        'tracking', 'analytics', 'stats', 'report', 'reports', 'export',
        # 内容 / 服务 / 常见部署类子域
        'book', 'books', 'read', 'reader', 'novel', 'magazine', 'paper',
        'library', 'ebook', 'story', 'comic', 'article', 'doc', 'documents',
        'home', 'homepage', 'index', 'main', 'web', 'webapp', 'site', 'sites',
        'cms', 'phpmyadmin', 'phpadmin', 'mysqladmin', 'pgadmin',
        'graphql', 'grpc', 'websocket', 'socket', 'ws', 'wss', 'sse',
        'push', 'notify', 'notification', 'message', 'sms', 'mailchimp',
        'cdn2', 'cdn3', 'static2', 'static3', 'img2', 'img3', 'upload',
        'oss', 'cos', 'storage', 'bucket', 'backup', 'bak', 'temp', 'tmp',
        'cache', 'cached', 'sess', 'session', 'token', 'auth2', 'sso2',
        'staging2', 'dev2', 'test2', 'pre', 'preprod', 'canary', 'gray',
        'job', 'jobs', 'career', 'careers', 'hr', 'recruit', 'about',
        'contact', 'faq', 'service', 'services', 'product', 'products',
        'buy', 'sale', 'market', 'mall', 'order2', 'pay2', 'checkout',
        'wx', 'wechat', 'weixin', 'wechat2', 'alipay', 'unionpay',
        'android', 'ios', 'h5', 'wap2', 'm2', 'mini', 'miniapp', 'webview',
        'api-dev', 'api-test', 'api-staging', 'api-prod', 'api-internal',
        'gw', 'api-gw', 'api-gateway', 'open', 'openapi', 'public', 'pub',
    ]

    def _dns_brute_subdomains(self, domain, max_workers=30, timeout=3.0):
        """用内置词表暴力枚举子域名 A 记录，无需外部工具。

        返回解析成功的子域列表；网络异常时静默降级，不影响主流程。
        """
        import concurrent.futures as cf
        found = []

        def _probe(sub):
            full = f"{sub}.{domain}"
            try:
                import dns.resolver
                answers = dns.resolver.resolve(full, 'A', lifetime=timeout, raise_on_no_answer=True)
                return full if answers else None
            except Exception:
                return None

        try:
            with cf.ThreadPoolExecutor(max_workers=max_workers) as pool:
                futures = [pool.submit(_probe, sub) for sub in self._BRUTE_SUBDOMAINS]
                for future in cf.as_completed(futures, timeout=max(10, timeout * 8)):
                    try:
                        result = future.result()
                    except Exception:
                        result = None
                    if result:
                        found.append(result)
        except cf.TimeoutError:
            logger.warning(_("DNS brute-force timed out for {domain}").format(domain=domain))
        return found
    
    def _filter_subdomains(self, subdomains, domain):
        """过滤通配符(`*.`)以及格式非法的子域名，避免派生 `https://*.domain` 等无效资产"""
        filtered = set()
        for sub in subdomains:
            if not sub or not isinstance(sub, str):
                continue
            sub = sub.strip().lower()
            if sub.startswith('*'):
                logger.warning(_("Skipping wildcard subdomain: {subdomain}").format(subdomain=sub))
                continue
            if sub == domain:
                filtered.add(sub)
                continue
            if not re.match(r'^([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,}$', sub):
                logger.warning(_("Skipping invalid subdomain: {subdomain}").format(subdomain=sub))
                continue
            filtered.add(sub)
        return list(filtered)
    
    def _query_crtsh(self, domain):
        subdomains = set()
        try:
            url = f"https://crt.sh/?q=%25.{domain}&output=json"
            from core.zsans_engine import http_session
            response = http_session.get(url, timeout=self.timeout)
            
            if response.status_code == 200:
                data = response.json()
                for entry in data:
                    name = entry.get("name_value", "")
                    for sub in name.split("\n"):
                        sub = sub.strip().lower()
                        if sub and f".{domain}" in sub:
                            subdomains.add(sub)
                            if len(subdomains) >= 2000:
                                break
                    if len(subdomains) >= 2000:
                        break
        except Exception as e:
            logger.error(_("crt.sh query failed: {error}").format(error=str(e)))
        
        return list(subdomains)
    
    def _resolve_domain(self, domain):
        ip_addresses = set()
        try:
            info = socket.getaddrinfo(domain, None)
            for _family, _socktype, _proto, _canonname, sockaddr in info:
                ip = sockaddr[0]
                if self._is_valid_ip(ip):
                    ip_addresses.add(ip)
        except Exception as e:
            logger.error(_("Domain resolution failed: {error}").format(error=str(e)))
        
        return list(ip_addresses)
    
    def _is_valid_ip(self, ip):
        pattern = r'^(\d{1,3}\.){3}\d{1,3}$'
        return bool(re.match(pattern, ip))
    
    def _check_certificate(self, domain):
        cert_domains = set()
        try:
            context = ssl.create_default_context()
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
            
            with socket.create_connection((domain, 443), timeout=self.timeout) as sock:
                with context.wrap_socket(sock, server_hostname=domain) as ssock:
                    cert = ssock.getpeercert()
                    
                    if 'subjectAltName' in cert:
                        for type_name, value in cert['subjectAltName']:
                            if type_name == 'DNS':
                                cert_domains.add(value.lower())
        except Exception as e:
            logger.debug(_("Certificate check failed: {error}").format(error=str(e)))
        
        return list(cert_domains)


class IPBreeder(BreederBase):
    def execute(self, asset, tool_manager):
        if asset.type != ASSET_TYPE_IP:
            logger.warning(_("IP breeder received non-IP asset: {uid}").format(uid=asset.uid))
            return []
        
        ip = asset.value
        new_assets = []
        
        restrict_to_seed_ip_ranges = self.config.get('asset_scope', {}).get('restrict_to_seed_ip_ranges', True)
        if restrict_to_seed_ip_ranges and not self._is_in_seed_ip_range(ip):
            logger.debug(_("Skipping IP outside seed range: {ip}").format(ip=ip))
            return []
        
        open_ports = self._scan_ports(ip, tool_manager)
        for port, service in open_ports.items():
            new_asset = PortAsset(ip, port, service, source=asset.uid, depth=asset.depth+1)
            new_assets.append(new_asset)
            
            if service in ['http', 'https'] or port in [80, 443, 8080, 8443]:
                protocol = 'https' if port == 443 or port == 8443 or service == 'https' else 'http'
                url = f"{protocol}://{ip}:{port}"
                new_asset = URLAsset(url, source=asset.uid, depth=asset.depth+1)
                new_assets.append(new_asset)
        
        reverse_dns_enabled = self.config.get('asset_types', {}).get('ip', {}).get('tools', {}).get('reverse_dns', True)
        domains = []
        if reverse_dns_enabled:
            domains = self._reverse_dns(ip)
            logger.info(_("Reverse DNS for IP {ip} found {count} domains").format(ip=ip, count=len(domains)))
        else:
            logger.info(_("Reverse DNS query disabled"))
        for domain in domains:
            restrict_to_seed_domains = self.config.get('asset_scope', {}).get('restrict_to_seed_domains', True)
            if restrict_to_seed_domains:
                is_related = self._is_related_to_seed_domain(domain)
                logger.info(_("Reverse DNS domain relevance check: {domain}, related: {related}").format(domain=domain, related=is_related))
                if not is_related:
                    logger.warning(_("Skipping non-seed-related domain: {domain} from IP {ip}").format(domain=domain, ip=ip))
                    continue
                else:
                    logger.info(_("Adding seed-related domain: {domain} from IP {ip}").format(domain=domain, ip=ip))
            else:
                logger.info(_("Domain scope restriction disabled, adding all domains: {domain}").format(domain=domain))
                
            new_asset = DomainAsset(domain, source=asset.uid, depth=asset.depth+1)
            new_assets.append(new_asset)
        
        return new_assets
        
    def _scan_ports(self, ip, tool_manager):
        open_ports = {}
        
        naabu_enabled = self.config.get('asset_types', {}).get('ip', {}).get('tools', {}).get('naabu', True)
        
        used_external_scan = False
        if naabu_enabled and tool_manager and hasattr(tool_manager, 'run_naabu'):
            tool_ports = tool_manager.run_naabu(ip)
            open_ports.update(tool_ports)
            used_external_scan = bool(tool_ports)
        
        if used_external_scan:
            return open_ports
        
        common_ports = [21, 22, 23, 25, 53, 80, 110, 111, 135, 139, 143, 443, 445, 993, 995, 1723, 3306, 3389, 5900, 8080, 8443]
        
        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = {executor.submit(self._check_port, ip, port): port for port in common_ports}
            
            for future in as_completed(futures):
                port = futures[future]
                try:
                    is_open, service = future.result()
                    if is_open:
                        open_ports[port] = service
                except Exception as e:
                    logger.debug(_("Port {port} scan failed: {error}").format(port=port, error=str(e)))
        
        return open_ports
    
    def _check_port(self, ip, port):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(2)
        result = sock.connect_ex((ip, port))
        sock.close()
        
        if result == 0:
            service = self._identify_service(ip, port)
            return True, service
        else:
            self._mark_port_as_eliminated(ip, port, _("Port not open: {result}").format(result=result))
        return False, None
        
    def _mark_port_as_eliminated(self, ip, port, reason):
        if self.engine and hasattr(self.engine, 'asset_graph'):
            with self.engine.asset_graph.lock:
                asset_uid = f"port:{ip}:{port}"
                if asset_uid in self.engine.asset_graph.nodes:
                    asset = self.engine.asset_graph.nodes[asset_uid]
                    asset.state = "eliminated"
                    asset.properties['eliminated_reason'] = reason
                    logger.debug(_("Asset marked as eliminated: {ip}:{port}, reason: {reason}").format(ip=ip, port=port, reason=reason))
    
    def _identify_service(self, ip, port):
        service_map = {
            21: 'ftp',
            22: 'ssh',
            23: 'telnet',
            25: 'smtp',
            53: 'dns',
            80: 'http',
            81: 'hosts2-ns',
            110: 'pop3',
            143: 'imap',
            443: 'https',
            445: 'smb',
            591: 'http-alt',
            777: 'multiling-http',
            3306: 'mysql',
            3389: 'rdp',
            5900: 'vnc',
            8080: 'http-proxy',
            8443: 'https-alt',
            8081: 'http-alt',
            8088: 'radan-http',
            8990: 'http-wmap',
            8991: 'https-wmap',
            9418: 'git',
            9443: 'tungsten-https'
        }
        
        return service_map.get(port, 'unknown')
    
    def _reverse_dns(self, ip):
        domains = set()
        try:
            logger.info(_("Starting reverse DNS for IP: {ip}").format(ip=ip))
            hostname, _aliases, _addresses = socket.gethostbyaddr(ip)
            if hostname:
                hostname = hostname.lower()
                logger.info(_("Reverse DNS successful: IP {ip} resolved to {hostname}").format(ip=ip, hostname=hostname))
                domains.add(hostname)
        except (socket.herror, socket.gaierror) as e:
            logger.debug(_("Reverse DNS failed: IP {ip}, error: {error}").format(ip=ip, error=str(e)))
            pass
        
        return list(domains)


class URLBreeder(BreederBase):
    def execute(self, asset, tool_manager):
        self.current_asset = asset
        
        if asset.type != ASSET_TYPE_URL:
            logger.warning(_("URL breeder received non-URL asset: {uid}").format(uid=asset.uid))
            return []
        
        if asset.state == "scanned" or asset.state == "eliminated":
            logger.info(_("URL asset already processed, skipping: {uid}, state: {state}").format(uid=asset.uid, state=asset.state))
            return []
        
        asset.state = "scanning"
        
        url = asset.value
        new_assets = []
        
        self.redirect_targets = []
        
        asset_type_config = self.config.get('asset_types', {}).get('url', {})
        tools = asset_type_config.get('tools', {}) or {}
        
        if tools.get('jsfinder', False) and tool_manager and hasattr(tool_manager, 'run_jsfinder'):
            logger.info(_("Using JSFinder tool for URL: {url}").format(url=url))
            jsfinder_urls, jsfinder_subdomains = tool_manager.run_jsfinder(url)
            
            parsed_url = urlparse(url)
            domain = parsed_url.netloc
            
            restrict_to_seed_domains = self.config.get('asset_scope', {}).get('restrict_to_seed_domains', True)
            related_urls_count = 0
            total_urls_count = len(jsfinder_urls)
            
            for jsfinder_url in jsfinder_urls:
                normalized_url = self._normalize_url(jsfinder_url, url)
                if not normalized_url:
                    logger.debug(_("Skipping invalid URL: {url}").format(url=jsfinder_url))
                    continue
                
                try:
                    link_parsed = urlparse(normalized_url)
                    link_domain = link_parsed.netloc
                    
                    if not link_domain:
                        logger.debug(_("Skipping URL without domain: {url}").format(url=normalized_url))
                        continue
                        
                    if not link_parsed.scheme or not link_parsed.scheme.startswith('http'):
                        logger.debug(_("Skipping non-HTTP URL: {url}").format(url=normalized_url))
                        continue
                except Exception as e:
                    logger.debug(_("URL parsing failed: {url}, error: {error}").format(url=normalized_url, error=str(e)))
                    continue
                
                is_related = self._is_related_to_seed_domain(link_domain)
                
                if restrict_to_seed_domains:
                    if not is_related:
                        logger.warning(_("Skipping non-seed-related domain: {domain}, URL: {url}").format(domain=link_domain, url=normalized_url))
                        continue
                    else:
                        logger.info(_("Adding seed-related URL: {url}").format(url=normalized_url))
                        related_urls_count += 1
                else:
                    if is_related:
                        logger.info(_("Adding seed-related URL: {url}").format(url=normalized_url))
                        related_urls_count += 1
                    else:
                        logger.info(_("Adding non-seed-related URL (restriction disabled): {url}").format(url=normalized_url))
                
                new_asset = URLAsset(normalized_url, source=asset.uid, depth=asset.depth+1)
                new_asset.properties['source_tool'] = 'jsfinder'
                new_assets.append(new_asset)
                
                if domain != link_domain:
                    new_asset = DomainAsset(link_domain, source=asset.uid, depth=asset.depth+1)
                    new_asset.properties['source_tool'] = 'jsfinder'
                    new_assets.append(new_asset)
            
            if restrict_to_seed_domains:
                logger.info(_("JSFinder extracted {related} seed-related URLs from {url}, total: {total}").format(related=related_urls_count, url=url, total=total_urls_count))
            else:
                logger.info(_("JSFinder extracted {total} URLs from {url}, no domain restriction").format(total=total_urls_count, url=url))
                
            logger.info(_("JSFinder extracted {count} URLs from {url}").format(count=len(jsfinder_urls), url=url))
                
            for subdomain in jsfinder_subdomains:
                if re.search(r'[A-Za-z]:\\', subdomain) or '/' in subdomain or '\\' in subdomain or ' ' in subdomain or ':' in subdomain:
                    logger.warning(_("Skipping invalid subdomain format: {subdomain}").format(subdomain=subdomain))
                    continue
                    
                if subdomain.lower().startswith('output ') or subdomain.lower().startswith('path:'):
                    logger.warning(_("Skipping non-domain string: {subdomain}").format(subdomain=subdomain))
                    continue
                
                if not re.match(r'^[a-zA-Z0-9][-a-zA-Z0-9.]*\.[a-zA-Z]{2,}$', subdomain):
                    logger.warning(_("Skipping invalid domain format: {subdomain}").format(subdomain=subdomain))
                    continue
                
                if restrict_to_seed_domains and not self._is_related_to_seed_domain(subdomain):
                    logger.warning(_("Skipping non-seed-related subdomain: {subdomain}").format(subdomain=subdomain))
                    continue
                
                new_asset = DomainAsset(subdomain, source=asset.uid, depth=asset.depth+1)
                new_asset.properties['source_tool'] = 'jsfinder'
                new_assets.append(new_asset)
                logger.info(_("Adding seed-related subdomain: {subdomain}").format(subdomain=subdomain))

        
        html_content = self._fetch_url(url, tool_manager=tool_manager)
        
        redirect_as_new_asset = self.config.get('http', {}).get('redirect_as_new_asset', True)
        if redirect_as_new_asset and hasattr(self, 'redirect_targets') and self.redirect_targets:
            for redirect_url in self.redirect_targets:
                original_domain = urlparse(url).netloc
                redirect_domain = urlparse(redirect_url).netloc
                
                new_asset = URLAsset(redirect_url, source=asset.uid, depth=asset.depth+1)
                new_asset.properties['redirect_from'] = url
                new_assets.append(new_asset)
                
                if redirect_domain and redirect_domain != original_domain:
                    restrict_to_seed_domains = self.config.get('asset_scope', {}).get('restrict_to_seed_domains', True)
                    if not restrict_to_seed_domains or self._is_related_to_seed_domain(redirect_domain):
                        new_asset = DomainAsset(redirect_domain, source=asset.uid, depth=asset.depth+1)
                        new_asset.properties['redirect_from'] = url
                        new_assets.append(new_asset)
                    else:
                        logger.debug(_("Skipping non-seed-related redirect domain: {domain}").format(domain=redirect_domain))

        # 方案C闭环：将响应头捕获的端点提示物化为 URL 资产
        try:
            if self.engine and hasattr(self.engine, 'asset_graph'):
                asset_uid = f"url:{url}"
                with self.engine.asset_graph.lock:
                    nodes = self.engine.asset_graph.nodes
                    if asset_uid in nodes:
                        hints = nodes[asset_uid].properties.get('http_endpoint_hints', {})
                        for hint_url in hints.values():
                            if not hint_url:
                                continue
                            hint_url = hint_url.strip()
                            if not hint_url.startswith(('http://', 'https://')) and not hint_url.startswith('/'):
                                continue
                            normalized_hint = self._normalize_url(hint_url, url)
                            if not normalized_hint:
                                continue
                            new_asset = URLAsset(normalized_hint, source=asset.uid, depth=asset.depth+1)
                            new_asset.properties['source_tool'] = 'http_endpoint_hint'
                            new_assets.append(new_asset)
                            logger.debug(_("Endpoint hint materialized as asset: {target}").format(target=normalized_hint))
        except Exception as e:
            logger.debug(_("Endpoint hint materialization failed: {error}").format(error=str(e)))
        
        if not html_content:
            asset.state = "eliminated"
            if 'eliminated_reason' not in asset.properties:
                asset.properties['eliminated_reason'] = _("Unable to fetch content")
            
            if self.engine and hasattr(self.engine, 'asset_graph'):
                asset_uid = asset.uid
                if asset_uid in self.engine.asset_graph.nodes:
                    graph_asset = self.engine.asset_graph.nodes[asset_uid]
                    graph_asset.state = "eliminated"
                    if 'eliminated_reason' not in graph_asset.properties:
                        graph_asset.properties['eliminated_reason'] = _("Unable to fetch content")
                    logger.info(_("Updated asset graph state to eliminated: {uid}, state: {state}").format(uid=asset_uid, state=graph_asset.state))
                else:
                    logger.info(_("Asset not found in graph: {uid}").format(uid=asset_uid))
            else:
                logger.info(_("Unable to access asset graph, cannot update state: {url}").format(url=url))
            
            logger.info(_("URL asset content fetch failed, marked as eliminated: {url}, state: {state}").format(url=url, state=asset.state))
            return new_assets
        
        parsed_url = urlparse(url)
        domain = parsed_url.netloc
        
        js_files = self._extract_js_files(html_content, url)
        for js_url in js_files:
            new_asset = JSAsset(js_url, source=asset.uid, depth=asset.depth+1)
            new_assets.append(new_asset)
        
        if tools.get('link_extract', True):
            links = self._extract_links(html_content, url)
            for link in links:
                restrict_to_seed_domains = self.config.get('asset_scope', {}).get('restrict_to_seed_domains', True)
                
                link_domain = urlparse(link).netloc
                
                if restrict_to_seed_domains and link_domain:
                    is_related = self._is_related_to_seed_domain(link_domain)
                    logger.info(_("URL link domain relevance: {domain}, related: {related}").format(domain=link_domain, related=is_related))
                    if not is_related:
                        logger.warning(_("Skipping non-seed-related link: {link}, domain: {domain}").format(link=link, domain=link_domain))
                        continue
                    else:
                        logger.info(_("Adding seed-related link: {link}, domain: {domain}").format(link=link, domain=link_domain))
                else:
                    if not link_domain:
                        logger.info(_("Link has no domain part, possibly relative: {link}").format(link=link))
                    else:
                        logger.info(_("Domain scope restriction disabled, adding all links: {link}, domain: {domain}").format(link=link, domain=link_domain))
                    
                if domain == link_domain or not link_domain:
                    new_asset = URLAsset(link, source=asset.uid, depth=asset.depth+1)
                    new_assets.append(new_asset)
                else:
                    if not restrict_to_seed_domains or self._is_related_to_seed_domain(link_domain):
                        new_asset = DomainAsset(link_domain, source=asset.uid, depth=asset.depth+1)
                        new_assets.append(new_asset)
                    else:
                        logger.debug(_("Skipping non-seed-related domain asset: {domain}").format(domain=link_domain))

        # robots.txt / sitemap.xml：隐藏路径与站点地图是子路径/新URL的高发来源
        if tools.get('link_extract', True):
            discovery_links = self._fetch_robots_sitemap(url)
            for dl in discovery_links:
                restrict_to_seed_domains = self.config.get('asset_scope', {}).get('restrict_to_seed_domains', True)
                dl_domain = urlparse(dl).netloc
                if restrict_to_seed_domains and dl_domain and not self._is_related_to_seed_domain(dl_domain):
                    logger.debug(_("Skipping non-seed-related discovery link: {link}").format(link=dl))
                    continue
                new_asset = URLAsset(dl, source=asset.uid, depth=asset.depth+1)
                new_asset.properties['source_tool'] = 'robots_sitemap'
                new_assets.append(new_asset)
        
        return new_assets

    def _fetch_robots_sitemap(self, url):
        """抓取同源的 robots.txt 与 sitemap.xml，解析出其中的 URL 路径。

        仅在链接提取开启时调用；失败静默降级，不影响主流程。
        """
        found = []
        try:
            from urllib.parse import urlparse
            from core.zsans_engine import http_session
            parsed = urlparse(url)
            origin = f"{parsed.scheme}://{parsed.netloc}"
            candidates = [
                f"{origin}/robots.txt",
                f"{origin}/sitemap.xml",
            ]
            for cu in candidates:
                try:
                    resp = http_session.get(cu, timeout=min(self.timeout, 10), verify=False)
                    if resp.status_code != 200 or not resp.text:
                        continue
                    text = resp.text
                    # robots.txt: 提取 Allow / Disallow / Sitemap 行
                    for line in text.splitlines():
                        line = line.strip()
                        if not line or line.startswith('#') or ':' not in line:
                            continue
                        key, _sep, val = line.partition(':')
                        key = key.strip().lower()
                        val = val.strip()
                        if not val:
                            continue
                        if key in ('allow', 'disallow'):
                            link = self._normalize_url(val, origin)
                            if link:
                                found.append(link)
                        elif key == 'sitemap':
                            found.append(val)
                    # sitemap.xml: 提取 <loc> 标签内容
                    for m in re.finditer(r'<loc>\s*(.*?)\s*</loc>', text, re.I | re.S):
                        loc = m.group(1).strip()
                        if loc:
                            found.append(loc)
                except Exception as e:
                    logger.debug(_("Discovery fetch failed for {candidate}: {error}").format(candidate=cu, error=str(e)))
        except Exception as e:
            logger.debug(_("robots/sitemap discovery failed: {error}").format(error=str(e)))
        return found
    
        
    def _fetch_url(self, url, tool_manager=None):
        try:
            follow_redirects = self.config.get('http', {}).get('follow_redirects', True)
            redirect_as_new_asset = self.config.get('http', {}).get('redirect_as_new_asset', True)
            max_redirects = self.config.get('http', {}).get('max_redirects', 5)
            
            logger.info(_("Processing URL: {url}").format(url=url))
            
            if self.engine and hasattr(self.engine, 'asset_graph'):
                asset_uid = f"url:{url}"
                if asset_uid in self.engine.asset_graph.nodes:
                    asset = self.engine.asset_graph.nodes[asset_uid]
                    logger.info(_("URL asset exists in graph: {uid}, state: {state}").format(uid=asset_uid, state=asset.state))
                else:
                    logger.info(_("URL asset not found in graph: {uid}").format(uid=asset_uid))
            
            logger.info(_("Sending HTTP request: {url}").format(url=url))
            from core.zsans_engine import http_session
            response = http_session.get(
                url,
                allow_redirects=follow_redirects,
                timeout=self.timeout
            )
            logger.debug(_("HTTP request completed: {url}, status: {status}").format(url=url, status=response.status_code))
            
            # 编码兜底：避免 charset 缺失或声明错误时乱码/空标题
            try:
                if not response.encoding or response.encoding.lower() in ('iso-8859-1', 'latin-1', 'ascii'):
                    response.encoding = response.apparent_encoding or 'utf-8'
            except Exception:
                pass

            # 方案C：记录响应头中暴露的接口提示（Link、X-Endpoint 等）
            try:
                self._record_endpoint_headers(response, url)
            except Exception as e:
                logger.debug(_("Endpoint header capture failed for {url}: {error}").format(url=url, error=str(e)))
            
            if redirect_as_new_asset and response.history:
                for r in response.history:
                    if 300 <= r.status_code < 400:
                        self._handle_redirect(url, response.url)
                        logger.debug(_("Handling redirect: {from_} -> {to}").format(from_=url, to=response.url))
                        break
            
            if response.status_code == 200:
                logger.debug(_("URL request successful: {url}, status: 200").format(url=url))
                url_config = self.config.get('asset_types', {}).get('url', {})
                
                if url_config.get('tools', {}).get('fingerprint', False) and tool_manager and hasattr(tool_manager, 'run_fingerprint'):
                    try:
                        cleaned_url = url.strip()
                        while '`' in cleaned_url or '"' in cleaned_url:
                            cleaned_url = cleaned_url.replace('`', '').replace('"', '')
                        cleaned_url = cleaned_url.strip()
                        
                        if not cleaned_url.startswith(('http://', 'https://')):
                            logger.warning(_("Invalid URL format for fingerprinting: {url}").format(url=url))
                        else:
                            logger.info(_("Starting fingerprinting, original URL: {url}, cleaned URL: {cleaned}").format(url=url, cleaned=cleaned_url))
                            fingerprint_result = tool_manager.run_fingerprint(cleaned_url)
                            if fingerprint_result:
                                asset_uid = f"url:{url}"
                                if self.engine and hasattr(self.engine, 'asset_graph') and asset_uid in self.engine.asset_graph.nodes:
                                    with self.engine.asset_graph.lock:
                                        asset = self.engine.asset_graph.nodes[asset_uid]
                                        asset.properties['fingerprints'] = fingerprint_result.get('fingerprints', [])
                                        if fingerprint_result.get('cms'):
                                            asset.properties['cms'] = fingerprint_result.get('cms')
                                        if fingerprint_result.get('server'):
                                            asset.properties['server'] = fingerprint_result.get('server')
                                        if fingerprint_result.get('title'):
                                            asset.properties['title'] = fingerprint_result.get('title')
                                            logger.info(_("Title extracted from fingerprint tool: {url}, title: {title}").format(url=url, title=fingerprint_result.get('title')))
                                        logger.info(_("URL fingerprint extracted: {url}, fingerprints: {fingerprints}").format(url=url, fingerprints=fingerprint_result.get('fingerprints')))
                            else:
                                logger.warning(_("Fingerprinting returned no results: {url}").format(url=url))
                    except Exception as e:
                        logger.info(_("Fingerprinting failed: {url}, {error}").format(url=url, error=str(e)))
                
                title_extraction_config = url_config.get('title_extraction', {})
                if title_extraction_config.get('enabled', True):
                    asset_uid = f"url:{url}"
                    title_already_extracted = False
                    if self.engine and hasattr(self.engine, 'asset_graph') and asset_uid in self.engine.asset_graph.nodes:
                        if 'title' in self.engine.asset_graph.nodes[asset_uid].properties:
                            title_already_extracted = True
                            logger.info(_("Title already extracted from EHole, skipping BeautifulSoup: {url}").format(url=url))
                    
                    if not title_already_extracted:
                        try:
                            soup = BeautifulSoup(response.text, 'html.parser')
                            title_tag = soup.title
                            title = title_tag.get_text(strip=True) if title_tag else _("No title")
                            
                            parsed_url = urlparse(url)
                            domain = parsed_url.netloc
                            if title == domain or not title:
                                title = _("No title")
                            
                            max_length = title_extraction_config.get('max_length', 50)
                            if len(title) > max_length:
                                title = title[:max_length] + "..."
                            
                            if self.engine and hasattr(self.engine, 'asset_graph'):
                                if asset_uid in self.engine.asset_graph.nodes:
                                    self.engine.asset_graph.nodes[asset_uid].properties['title'] = title
                                    logger.info(_("Title extracted from BeautifulSoup: {url}, title: {title}").format(url=url, title=title))
                        except Exception as e:
                            logger.info(_("Title extraction failed: {url}, {error}").format(url=url, error=str(e)))
                else:
                    logger.info(_("Title extraction disabled for URL: {url}").format(url=url))
                
                return response.text
            else:
                logger.info(_("URL request returned non-200 status: {url}, status: {status}").format(url=url, status=response.status_code))
                self._mark_asset_as_eliminated(url, _("HTTP status code: {status}").format(status=response.status_code))
                
                if self.engine and hasattr(self.engine, 'asset_graph'):
                    asset_uid = f"url:{url}"
                    if asset_uid in self.engine.asset_graph.nodes:
                        asset = self.engine.asset_graph.nodes[asset_uid]
                        asset.properties['status_code'] = response.status_code
                        asset.state = "eliminated"
                        logger.info(_("URL asset marked as eliminated: {url}, status: {status}, state: {state}").format(url=url, status=response.status_code, state=asset.state))
                        
                        logger.info(_("Rechecking asset state: {uid}, state: {state}").format(uid=asset_uid, state=asset.state))
                    else:
                        logger.info(_("Asset not found in graph, cannot set state: {uid}").format(uid=asset_uid))
                else:
                    logger.info(_("Unable to access asset graph, cannot set state: {url}").format(url=url))
        except Exception as e:
            error_str = str(e)
            # 检测代理错误，触发自动降级
            if 'ProxyError' in error_str or 'proxy' in error_str.lower():
                from core.zsans_engine import report_proxy_failure
                proxy_disabled = report_proxy_failure()
                if proxy_disabled:
                    logger.warning(
                        _("Proxy auto-disabled. Will retry {url} with direct connection on next cycle.").format(url=url)
                    )
                else:
                    logger.warning(
                        _("Proxy error detected for {url}. Check your proxy configuration.").format(url=url)
                    )
            logger.info(_("URL content fetch failed: {url}, {error}").format(url=url, error=error_str))
            self._mark_asset_as_eliminated(url, _("Access failed: {error}").format(error=error_str))
        return None
        
    def _handle_redirect(self, original_url, redirect_url):
        logger.debug(_("Redirect detected: {from_} -> {to}").format(from_=original_url, to=redirect_url))
        self.redirect_targets = getattr(self, 'redirect_targets', [])
        self.redirect_targets.append(redirect_url)
    
    def _mark_asset_as_eliminated(self, url, reason):
        logger.info(_("Marking asset as eliminated: {url}, reason: {reason}").format(url=url, reason=reason))
        if self.engine and hasattr(self.engine, 'asset_graph'):
            with self.engine.asset_graph.lock:
                asset_uid = f"url:{url}"
                logger.info(_("Checking if asset exists in graph: {uid}").format(uid=asset_uid))
                if asset_uid in self.engine.asset_graph.nodes:
                    asset = self.engine.asset_graph.nodes[asset_uid]
                    logger.info(_("Found asset: {uid}, current state: {state}").format(uid=asset_uid, state=asset.state))
                    asset.state = "eliminated"
                    asset.properties['eliminated_reason'] = reason
                    logger.info(_("Asset state updated to eliminated: {uid}, new state: {state}").format(uid=asset_uid, state=asset.state))
                    
                    if reason.startswith(_("HTTP status code:")):
                        try:
                            status_code = int(reason.split(":")[1].strip())
                            asset.properties['status_code'] = status_code
                            logger.info(_("Status code set for asset: {uid}, code: {code}").format(uid=asset_uid, code=status_code))
                        except (ValueError, IndexError):
                            logger.info(_("Unable to extract status code from reason: {reason}").format(reason=reason))
                    
                    logger.info(_("Asset marked as eliminated: {url}, reason: {reason}, state: {state}").format(url=url, reason=reason, state=asset.state))
                    
                    logger.info(_("Rechecking asset state: {uid}, state: {state}").format(uid=asset_uid, state=self.engine.asset_graph.nodes[asset_uid].state))
                else:
                    logger.info(_("Asset not found in graph: {uid}").format(uid=asset_uid))
        else:
            logger.info(_("Unable to access asset graph, cannot mark asset: {url}").format(url=url))
            
        if hasattr(self, 'current_asset') and self.current_asset and self.current_asset.type == "url" and self.current_asset.value == url:
            logger.info(_("Updating current asset state: {url}").format(url=url))
            self.current_asset.state = "eliminated"
            self.current_asset.properties['eliminated_reason'] = reason
            logger.info(_("Current asset state updated to eliminated: {url}, state: {state}").format(url=url, state=self.current_asset.state))
        else:
            logger.info(_("No current URL asset being processed: {url}").format(url=url))


    def _record_endpoint_headers(self, response, url):
        """方案C：从响应头提取接口/资源提示，记入图谱资产 properties。

        部分后端会在 Link / X-Endpoint / X-Api 等头里暴露接口地址，
        这些不会出现在页面 HTML 中，属于"行为侧"线索。
        """
        endpoint_header_keys = (
            'Link', 'X-Endpoint', 'X-Endpoint-Url', 'X-Api', 'X-Api-Base',
            'X-Resource', 'X-Resource-Url', 'X-Service', 'X-Service-Url',
            'X-Href', 'X-Origin-Endpoint',
        )
        hints = {}
        for key in endpoint_header_keys:
            val = response.headers.get(key)
            if val:
                hints[key] = val

        # 从 Link 头解析 <url> 与 rel
        link_header = response.headers.get('Link')
        if link_header:
            for m in re.finditer(r'<([^>]+)>;\s*rel="?([^";,]+)"?', link_header):
                target, rel = m.group(1), m.group(2).strip()
                if target.startswith(('http://', 'https://')) or target.startswith('/'):
                    hints.setdefault('Link:' + rel, target)

        if not hints:
            return
        if self.engine and hasattr(self.engine, 'asset_graph'):
            asset_uid = f"url:{url}"
            with self.engine.asset_graph.lock:
                nodes = self.engine.asset_graph.nodes
                if asset_uid in nodes:
                    nodes[asset_uid].properties.setdefault('http_endpoint_hints', {})
                    nodes[asset_uid].properties['http_endpoint_hints'].update(hints)
                    logger.debug(_("Captured endpoint hints for {url}: {hints}").format(url=url, hints=hints))


    def _extract_js_files(self, html_content, base_url):
        js_files = set()
        try:
            soup = BeautifulSoup(html_content, 'html.parser')
            for script in soup.find_all('script'):
                src = script.get('src')
                if src:
                    js_url = self._normalize_url(src, base_url)
                    if js_url:
                        js_files.add(js_url)
                        logger.debug(_("JS file extracted from HTML: {url}").format(url=js_url))
        except Exception as e:
            logger.error(_("JS extraction failed: {error}").format(error=str(e)))
        
        return list(js_files)
    
    def _extract_links(self, html_content, base_url):
        links = set()
        try:
            soup = BeautifulSoup(html_content, 'html.parser')
            # <a href> 常规链接
            for a_tag in soup.find_all('a'):
                href = a_tag.get('href')
                if href:
                    link = self._normalize_url(href, base_url)
                    if link:
                        links.add(link)
            # iframe / frame src
            for tag in soup.find_all(['iframe', 'frame']):
                src = tag.get('src')
                if src:
                    link = self._normalize_url(src, base_url)
                    if link:
                        links.add(link)
            # form action
            for form in soup.find_all('form'):
                action = form.get('action')
                if action:
                    link = self._normalize_url(action, base_url)
                    if link:
                        links.add(link)
            # meta refresh / meta og:url
            for meta in soup.find_all('meta'):
                http_equiv = (meta.get('http-equiv') or '').lower()
                content = meta.get('content')
                if content:
                    if http_equiv == 'refresh':
                        # format: 5;url=https://...
                        m = re.search(r'url\s*=\s*(.+?)\s*$', content, re.I)
                        if m:
                            link = self._normalize_url(m.group(1).strip(), base_url)
                            if link:
                                links.add(link)
                    prop = (meta.get('property') or '').lower()
                    if prop in ('og:url', 'twitter:url'):
                        link = self._normalize_url(content.strip(), base_url)
                        if link:
                            links.add(link)
            # img / link / video / audio / source / embed / object / area
            for tag in soup.find_all(['img', 'link', 'video', 'audio', 'source', 'embed', 'object', 'area']):
                for attr in ('src', 'href', 'data'):
                    val = tag.get(attr)
                    if val:
                        link = self._normalize_url(val, base_url)
                        if link:
                            links.add(link)
            # HTML 注释中的 URL（开发/测试地址泄露高发区）
            for comment in soup.find_all(string=lambda s: isinstance(s, str) and '<!--' in s):
                for m in re.finditer(r'https?://[^\s"\'<>()]+', str(comment)):
                    link = self._normalize_url(m.group(0).rstrip('.,;:!?'), base_url)
                    if link:
                        links.add(link)
        except Exception as e:
            logger.error(_("Link extraction failed: {error}").format(error=str(e)))
        
        return list(links)
    
    def _normalize_url(self, url, base_url):
        if not url or not isinstance(url, str):
            return None

        url = url.strip()
        if url.startswith('javascript:') or url.startswith('#'):
            return None

        # 过滤系统路径
        if re.search(r'[A-Za-z]:\\', url):
            logger.warning(_("Skipping system path: {path}").format(path=url))
            return None

        if url.startswith('//'):
            parsed_base = urlparse(base_url)
            url = f"{parsed_base.scheme}:{url}"

        if url.startswith('http'):
            normalized = url
        elif url.startswith('/'):
            parsed_base = urlparse(base_url)
            normalized = f"{parsed_base.scheme}://{parsed_base.netloc}{url}"
        elif '.' in url:
            if re.match(r'^[a-zA-Z0-9][-a-zA-Z0-9.]*\.[a-zA-Z]{2,}$', url):
                last_label = url.rsplit('.', 1)[-1].lower()
                if last_label not in ('php', 'asp', 'aspx', 'jsp', 'htm', 'html', 'css', 'js', 'json', 'txt', 'png', 'jpg', 'gif', 'ico', 'svg', 'webp', 'xml', 'pdf', 'zip'):
                    normalized = f"https://{url}"
                else:
                    normalized = requests.compat.urljoin(base_url, url)
            else:
                normalized = requests.compat.urljoin(base_url, url)
        else:
            normalized = requests.compat.urljoin(base_url, url)

        if not normalized:
            return None

        # 过滤 JS 源码字符串拼接/模板衍生的畸形 URL（如 "...js/' + u + '"）
        if re.search(r'[\s\'"\\{}\[\]]', normalized):
            logger.debug(_("Skipping malformed URL (JS concat/template): {url}").format(url=normalized))
            return None

        if not normalized.lower().startswith(('http://', 'https://')):
            return None

        return normalized


class JSBreeder(BreederBase):
    def execute(self, asset, tool_manager):
        if asset.type != ASSET_TYPE_JS:
            logger.warning(_("JS breeder received non-JS asset: {uid}").format(uid=asset.uid))
            return []
            
        if self.engine and hasattr(self.engine, 'seed_domains'):
            if not self.engine.seed_domains:
                logger.warning(_("Seed domains list is empty, all domains will be considered unrelated"))
            else:
                logger.info(_("Current seed domains: {domains}").format(domains=', '.join(self.engine.seed_domains)))
        else:
            logger.warning(_("Seed domains list attribute not found in engine, all domains will be considered unrelated"))
        
        js_url = asset.value
        new_assets = []
        
        asset_type_config = self.config.get('asset_types', {}).get('js', {})
        tools = asset_type_config.get('tools', {}) or {}
        
        if tools.get('jsfinder', False) and tool_manager and hasattr(tool_manager, 'run_jsfinder'):
            logger.info(_("Using JSFinder tool for JS: {url}").format(url=js_url))
            jsfinder_urls, jsfinder_subdomains = tool_manager.run_jsfinder(js_url)
            
            parsed_url = urlparse(js_url)
            domain = parsed_url.netloc
            
            restrict_to_seed_domains = self.config.get('asset_scope', {}).get('restrict_to_seed_domains', True)
            related_count = 0
            total_count = 0
            
            for jsfinder_url in jsfinder_urls:
                normalized_url = self._normalize_url(jsfinder_url, js_url)
                if not normalized_url:
                    logger.debug(_("Skipping invalid URL: {url}").format(url=jsfinder_url))
                    continue
                
                try:
                    link_parsed = urlparse(normalized_url)
                    link_domain = link_parsed.netloc
                    
                    if not link_domain:
                        logger.debug(_("Skipping URL without domain: {url}").format(url=normalized_url))
                        continue
                        
                    if not link_parsed.scheme or not link_parsed.scheme.startswith('http'):
                        logger.debug(_("Skipping non-HTTP URL: {url}").format(url=normalized_url))
                        continue
                except Exception as e:
                    logger.debug(_("URL parsing failed: {url}, error: {error}").format(url=normalized_url, error=str(e)))
                    continue
                
                is_related = self._is_related_to_seed_domain(link_domain)
                
                if restrict_to_seed_domains:
                    if is_related:
                        logger.info(_("Domain {domain} is seed-related").format(domain=link_domain))
                        related_count += 1
                        new_asset = URLAsset(normalized_url, source=asset.uid, depth=asset.depth+1)
                        new_asset.properties['source_tool'] = 'jsfinder'
                        new_assets.append(new_asset)
                        logger.info(_("Adding seed-related URL asset: {url}").format(url=normalized_url))
                        total_count += 1
                    else:
                        logger.warning(_("Skipping non-seed-related domain: {domain}, URL: {url}").format(domain=link_domain, url=normalized_url))
                else:
                    new_asset = URLAsset(normalized_url, source=asset.uid, depth=asset.depth+1)
                    new_asset.properties['source_tool'] = 'jsfinder'
                    total_count += 1
                    if is_related:
                        related_count += 1
                        logger.info(_("Domain {domain} is seed-related, adding URL asset: {url}").format(domain=link_domain, url=normalized_url))
                    else:
                        logger.info(_("Domain {domain} not seed-related but added due to no restriction: {url}").format(domain=link_domain, url=normalized_url))
                    new_assets.append(new_asset)
            
            if restrict_to_seed_domains:
                logger.info(_("JSFinder extracted {related} seed-related URLs from {url}, total: {total}").format(related=related_count, url=js_url, total=len(jsfinder_urls)))
            else:
                logger.info(_("JSFinder extracted {total} URLs from {url}, {related} seed-related").format(total=total_count, url=js_url, related=related_count))
            
            subdomains_count = 0
            related_subdomains_count = 0
            
            for subdomain in jsfinder_subdomains:
                if not subdomain or not isinstance(subdomain, str):
                    logger.debug(_("Skipping invalid subdomain: {subdomain}").format(subdomain=subdomain))
                    continue
                    
                if re.search(r'[A-Za-z]:\\', subdomain) or '/' in subdomain or '\\' in subdomain or ' ' in subdomain or ':' in subdomain:
                    logger.debug(_("Skipping subdomain with invalid characters: {subdomain}").format(subdomain=subdomain))
                    continue
                
                if not re.match(r'^[a-zA-Z0-9][-a-zA-Z0-9]*\.[a-zA-Z0-9][-a-zA-Z0-9\.]*$', subdomain):
                    logger.debug(_("Skipping malformed subdomain: {subdomain}").format(subdomain=subdomain))
                    continue
                    
                subdomains_count += 1
                    
                is_related = self._is_related_to_seed_domain(subdomain)
                
                if restrict_to_seed_domains:
                    if not is_related:
                        logger.warning(_("Skipping non-seed-related subdomain: {subdomain}").format(subdomain=subdomain))
                        continue
                    else:
                        logger.info(_("Subdomain {subdomain} is seed-related, adding as domain asset").format(subdomain=subdomain))
                        related_subdomains_count += 1
                else:
                    if is_related:
                        logger.info(_("Subdomain {subdomain} is seed-related, adding as domain asset").format(subdomain=subdomain))
                        related_subdomains_count += 1
                    else:
                        logger.info(_("Subdomain {subdomain} not seed-related but added due to no restriction").format(subdomain=subdomain))
                
                new_asset = DomainAsset(subdomain, source=asset.uid, depth=asset.depth+1)
                new_asset.properties['source_tool'] = 'jsfinder'
                new_assets.append(new_asset)
            
            if restrict_to_seed_domains:
                logger.info(_("JSFinder extracted {related} seed-related subdomains from {url}, total: {total}").format(related=related_subdomains_count, url=js_url, total=len(jsfinder_subdomains)))
            else:
                logger.info(_("JSFinder extracted {count} subdomains from {url}, {related} seed-related").format(count=subdomains_count, url=js_url, related=related_subdomains_count))
        
        js_content = self._fetch_js(js_url)
        if not js_content:
            return new_assets
        
        urls = self._extract_urls_from_js(js_content)
        logger.info(_("Extracted {count} URLs from JS content (filtered)").format(count=len(urls)))
        
        for url in urls:
            if url.startswith('http'):
                try:
                    parsed_url = urlparse(url)
                    domain = parsed_url.netloc
                    
                    new_asset = URLAsset(url, source=asset.uid, depth=asset.depth+1)
                    new_assets.append(new_asset)
                    logger.info(_("Adding seed-related URL asset: {url}").format(url=url))
                except Exception as e:
                    logger.warning(_("URL processing failed: {url}, error: {error}").format(url=url, error=str(e)))
                    continue
            else:
                logger.warning(_("Skipping non-HTTP URL: {url}").format(url=url))
                continue

        # 从 JS 内容中提取 API/接口路径（方案B：字符串拼接、baseURL 变量、任意路径片段）
        try:
            api_urls = self._extract_paths_from_js(js_content, js_url)
            for api_url in api_urls:
                restrict_to_seed_domains = self.config.get('asset_scope', {}).get('restrict_to_seed_domains', True)
                api_domain = urlparse(api_url).netloc
                if restrict_to_seed_domains and api_domain and not self._is_related_to_seed_domain(api_domain):
                    logger.debug(_("Skipping non-seed-related API path: {url}").format(url=api_url))
                    continue
                new_asset = URLAsset(api_url, source=asset.uid, depth=asset.depth+1)
                new_asset.properties['source_tool'] = 'js_api_extract'
                new_assets.append(new_asset)
        except Exception as e:
            logger.debug(_("API path extraction failed: {error}").format(error=str(e)))
        
        logger.debug(_("JS asset {url} processed, found {count} new assets").format(url=js_url, count=len(new_assets)))
        
        if self.engine and hasattr(self.engine, 'seed_domains') and self.engine.seed_domains:
            logger.debug(_("Current seed domains: {domains}").format(domains=', '.join(self.engine.seed_domains)))
        
        return new_assets
    
    def _fetch_js(self, js_url):
        try:
            from core.zsans_engine import http_session
            response = http_session.get(js_url, headers=HEADERS, timeout=self.timeout, verify=False)
            if response.status_code == 200:
                return response.text
            else:
                self._mark_asset_as_eliminated(js_url, _("HTTP status code: {status}").format(status=response.status_code))
        except Exception as e:
            error_str = str(e)
            if 'ProxyError' in error_str or 'proxy' in error_str.lower():
                from core.zsans_engine import report_proxy_failure
                report_proxy_failure()
            logger.debug(_("JS content fetch failed: {url}, {error}").format(url=js_url, error=error_str))
            self._mark_asset_as_eliminated(js_url, _("Access failed: {error}").format(error=error_str))
        return None
        
    def _mark_asset_as_eliminated(self, js_url, reason):
        if self.engine and hasattr(self.engine, 'asset_graph'):
            with self.engine.asset_graph.lock:
                asset_uid = f"js:{js_url}"
                if asset_uid in self.engine.asset_graph.nodes:
                    asset = self.engine.asset_graph.nodes[asset_uid]
                    asset.state = "eliminated"
                    asset.properties['eliminated_reason'] = reason
                    logger.debug(_("Asset marked as eliminated: {url}, reason: {reason}").format(url=js_url, reason=reason))
    
    def _extract_urls_from_js(self, js_content):
        pattern = r'https?://[^\s"\'\{\}\(\)\[\]\<\>\`]+'            
        
        matches = re.findall(pattern, js_content)
        
        restrict_to_seed_domains = self.config.get('asset_scope', {}).get('restrict_to_seed_domains', True)
        
        filtered_urls = []
        related_count = 0
        
        if self.engine and hasattr(self.engine, 'seed_domains') and self.engine.seed_domains:
            logger.info(_("Current seed domains: {domains}").format(domains=', '.join(self.engine.seed_domains)))
        else:
            logger.warning(_("Seed domains list not found, domain relevance may be inaccurate"))
        
        clean_matches = []
        for url in matches:
            if '${' in url or '`' in url or '}' in url or ')' in url:
                logger.warning(_("Skipping possible JavaScript code URL: {url}").format(url=url))
                continue
            clean_matches.append(url)
        
        logger.info(_("Preliminary extraction: {initial} URLs, cleaned: {cleaned} valid URLs").format(initial=len(matches), cleaned=len(clean_matches)))
        
        for url in clean_matches:
            try:
                parsed_url = urlparse(url)
                domain = parsed_url.netloc
                
                if not domain:
                    logger.warning(_("Skipping URL without domain: {url}").format(url=url))
                    continue
                
                is_related = self._is_related_to_seed_domain(domain)
                
                if restrict_to_seed_domains:
                    if is_related:
                        logger.info(_("Domain {domain} is seed-related, adding URL asset").format(domain=domain))
                        filtered_urls.append(url)
                        related_count += 1
                    else:
                        logger.warning(_("Domain {domain} not seed-related, skipping URL: {url}").format(domain=domain, url=url))
                else:
                    filtered_urls.append(url)
                    if is_related:
                        related_count += 1
                        logger.info(_("Domain {domain} is seed-related").format(domain=domain))
                    else:
                        logger.info(_("Domain {domain} not seed-related but added due to no restriction").format(domain=domain))
            except Exception as e:
                logger.warning(_("Pre-filtering: URL processing failed: {url}, error: {error}").format(url=url, error=str(e)))
                continue
        
        if restrict_to_seed_domains:
            logger.info(_("Extracted {count} seed-related URLs from JS content").format(count=related_count))
        else:
            logger.info(_("Extracted {related} seed-related URLs from JS content, total: {total}").format(related=related_count, total=len(filtered_urls)))
        
        logger.info(_("Extracted {count} URLs from JS content (filtered)").format(count=len(filtered_urls)))
        
        return filtered_urls

    def _extract_api_paths_from_js(self, js_content, js_url):
        """从 JS 源码中提取相对 API 路径并基于 JS 源域名拼成完整 URL。

        常见模式：/api/xxx、/v1/xxx、/rest/xxx、/graphql 等。
        """
        try:
            from urllib.parse import urlparse as _up
            parsed = _up(js_url)
            base = f"{parsed.scheme}://{parsed.netloc}"
        except Exception:
            return []

        # 匹配引号包裹的相对 API 路径
        pattern = r'["\'](/(?:api|v\d+|rest|graphql|service|services|rpc|jsonrpc)[^"\']*?)["\']'
        seen = set()
        results = []
        try:
            for m in re.finditer(pattern, js_content):
                path = m.group(1)
                # 过滤带模板/拼接/通配符的
                if any(ch in path for ch in '${}`%'):
                    continue
                if not path.startswith('/') or path == '/':
                    continue
                if path in seen:
                    continue
                seen.add(path)
                results.append(f"{base}{path}")
        except Exception as e:
            logger.debug(_("API path regex failed: {error}").format(error=str(e)))
        return results

    # 常见 API base / endpoint 变量名
    _PATH_VAR_NAMES = re.compile(
        r'\b(?:baseURL|baseUrl|base_url|BASE_URL|apiBase|apiUrl|api_url|API_URL'
        r'|endpoint|endpoints|serverUrl|server_url|host|hostname'
        r'|requestUrl|ajaxUrl|httpBase|urlBase|resourceUrl)\b'
    )

    def _extract_paths_from_js(self, js_content, js_url):
        """综合提取 JS 源码中可能存在的 API / 接口路径（方案B）。

        比 `_extract_api_paths_from_js` 更进一步：
          B1) 字符串拼接字面量组合：'..' + '..' 相邻字面量拼成完整路径
          B2) 常见 baseURL/endpoint 变量赋值提取
          B3) 任意引号包裹的路径片段（不限于 /api /v1 前缀）
        所有路径最终基于 JS 源域名拼成完整 URL 返回。
        """
        try:
            from urllib.parse import urlparse as _up
            parsed = _up(js_url)
            base = f"{parsed.scheme}://{parsed.netloc}"
        except Exception:
            return []

        paths = set()
        try:
            # B1) 字符串拼接：'a' + 'b' + 'c' … 整条连续字面量链拼起来
            concat_chain = re.compile(
                r'((?:[\'"`](?:[^\\\'\"]|\\.)*[\'"`])\s*\+\s*)+'
                r'[\'"`]([^\'\"`]*)[\'"`]', re.S
            )
            for m in concat_chain.finditer(js_content):
                combined = m.group(0)
                # 剥掉 + 号和引号，拼接各字面量片段
                pieces = re.findall(r'[\'"`]([^\'\"`]*)[\'"`]', combined)
                if not pieces:
                    continue
                joined = ''.join(pieces)
                if self._looks_like_api_path(joined):
                    paths.add(joined)
                # 也记录部分前缀（拼接链的中间态，如 '/api' 单独作为基础路径）
                prefix = ''
                for piece in pieces:
                    prefix += piece
                    if self._looks_like_api_path(prefix):
                        paths.add(prefix)

            # B2) baseURL/endpoint 等变量赋值
            assign_pattern = re.compile(
                r'\b(?:baseURL|baseUrl|base_url|apiBase|apiUrl|api_url|API_URL'
                r'|endpoint|endpoints|serverUrl|server_url|requestUrl|ajaxUrl'
                r'|httpBase|urlBase|resourceUrl)\b\s*[:=]\s*'
                r'([\'"`])([^\'\"`]+?)\1', re.I
            )
            for m in assign_pattern.finditer(js_content):
                val = m.group(2).strip()
                # baseURL/endpoint 变量赋值允许单段路径（如 '/gateway'）
                if val.startswith('http') or (val.startswith('/') and not any(
                        ch in val for ch in '${}`\\') and not re.search(
                        r'\.(?:png|jpe?g|gif|svg|webp|ico|css|js|mjs|woff2?|ttf|eot|map|json)$', val, re.I)):
                    paths.add(val)

            # B3) 任意引号包裹的相对路径片段（开头是 /，含至少一个子路径）
            generic_pattern = re.compile(
                r'[\'"`](/[A-Za-z0-9_\-./{}?=&:%]+?)[\'"`]'
            )
            for m in generic_pattern.finditer(js_content):
                path = m.group(1)
                if self._looks_like_api_path(path):
                    paths.add(path)

            # B4) webpack 模块路径映射（如 "abc": function(...) 前的字符串多为资源路径）
            wp_pattern = re.compile(
                r'[\'"`](/static/(?:js|css|img|media|assets|chunks?)/[^\'"`]+)[\'"`]'
            )
            for m in wp_pattern.finditer(js_content):
                path = m.group(1)
                if self._looks_like_api_path(path):
                    paths.add(path)
        except Exception as e:
            logger.debug(_("JS path extraction failed: {error}").format(error=str(e)))

        results = []
        seen = set()
        for p in sorted(paths):
            if p.startswith('http'):
                full = p
            elif p.startswith('/'):
                full = f"{base}{p}"
            else:
                continue
            if full in seen:
                continue
            seen.add(full)
            results.append(full)
        return results

    @staticmethod
    def _looks_like_api_path(path):
        """判定一个字符串片段是否像可用的 API / 资源路径。"""
        if not path:
            return False
        p = path.strip()
        if len(p) < 2 or len(p) > 300:
            return False
        # 含模板/拼接/通配符/换行的直接排除
        if any(ch in p for ch in '${}`\\'):
            return False
        if p.startswith('http'):
            # 完整 URL 交给 _extract_urls_from_js，这里仍可接受但需是 http(s)
            return p.startswith(('http://', 'https://'))
        if not p.startswith('/'):
            return False
        # 排除静态资源文件（图/样式/脚本/字体/地图等）
        if re.search(r'\.(?:png|jpe?g|gif|svg|webp|ico|css|js|mjs|woff2?|ttf|eot|map|json)$', p, re.I):
            return False
        # 排除单段短路径（如 /a）；baseURL 变量赋值场景由调用方放宽
        segments = [s for s in p.split('/') if s]
        if len(segments) < 2:
            return False
        return True


class PortBreeder(BreederBase):
    def execute(self, asset, tool_manager):
        if asset.type != ASSET_TYPE_PORT:
            logger.warning(_("Port breeder received non-port asset: {uid}").format(uid=asset.uid))
            return []

        # 兼容 IPv6：value 可能形如 2001:db8::1:443 或多个冒号，用 rsplit 取端口。
        try:
            ip, port_str = asset.value.rsplit(':', 1)
            port = int(port_str)
        except (ValueError, AttributeError) as e:
            logger.warning(_("Invalid port asset value: {value}, error: {error}").format(value=asset.value, error=str(e)))
            return []

        service = asset.properties.get('service', 'unknown')
        new_assets = []
        
        # 对任意开放端口生成 http(s) URL 资产：很多 Web 服务跑在非标端口上。
        # 常见 Web 端口优先用对应协议；其余端口默认尝试 http。
        web_http_ports = {80, 8000, 8001, 8008, 8080, 8081, 8088, 8090, 8888, 9000, 9090, 3000, 5000, 7001, 8881}
        web_https_ports = {443, 8443, 9443}
        if service in ('http', 'http-proxy', 'http-alt') or port in web_http_ports:
            protocol = 'http'
        elif service in ('https', 'https-alt') or port in web_https_ports:
            protocol = 'https'
        elif service not in ('ftp', 'ssh', 'smtp', 'dns', 'mysql', 'rdp', 'mongodb', 'redis') and not self._is_known_nonweb_port(port):
            # 未知服务但非典型非Web端口时，也尝试 http 探测
            protocol = 'http'
        else:
            return new_assets

        # IPv6 地址需用方括号包裹，否则 urlparse 无法识别 host/port
        ip_for_url = f"[{ip}]" if ':' in ip else ip
        url = f"{protocol}://{ip_for_url}:{port}"
        new_asset = URLAsset(url, source=asset.uid, depth=asset.depth+1)
        new_assets.append(new_asset)
        
        return new_assets

    @staticmethod
    def _is_known_nonweb_port(port):
        """已知的明确非 Web 协议端口，不生成 http URL。"""
        return port in {
            21, 22, 23, 25, 53, 67, 68, 69, 110, 111, 119, 123, 135, 137,
            138, 139, 143, 161, 162, 179, 445, 465, 514, 587, 636, 873,
            990, 993, 995, 1080, 1433, 1521, 2049, 2181, 2375, 3306, 3389,
            5432, 5672, 5900, 6379, 7002, 8009, 9042, 9200, 9300, 11211,
            27017, 27018, 28017, 50070, 50030,
        }


class BreederFactory:
    @staticmethod
    def get_breeder(asset_type, config=None, engine=None):
        if asset_type == ASSET_TYPE_DOMAIN:
            return DomainBreeder(config, engine)
        elif asset_type == ASSET_TYPE_IP:
            return IPBreeder(config, engine)
        elif asset_type == ASSET_TYPE_URL:
            return URLBreeder(config, engine)
        elif asset_type == ASSET_TYPE_JS:
            return JSBreeder(config, engine)
        elif asset_type == ASSET_TYPE_PORT:
            return PortBreeder(config, engine)
        else:
            logger.warning(_("Unknown asset type: {type}, using base breeder").format(type=asset_type))
            return BreederBase(config, engine)