#!/usr/bin/env python3
"""
DNS解析工具
"""

import sys
import os
import dns.message
import dns.query
import dns.reversename
import dns.rdatatype
import dns.rdataclass
import dns.exception
import argparse
from concurrent.futures import ThreadPoolExecutor
import socket

# 支持的记录类型映射
RECORD_TYPES = {
    "A": dns.rdatatype.A,
    "AAAA": dns.rdatatype.AAAA,
    "CNAME": dns.rdatatype.CNAME,
    "MX": dns.rdatatype.MX,
    "TXT": dns.rdatatype.TXT,
    "NS": dns.rdatatype.NS,
    "SOA": dns.rdatatype.SOA,
    "PTR": dns.rdatatype.PTR,
    "ANY": "ANY"
}

# 默认备用DNS服务器列表
BACKUP_DNS_SERVERS = ['8.8.8.8', '8.8.4.4', '1.1.1.1', '1.0.0.1', '114.114.114.114', '223.5.5.5']

def is_valid_ip(ip):
    """检查是否为有效的IP地址"""
    try:
        socket.inet_pton(socket.AF_INET, ip)
        return True
    except socket.error:
        try:
            socket.inet_pton(socket.AF_INET6, ip)
            return True
        except socket.error:
            return False

def direct_dns_query(target, record_type, dns_servers, timeout=2.0):
    """
    直接向DNS服务器发送查询，优化PTR查询处理
    """
    # 创建DNS查询消息
    if record_type == "PTR":
        # PTR查询需要特殊处理
        try:
            # 验证目标是否为有效IP地址
            if not is_valid_ip(target):
                return (-1, [f"ERROR: '{target}' 不是有效的IP地址"])
                
            reverse_target = dns.reversename.from_address(target)
            qname = reverse_target
            qtype = dns.rdatatype.PTR
        except Exception as e:
            return (-1, [f"ERROR: {str(e)}"])
    else:
        try:
            qname = dns.name.from_text(target)
            qtype = RECORD_TYPES[record_type]
        except Exception as e:
            return (-1, [f"ERROR: {str(e)}"])
    
    # 创建查询消息
    request = dns.message.make_query(qname, qtype)
    
    # 尝试所有DNS服务器直到成功
    errors = []
    for server in dns_servers:
        try:
            # 发送DNS查询
            response = dns.query.udp(request, server, timeout=timeout)
            
            # 检查响应状态
            if response.rcode() == dns.rcode.NXDOMAIN:
                # 域名不存在
                return (0, ["NXDOMAIN"])
            elif response.rcode() != dns.rcode.NOERROR:
                errors.append(f"服务器 {server} 返回错误: {dns.rcode.to_text(response.rcode())}")
                continue
                
            # 提取答案
            answers = []
            for rrset in response.answer:
                for rr in rrset:
                    answers.append(str(rr))
            
            # 如果找到结果立即返回
            if answers:
                return (1, answers)
            else:
                # 没有结果但查询成功
                return (0, [])
        except dns.exception.Timeout:
            errors.append(f"服务器 {server} 超时")
        except Exception as e:
            errors.append(f"服务器 {server} 错误: {str(e)}")
    
    # 所有服务器都失败
    return (-1, [f"ERROR: 所有DNS服务器查询失败: {'; '.join(errors)}"])

def resolve_dns(target, record_type, dns_servers, timeout=2.0):
    """
    解析DNS记录 - 使用直接查询方法
    """
    # 特殊处理ANY查询
    if record_type == "ANY":
        results = {}
        success = False
        
        # 查询所有常见记录类型
        for rt in ["A", "AAAA", "CNAME", "MX", "TXT", "NS", "SOA"]:
            status, answers = direct_dns_query(target, rt, dns_servers, timeout)
            if status == 1:
                results[rt] = answers
                success = True
            elif status == 0:
                results[rt] = []
            else:
                results[rt] = answers
        
        return (1 if success else 0, results)
    
    # 普通查询
    return direct_dns_query(target, record_type, dns_servers, timeout)

def format_results(target, record_type, status, results, quiet=False):
    """
    格式化输出结果
    """
    # 静默模式只输出解析结果
    if quiet:
        if status == 1:
            if record_type == "ANY":
                output = []
                for rt, records in results.items():
                    if records:
                        for r in records:
                            output.append(f"{rt}: {r}")
                return "\n".join(output)
            else:
                return "\n".join(results)
        return None
    
    # 详细模式输出
    output = []
    
    if status == 1:  # 成功
        if record_type == "ANY":
            output.append(f"[+] {target} 所有记录:")
            for rt, records in results.items():
                if records:
                    output.append(f"  {rt}:")
                    for i, r in enumerate(records, 1):
                        output.append(f"    {i}. {r}")
        else:
            output.append(f"[+] {target} {record_type} 记录:")
            for i, r in enumerate(results, 1):
                output.append(f"  {i}. {r}")
    
    elif status == 0:  # 无结果
        if "NXDOMAIN" in results:
            output.append(f"[-] {target} {record_type} 记录不存在 (NXDOMAIN)")
        else:
            output.append(f"[-] {target} {record_type} 无记录")
    
    else:  # 错误
        output.append(f"[!] {target} {record_type} 查询错误:")
        for i, error in enumerate(results, 1):
            output.append(f"  {i}. {error}")
    
    return "\n".join(output)

def main():
    parser = argparse.ArgumentParser(
        description="终极DNS解析工具 - 优化PTR查询处理",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    
    parser.add_argument("targets", 
                        nargs="*",
                        help="要查询的目标 (域名、IP地址或包含多个目标的文件)")
    
    parser.add_argument("-t", "--type", 
                        default="A",
                        choices=list(RECORD_TYPES.keys()),
                        help="查询的记录类型")
    
    parser.add_argument("-f", "--file", 
                        help="从文件读取批量目标")
    
    parser.add_argument("--dns", 
                        default=",".join(BACKUP_DNS_SERVERS),
                        help=f"使用自定义DNS服务器(多个用逗号分隔，默认: {','.join(BACKUP_DNS_SERVERS)})")
    
    parser.add_argument("--timeout", 
                        type=float, 
                        default=2.0,
                        help="查询超时时间(秒)")
    
    parser.add_argument("-q", "--quiet", 
                        action="store_true",
                        help="静默模式，只输出结果")
    
    parser.add_argument("-o", "--output", 
                        help="将结果保存到文件")
    
    parser.add_argument("-c", "--concurrency", 
                        type=int, 
                        default=5,
                        help="并发查询数量")
    
    args = parser.parse_args()
    
    # 处理目标列表
    targets = []
    
    # 1. 命令行参数目标
    if args.targets:
        targets.extend(args.targets)
    
    # 2. 文件中的目标
    if args.file:
        try:
            with open(args.file, "r") as f:
                for line in f:
                    target = line.strip()
                    if target and not target.startswith("#"):
                        targets.append(target)
        except Exception as e:
            if not args.quiet:
                print(f"错误: 无法读取文件 - {str(e)}", file=sys.stderr)
            sys.exit(1)
    
    # 没有目标时提示
    if not targets:
        if not args.quiet:
            print("错误: 未指定目标", file=sys.stderr)
            print("用法示例:")
            print("  dns_tool.py example.com")
            print("  dns_tool.py -f targets.txt")
            print("  dns_tool.py 8.8.8.8 -t PTR")
        sys.exit(1)
    
    # 处理DNS服务器
    dns_servers = [s.strip() for s in args.dns.split(",") if s.strip()]
    if not dns_servers:
        dns_servers = BACKUP_DNS_SERVERS
    
    # 非静默模式显示概要
    if not args.quiet:
        print(f"[*] 开始解析 {len(targets)} 个目标的 {args.type} 记录")
        print(f"[*] 使用DNS服务器: {', '.join(dns_servers)}")
        if args.concurrency > 1:
            print(f"[*] 并发查询: {args.concurrency} 个")
    
    # 结果收集
    all_results = []
    
    # 使用线程池并发查询
    with ThreadPoolExecutor(max_workers=args.concurrency) as executor:
        futures = {}
        for target in targets:
            # 静默模式跳过空白目标
            if args.quiet and not target.strip():
                continue
            future = executor.submit(
                resolve_dns, 
                target, 
                args.type,
                dns_servers,
                args.timeout
            )
            futures[future] = target
        
        # 处理结果
        for future in futures:
            target = futures[future]
            try:
                status, results = future.result()
                formatted = format_results(target, args.type, status, results, args.quiet)
                
                if formatted:
                    all_results.append(formatted)
            except Exception as e:
                if not args.quiet:
                    all_results.append(f"[-] {target} 查询失败: {str(e)}")
    
    # 组合所有输出
    output_text = "\n".join(all_results) if args.quiet else "\n\n".join(all_results)
    
    # 保存到文件
    if args.output:
        try:
            with open(args.output, "w") as f:
                f.write(output_text)
            if not args.quiet:
                print(f"\n[*] 结果已保存到 {args.output}")
        except Exception as e:
            if not args.quiet:
                print(f"[-] 无法保存结果: {str(e)}", file=sys.stderr)
    
    # 输出结果
    if args.quiet:
        # 静默模式直接输出有效结果
        print(output_text.strip())
    elif output_text.strip():
        # 普通模式输出
        print("\n" + "-" * 40 + "\n")
        print(output_text)
        
        # 统计信息
        if not args.quiet:
            success_count = sum(1 for r in all_results if "[+]" in r)
            fail_count = sum(1 for r in all_results if "[-]" in r or "[!]" in r)
            print("\n" + "=" * 40)
            print(f"[*] 解析完成! 成功: {success_count}, 失败: {fail_count}")
    elif not args.quiet:
        print("[-] 未找到任何结果")

if __name__ == "__main__":
    main()
