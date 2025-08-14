#!/usr/bin/env python3
"""
端口扫描器
"""

import sys
import socket
import concurrent.futures
import argparse
import ipaddress

def is_valid_ip(ip):
    """检查是否为有效的IPv4或IPv6地址"""
    try:
        ipaddress.ip_address(ip)
        return True
    except ValueError:
        return False

def port_scan(target, port, timeout=1.0):
    """尝试连接到指定端口"""
    try:
        # 创建TCP套接字
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(timeout)
            # 尝试连接
            result = s.connect_ex((target, port))
            return (port, result == 0)
    except socket.gaierror:
        return (port, False)
    except Exception:
        return (port, False)

def resolve_domain(domain):
    """解析域名到IP地址"""
    try:
        # 获取IPv4地址
        ipv4_info = socket.getaddrinfo(domain, 0, socket.AF_INET, socket.SOCK_STREAM)
        return ipv4_info[0][4][0]
    except socket.gaierror:
        try:
            # 获取IPv6地址
            ipv6_info = socket.getaddrinfo(domain, 0, socket.AF_INET6, socket.SOCK_STREAM)
            return ipv6_info[0][4][0]
        except Exception:
            raise ValueError(f"无法解析域名: {domain}")

def parse_port_range(port_range):
    """解析端口范围字符串"""
    ports = set()
    
    # 处理逗号分隔
    segments = port_range.split(',')
    for segment in segments:
        segment = segment.strip()
        if '-' in segment:
            # 处理范围 1000-2000
            try:
                start, end = segment.split('-')
                start = int(start.strip())
                end = int(end.strip())
                if 1 <= start <= 65535 and 1 <= end <= 65535 and start <= end:
                    ports.update(range(start, end + 1))
                else:
                    raise ValueError(f"无效端口范围: {segment}")
            except ValueError:
                raise ValueError(f"无效端口范围格式: {segment}")
        else:
            # 处理单个端口
            try:
                port = int(segment)
                if 1 <= port <= 65535:
                    ports.add(port)
                else:
                    raise ValueError(f"端口超出范围: {port}")
            except ValueError:
                raise ValueError(f"无效端口号: {segment}")
    
    return ports

def main():
    parser = argparse.ArgumentParser(
        description="高级端口扫描器",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    
    parser.add_argument("target", 
                        help="要扫描的目标 (IP地址或域名)")
    
    parser.add_argument("-p", "--ports", 
                        default="1-1024", 
                        help="要扫描的端口范围，格式: 80,443,1000-2000")
    
    parser.add_argument("-t", "--threads", 
                        type=int, 
                        default=50,
                        help="并发线程数")
    
    parser.add_argument("--timeout", 
                        type=float, 
                        default=1.0,
                        help="连接超时时间(秒)")
    
    parser.add_argument("-q", "--quiet", 
                        action="store_true",
                        help="静默模式，只输出开放端口")
    
    args = parser.parse_args()

    # 解析目标地址
    target = args.target
    
    # 如果目标是域名，则解析为IP地址
    if not is_valid_ip(target):
        try:
            target = resolve_domain(target)
            if not args.quiet:
                print(f"[*] 已解析域名: {args.target} -> {target}")
        except Exception as e:
            if not args.quiet:
                print(f"错误: {str(e)}", file=sys.stderr)
            sys.exit(1)
    
    # 解析端口范围
    try:
        ports_to_scan = parse_port_range(args.ports)
        if not args.quiet:
            print(f"[*] 扫描目标: {target}")
            print(f"[*] 扫描端口: {len(ports_to_scan)} 个")
    except ValueError as e:
        if not args.quiet:
            print(f"错误: {str(e)}", file=sys.stderr)
        sys.exit(1)

    # 输出开始信息（静默模式不显示）
    if not args.quiet:
        print(f"[*] 开始扫描，使用 {args.threads} 个线程 (超时: {args.timeout}s)")

    # 使用线程池进行并发扫描
    open_ports = []
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.threads) as executor:
        # 提交所有扫描任务
        futures = {executor.submit(port_scan, target, port, args.timeout): port for port in ports_to_scan}
        
        # 显示进度（静默模式不显示）
        if not args.quiet:
            total = len(ports_to_scan)
            completed = 0
            
        # 处理已完成的任务
        for future in concurrent.futures.as_completed(futures):
            port, is_open = future.result()
            
            if not args.quiet:
                completed += 1
                progress = completed / total * 100
                print(f"\r[进度] {completed}/{total} ({progress:.1f}%)", end="")
                
                # 只显示开放端口状态，节省输出空间
                if is_open:
                    print(f"\n[+] 端口开放: {port}")
            
            if is_open:
                open_ports.append(port)
    
    # 结果统计
    if not args.quiet:
        print("\n" + "=" * 40)
        print(f"[+] 扫描完成! 共发现 {len(open_ports)} 个开放端口")
        
        if open_ports:
            print("\n开放端口列表:")
            for port in sorted(open_ports):
                print(f"  - {port}")
        else:
            print("[-] 未发现开放端口")
    
    # 静默模式只输出开放端口
    if args.quiet and open_ports:
        for port in sorted(open_ports):
            print(port)

if __name__ == "__main__":
    main()
