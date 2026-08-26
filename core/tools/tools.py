#!/usr/bin/env python3
# coding: utf-8

import json
import logging
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse, urljoin
from bs4 import BeautifulSoup
from core.i18n import _

logger = logging.getLogger('zsans.tools')

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PY_EXE = sys.executable or 'python'

def _script_path(name):
    return os.path.join(REPO_ROOT, 'assets', name)


def _copy_peer(src, dst):
    import shutil
    shutil.copyfile(src, dst)
    try:
        os.chmod(dst, os.stat(src).st_mode)
    except OSError:
        pass


# ---- 平台 / 本地工具解析的必留逻辑（原 provisioning.py，已并入本模块）----

_TEMP_DIR_CACHE = None


def _default_tools_dir():
    """默认本地工具目录：项目根下的 tools/。"""
    return os.path.join(REPO_ROOT, 'tools')


def _detect_platform():
    """返回 (os_name, arch)，如 ("linux", "amd64") / ("windows", "arm64")。"""
    import platform
    raw = platform.system().lower()
    if raw.startswith('win'):
        os_name = 'windows'
    elif raw == 'darwin':
        os_name = 'darwin'
    else:
        os_name = 'linux'

    machine = platform.machine().lower()
    if machine in ('x86_64', 'amd64'):
        arch = 'amd64'
    elif machine in ('aarch64', 'arm64'):
        arch = 'arm64'
    elif machine in ('i386', 'i686', 'x86'):
        arch = '386'
    elif machine.startswith('arm'):
        arch = 'arm'
    else:
        arch = 'amd64'
    return os_name, arch


def _exe_name(bin_name):
    """按平台给可执行文件补充 .exe 后缀。"""
    return bin_name + ('.exe' if os.name == 'nt' else '')


# 各工具在本地多架构目录中的可执行文件名（无则与工具名同名）
_MULTIARCH_BINS = {'ehole': 'EHole'}


def _local_multiarch_binary(name, tools_dir=None):
    """返回 tools/<name>/{os}/{arch}/ 下当前平台的可执行文件，无则返回 None。

    用于 EHole 这类“本机 arm64 自行编译、其余架构预下载到本地”的内部工具。
    """
    tools_dir = tools_dir or _default_tools_dir()
    os_name, arch = _detect_platform()
    cand = os.path.join(tools_dir, name, os_name, arch, _exe_name(_MULTIARCH_BINS.get(name, name)))
    if os.path.isfile(cand):
        return cand
    return None


def _writable_temp_dir(tools_dir=None):
    """返回一个确定可写的临时目录（低权限环境 /tmp 可能不可写）。

    候选顺序：
      1. 环境变量 ZSANS_TEMP_DIR（用户显式指定，最高优先级）
      2. $TMPDIR
      3. tempfile.gettempdir()（通常为 /tmp）
    以上都不可写时，回退到 tools/.tmp（tools 目录本身即可写时）。
    """
    global _TEMP_DIR_CACHE
    candidates = []
    env_dir = os.environ.get('ZSANS_TEMP_DIR')
    if env_dir:
        candidates.append(env_dir)
    tdir = os.environ.get('TMPDIR')
    if tdir:
        candidates.append(tdir)
    candidates.append(tempfile.gettempdir())

    extra = os.path.join(tools_dir or _default_tools_dir(), '.tmp')
    candidates.append(extra)

    probe = _TEMP_DIR_CACHE
    for cand in candidates:
        try:
            os.makedirs(cand, exist_ok=True)
            test_file = os.path.join(cand, '.zsans_wtest_%d' % os.getpid())
            with open(test_file, 'w') as f:
                f.write('ok')
            os.unlink(test_file)
            _TEMP_DIR_CACHE = cand
            return cand
        except OSError:
            continue
    _TEMP_DIR_CACHE = None
    return None


def _ensure_tempdir_global():
    """把 tempfile 全局临时目录改为可写位置（低权限环境兜底）。"""
    wd = _writable_temp_dir()
    if wd:
        tempfile.tempdir = wd
    return tempfile.gettempdir()


class ToolOrchestrator:
    def __init__(self, config=None, engine=None):
        self.config = config or {}
        self.engine = engine
        # 低权限环境兼容: /tmp 不可写时把 tempfile 全局临时目录
        # 落到可写位置(如项目 tools/.tmp),让 run_ehole/subfinder/JSFinder
        # 等内部的 NamedTemporaryFile 不再依赖 /tmp。
        try:
            _ensure_tempdir_global()
        except Exception:
            pass
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
                logger.debug(_("Tool {tool} path is empty, ignoring configuration").format(tool=tool))
            elif not os.path.isabs(path):
                self.tool_paths[tool] = os.path.abspath(path)
        
        for tool in tools_to_remove:
            self.tool_paths.pop(tool)
        
        # 工具定义:每个外部工具的附加参数等元信息,插件可通过 register_tool 扩展
        self.tool_defs = {}
        for tool_name in ('subfinder', 'naabu', 'ehole', 'whatweb'):
            self.tool_defs[tool_name] = {'extra_args': []}
    
    def register_tool(self, name, path=None, version=None, extra_args=None, **kwargs):
        """注册 / 覆盖一个外部工具的定义(供插件扩展)。

        - path:        可执行文件完整路径;提供则写入 self.tool_paths,引擎调用即生效
        - version:     工具的版本号(仅记录,不校验)
        - extra_args:  追加到该工具命令末尾的参数列表,如 ['-top-ports', '1000']
        """
        if not isinstance(name, str) or not name:
            return False
        self.tool_defs.setdefault(name, {'extra_args': []})
        if extra_args:
            self.tool_defs[name]['extra_args'] = [str(a) for a in extra_args]
        if version:
            self.tool_defs[name]['version'] = str(version)
        if path:
            self.tool_paths[name] = os.path.abspath(path)
        logger.info(_("Tool registered: {name} (path={path}, args={args})").format(
            name=name,
            path=self.tool_paths.get(name, '-'),
            args=self.tool_defs[name].get('extra_args'),
        ))
        return True

    def tool_extra_args(self, name):
        """返回某工具已注册的附加参数列表。"""
        return list((self.tool_defs.get(name) or {}).get('extra_args') or [])
    
    def run_subfinder(self, domain):
        if not self._check_tool_exists('subfinder'):
            logger.warning(_("Subfinder tool not found, using internal method instead"))
            return self._run_internal_dns_resolver(domain)
        
        subdomains = []
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(delete=False, mode='w+t') as temp_file:
                temp_path = temp_file.name
            
            subfinder_path = 'subfinder'
            
            if 'subfinder' in self.tool_paths:
                tool_path = self.tool_paths['subfinder']
                if os.path.exists(tool_path):
                    subfinder_path = tool_path
                    logger.debug(_("Using subfinder path from configuration: {path}").format(path=subfinder_path))
            else:
                local_path = self._resolve_local_tool_binary('subfinder')
                if local_path:
                    subfinder_path = local_path
                    logger.debug(_("Using subfinder from local directory: {path}").format(path=subfinder_path))
            if os.name == 'nt' and os.path.exists(os.path.join('assets', 'subfinder.exe')):
                subfinder_path = os.path.abspath(os.path.join('assets', 'subfinder.exe'))
                logger.debug(_("Using subfinder.exe from assets directory: {path}").format(path=subfinder_path))
            
            cmd = [subfinder_path, '-d', domain, '-o', temp_path, '-silent'] + self.tool_extra_args('subfinder')
            logger.debug(_("Executing command: {cmd}").format(cmd=' '.join(cmd)))

            # 统一使用参数列表调用（Windows 同样支持）：
            # 绝不用 shell=True 字符串拼接 —— domain 来自被爬页面/Web 输入，
            # 拼接进 shell 命令会构成命令注入链。
            try:
                subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
            except subprocess.CalledProcessError as e:
                logger.error(_("Failed to use {path}: {error}").format(path=subfinder_path, error=e.stderr.decode() if e.stderr else str(e)))
                if subfinder_path != 'subfinder':
                    logger.debug(_("Trying subfinder from system PATH"))
                    cmd = ['subfinder', '-d', domain, '-o', temp_path, '-silent']
                    logger.debug(_("Executing command: {cmd}").format(cmd=' '.join(cmd)))
                    try:
                        subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
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
        except subprocess.CalledProcessError as e:
            logger.error(_("Subfinder execution failed: {error}").format(error=e.stderr.decode() if e.stderr else str(e)))
            return self._run_internal_dns_resolver(domain)
        except Exception as e:
            logger.error(_("Subfinder call exception: {error}").format(error=str(e)))
            return self._run_internal_dns_resolver(domain)
        finally:
            if temp_path and os.path.exists(temp_path):
                try:
                    os.unlink(temp_path)
                except OSError:
                    pass
        
        return subdomains
        
    def _run_internal_dns_resolver(self, domain):
        logger.debug(_("Using internal DNS resolver for {domain}").format(domain=domain))
        subdomains = []
        
        try:
            dnsx_enabled = self.config.get('asset_types', {}).get('domain', {}).get('tools', {}).get('dnsx', True)
            if not dnsx_enabled:
                logger.debug(_("DNSx resolution disabled"))
                return subdomains
            
            if not os.path.exists(_script_path('dnsxs.py')):
                logger.error(_("Internal DNS resolver dnsxs.py does not exist"))
                return []
                
            with tempfile.NamedTemporaryFile(delete=False, mode='w+t') as temp_file:
                temp_path = temp_file.name
            
            cmd = [PY_EXE, _script_path('dnsxs.py'), domain, '-t', 'A', '-q']
            process = subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
            
            try:
                output = process.stdout.decode('utf-8').strip()
            except UnicodeDecodeError:
                output = process.stdout.decode('latin-1').strip()
            
            if output and os.path.exists(_script_path('free-subfinder.py')):
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
            # 与 subfinder 行为保持一致：配置路径缺失时尝试本地/系统 PATH 中的 naabu
            local_path = self._resolve_local_tool_binary('naabu')
            if local_path:
                naabu_path = local_path
                logger.debug(_("Using naabu from local directory: {path}").format(path=naabu_path))
            else:
                from shutil import which
                if which('naabu'):
                    naabu_path = 'naabu'
                    logger.debug(_("Using naabu from system PATH"))
                else:
                    logger.warning(_("Naabu tool not found or invalid path, using internal method instead"))
                    return self._run_internal_port_scanner(ip)
        
        open_ports = {}
        try:
            with tempfile.NamedTemporaryFile(delete=False, mode='w+t') as temp_file:
                temp_path = temp_file.name
            
            cmd = [naabu_path, '-host', ip, '-json', '-o', temp_path, '-silent'] + self.tool_extra_args('naabu')
            logger.debug(_("Executing naabu command: {cmd}").format(cmd=' '.join(cmd)))
            subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=240)
            
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
        logger.debug(_("Using internal port scanner for {ip}").format(ip=ip))
        open_ports = {}
        try:
            if not os.path.exists(_script_path('port.py')):
                logger.error(_("Internal port scanner port.py does not exist"))
                return {}
                
            with tempfile.NamedTemporaryFile(delete=False, mode='w+t') as temp_file:
                temp_path = temp_file.name
            
            # 端口范围可配置；默认覆盖常见 Web / 数据库 / 缓存 / 远程管理端口
            port_range = (
                self.config.get('asset_types', {}).get('ip', {})
                .get('tools', {}).get('port_range',
                                      '1-1024,3306,3389,5432,5900,6379,7001,8000-8500,8888,9000-9100,9200,27017,11211')
            )
            cmd = [PY_EXE, _script_path('port.py'), ip, '-p', port_range, '-q']
            process = subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=180)
            
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
            logger.debug(_("Normalized URL: {url}").format(url=url))
            
        if not os.path.exists(_script_path('JSfinder.py')):
            logger.warning(_("JSfinder.py not found, using internal method instead"))
            return self._internal_jsfinder(url)
            
        logger.debug(_("Starting JSFinder for URL: {url}").format(url=url))
        
        urls = []
        subdomains = []
        try:
            with tempfile.NamedTemporaryFile(delete=False, mode='w+t') as url_file:
                url_path = url_file.name
            with tempfile.NamedTemporaryFile(delete=False, mode='w+t') as subdomain_file:
                subdomain_path = subdomain_file.name
            
            cmd = [PY_EXE, _script_path('JSfinder.py'), '-u', url, '-ou', url_path, '-os', subdomain_path]
            logger.debug(_("Executing JSFinder command: {cmd}").format(cmd=' '.join(cmd)))
            js_timeout = self.config.get('external_tools', {}).get('jsfinder_timeout', 30) or 30
            process = subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=js_timeout, text=True, encoding='utf-8')
            if process.stdout:
                # 仅记录前若干行，避免把整个JSFinder输出倾倒进日志
                stdout_lines = process.stdout.rstrip('\n').split('\n')
                if len(stdout_lines) > 20:
                    preview = '\n'.join(stdout_lines[:20]) + '\n... (truncated, {n} lines total)'.format(n=len(stdout_lines))
                else:
                    preview = process.stdout
                logger.debug(_("JSFinder output preview:\n{output}").format(output=preview))
            if process.stderr:
                if process.stderr.strip():
                    logger.warning(_("JSFinder error output:\n{error}").format(error=process.stderr[:2000]))
            
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
            
            # 注:JSFinder 的真实结果已从 -ou / -os 输出文件可靠读取（见上方），
            # 不再解析 stdout —— stdout 中的 "Output N urls" / "Path:..." 等辅助行
            # 会被误判为子域名/URL，产生大量垃圾资产。
            
            if self.config.get('asset_scope', {}).get('restrict_to_seed_domains', True):
                seed_domains = []
                if hasattr(self, 'engine') and hasattr(self.engine, 'seed_domains'):
                    seed_domains = self.engine.seed_domains
                    logger.debug(_("Using seed domains list from engine: {domains}").format(domains=seed_domains))
                else:
                    try:
                        parsed_url = urlparse(url)
                        # 用 PSL 计算注册域（eTLD+1），避免旧式"取后两段"把
                        # beijing.edu.cn 拆成公共后缀 edu.cn 导致范围误判
                        from core.domain_utils import get_registrable_domain
                        hostname = (parsed_url.hostname or parsed_url.netloc or '').lower()
                        registrable = get_registrable_domain(hostname)
                        seed_domains = [registrable if registrable else hostname]
                        logger.debug(_("Extracted seed domain from URL {url}: {domain}").format(url=url, domain=seed_domains[0]))
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
                    
                    logger.debug(_("Before filtering: {url_count} URLs, {subdomain_count} subdomains").format(url_count=len(urls), subdomain_count=len(subdomains)))
                    urls = filtered_urls
                    subdomains = filtered_subdomains
                    logger.debug(_("After filtering: {url_count} URLs, {subdomain_count} subdomains").format(url_count=len(urls), subdomain_count=len(subdomains)))
            
            logger.debug(_("JSFinder extracted {url_count} URLs and {subdomain_count} subdomains from {url}").format(url_count=len(urls), subdomain_count=len(subdomains), url=url))
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
            timeout = self.config.get('http', {}).get('timeout', 15)
            response = http_session.get(url, timeout=timeout)
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
            
            logger.debug(_("Internal JSFinder extracted {url_count} URLs and {subdomain_count} subdomains from {url}").format(url_count=len(urls), subdomain_count=len(subdomains), url=url))
        except Exception as e:
            error_str = str(e)
            if 'ProxyError' in error_str or 'proxy' in error_str.lower():
                from core.zsans_engine import report_proxy_failure
                report_proxy_failure()
            logger.error(_("Internal JSFinder execution failed: {error}").format(error=error_str))
        
        return urls, subdomains
    
    def run_free_subfinder(self, domain):
        if not os.path.exists(_script_path('free-subfinder.py')):
            logger.warning(_("free-subfinder.py not found, using internal method instead"))
            return []
        
        subdomains = []
        try:
            if domain not in subdomains:
                subdomains.append(domain)
                logger.debug(_("Adding original domain to subdomains list: {domain}").format(domain=domain))
            
            cmd = [PY_EXE, _script_path('free-subfinder.py'), domain, '-q']
            process = subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
            
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
                        logger.debug(_("Added original domain from ww-prefix domain: {domain}").format(domain=original_domain))
            
            logger.debug(_("free-subfinder found {count} subdomains: {subdomains}").format(count=len(subdomains), subdomains=', '.join(subdomains)))
        except subprocess.CalledProcessError as e:
            logger.error(_("free-subfinder execution failed: {error}").format(error=e.stderr.decode('utf-8', errors='ignore') if e.stderr else str(e)))
        except Exception as e:
            logger.error(_("free-subfinder call exception: {error}").format(error=str(e)))
        
        return subdomains
    
    def _resolve_ehole_binary(self):
        """解析当前平台可用的 EHole 可执行文件路径，返回 None 表示无可用二进制。

        优先级：
          1. 配置 external_tools.paths.ehole（用户显式指定，人为空则跳过）
          2. 内置多架构目录 tools/ehole/{os}/{arch}/EHole[.exe]
             （linux/arm64 已本机编译，其余架构预下载到本地，按平台自动选择）
          3. 旧的单目录布局 tools/ehole/EHole[.exe] / assets/ehole
          4. 系统 PATH

        EHole 要求 finger.json 与可执行文件同目录，因此多架构目录内每份
        都自带 finger.json 与 config.ini；返回前会补齐缺失的配套文件。
        """
        if 'ehole' in self.tool_paths:
            tp = self.tool_paths['ehole']
            if tp and os.path.isfile(tp):
                logger.debug(_("Using EHole from configured path: {path}").format(path=tp))
                return tp
            if tp:
                logger.warning(_("Configured EHole path does not exist, falling back: {path}").format(path=tp))

        try:
            os_name, arch = _detect_platform()
            candidates = []
            arch_dir = os.path.join(REPO_ROOT, 'tools', 'ehole', os_name, arch)
            if os.path.isdir(arch_dir):
                candidates.append(os.path.join(arch_dir, _exe_name('EHole')))
            for cand in candidates:
                if os.path.isfile(cand):
                    self._ensure_ehole_peers(cand)
                    logger.debug(_("Using local EHole for {os}/{arch}: {path}").format(os=os_name, arch=arch, path=cand))
                    return cand
        except Exception as e:
            logger.debug(_("Local EHole resolution failed: {error}").format(error=e))

        for cand in (
            os.path.join(REPO_ROOT, 'tools', 'ehole', 'EHole' + ('.exe' if os.name == 'nt' else '')),
            os.path.join('assets', 'ehole'),
            os.path.join('assets', 'ehole.exe'),
        ):
            if os.path.isfile(cand):
                self._ensure_ehole_peers(cand)
                return cand

        try:
            subprocess.run(['which', 'ehole'], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            return 'ehole'
        except subprocess.CalledProcessError:
            return None

    def _resolve_whatweb_binary(self):
        """解析当前平台可用的 WhatWeb 可执行文件路径，返回 None 表示无可用二进制。

        优先级：
          1. 配置 external_tools.paths.whatweb（用户显式指定，人为空则跳过）
          2. 本地目录 tools/whatweb/whatweb（含多架构 tools/whatweb/{os}/{arch}/）
          3. assets/whatweb
          4. 系统 PATH

        WhatWeb 以 Ruby 脚本形式分发（无预编译二进制），因此主要靠 PATH /
        用户配置路径发现；放在 tools/whatweb 下亦可自动识别。
        """
        if 'whatweb' in self.tool_paths:
            tp = self.tool_paths['whatweb']
            if tp and os.path.isfile(tp):
                logger.debug(_("Using WhatWeb from configured path: {path}").format(path=tp))
                return tp
            if tp:
                logger.warning(_("Configured WhatWeb path does not exist, falling back: {path}").format(path=tp))

        local = self._resolve_local_tool_binary('whatweb')
        if local:
            logger.debug(_("Using local WhatWeb: {path}").format(path=local))
            return local

        for cand in (os.path.join('assets', 'whatweb'), os.path.join('assets', 'whatweb.rb')):
            if os.path.isfile(cand):
                return cand

        try:
            subprocess.run(['which', 'whatweb'], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            return 'whatweb'
        except subprocess.CalledProcessError:
            return None

    @staticmethod
    def _ensure_ehole_peers(bin_path):
        """EHole 启动需在可执行文件同目录读取 finger.json / config.ini。

        多架构目录内已逐个预置,此处仅对可能的旧单文件布局做兜底补齐。
        """
        if os.name == 'nt' or not os.path.isfile(bin_path):
            return
        base = os.path.dirname(bin_path) or '.'
        got = os.listdir(base)
        files = {}
        for n in got:
            full = os.path.join(base, n)
            if os.path.isfile(full):
                files[n.lower()] = full
        for src_cand in (
            os.path.join(REPO_ROOT, 'tools', 'ehole', 'linux', 'amd64', 'finger.json'),
            os.path.join(REPO_ROOT, 'tools', 'ehole', 'linux', 'arm64', 'finger.json'),
        ):
            if os.path.isfile(src_cand) and 'finger.json' not in files:
                try:
                    _copy_peer(src_cand, os.path.join(base, 'finger.json'))
                except OSError as e:
                    logger.debug(_("EHole finger.json copy failed: {error}").format(error=e))
            if os.path.isfile(src_cand) and 'config.ini' not in files:
                try:
                    _copy_peer(src_cand.replace('finger.json', 'config.ini'), os.path.join(base, 'config.ini'))
                except OSError as e:
                    logger.debug(_("EHole config.ini copy failed: {error}").format(error=e))
            break

    def run_ehole(self, url):
        fingerprint_enabled = self.config.get('external_tools', {}).get('fingerprint', {}).get('enabled', False)
        if not fingerprint_enabled:
            logger.debug(_("Fingerprinting feature not enabled, skipping EHole call"))
            return None

        ehole_path = self._resolve_ehole_binary()
        if not ehole_path:
            logger.warning(_("EHole tool not found (no binary for current platform), keeping internal title extraction"))
            return None
        
        cleaned_url = url.strip()
        cleaned_url = cleaned_url.replace('`', '').replace('"', '')
        if not cleaned_url.startswith(('http://', 'https://')):
            logger.warning(_("Invalid URL format: {url}, skipping fingerprinting").format(url=url))
            return None
            
        logger.debug(_("Cleaned URL: {url}").format(url=cleaned_url))
        
        result = {
            'url': url,
            'fingerprints': [],
            'cms': None,
            'server': None,
            'status_code': None,
            'title': None
        }
        
        out_json_path = None
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix='.json', mode='w+t') as tmp_out:
                out_json_path = tmp_out.name

            cmd = [ehole_path, 'finger', '-u', cleaned_url, '-o', out_json_path] + self.tool_extra_args('ehole')
            logger.debug(_("Executing command: {cmd}").format(cmd=' '.join(cmd)))
            
            process = None
            try:
                # 统一使用参数列表调用（Windows 同样支持），避免 shell=True
                # 字符串拼接 —— URL 来自被爬页面内容，拼接进 shell 命令会构成
                # 命令注入链（cleaned_url 清洗仅作纵深防御，不再是安全边界）。
                process = subprocess.run(cmd, check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
            except subprocess.TimeoutExpired:
                logger.error(_("EHole execution timed out: {url}").format(url=cleaned_url))
                return None
            
            if process and process.stderr:
                logger.debug(_("EHole stderr: {err}").format(err=process.stderr[:300].decode('utf-8', errors='ignore')))
            
            return self._parse_ehole_json(out_json_path, result)
        except Exception as e:
            logger.error(_("EHole call exception: {error}").format(error=str(e)))
            return None
        finally:
            try:
                if out_json_path and os.path.exists(out_json_path):
                    os.unlink(out_json_path)
            except OSError:
                pass

    def _parse_ehole_json(self, json_path, result):
        """解析 EHole v3.x 的 -o json 输出。返回 result；找不到目标时也尽力用首条。

        输出样例：
            [{ "url": "...", "cms": "", "server": "nginx/1.18.0",
               "statuscode": 200, "length": 77, "title": "..." }]
        """
        if not json_path or not os.path.isfile(json_path):
            logger.debug(_("EHole json output missing: {path}").format(path=json_path))
            return result
        try:
            with open(json_path, 'r', encoding='utf-8', errors='ignore') as f:
                data = json.load(f)
        except Exception as e:
            logger.warning(_("EHole json parse failed: {err}").format(err=e))
            return result
        if not isinstance(data, list) or not data:
            logger.debug(_("EHole returned empty fingerprint list"))
            return result

        entry = data[0]
        if not isinstance(entry, dict):
            return result

        cms = str(entry.get('cms') or '').strip()
        fingerprints = [fp.strip() for fp in cms.split(',') if fp.strip()]
        result['fingerprints'] = fingerprints
        result['cms'] = fingerprints[0] if fingerprints else (cms or None)
        result['server'] = (str(entry.get('server') or '').strip()) or None
        title = (str(entry.get('title') or '').strip()) or None
        if title:
            result['title'] = title
            logger.debug(_("Title extracted from EHole: {title}").format(title=title))
        try:
            result['status_code'] = int(entry.get('statuscode') or entry.get('status') or 0) or None
        except (TypeError, ValueError):
            pass
        logger.debug(_("Fingerprint result: {result}").format(result=result))
        return result

    def run_whatweb(self, url):
        """使用 WhatWeb 对 URL 做指纹识别，返回与 run_ehole 相同结构的 result。

        WhatWeb 输出信息比 EHole 更丰富（JS 框架、博客/电商系统、版本等），
        通过 ``--log-json`` 把结构化结果写入临时文件再解析。找不到工具时
        返回 None，由上层回退到内置标题提取。
        """
        whatweb_path = self._resolve_whatweb_binary()
        if not whatweb_path:
            logger.warning(_("WhatWeb tool not found (no binary for current platform), keeping internal title extraction"))
            return None

        cleaned_url = url.strip()
        cleaned_url = cleaned_url.replace('`', '').replace('"', '')
        if not cleaned_url.startswith(('http://', 'https://')):
            logger.warning(_("Invalid URL format: {url}, skipping fingerprinting").format(url=url))
            return None

        logger.debug(_("Cleaned URL: {url}").format(url=cleaned_url))

        result = {
            'url': url,
            'fingerprints': [],
            'cms': None,
            'server': None,
            'status_code': None,
            'title': None
        }

        out_json_path = None
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix='.json', mode='w+t') as tmp_out:
                out_json_path = tmp_out.name

            cmd = [whatweb_path, '--no-errors',
                   '--log-json=' + out_json_path, cleaned_url] + self.tool_extra_args('whatweb')
            logger.debug(_("Executing command: {cmd}").format(cmd=' '.join(cmd)))

            try:
                process = subprocess.run(cmd, check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=90)
            except subprocess.TimeoutExpired:
                logger.error(_("WhatWeb execution timed out: {url}").format(url=cleaned_url))
                return None

            if process and process.stderr:
                logger.debug(_("WhatWeb stderr: {err}").format(err=process.stderr[:300].decode('utf-8', errors='ignore')))

            return self._parse_whatweb_json(out_json_path, result)
        except Exception as e:
            logger.error(_("WhatWeb call exception: {error}").format(error=str(e)))
            return None
        finally:
            try:
                if out_json_path and os.path.exists(out_json_path):
                    os.unlink(out_json_path)
            except OSError:
                pass

    @staticmethod
    def _whatweb_meta_plugins():
        """WhatWeb 输出中与“指纹/CMS”无关的元信息插件名。

        其余命中的插件名会全部进入 fingerprints 列表（可含多个，代表完整
        技术栈），首个作为 cms。
        """
        return {
            'Title', 'HTTPServer', 'IP', 'Country', 'RedirectLocation', 'Script',
            'MetaGenerator', 'Cookies', 'HTML5', 'MetaAuthor', 'MetaDescription',
            'MetaKeywords', 'MetaRefresh', 'X-Powered-By', 'HTTPStatusCode',
            'HTTPHeaders', 'Via-Proxy', 'X-Forwarded-For', 'Allow', 'UncommonHeaders',
            'InterestingHeaders', 'Content-Type', 'Content-Language', 'Content-Encoding',
            'Content-Length', 'X-Frame-Options', 'X-Content-Type-Options',
            'Clickjacking', 'Framing', 'PasswordField', 'Form', 'Robots.txt',
            'ServerHeader', 'Strict-Transport-Security', 'Content-Security-Policy',
            'HttpOnly', 'Secure', 'Set-Cookie', 'Date', 'Expires', 'Last-Modified',
        }

    def _parse_whatweb_json(self, json_path, result):
        """解析 WhatWeb 的 --log-json 输出，返回 result。

        输出样例：
            [{ "target": "http://example.com/", "http_status": 200,
               "plugins": { "HTTPServer": {"string": ["nginx"], ...},
                            "Title": {"string": ["Example"], ...},
                            "WordPress": {"version": ["6.4"], ...} } }]
        """
        if not json_path or not os.path.isfile(json_path):
            logger.debug(_("WhatWeb json output missing: {path}").format(path=json_path))
            return result
        try:
            with open(json_path, 'r', encoding='utf-8', errors='ignore') as f:
                data = json.load(f)
        except Exception as e:
            logger.warning(_("WhatWeb json parse failed: {err}").format(err=e))
            return result
        if not isinstance(data, list) or not data:
            logger.debug(_("WhatWeb returned empty fingerprint list"))
            return result

        entry = data[0]
        if not isinstance(entry, dict):
            return result

        plugins = entry.get('plugins') or {}
        meta = self._whatweb_meta_plugins()
        fingerprints = []
        for pname, pinfo in sorted(plugins.items()):
            if pname in meta:
                continue
            info = pinfo if isinstance(pinfo, dict) else {}
            if info.get('string') or info.get('version') or info.get('module') or info.get('os'):
                fingerprints.append(pname)

        result['fingerprints'] = fingerprints
        result['cms'] = fingerprints[0] if fingerprints else None

        for pname in ('HTTPServer', 'WebServer', 'X-Powered-By'):
            pinfo = plugins.get(pname)
            if isinstance(pinfo, dict) and pinfo.get('string'):
                result['server'] = str(pinfo['string'][0]).strip() or None
                break

        title_info = plugins.get('Title')
        if isinstance(title_info, dict) and title_info.get('string'):
            title = str(title_info['string'][0]).strip() or None
            if title:
                result['title'] = title
                logger.debug(_("Title extracted from WhatWeb: {title}").format(title=title))

        try:
            result['status_code'] = int(entry.get('http_status') or 0) or None
        except (TypeError, ValueError):
            pass

        logger.debug(_("Fingerprint result: {result}").format(result=result))
        return result

    def run_fingerprint(self, url):
        """统一指纹识别入口：按配置选择引擎（auto/ehole/whatweb/both）。

        返回与 run_ehole 相同的 result 结构；'both' 会合并两个工具的
        fingerprints 并优先采用更完整的一侧。没有任何工具可用或功能未启用时
        返回 None，上层自动回退到内置标题提取。
        """
        fp_cfg = self.config.get('external_tools', {}).get('fingerprint', {}) or {}
        if not fp_cfg.get('enabled', False):
            logger.debug(_("Fingerprinting feature not enabled, skipping fingerprint call"))
            return None

        engine = str(fp_cfg.get('engine', 'auto') or 'auto').lower()
        if engine == 'none':
            logger.debug(_("Fingerprint engine disabled (engine=none), using internal title extraction"))
            return None

        if engine == 'ehole':
            selected = ['ehole']
        elif engine == 'whatweb':
            selected = ['whatweb']
        elif engine == 'both':
            selected = ['ehole', 'whatweb']
        else:  # auto
            selected = []
            if self._resolve_ehole_binary():
                selected.append('ehole')
            if self._resolve_whatweb_binary():
                selected.append('whatweb')
            if not selected:
                logger.warning(_("No fingerprint tool available (ehole/whatweb), using internal title extraction"))
                return None

        merged = {
            'url': url,
            'fingerprints': [],
            'cms': None,
            'server': None,
            'status_code': None,
            'title': None,
            'fingerprint_tools': [],
        }

        runner = {
            'ehole': self.run_ehole,
            'whatweb': self.run_whatweb,
        }
        available = {
            'ehole': self._resolve_ehole_binary() is not None,
            'whatweb': self._resolve_whatweb_binary() is not None,
        }
        for tool in selected:
            if not available[tool]:
                if engine != 'auto':
                    logger.warning(_("Selected fingerprint tool {tool} not available, skipping").format(tool=tool))
                continue
            res = runner[tool](url)
            if not res:
                continue
            merged['fingerprint_tools'].append(tool)
            if res.get('fingerprints'):
                for fp in res['fingerprints']:
                    if fp not in merged['fingerprints']:
                        merged['fingerprints'].append(fp)
            if res.get('cms') and not merged['cms']:
                merged['cms'] = res['cms']
            if res.get('server') and not merged['server']:
                merged['server'] = res['server']
            if res.get('status_code') and not merged['status_code']:
                merged['status_code'] = res['status_code']
            if res.get('title') and not merged['title']:
                merged['title'] = res['title']

        if not merged['fingerprint_tools']:
            logger.warning(_("Fingerprinting returned no results: {url}").format(url=url))
            return None
        logger.debug(_("Fingerprint merged result: {result}").format(result=merged))
        return merged

    def _resolve_local_tool_binary(self, tool_name):
        """在本地目录解析工具二进制路径(架构自选)。

        兼容两种布局:
          tools/<name>/<bin>                    (单目录)
          tools/<name>/{os}/{arch}/<bin>        (多架构预置, 如 EHole)
        找不到返回 None。
        """
        try:
            tools_dir = _default_tools_dir()
            exe = _exe_name(tool_name)
            cands = []
            ma = _local_multiarch_binary(tool_name, tools_dir)
            if ma:
                cands.append(ma)
            cands.append(os.path.join(tools_dir, tool_name, exe))
            # 兼容 assets/ 下的内置位置
            cands.append(os.path.join('assets', tool_name))
            if os.name == 'nt':
                cands.append(os.path.join('assets', tool_name + '.exe'))
            for c in cands:
                try:
                    if c and os.path.isfile(c):
                        return os.path.abspath(c)
                except OSError:
                    continue
        except Exception as e:
            logger.debug(_("Local tool resolution failed for {name}: {err}").format(name=tool_name, err=e))
        return None

    def _check_tool_exists(self, tool_name):
        if tool_name in self.tool_paths:
            tool_path = self.tool_paths[tool_name]
            if os.path.exists(tool_path):
                logger.debug(_("Found tool {tool} at configured path: {path}").format(tool=tool_name, path=tool_path))
                return True
        
        if tool_name == 'ehole':
            if self._resolve_ehole_binary():
                logger.debug(_("Found tool ehole in local multi-arch directory"))
                return True

        if tool_name == 'whatweb':
            if self._resolve_whatweb_binary():
                logger.debug(_("Found tool whatweb"))
                return True
        
        if self._resolve_local_tool_binary(tool_name):
            logger.debug(_("Found tool {tool} in local directory").format(tool=tool_name))
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