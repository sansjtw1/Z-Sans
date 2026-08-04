#!/usr/bin/env python3
# coding: utf-8

import json
import logging
import os
import re
import subprocess
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse, urljoin
from bs4 import BeautifulSoup
from core.i18n import _

logger = logging.getLogger('zsans.tools')

class ToolOrchestrator:
    def __init__(self, config=None, engine=None):
        self.config = config or {}
        self.engine = engine
        self.concurrency = self.config.get('concurrency', {}).get('max_tasks', 5)
        self.executor = ThreadPoolExecutor(max_workers=self.concurrency)
        self.running_tasks = {}
        self.lock = threading.Lock()
        
        self.tool_paths = {}
        if 'external_tools' in self.config and 'paths' in self.config['external_tools']:
            self.tool_paths = self.config['external_tools']['paths']
            logger.debug(_("Loaded external tool path configuration: {paths}").format(paths=self.tool_paths))
        else:
            logger.debug(_("No external tool path configuration found, using default paths"))
            
        tools_to_remove = []
        for tool, path in self.tool_paths.items():
            if path is None:
                tools_to_remove.append(tool)
                logger.warning(_("Tool {tool} path is empty, ignoring configuration").format(tool=tool))
            elif not os.path.isabs(path):
                self.tool_paths[tool] = os.path.abspath(path)
        
        for tool in tools_to_remove:
            self.tool_paths.pop(tool)
    
    def run_subfinder(self, domain):
        if not self._check_tool_exists('subfinder'):
            logger.warning(_("Subfinder tool not found, using internal method instead"))
            return self._run_internal_dns_resolver(domain)
        
        subdomains = []
        try:
            with tempfile.NamedTemporaryFile(delete=False, mode='w+t') as temp_file:
                temp_path = temp_file.name
            
            subfinder_path = 'subfinder'
            
            if 'subfinder' in self.tool_paths:
                tool_path = self.tool_paths['subfinder']
                if os.path.exists(tool_path):
                    subfinder_path = tool_path
                    logger.info(_("Using subfinder path from configuration: {path}").format(path=subfinder_path))
            elif os.name == 'nt' and os.path.exists(os.path.join('assets', 'subfinder.exe')):
                subfinder_path = os.path.abspath(os.path.join('assets', 'subfinder.exe'))
                logger.info(_("Using subfinder.exe from assets directory: {path}").format(path=subfinder_path))
            
            cmd = [subfinder_path, '-d', domain, '-o', temp_path, '-silent']
            logger.info(_("Executing command: {cmd}").format(cmd=' '.join(cmd)))
            
            try:
                if os.name == 'nt' and subfinder_path.endswith('.exe'):
                    cmd_str = f'"{subfinder_path}" -d {domain} -o "{temp_path}" -silent'
                    logger.info(_("Using shell execution: {cmd}").format(cmd=cmd_str))
                    subprocess.run(cmd_str, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=True)
                else:
                    subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            except subprocess.CalledProcessError as e:
                logger.error(_("Failed to use {path}: {error}").format(path=subfinder_path, error=e.stderr.decode() if e.stderr else str(e)))
                if subfinder_path != 'subfinder':
                    logger.info(_("Trying subfinder from system PATH"))
                    cmd = ['subfinder', '-d', domain, '-o', temp_path, '-silent']
                    logger.info(_("Executing command: {cmd}").format(cmd=' '.join(cmd)))
                    try:
                        subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                    except subprocess.CalledProcessError:
                        logger.warning(_("System PATH subfinder failed, using internal method instead"))
                        return self._run_internal_dns_resolver(domain)
                else:
                    raise
            
            with open(temp_path, 'r') as f:
                for line in f:
                    subdomain = line.strip()
                    if subdomain:
                        subdomains.append(subdomain)
            
            os.unlink(temp_path)
        except subprocess.CalledProcessError as e:
            logger.error(_("Subfinder execution failed: {error}").format(error=e.stderr.decode() if e.stderr else str(e)))
            return self._run_internal_dns_resolver(domain)
        except Exception as e:
            logger.error(_("Subfinder call exception: {error}").format(error=str(e)))
            return self._run_internal_dns_resolver(domain)
        
        return subdomains
        
    def _run_internal_dns_resolver(self, domain):
        logger.info(_("Using internal DNS resolver for {domain}").format(domain=domain))
        subdomains = []
        
        try:
            dnsx_enabled = self.config.get('asset_types', {}).get('domain', {}).get('tools', {}).get('dnsx', True)
            if not dnsx_enabled:
                logger.info(_("DNSx resolution disabled"))
                return subdomains
            
            if not os.path.exists('assets/dnsxs.py'):
                logger.error(_("Internal DNS resolver dnsxs.py does not exist"))
                return []
                
            with tempfile.NamedTemporaryFile(delete=False, mode='w+t') as temp_file:
                temp_path = temp_file.name
            
            cmd = ['python', 'assets/dnsxs.py', domain, '-t', 'A', '-q']
            process = subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            
            try:
                output = process.stdout.decode('utf-8').strip()
            except UnicodeDecodeError:
                output = process.stdout.decode('latin-1').strip()
            
            if output:
                subdomains.append(domain)
                
                if os.path.exists('assets/free-subfinder.py'):
                    free_subdomains = self.run_free_subfinder(domain)
                    if free_subdomains:
                        subdomains.extend(free_subdomains)
        except subprocess.CalledProcessError as e:
            error_msg = e.stderr.decode('utf-8') if e.stderr else str(e)
            logger.error(_("Internal DNS resolver execution failed: {error}").format(error=error_msg))
        except Exception as e:
            logger.error(_("Internal DNS resolver call exception: {error}").format(error=str(e)))
        
        return list(set(subdomains))
    
    def run_naabu(self, ip):
        naabu_path = self.tool_paths.get('naabu')
        if not naabu_path or not self._check_tool_exists('naabu'):
            logger.warning(_("Naabu tool not found or invalid path, using internal method instead"))
            return self._run_internal_port_scanner(ip)
        
        open_ports = {}
        try:
            with tempfile.NamedTemporaryFile(delete=False, mode='w+t') as temp_file:
                temp_path = temp_file.name
            
            cmd = [naabu_path, '-host', ip, '-json', '-o', temp_path, '-silent']
            logger.info(_("Executing naabu command: {cmd}").format(cmd=' '.join(cmd)))
            subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            
            with open(temp_path, 'r') as f:
                for line in f:
                    try:
                        result = json.loads(line)
                        port = result.get('port')
                        if port:
                            open_ports[port] = 'unknown'
                    except json.JSONDecodeError:
                        continue
            
            os.unlink(temp_path)
        except subprocess.CalledProcessError as e:
            logger.error(_("Naabu execution failed: {error}").format(error=e.stderr.decode() if e.stderr else str(e)))
            return self._run_internal_port_scanner(ip)
        except Exception as e:
            logger.error(_("Naabu call exception: {error}").format(error=str(e)))
            return self._run_internal_port_scanner(ip)
        
        return open_ports
        
    def _run_internal_port_scanner(self, ip):
        logger.info(_("Using internal port scanner for {ip}").format(ip=ip))
        open_ports = {}
        try:
            if not os.path.exists('assets/port.py'):
                logger.error(_("Internal port scanner port.py does not exist"))
                return {}
                
            with tempfile.NamedTemporaryFile(delete=False, mode='w+t') as temp_file:
                temp_path = temp_file.name
            
            cmd = ['python', 'assets/port.py', ip, '-p', '1-1024,8000-8100', '-q']
            process = subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            
            output = process.stdout.decode().strip()
            if output:
                for line in output.split('\n'):
                    try:
                        port = int(line.strip())
                        open_ports[port] = 'unknown'
                    except ValueError:
                        continue
        except subprocess.CalledProcessError as e:
            logger.error(_("Internal port scanner execution failed: {error}").format(error=e.stderr.decode() if e.stderr else str(e)))
        except Exception as e:
            logger.error(_("Internal port scanner call exception: {error}").format(error=str(e)))
        
        return open_ports
    
    def run_jsfinder(self, url):
        if not url or not isinstance(url, str):
            logger.warning(_("Invalid URL format: {url}").format(url=url))
            return [], []
            
        if re.search(r'[A-Za-z]:\\', url) or url.startswith('//'):
            logger.warning(_("Skipping system path: {url}").format(url=url))
            return [], []
            
        if not url.startswith('http://') and not url.startswith('https://'):
            url = f"https://{url}"
            logger.info(_("Normalized URL: {url}").format(url=url))
            
        if not os.path.exists('assets/JSfinder.py'):
            logger.warning(_("JSfinder.py not found, using internal method instead"))
            return self._internal_jsfinder(url)
            
        logger.info(_("Starting JSFinder for URL: {url}").format(url=url))
        
        urls = []
        subdomains = []
        try:
            with tempfile.NamedTemporaryFile(delete=False, mode='w+t') as url_file:
                url_path = url_file.name
            with tempfile.NamedTemporaryFile(delete=False, mode='w+t') as subdomain_file:
                subdomain_path = subdomain_file.name
            
            cmd = ['python', 'assets/JSfinder.py', '-u', url, '-ou', url_path, '-os', subdomain_path]
            logger.debug(_("Executing JSFinder command: {cmd}").format(cmd=' '.join(cmd)))
            process = subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120, text=True, encoding='utf-8')
            if process.stdout:
                logger.debug(_("JSFinder full output:\n{output}").format(output=process.stdout))
            if process.stderr:
                logger.warning(_("JSFinder error output:\n{error}").format(error=process.stderr))
            
            logger.debug(_("JSFinder execution completed, exit code: {code}").format(code=process.returncode))
            
            try:
                with open(url_path, 'r') as f:
                    for line in f:
                        found_url = line.strip()
                        if found_url:
                            if re.search(r'[A-Za-z]:\\', found_url) or found_url.startswith('//'):
                                logger.warning(_("Skipping system path URL: {url}").format(url=found_url))
                                continue
                            urls.append(found_url)
                            logger.debug(_("JSFinder found URL: {url}").format(url=found_url))
            except Exception as e:
                logger.error(_("Failed to read JSFinder URL result file: {error}").format(error=str(e)))
            
            try:
                with open(subdomain_path, 'r') as f:
                    for line in f:
                        subdomain = line.strip()
                        if subdomain:
                            if re.search(r'[A-Za-z]:\\', subdomain) or '/' in subdomain or '\\' in subdomain or ' ' in subdomain or ':' in subdomain:
                                logger.warning(_("Skipping invalid subdomain: {subdomain}").format(subdomain=subdomain))
                                continue
                            subdomains.append(subdomain)
                            logger.debug(_("JSFinder found subdomain: {subdomain}").format(subdomain=subdomain))
            except Exception as e:
                logger.error(_("Failed to read JSFinder subdomain result file: {error}").format(error=str(e)))
            
            try:
                os.unlink(url_path)
                os.unlink(subdomain_path)
            except Exception as e:
                logger.error(_("Failed to delete JSFinder temporary files: {error}").format(error=str(e)))
            
            if process.stdout:
                output_lines = process.stdout.splitlines()
                url_section = False
                subdomain_section = False
                
                for line in output_lines:
                    if "Find" in line and "URL:" in line:
                        url_section = True
                        subdomain_section = False
                        continue
                    elif "Find" in line and "Subdomain:" in line:
                        url_section = False
                        subdomain_section = True
                        continue
                    
                    if url_section and line.strip() and not line.startswith("Find"):
                        found_url = line.strip()
                        if found_url not in urls:
                            urls.append(found_url)
                            logger.debug(_("Extracted URL from command output: {url}").format(url=found_url))
                    elif subdomain_section and line.strip() and not line.startswith("Find"):
                        subdomain = line.strip()
                        if subdomain not in subdomains:
                            subdomains.append(subdomain)
                            logger.debug(_("Extracted subdomain from command output: {subdomain}").format(subdomain=subdomain))
            
            if self.config.get('asset_scope', {}).get('restrict_to_seed_domains', True):
                seed_domains = []
                if hasattr(self, 'engine') and hasattr(self.engine, 'seed_domains'):
                    seed_domains = self.engine.seed_domains
                    logger.info(_("Using seed domains list from engine: {domains}").format(domains=seed_domains))
                else:
                    try:
                        parsed_url = urlparse(url)
                        seed_domain = parsed_url.netloc
                        parts = seed_domain.split('.')
                        if len(parts) > 2:
                            seed_domain = '.'.join(parts[-2:])
                        seed_domains = [seed_domain]
                        logger.info(_("Extracted seed domain from URL {url}: {domain}").format(url=url, domain=seed_domain))
                    except Exception as e:
                        logger.error(_("Failed to extract seed domain from URL: {error}").format(error=str(e)))
                        seed_domains = []
                
                if seed_domains:
                    filtered_urls = []
                    for found_url in urls:
                        try:
                            parsed_found_url = urlparse(found_url)
                            found_domain = parsed_found_url.netloc
                            is_related = False
                            for seed_domain in seed_domains:
                                if found_domain and (found_domain == seed_domain or found_domain.endswith('.' + seed_domain)):
                                    is_related = True
                                    break
                            if is_related:
                                filtered_urls.append(found_url)
                                logger.debug(_("Keeping seed-related URL: {url}").format(url=found_url))
                            else:
                                logger.debug(_("Filtering non-seed-related URL: {url}").format(url=found_url))
                        except Exception as e:
                            logger.warning(_("URL filtering error: {error}, URL: {url}").format(error=str(e), url=found_url))
                            filtered_urls.append(found_url)
                    
                    filtered_subdomains = []
                    for subdomain in subdomains:
                        is_related = False
                        for seed_domain in seed_domains:
                            if subdomain == seed_domain or subdomain.endswith('.' + seed_domain):
                                is_related = True
                                break
                        if is_related:
                            filtered_subdomains.append(subdomain)
                            logger.debug(_("Keeping seed-related subdomain: {subdomain}").format(subdomain=subdomain))
                        else:
                            logger.debug(_("Filtering non-seed-related subdomain: {subdomain}").format(subdomain=subdomain))
                    
                    logger.info(_("Before filtering: {url_count} URLs, {subdomain_count} subdomains").format(url_count=len(urls), subdomain_count=len(subdomains)))
                    urls = filtered_urls
                    subdomains = filtered_subdomains
                    logger.info(_("After filtering: {url_count} URLs, {subdomain_count} subdomains").format(url_count=len(urls), subdomain_count=len(subdomains)))
            
            logger.info(_("JSFinder extracted {url_count} URLs and {subdomain_count} subdomains from {url}").format(url_count=len(urls), subdomain_count=len(subdomains), url=url))
        except subprocess.CalledProcessError as e:
            logger.error(_("JSFinder execution failed: {error}").format(error=e.stderr.decode() if e.stderr else str(e)))
            logger.info(_("Trying internal method instead"))
            return self._internal_jsfinder(url)
        except subprocess.TimeoutExpired:
            logger.error(_("JSFinder execution timed out, trying internal method instead"))
            return self._internal_jsfinder(url)
        except Exception as e:
            logger.error(_("JSFinder call exception: {error}").format(error=str(e)))
            logger.info(_("Trying internal method instead"))
            return self._internal_jsfinder(url)
        
        return urls, subdomains
        
    def _internal_jsfinder(self, url):
        urls = []
        subdomains = []
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
        }
        
        try:
            from core.zsans_engine import http_session
            response = http_session.get(url)
            if response.status_code != 200:
                logger.warning(_("Failed to get URL content, status code: {code}").format(code=response.status_code))
                return [], []
            
            content = response.text
            
            soup = BeautifulSoup(content, 'html.parser')
            for script in soup.find_all('script'):
                src = script.get('src')
                if src:
                    if src.startswith('//'):
                        js_url = 'https:' + src
                    elif src.startswith('/'):
                        base_url = urlparse(url)
                        js_url = f"{base_url.scheme}://{base_url.netloc}{src}"
                    elif not src.startswith(('http://', 'https://')):
                        base_url = url.rstrip('/') + '/'
                        js_url = urljoin(base_url, src)
                    else:
                        js_url = src
                    
                    urls.append(js_url)
                    
                    js_domain = urlparse(js_url).netloc
                    if js_domain and js_domain not in subdomains:
                        if any(js_domain.endswith(tld) for tld in [".com", ".net", ".org", ".io", ".cn", ".xyz", ".edu", ".gov", ".mil", ".int", ".info", ".biz", ".name", ".pro", ".mobi", ".app", ".dev", ".site", ".online", ".tech", ".ai", ".co", ".me", ".tv", ".cc"]):
                            subdomains.append(js_domain)
            
            for a in soup.find_all('a'):
                href = a.get('href')
                if href and not href.startswith(('#', 'javascript:', 'mailto:')):
                    if href.startswith('//'):
                        link_url = 'https:' + href
                    elif href.startswith('/'):
                        base_url = urlparse(url)
                        link_url = f"{base_url.scheme}://{base_url.netloc}{href}"
                    elif not href.startswith(('http://', 'https://')):
                        base_url = url.rstrip('/') + '/'
                        link_url = urljoin(base_url, href)
                    else:
                        link_url = href
                    
                    urls.append(link_url)
                    
                    link_domain = urlparse(link_url).netloc
                    if link_domain and link_domain not in subdomains:
                        if any(link_domain.endswith(tld) for tld in [".com", ".net", ".org", ".io", ".cn", ".xyz", ".edu", ".gov", ".mil", ".int", ".info", ".biz", ".name", ".pro", ".mobi", ".app", ".dev", ".site", ".online", ".tech", ".ai", ".co", ".me", ".tv", ".cc"]):
                            subdomains.append(link_domain)
            
            logger.info(_("Internal JSFinder extracted {url_count} URLs and {subdomain_count} subdomains from {url}").format(url_count=len(urls), subdomain_count=len(subdomains), url=url))
        except Exception as e:
            error_str = str(e)
            if 'ProxyError' in error_str or 'proxy' in error_str.lower():
                from core.zsans_engine import report_proxy_failure
                report_proxy_failure()
            logger.error(_("Internal JSFinder execution failed: {error}").format(error=error_str))
        
        return urls, subdomains
    
    def run_free_subfinder(self, domain):
        if not os.path.exists('assets/free-subfinder.py'):
            logger.warning(_("free-subfinder.py not found, using internal method instead"))
            return []
        
        subdomains = []
        try:
            if domain not in subdomains:
                subdomains.append(domain)
                logger.info(_("Adding original domain to subdomains list: {domain}").format(domain=domain))
            
            cmd = ['python', 'assets/free-subfinder.py', domain, '-q']
            process = subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            
            output = process.stdout.decode('utf-8', errors='ignore')
            for line in output.split('\n'):
                subdomain = line.strip()
                if subdomain and subdomain not in subdomains:
                    subdomains.append(subdomain)
            
            ww_domains = [d for d in subdomains if d.startswith('ww') and len(d.split('.')) > 2]
            for ww_domain in ww_domains:
                parts = ww_domain.split('.')
                if len(parts) > 2:
                    original_domain = '.'.join(parts[1:])
                    if original_domain not in subdomains:
                        subdomains.append(original_domain)
                        logger.info(_("Added original domain from ww-prefix domain: {domain}").format(domain=original_domain))
            
            logger.info(_("free-subfinder found {count} subdomains: {subdomains}").format(count=len(subdomains), subdomains=', '.join(subdomains)))
        except subprocess.CalledProcessError as e:
            logger.error(_("free-subfinder execution failed: {error}").format(error=e.stderr.decode('utf-8', errors='ignore') if e.stderr else str(e)))
        except Exception as e:
            logger.error(_("free-subfinder call exception: {error}").format(error=str(e)))
        
        return subdomains
    
    def run_ehole(self, url):
        fingerprint_enabled = self.config.get('external_tools', {}).get('fingerprint', {}).get('enabled', False)
        if not fingerprint_enabled:
            logger.info(_("Fingerprinting feature not enabled, skipping EHole call"))
            return None
            
        if not self._check_tool_exists('ehole'):
            logger.warning(_("EHole tool not found, cannot perform fingerprinting"))
            return None
        
        cleaned_url = url.strip()
        cleaned_url = cleaned_url.replace('`', '').replace('"', '')
        if not cleaned_url.startswith(('http://', 'https://')):
            logger.warning(_("Invalid URL format: {url}, skipping fingerprinting").format(url=url))
            return None
            
        logger.info(_("Cleaned URL: {url}").format(url=cleaned_url))
        
        result = {
            'url': url,
            'fingerprints': [],
            'cms': None,
            'server': None,
            'status_code': None,
            'title': None
        }
        
        try:
            ehole_path = 'ehole'
            
            if 'ehole' in self.tool_paths:
                tool_path = self.tool_paths['ehole']
                if os.path.exists(tool_path):
                    ehole_path = tool_path
                    logger.info(_("Using EHole path from configuration: {path}").format(path=ehole_path))
            
            cmd = [ehole_path, 'finger', '-u', cleaned_url]
            logger.info(_("Executing command: {cmd}").format(cmd=' '.join(cmd)))
            
            try:
                if os.name == 'nt' and ehole_path.endswith('.exe'):
                    final_url = cleaned_url.strip()
                    while '`' in final_url or '"' in final_url:
                        final_url = final_url.replace('`', '').replace('"', '')
                    final_url = final_url.strip()
                    logger.debug(_("Original URL: {url}").format(url=url))
                    logger.debug(_("Cleaned URL: {url}").format(url=cleaned_url))
                    logger.debug(_("Final URL: {url}").format(url=final_url))
                    cmd_str = f'"{ehole_path}" finger -u "{final_url}"'
                    logger.debug(_("Using shell string execution: {cmd}").format(cmd=cmd_str))
                    process = subprocess.run(cmd_str, check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=True)
                else:
                    process = subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                
                output = process.stdout.decode('utf-8', errors='ignore')
                logger.debug(_("EHole raw output: {output}").format(output=output))
                
                found_result = False
                for line in output.split('\n'):
                    if '|' in line and (cleaned_url in line or any(part.strip() == cleaned_url for part in line.split('|'))):
                        parts = line.strip().strip('[]').split('|')
                        logger.debug(_("EHole output parsing: {parts}").format(parts=parts))
                        
                        if len(parts) >= 6:
                            fingerprints = parts[1].strip().split(',')
                            result['fingerprints'] = [fp.strip() for fp in fingerprints if fp.strip()]
                            result['cms'] = fingerprints[0].strip() if fingerprints and fingerprints[0].strip() else None
                            result['server'] = parts[2].strip() if parts[2].strip() else None
                            try:
                                result['status_code'] = int(parts[3].strip())
                            except (ValueError, IndexError):
                                pass
                            
                            if len(parts) > 5 and parts[5].strip():
                                result['title'] = parts[5].strip()
                                logger.info(_("Title extracted from EHole: {title}").format(title=result['title']))
                            
                            logger.info(_("Fingerprint result: {result}").format(result=result))
                            found_result = True
                            break
                
                if not found_result:
                    logger.info(_("No exact match found, trying looser matching"))
                    for line in output.split('\n'):
                        if '|' in line:
                            parts = line.strip().strip('[]').split('|')
                            logger.debug(_("EHole loose match output parsing: {parts}").format(parts=parts))
                            
                            if len(parts) >= 6:
                                fingerprints = parts[1].strip().split(',')
                                result['fingerprints'] = [fp.strip() for fp in fingerprints if fp.strip()]
                                result['cms'] = fingerprints[0].strip() if fingerprints and fingerprints[0].strip() else None
                                result['server'] = parts[2].strip() if parts[2].strip() else None
                                try:
                                    result['status_code'] = int(parts[3].strip())
                                except (ValueError, IndexError):
                                    pass
                                
                                if len(parts) > 5 and parts[5].strip():
                                    result['title'] = parts[5].strip()
                                    logger.info(_("Title extracted from EHole loose match: {title}").format(title=result['title']))
                                
                                logger.info(_("Fingerprint loose match result: {result}").format(result=result))
                                found_result = True
                                break
                
                return result
            except subprocess.CalledProcessError as e:
                error_msg = e.stderr.decode('utf-8', errors='ignore') if e.stderr else str(e)
                logger.error(_("EHole execution failed: {error}").format(error=error_msg))
                
                if error_msg and 'error' in error_msg.lower():
                    logger.error(_("EHole error details: {error}").format(error=error_msg))
                
                if 'invalid' in error_msg.lower() and 'url' in error_msg.lower():
                    logger.error(_("Possible URL format issue caused EHole failure: {url}").format(url=cleaned_url))
                
                return None
        except Exception as e:
            logger.error(_("EHole call exception: {error}").format(error=str(e)))
            import traceback
            logger.error(_("EHole exception details: {traceback}").format(traceback=traceback.format_exc()))
            return None
    
    def _check_tool_exists(self, tool_name):
        if tool_name in self.tool_paths:
            tool_path = self.tool_paths[tool_name]
            if os.path.exists(tool_path):
                logger.debug(_("Found tool {tool} at configured path: {path}").format(tool=tool_name, path=tool_path))
                return True
        
        assets_tool_path = os.path.join('assets', tool_name)
        if os.path.exists(assets_tool_path):
            logger.debug(_("Found tool {tool} in assets directory: {path}").format(tool=tool_name, path=assets_tool_path))
            return True
            
        if os.name == 'nt':
            assets_tool_exe_path = os.path.join('assets', f"{tool_name}.exe")
            if os.path.exists(assets_tool_exe_path):
                logger.debug(_("Found tool {tool} in assets directory: {path}").format(tool=tool_name, path=assets_tool_exe_path))
                return True
        
        try:
            if os.name == 'nt':
                subprocess.run(['where', tool_name], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            else:
                subprocess.run(['which', tool_name], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            logger.debug(_("Found tool {tool} in system PATH").format(tool=tool_name))
            return True
        except subprocess.CalledProcessError:
            logger.debug(_("Tool {tool} not found").format(tool=tool_name))
            return False
    
    def reduce_concurrency(self):
        with self.lock:
            if self.concurrency > 1:
                self.concurrency -= 1
                old_executor = self.executor
                self.executor = ThreadPoolExecutor(max_workers=self.concurrency)
                old_executor.shutdown(wait=False)
                logger.info(_("Reduced concurrency to {concurrency}").format(concurrency=self.concurrency))
    
    def increase_concurrency(self):
        with self.lock:
            max_concurrency = self.config.get('max_concurrency', 20)
            if self.concurrency < max_concurrency:
                self.concurrency += 1
                old_executor = self.executor
                self.executor = ThreadPoolExecutor(max_workers=self.concurrency)
                old_executor.shutdown(wait=False)
                logger.info(_("Increased concurrency to {concurrency}").format(concurrency=self.concurrency))
    
    def submit_task(self, func, *args, **kwargs):
        future = self.executor.submit(func, *args, **kwargs)
        task_id = id(future)
        
        with self.lock:
            self.running_tasks[task_id] = {
                'future': future,
                'start_time': time.time(),
                'func': func.__name__,
                'args': args
            }
        
        future.add_done_callback(lambda f: self._task_done_callback(task_id))
        return future
    
    def _task_done_callback(self, task_id):
        with self.lock:
            if task_id in self.running_tasks:
                task = self.running_tasks.pop(task_id)
                duration = time.time() - task['start_time']
                logger.debug(_("Task {func} completed, duration: {duration:.2f} seconds").format(func=task['func'], duration=duration))
    
    def get_running_tasks(self):
        with self.lock:
            return {
                task_id: {
                    'func': task['func'],
                    'args': task['args'],
                    'running_time': time.time() - task['start_time']
                } for task_id, task in self.running_tasks.items()
            }
    
    def shutdown(self):
        self.executor.shutdown(wait=True)
        logger.info(_("Tool manager shutdown"))