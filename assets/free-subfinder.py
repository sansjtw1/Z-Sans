#!/usr/bin/env python3
"""
免费子域名查找工具
"""

import sys
import re
import argparse
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from bs4 import BeautifulSoup

# 全局配置
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
DEFAULT_TIMEOUT = 15
MAX_WORKERS = 5
RETRY_COUNT = 2  # 重试次数
RETRY_BACKOFF = 1  # 重试间隔时间(秒)

headers = {"User-Agent": USER_AGENT}

def requests_session():
    """创建带重试机制的会话"""
    session = requests.Session()
    retry_strategy = Retry(
        total=RETRY_COUNT,
        backoff_factor=RETRY_BACKOFF,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET", "POST"]
    )
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session

def query_crtsh(domain, timeout, quiet):
    """通过证书透明日志查询子域名"""
    subs = set()
    try:
        url = f"https://crt.sh/?q=%25.{domain}&output=json"
        response = requests_session().get(url, headers=headers, timeout=timeout)
        
        if response.status_code == 200:
            data = response.json()
            for entry in data:
                name = entry.get("name_value", "")
                for sub in name.split("\n"):
                    sub = sub.strip().lower()
                    if sub and f".{domain}" in sub:
                        subs.add(sub)
    except Exception as e:
        if not quiet:
            print(f"[crtsh] 查询失败: {str(e)}", file=sys.stderr)
    return subs

def query_alienvault(domain, timeout, quiet):
    """通过AlienVault开放威胁情报平台查询"""
    subs = set()
    try:
        url = f"https://otx.alienvault.com/api/v1/indicators/domain/{domain}/passive_dns"
        response = requests_session().get(url, headers=headers, timeout=timeout)
        
        if response.status_code == 200:
            data = response.json()
            for entry in data.get("passive_dns", []):
                hostname = entry.get("hostname", "").lower()
                if hostname.endswith(f".{domain}"):
                    subs.add(hostname)
    except Exception as e:
        if not quiet:
            print(f"[alienvault] 查询失败: {str(e)}", file=sys.stderr)
    return subs

def query_hackertarget(domain, timeout, quiet):
    """使用HackerTarget的API查询"""
    subs = set()
    try:
        url = f"https://api.hackertarget.com/hostsearch/?q={domain}"
        response = requests_session().get(url, headers=headers, timeout=timeout)
        
        if response.status_code == 200:
            for line in response.text.split("\n"):
                if line.strip():
                    parts = line.split(",")
                    if len(parts) > 0 and f".{domain}" in parts[0]:
                        subs.add(parts[0].strip().lower())
    except Exception as e:
        if not quiet:
            print(f"[hackertarget] 查询失败: {str(e)}", file=sys.stderr)
    return subs

def query_dnsdumpster(domain, timeout, quiet):
    """解析DNSdumpster的HTML结果"""
    subs = set()
    session = requests_session()
    
    try:
        # 首先获取CSRF token
        resp = session.get("https://dnsdumpster.com", headers=headers, timeout=timeout)
        soup = BeautifulSoup(resp.text, "html.parser")
        token_input = soup.find("input", {"name": "csrfmiddlewaretoken"})
        
        if not token_input:
            if not quiet:
                print("[dnsdumpster] 无法获取CSRF token，跳过查询", file=sys.stderr)
            return subs
        
        token = token_input.get("value", "")
        
        # 提交查询请求
        post_data = {
            "csrfmiddlewaretoken": token,
            "targetip": domain
        }
        headers_post = headers.copy()
        headers_post["Referer"] = "https://dnsdumpster.com/"
        resp = session.post("https://dnsdumpster.com/", 
                           data=post_data, 
                           headers=headers_post, 
                           timeout=timeout)
        
        # 解析结果表格
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.text, "html.parser")
            table = soup.find("table", {"class": "table"})
            if table:
                for row in table.find_all("tr"):
                    cells = row.find_all("td")
                    if len(cells) > 0:
                        subdomain = cells[0].text.strip().lower()
                        if f".{domain}" in subdomain:
                            subs.add(subdomain)
            elif not quiet:
                print("[dnsdumpster] 未找到结果表格，可能被防护机制拦截", file=sys.stderr)
    except Exception as e:
        if not quiet:
            print(f"[dnsdumpster] 查询失败: {str(e)}", file=sys.stderr)
    return subs

def query_anubis(domain, timeout, quiet):
    """通过Anubis API查询"""
    subs = set()
    try:
        url = f"https://jonlu.ca/anubis/subdomains/{domain}"
        response = requests_session().get(url, headers=headers, timeout=timeout)
        
        if response.status_code == 200:
            data = response.json()
            for item in data:
                sub = item.lower().strip()
                if f".{domain}" in sub:
                    subs.add(sub)
    except Exception as e:
        if not quiet:
            print(f"[anubis] 查询失败: {str(e)}", file=sys.stderr)
    return subs

def main():
    parser = argparse.ArgumentParser(description="免费子域名发现工具")
    parser.add_argument("domain", help="要扫描的目标域名（例如：example.com）")
    parser.add_argument("-o", "--output", help="将结果保存到文件")
    parser.add_argument("-v", "--verbose", action="store_true", help="显示详细输出")
    parser.add_argument("-q", "--quiet", action="store_true", help="静默模式，只输出子域名")
    parser.add_argument("-t", "--timeout", type=int, default=DEFAULT_TIMEOUT,
                        help=f"请求超时时间(秒，默认: {DEFAULT_TIMEOUT})")
    args = parser.parse_args()

    domain = args.domain.lower().strip()
    if not re.match(r"^[a-z0-9.-]+\.[a-z]{2,}$", domain):
        if not args.quiet:
            print("错误：域名格式无效", file=sys.stderr)
        sys.exit(1)

    sources = {
        "CRT.sh": query_crtsh,
        "AlienVault": query_alienvault,
        "HackerTarget": query_hackertarget,
        "DNSdumpster": query_dnsdumpster,
        "Anubis": query_anubis
    }

    if not args.quiet:
        print(f"[*] 开始扫描 {domain} 的子域名\n")

    all_subs = set()

    # 并发执行查询
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {}
        for name, func in sources.items():
            future = executor.submit(func, domain, args.timeout, args.quiet)
            futures[future] = name
        
        for future in as_completed(futures):
            source_name = futures[future]
            try:
                subs = future.result()
                all_subs.update(subs)
                if args.verbose and not args.quiet:
                    print(f"[{source_name}] 发现 {len(subs)} 个子域名")
            except Exception as e:
                if not args.quiet:
                    print(f"[{source_name}] 查询失败: {str(e)}", file=sys.stderr)

    sorted_subs = sorted(all_subs)
    
    # 静默模式只输出结果
    if args.quiet:
        for sub in sorted_subs:
            print(sub)
        sys.exit(0)
    
    # 普通模式输出
    if sorted_subs:
        print(f"\n[+] 共发现 {len(sorted_subs)} 个唯一子域名")
        for i, sub in enumerate(sorted_subs, 1):
            print(f"  {i}. {sub}")
    else:
        print("\n[-] 未找到任何子域名")

    # 保存到文件
    if args.output:
        with open(args.output, "w") as f:
            f.write("\n".join(sorted_subs))
        print(f"\n[*] 结果已保存到 {args.output}")

if __name__ == "__main__":
    main()
