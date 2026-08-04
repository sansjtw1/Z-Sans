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
        self.timeout = self.config.get('timeout', 15)
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
            return f"https://{url}"
            
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
            if domain.endswith('.' + seed_domain):
                domain_parts = domain.split('.')
                seed_parts = seed_domain.split('.')
                
                if domain_parts[-len(seed_parts):] == seed_parts:
                    logger.info(_("Domain {domain} is a subdomain of seed domain {seed}").format(domain=domain, seed=seed_domain))
                    return True
                else:
                    logger.warning(_("Domain {domain} ends with .{seed} but is not a direct subdomain").format(domain=domain, seed=seed_domain))
                    continue
        
        for seed_domain in self.engine.seed_domains:
            domain_parts = domain.split('.')
            seed_parts = seed_domain.split('.')
            
            if len(domain_parts) >= len(seed_parts):
                if domain_parts[-len(seed_parts):] == seed_parts:
                    logger.info(_("Domain {domain} contains all parts of seed domain {seed}").format(domain=domain, seed=seed_domain))
                    return True
        
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
        try:
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
        subdomains = set()
        
        subfinder_enabled = self.config.get('asset_types', {}).get('domain', {}).get('tools', {}).get('subfinder', True)
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
        
        return list(subdomains)
    
    def _query_crtsh(self, domain):
        subdomains = set()
        try:
            url = f"https://crt.sh/?q=%25.{domain}&output=json"
            response = requests.get(url, headers=HEADERS, timeout=self.timeout)
            
            if response.status_code == 200:
                data = response.json()
                for entry in data:
                    name = entry.get("name_value", "")
                    for sub in name.split("\n"):
                        sub = sub.strip().lower()
                        if sub and f".{domain}" in sub:
                            subdomains.add(sub)
        except Exception as e:
            logger.error(_("crt.sh query failed: {error}").format(error=str(e)))
        
        return list(subdomains)
    
    def _resolve_domain(self, domain):
        ip_addresses = set()
        try:
            info = socket.getaddrinfo(domain, None)
            for _, _, _, _, sockaddr in info:
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
        
        if naabu_enabled and tool_manager and hasattr(tool_manager, 'run_naabu'):
            tool_ports = tool_manager.run_naabu(ip)
            open_ports.update(tool_ports)
        
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
            hostname, _, _ = socket.gethostbyaddr(ip)
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
        tools = asset_type_config.get('tools', [])
        
        if 'jsfinder' in tools and tool_manager and hasattr(tool_manager, 'run_jsfinder'):
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
        
        return new_assets
        
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
                allow_redirects=follow_redirects
            )
            logger.debug(_("HTTP request completed: {url}, status: {status}").format(url=url, status=response.status_code))
            
            if redirect_as_new_asset and response.history:
                for r in response.history:
                    if 300 <= r.status_code < 400:
                        self._handle_redirect(url, response.url)
                        logger.debug(_("Handling redirect: {from_} -> {to}").format(from_=url, to=response.url))
                        break
            
            if response.status_code == 200:
                logger.debug(_("URL request successful: {url}, status: 200").format(url=url))
                url_config = self.config.get('asset_types', {}).get('url', {})
                
                if url_config.get('tools', {}).get('fingerprint', False) and tool_manager and hasattr(tool_manager, 'run_ehole'):
                    try:
                        cleaned_url = url.strip()
                        while '`' in cleaned_url or '"' in cleaned_url:
                            cleaned_url = cleaned_url.replace('`', '').replace('"', '')
                        cleaned_url = cleaned_url.strip()
                        
                        if not cleaned_url.startswith(('http://', 'https://')):
                            logger.warning(_("Invalid URL format for fingerprinting: {url}").format(url=url))
                        else:
                            logger.info(_("Starting fingerprinting, original URL: {url}, cleaned URL: {cleaned}").format(url=url, cleaned=cleaned_url))
                            fingerprint_result = tool_manager.run_ehole(cleaned_url)
                            if fingerprint_result:
                                asset_uid = f"url:{url}"
                                if self.engine and hasattr(self.engine, 'asset_graph') and asset_uid in self.engine.asset_graph.nodes:
                                    asset = self.engine.asset_graph.nodes[asset_uid]
                                    asset.properties['fingerprints'] = fingerprint_result.get('fingerprints', [])
                                    if fingerprint_result.get('cms'):
                                        asset.properties['cms'] = fingerprint_result.get('cms')
                                    if fingerprint_result.get('server'):
                                        asset.properties['server'] = fingerprint_result.get('server')
                                    if fingerprint_result.get('title'):
                                        asset.properties['title'] = fingerprint_result.get('title')
                                        logger.info(_("Title extracted from EHole: {url}, title: {title}").format(url=url, title=fingerprint_result.get('title')))
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
                            title = soup.title.string.strip() if soup.title else _("No title")
                            
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
            logger.info(_("URL content fetch failed: {url}, {error}").format(url=url, error=str(e)))
            self._mark_asset_as_eliminated(url, _("Access failed: {error}").format(error=str(e)))
        return None
        
    def _handle_redirect(self, original_url, redirect_url):
        logger.debug(_("Redirect detected: {from_} -> {to}").format(from_=original_url, to=redirect_url))
        self.redirect_targets = getattr(self, 'redirect_targets', [])
        self.redirect_targets.append(redirect_url)
    
    def _mark_asset_as_eliminated(self, url, reason):
        logger.info(_("Marking asset as eliminated: {url}, reason: {reason}").format(url=url, reason=reason))
        if self.engine and hasattr(self.engine, 'asset_graph'):
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
            for a_tag in soup.find_all('a'):
                href = a_tag.get('href')
                if href:
                    link = self._normalize_url(href, base_url)
                    if link:
                        links.add(link)
        except Exception as e:
            logger.error(_("Link extraction failed: {error}").format(error=str(e)))
        
        return list(links)
    
    def _normalize_url(self, url, base_url):
        if not url or not isinstance(url, str):
            return None

        if url.startswith('javascript:') or url.startswith('#'):
            return None

        # 过滤系统路径
        if re.search(r'[A-Za-z]:\\', url) or url.startswith('//'):
            if url.startswith('//'):
                parsed_base = urlparse(base_url)
                return f"{parsed_base.scheme}:{url}"
            logger.warning(_("Skipping system path: {path}").format(path=url))
            return None
        
        if not url.startswith('http'):
            if url.startswith('/'):
                parsed_base = urlparse(base_url)
                return f"{parsed_base.scheme}://{parsed_base.netloc}{url}"
            
            if '.' in url and not url.startswith('/'):
                try:
                    if re.match(r'^[a-zA-Z0-9][-a-zA-Z0-9.]*\.[a-zA-Z]{2,}$', url):
                        return f"https://{url}"
                except Exception:
                    pass
            
            return requests.compat.urljoin(base_url, url)
        
        return url


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
        tools = asset_type_config.get('tools', [])
        
        if 'jsfinder' in tools and tool_manager and hasattr(tool_manager, 'run_jsfinder'):
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
        
        logger.debug(_("JS asset {url} processed, found {count} new assets").format(url=js_url, count=len(new_assets)))
        
        if self.engine and hasattr(self.engine, 'seed_domains') and self.engine.seed_domains:
            logger.debug(_("Current seed domains: {domains}").format(domains=', '.join(self.engine.seed_domains)))
        
        return new_assets
    
    def _fetch_js(self, js_url):
        try:
            response = requests.get(js_url, headers=HEADERS, timeout=self.timeout, verify=False)
            if response.status_code == 200:
                return response.text
            else:
                self._mark_asset_as_eliminated(js_url, _("HTTP status code: {status}").format(status=response.status_code))
        except Exception as e:
            logger.debug(_("JS content fetch failed: {url}, {error}").format(url=js_url, error=str(e)))
            self._mark_asset_as_eliminated(js_url, _("Access failed: {error}").format(error=str(e)))
        return None
        
    def _mark_asset_as_eliminated(self, js_url, reason):
        if self.engine and hasattr(self.engine, 'asset_graph'):
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


class PortBreeder(BreederBase):
    def execute(self, asset, tool_manager):
        if asset.type != ASSET_TYPE_PORT:
            logger.warning(_("Port breeder received non-port asset: {uid}").format(uid=asset.uid))
            return []
        
        ip, port = asset.value.split(':')
        port = int(port)
        service = asset.properties.get('service', 'unknown')
        new_assets = []
        
        if service in ['http', 'https', 'http-proxy', 'https-alt'] or port in [80, 443, 8080, 8443]:
            protocol = 'https' if port == 443 or port == 8443 or service == 'https' or service == 'https-alt' else 'http'
            url = f"{protocol}://{ip}:{port}"
            new_asset = URLAsset(url, source=asset.uid, depth=asset.depth+1)
            new_assets.append(new_asset)
        
        return new_assets


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