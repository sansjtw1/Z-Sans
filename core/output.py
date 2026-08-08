#!/usr/bin/env python3
# coding: utf-8

import csv
import json
import logging
import os
import time
from datetime import datetime
from core.i18n import _, get_current_language

logger = logging.getLogger('zsans.output')


def _format_duration(seconds):
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h {m}m {s}s"
    if m:
        return f"{m}m {s}s"
    return f"{s}s"


class OutputHandler:
    def __init__(self, engine, config=None):
        self.engine = engine
        self.config = config or {}
        self.output_dir = self.config.get('dir', self.config.get('output_dir', 'output'))
        
        if not os.path.exists(self.output_dir):
            os.makedirs(self.output_dir)
    
    def generate_output(self, formats=None):
        if formats is None:
            formats_cfg = self.config.get('formats')
            if isinstance(formats_cfg, dict):
                formats = [('report' if f == 'html' else f)
                           for f, enabled in formats_cfg.items() if enabled]
            else:
                formats = self.config.get('output_formats', ['json', 'csv', 'graphml', 'report'])
        
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        prefix = self.config.get('output_prefix', 'zsans')
        
        # 每次导出放到独立的时间戳子目录，避免多次扫描的文件混在一起
        run_subdir = os.path.join(self.output_dir, timestamp)
        os.makedirs(run_subdir, exist_ok=True)
        base_filename = os.path.join(timestamp, f"{prefix}_{timestamp}")
        
        results = {}
        
        for fmt in formats:
            try:
                if fmt == 'json':
                    filename = self._export_json(base_filename)
                    results['json'] = filename
                elif fmt == 'csv':
                    filename = self._export_csv(base_filename)
                    results['csv'] = filename
                elif fmt == 'graphml':
                    filename = self._export_graphml(base_filename)
                    results['graphml'] = filename
                elif fmt == 'report':
                    filename = self._generate_report(base_filename)
                    results['report'] = filename
                else:
                    logger.warning(_("Unsupported output format: {format}").format(format=fmt))
            except Exception as e:
                logger.error(_("Failed to export {format} format: {error}").format(format=fmt, error=str(e)))
        
        return results
    
    def _export_json(self, base_filename):
        filename = os.path.join(self.output_dir, f"{base_filename}.json")
        
        with open(filename, 'w', encoding='utf-8') as f:
            f.write(self.engine.asset_graph.export_json())
        
        logger.info(_("Exported JSON asset graph: {filename}").format(filename=filename))
        return filename
    
    def _export_csv(self, base_filename):
        assets_filename = os.path.join(self.output_dir, f"{base_filename}_assets.csv")
        relations_filename = os.path.join(self.output_dir, f"{base_filename}_relations.csv")
        
        with self.engine.asset_graph.lock:
            nodes_snapshot = list(self.engine.asset_graph.nodes.items())
            edges_snapshot = list(self.engine.asset_graph.edges.items())
        
        with open(assets_filename, 'w', encoding='utf-8-sig', newline='') as f:
            writer = csv.writer(f)
            writer.writerow([
                _('ID'), 
                _('Type'), 
                _('Value'), 
                _('Depth'), 
                _('Discovery Time'), 
                _('State'), 
                _('Title/Note'), 
                _('Fingerprints'), 
                _('CMS'), 
                _('Server')
            ])
            
            for uid, asset in nodes_snapshot:
                title_or_note = ""
                fingerprints = ""
                cms = ""
                server = ""
                
                if asset.type == "url":
                    url_config = self.config.get('asset_types', {}).get('url', {})
                    title_extraction_config = url_config.get('title_extraction', {})
                    
                    if "title" in asset.properties and title_extraction_config.get('enabled', True) and title_extraction_config.get('show_in_csv', True):
                        title = asset.properties["title"]
                        max_length = title_extraction_config.get('max_length', 50)
                        if len(title) > max_length:
                            title = title[:max_length] + "..."
                        title_or_note = title
                    
                    if "fingerprints" in asset.properties:
                        fingerprints = ",".join(asset.properties["fingerprints"])
                    if "cms" in asset.properties:
                        cms = asset.properties["cms"]
                    if "server" in asset.properties:
                        server = asset.properties["server"]
                elif asset.type == "port":
                    title_or_note = asset.properties.get("service", _("Unknown Service"))
                
                writer.writerow([
                    asset.uid,
                    asset.type,
                    asset.value,
                    asset.depth,
                    time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(asset.properties.get('discovery_time', time.time()))),
                    asset.state,
                    title_or_note,
                    fingerprints,
                    cms,
                    server
                ])
        
        with open(relations_filename, 'w', encoding='utf-8-sig', newline='') as f:
            writer = csv.writer(f)
            writer.writerow([
                _('Source Asset ID'), 
                _('Target Asset ID'), 
                _('Relation Type')
            ])
            
            for (source_id, target_id), relation_type in edges_snapshot:
                source_value = source_id.split(':', 1)[1] if ':' in source_id else source_id
                target_value = target_id.split(':', 1)[1] if ':' in target_id else target_id
                writer.writerow([source_value, target_value, relation_type])
        
        logger.info(_("Exported CSV asset list: {assets_file}, {relations_file}").format(
            assets_file=assets_filename, relations_file=relations_filename))
        return [assets_filename, relations_filename]
    
    def _export_graphml(self, base_filename):
        filename = os.path.join(self.output_dir, f"{base_filename}.graphml")
        
        with open(filename, 'w', encoding='utf-8') as f:
            f.write('<?xml version="1.0" encoding="UTF-8"?>\n')
            f.write('<graphml xmlns="http://graphml.graphdrawing.org/xmlns"\n')
            f.write('         xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"\n')
            f.write('         xsi:schemaLocation="http://graphml.graphdrawing.org/xmlns\n')
            f.write('         http://graphml.graphdrawing.org/xmlns/1.0/graphml.xsd">\n')
            
            f.write('  <key id="type" for="node" attr.name="type" attr.type="string"/>\n')
            f.write('  <key id="value" for="node" attr.name="value" attr.type="string"/>\n')
            f.write('  <key id="depth" for="node" attr.name="depth" attr.type="int"/>\n')
            f.write('  <key id="discovery_time" for="node" attr.name="discovery_time" attr.type="string"/>\n')
            f.write('  <key id="state" for="node" attr.name="state" attr.type="string"/>\n')
            f.write('  <key id="title" for="node" attr.name="title" attr.type="string"/>\n')
            f.write('  <key id="fingerprints" for="node" attr.name="fingerprints" attr.type="string"/>\n')
            f.write('  <key id="cms" for="node" attr.name="cms" attr.type="string"/>\n')
            f.write('  <key id="server" for="node" attr.name="server" attr.type="string"/>\n')
            
            f.write('  <key id="relation" for="edge" attr.name="relation" attr.type="string"/>\n')
            
            f.write('  <graph id="G" edgedefault="directed">\n')
            
            for uid, asset in self.engine.asset_graph.nodes.items():
                f.write(f'    <node id="{uid}">\n')
                f.write(f'      <data key="type">{asset.type}</data>\n')
                f.write(f'      <data key="value">{asset.value}</data>\n')
                f.write(f'      <data key="depth">{asset.depth}</data>\n')
                discovery_time = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(asset.properties.get('discovery_time', time.time())))
                f.write(f'      <data key="discovery_time">{discovery_time}</data>\n')
                f.write(f'      <data key="state">{asset.state}</data>\n')
                
                if "title" in asset.properties:
                    f.write(f'      <data key="title">{asset.properties["title"]}</data>\n')
                
                if asset.type == "url":
                    if "fingerprints" in asset.properties and asset.properties["fingerprints"]:
                        fingerprints = ",".join(asset.properties["fingerprints"])
                        f.write(f'      <data key="fingerprints">{fingerprints}</data>\n')
                    if "cms" in asset.properties and asset.properties["cms"]:
                        f.write(f'      <data key="cms">{asset.properties["cms"]}</data>\n')
                    if "server" in asset.properties and asset.properties["server"]:
                        f.write(f'      <data key="server">{asset.properties["server"]}</data>\n')
                
                f.write('    </node>\n')
            
            edge_id = 0
            for (source_id, target_id), relation_type in self.engine.asset_graph.edges.items():
                f.write(f'    <edge id="e{edge_id}" source="{source_id}" target="{target_id}">\n')
                f.write(f'      <data key="relation">{relation_type}</data>\n')
                f.write('    </edge>\n')
                edge_id += 1
            
            f.write('  </graph>\n')
            f.write('</graphml>\n')
        
        logger.info(_("Exported GraphML asset graph: {filename}").format(filename=filename))
        return filename
    
    def _generate_report(self, base_filename):
        filename = os.path.join(self.output_dir, f"{base_filename}_report.html")
        
        stats = self.engine.asset_graph.stats()
        metrics = self.engine.metrics
        gen_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        keep_eliminated = self.config.get('keep_eliminated_assets', False)

        active_assets = []
        eliminated_assets = []

        logger.info(_("Processing HTML report, total assets: {count}").format(count=len(self.engine.asset_graph.nodes)))

        with self.engine.asset_graph.lock:
            nodes_snapshot = list(self.engine.asset_graph.nodes.items())
            edges_snapshot = list(self.engine.asset_graph.edges.items())

        for uid, asset in nodes_snapshot:
            if asset.state == "eliminated":
                eliminated_assets.append(asset)
            else:
                active_assets.append(asset)

        active_assets.sort(key=lambda x: x.type)
        eliminated_assets.sort(key=lambda x: x.type)

        # Build asset type distribution data for chart
        asset_type_dist = stats.get('asset_types', {})
        type_colors = {
            'domain': '#4f46e5', 'ip': '#0891b2', 'url': '#059669',
            'port': '#d97706', 'js': '#dc2626', 'cert': '#7c3aed'
        }

        # --- Enrichment data ---
        start_time = getattr(self.engine, 'start_time', None)
        duration = (time.time() - start_time) if start_time else 0
        duration_str = _format_duration(duration)

        seed_domains = sorted(getattr(self.engine, 'seed_domains', set()) or set())
        seed_ips = sorted(getattr(self.engine, 'seed_ips', set()) or set())

        port_dist = {}
        for _node_uid, asset in nodes_snapshot:
            if asset.type == 'port':
                key = asset.properties.get('service', 'unknown')
                port_dist[key] = port_dist.get(key, 0) + 1
        port_dist = dict(sorted(port_dist.items(), key=lambda x: x[1], reverse=True))

        eliminated_reasons = {}
        for _node_uid, asset in nodes_snapshot:
            if asset.state == "eliminated":
                reason = asset.properties.get('eliminated_reason') or 'unknown'
                eliminated_reasons[reason] = eliminated_reasons.get(reason, 0) + 1
        eliminated_reasons = dict(sorted(eliminated_reasons.items(), key=lambda x: x[1], reverse=True))

        degree = {}
        for (source, target) in edges_snapshot:
            degree[source] = degree.get(source, 0) + 1
            degree[target] = degree.get(target, 0) + 1
        top_hosts = sorted(
            (node for _node_uid, node in nodes_snapshot
             if node.type in ('domain', 'ip', 'url')),
            key=lambda n: degree.get(n.uid, 0), reverse=True
        )[:15]
        top_hosts_data = [{'type': n.type, 'value': n.value, 'degree': degree.get(n.uid, 0)} for n in top_hosts]

        graph_nodes = [{'id': uid, 'type': a.type, 'label': a.value, 'depth': a.depth}
                       for uid, a in nodes_snapshot]
        graph_edges = [{'source': s, 'target': t} for (s, t) in edges_snapshot]
        graph_data = json.dumps({'nodes': graph_nodes, 'edges': graph_edges},
                                ensure_ascii=False).replace('<', '\\u003c')

        def _type_options(assets):
            types = sorted({a.type for a in assets})
            return ''.join(
                f'<option value="{t}">{t}</option>' for t in types
            )
        active_type_options = _type_options(active_assets)
        eliminated_type_options = _type_options(eliminated_assets)

        def _state_options(assets):
            states = sorted({a.state for a in assets if a.state})
            return ''.join(
                f'<option value="{s}">{s}</option>' for s in states
            )
        active_state_options = _state_options(active_assets)
        eliminated_state_options = _state_options(eliminated_assets)

        def _depth_options(assets):
            depths = sorted({a.depth for a in assets if a.depth is not None})
            return ''.join(
                f'<option value="{d}">{d}</option>' for d in depths
            )
        active_depth_options = _depth_options(active_assets)
        eliminated_depth_options = _depth_options(eliminated_assets)

        html_lang = 'zh-CN' if get_current_language().startswith('zh') else 'en'
        T = _  # translation shorthand for report strings

        js_i18n = json.dumps({
            'showing_assets': T('Showing {count} assets'),
            'filtered': T('(filtered)'),
        }, ensure_ascii=False)

        with open(filename, 'w', encoding='utf-8') as f:
            f.write('''<!DOCTYPE html>
<html lang="''' + html_lang + '''">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>''' + T('Z-Sans Asset Breeding Engine Report') + '''</title>
    <style>
        :root {
            --bg: #f0f2f5;
            --card-bg: #ffffff;
            --text: #1a1a2e;
            --text-secondary: #6b7280;
            --border: #e5e7eb;
            --accent: #4f46e5;
            --accent-light: #eef2ff;
            --success: #059669;
            --warning: #d97706;
            --danger: #dc2626;
            --info: #0891b2;
            --shadow: 0 1px 3px rgba(0,0,0,0.06), 0 1px 2px rgba(0,0,0,0.04);
            --shadow-lg: 0 4px 6px rgba(0,0,0,0.05), 0 10px 15px rgba(0,0,0,0.03);
            --radius: 10px;
            --radius-sm: 6px;
        }
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
            background: var(--bg);
            color: var(--text);
            line-height: 1.6;
            min-height: 100vh;
        }
        .header {
            background: linear-gradient(135deg, #1e1b4b 0%, #312e81 50%, #4338ca 100%);
            color: #fff;
            padding: 32px 0;
            position: sticky;
            top: 0;
            z-index: 100;
            box-shadow: 0 4px 20px rgba(79,70,229,0.25);
        }
        .header-inner {
            max-width: 1280px;
            margin: 0 auto;
            padding: 0 24px;
            display: flex;
            align-items: center;
            justify-content: space-between;
            flex-wrap: wrap;
            gap: 16px;
        }
        .header h1 {
            font-size: 1.6rem;
            font-weight: 700;
            letter-spacing: -0.02em;
        }
        .header .subtitle {
            font-size: 0.88rem;
            opacity: 0.8;
            font-weight: 400;
        }
        .header .badge {
            display: inline-block;
            background: rgba(255,255,255,0.15);
            padding: 4px 12px;
            border-radius: 20px;
            font-size: 0.8rem;
            backdrop-filter: blur(8px);
        }
        .container {
            max-width: 1280px;
            margin: 0 auto;
            padding: 24px;
        }
        .nav-tabs {
            display: flex;
            gap: 4px;
            margin-bottom: 24px;
            background: var(--card-bg);
            border-radius: var(--radius);
            padding: 4px;
            box-shadow: var(--shadow);
            flex-wrap: wrap;
        }
        .nav-tab {
            padding: 10px 20px;
            border: none;
            background: none;
            cursor: pointer;
            border-radius: var(--radius-sm);
            font-size: 0.9rem;
            font-weight: 500;
            color: var(--text-secondary);
            transition: all 0.2s;
            white-space: nowrap;
        }
        .nav-tab:hover { color: var(--text); background: var(--bg); }
        .nav-tab.active {
            background: var(--accent);
            color: #fff;
            box-shadow: 0 2px 8px rgba(79,70,229,0.3);
        }
        .tab-content { display: none; }
        .tab-content.active { display: block; }
        .stats-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
            gap: 16px;
            margin-bottom: 24px;
        }
        .stat-card {
            background: var(--card-bg);
            border-radius: var(--radius);
            padding: 20px 24px;
            box-shadow: var(--shadow);
            transition: box-shadow 0.2s, transform 0.2s;
            border-left: 4px solid transparent;
        }
        .stat-card:hover {
            box-shadow: var(--shadow-lg);
            transform: translateY(-2px);
        }
        .stat-card .stat-value {
            font-size: 2rem;
            font-weight: 700;
            line-height: 1.2;
            letter-spacing: -0.02em;
        }
        .stat-card .stat-label {
            font-size: 0.82rem;
            color: var(--text-secondary);
            margin-top: 4px;
            text-transform: uppercase;
            letter-spacing: 0.04em;
            font-weight: 500;
        }
        .stat-card.accent { border-left-color: var(--accent); }
        .stat-card.accent .stat-value { color: var(--accent); }
        .stat-card.success { border-left-color: var(--success); }
        .stat-card.success .stat-value { color: var(--success); }
        .stat-card.warning { border-left-color: var(--warning); }
        .stat-card.warning .stat-value { color: var(--warning); }
        .stat-card.info { border-left-color: var(--info); }
        .stat-card.info .stat-value { color: var(--info); }
        .stat-card.danger { border-left-color: var(--danger); }
        .stat-card.danger .stat-value { color: var(--danger); }
        .card {
            background: var(--card-bg);
            border-radius: var(--radius);
            padding: 24px;
            margin-bottom: 24px;
            box-shadow: var(--shadow);
        }
        .card h2 {
            font-size: 1.2rem;
            font-weight: 600;
            margin-bottom: 20px;
            color: var(--text);
            display: flex;
            align-items: center;
            gap: 8px;
        }
        .card h2 .icon {
            width: 32px;
            height: 32px;
            border-radius: 8px;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 1rem;
        }
        .chart-container {
            display: flex;
            flex-wrap: wrap;
            gap: 24px;
            align-items: flex-start;
        }
        .bar-chart {
            flex: 1;
            min-width: 280px;
        }
        .bar-item {
            display: flex;
            align-items: center;
            margin-bottom: 12px;
            gap: 12px;
        }
        .bar-label {
            width: 70px;
            text-align: right;
            font-size: 0.85rem;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.04em;
            color: var(--text-secondary);
        }
        .bar-track {
            flex: 1;
            height: 28px;
            background: var(--bg);
            border-radius: 14px;
            overflow: hidden;
            position: relative;
        }
        .bar-fill {
            height: 100%;
            border-radius: 14px;
            transition: width 0.6s ease;
            display: flex;
            align-items: center;
            padding-left: 12px;
            font-size: 0.8rem;
            font-weight: 600;
            color: #fff;
            min-width: 40px;
        }
        .donut-chart {
            flex: 0 0 200px;
            display: flex;
            align-items: center;
            justify-content: center;
        }
        .donut-svg { width: 180px; height: 180px; }
        .donut-legend {
            display: flex;
            flex-wrap: wrap;
            gap: 12px;
            margin-top: 16px;
        }
        .legend-item {
            display: flex;
            align-items: center;
            gap: 6px;
            font-size: 0.82rem;
            color: var(--text-secondary);
        }
        .legend-dot {
            width: 10px;
            height: 10px;
            border-radius: 50%;
        }
        .table-wrapper {
            overflow-x: auto;
            border-radius: var(--radius-sm);
            border: 1px solid var(--border);
        }
        table {
            width: 100%;
            border-collapse: collapse;
            font-size: 0.88rem;
        }
        thead { position: sticky; top: 0; z-index: 1; }
        th {
            background: #f8fafc;
            padding: 12px 14px;
            text-align: left;
            font-weight: 600;
            font-size: 0.78rem;
            text-transform: uppercase;
            letter-spacing: 0.04em;
            color: var(--text-secondary);
            border-bottom: 2px solid var(--border);
            white-space: nowrap;
        }
        td {
            padding: 10px 14px;
            border-bottom: 1px solid var(--border);
            vertical-align: middle;
        }
        tbody tr { transition: background 0.15s; }
        tbody tr:hover { background: #f8fafc; }
        .type-badge {
            display: inline-block;
            padding: 3px 10px;
            border-radius: 12px;
            font-size: 0.75rem;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.03em;
        }
        .type-domain { background: #eef2ff; color: #4f46e5; }
        .type-ip { background: #ecfeff; color: #0891b2; }
        .type-url { background: #ecfdf5; color: #059669; }
        .type-port { background: #fffbeb; color: #d97706; }
        .type-js { background: #fef2f2; color: #dc2626; }
        .type-cert { background: #f5f3ff; color: #7c3aed; }
        .state-badge {
            display: inline-block;
            padding: 3px 10px;
            border-radius: 12px;
            font-size: 0.75rem;
            font-weight: 600;
        }
        .state-scanned { background: #ecfdf5; color: #059669; }
        .state-eliminated { background: #fef2f2; color: #dc2626; }
        .state-scanning { background: #eff6ff; color: #2563eb; }
        .state-new { background: #f5f3ff; color: #7c3aed; }
        .state-failed { background: #fffbeb; color: #d97706; }
        .state-excluded { background: #f3f4f6; color: #6b7280; }
        .val-monospace {
            font-family: 'SF Mono', 'Fira Code', 'Consolas', monospace;
            font-size: 0.82rem;
            word-break: break-all;
        }
        .search-box {
            width: 100%;
            max-width: 360px;
            padding: 10px 16px;
            border: 2px solid var(--border);
            border-radius: 8px;
            font-size: 0.88rem;
            outline: none;
            transition: border-color 0.2s;
            background: var(--bg);
        }
        .search-box:focus { border-color: var(--accent); }
        .filter-select {
            min-width: 140px;
            padding: 10px 14px;
            border: 2px solid var(--border);
            border-radius: 8px;
            font-size: 0.88rem;
            outline: none;
            background: var(--card-bg);
            color: var(--text);
            cursor: pointer;
            transition: border-color 0.2s;
        }
        .filter-select:focus { border-color: var(--accent); }
        .toolbar {
            display: flex;
            justify-content: space-between;
            align-items: center;
            flex-wrap: wrap;
            gap: 12px;
            margin-bottom: 16px;
        }
        .count-info {
            font-size: 0.85rem;
            color: var(--text-secondary);
        }
        .footer {
            text-align: center;
            padding: 24px;
            color: var(--text-secondary);
            font-size: 0.82rem;
            border-top: 1px solid var(--border);
            margin-top: 32px;
        }
        .empty-state {
            text-align: center;
            padding: 48px 24px;
            color: var(--text-secondary);
        }
        .empty-state .empty-icon { font-size: 3rem; margin-bottom: 12px; }
        .metric-table td:first-child {
            font-weight: 600;
            text-transform: capitalize;
            color: var(--text);
        }
        @media (max-width: 768px) {
            .header-inner { flex-direction: column; align-items: flex-start; }
            .header { padding: 20px 0; }
            .header-inner, .container { padding-left: 14px; padding-right: 14px; }
            .header h1 { font-size: 1.25rem; }
            .container { padding-top: 16px; padding-bottom: 16px; }
            .stats-grid { grid-template-columns: repeat(2, 1fr); gap: 10px; margin-bottom: 16px; }
            .stat-card { padding: 14px 16px; }
            .stat-card .stat-value { font-size: 1.5rem; }
            .chart-container { flex-direction: column; }
            .donut-chart { flex: 0 0 auto; }
            .card { padding: 16px; margin-bottom: 16px; }
            .card h2 { font-size: 1.05rem; }
            .nav-tabs { overflow-x: auto; flex-wrap: nowrap; -webkit-overflow-scrolling: touch; }
            .nav-tab { flex: 1 0 auto; padding: 10px 14px; font-size: 0.82rem; }
            .toolbar { gap: 8px; }
            .search-box, .filter-select { max-width: 100%; width: 100%; }
            .topo-wrap { touch-action: none; }
            .topo-canvas { height: 46vh; min-height: 320px; touch-action: none; }
            .seed-list { flex-direction: column; }
            .table-wrapper { -webkit-overflow-scrolling: touch; }
            .topo-hint { font-size: 0.75rem; }
            .legend-item { font-size: 0.72rem; }
        }
        @media (max-width: 480px) {
            .stats-grid { grid-template-columns: repeat(2, 1fr); }
            .header h1 { font-size: 1.1rem; }
            .topo-canvas { height: 340px; min-height: 300px; }
        }
        .seed-list {
            display: flex;
            flex-wrap: wrap;
            gap: 24px;
        }
        .seed-col {
            flex: 1;
            min-width: 220px;
        }
        .seed-title {
            font-size: 0.8rem;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.04em;
            color: var(--text-secondary);
            margin-bottom: 8px;
        }
        .seed-items {
            display: flex;
            flex-wrap: wrap;
            gap: 6px;
        }
        .seed-items code {
            background: var(--bg);
            border: 1px solid var(--border);
            border-radius: 6px;
            padding: 2px 8px;
            font-size: 0.82rem;
            color: var(--text);
            word-break: break-all;
        }
        .topo-wrap {
            position: relative;
            border: 1px solid var(--border);
            border-radius: var(--radius-sm);
            background: #fbfbfe;
            overflow: hidden;
        }
        .topo-canvas {
            width: 100%;
            height: 520px;
            display: block;
            cursor: grab;
        }
        .topo-legend {
            display: flex;
            flex-wrap: wrap;
            gap: 14px;
            margin-top: 12px;
        }
        .topo-hint {
            font-size: 0.8rem;
            color: var(--text-secondary);
            margin-top: 8px;
        }
    </style>
</head>
<body>
    <div class="header">
        <div class="header-inner">
            <div>
                <h1>''' + T('Z-Sans Asset Breeding Engine') + '''</h1>
                <div class="subtitle">''' + T('Scan Report &mdash; Generated:') + ''' ''' + gen_time + '''</div>
            </div>
            <div>
                <span class="badge">v0.0.2</span>
            </div>
        </div>
    </div>
    <div class="container">
        <div class="nav-tabs">
            <button class="nav-tab active" onclick="switchTab('overview')">''' + T('Overview') + '''</button>
            <button class="nav-tab" onclick="switchTab('topology')">''' + T('Topology') + '''</button>
            <button class="nav-tab" onclick="switchTab('assets')">''' + T('Active Assets') + '''</button>
            <button class="nav-tab" onclick="switchTab('eliminated')">''' + T('Eliminated') + '''</button>
            <button class="nav-tab" onclick="switchTab('metrics')">''' + T('Metrics') + '''</button>
        </div>

        <!-- Overview Tab -->
        <div id="tab-overview" class="tab-content active">
            <div class="stats-grid">
                <div class="stat-card accent">
                    <div class="stat-value">''' + str(stats['total_assets']) + '''</div>
                    <div class="stat-label">''' + T('Total Assets') + '''</div>
                </div>
                <div class="stat-card info">
                    <div class="stat-value">''' + str(stats['total_relations']) + '''</div>
                    <div class="stat-label">''' + T('Relations') + '''</div>
                </div>
                <div class="stat-card success">
                    <div class="stat-value">''' + str(metrics.get('assets_processed', 0)) + '''</div>
                    <div class="stat-label">''' + T('Processed') + '''</div>
                </div>
                <div class="stat-card warning">
                    <div class="stat-value">''' + str(metrics.get('new_assets_found', 0)) + '''</div>
                    <div class="stat-label">''' + T('New Found') + '''</div>
                </div>
                <div class="stat-card danger">
                    <div class="stat-value">''' + str(metrics.get('depth_reached', 0)) + '''</div>
                    <div class="stat-label">''' + T('Max Depth') + '''</div>
                </div>
                <div class="stat-card success">
                    <div class="stat-value">''' + duration_str + '''</div>
                    <div class="stat-label">''' + T('Duration') + '''</div>
                </div>
                <div class="stat-card info">
                    <div class="stat-value">''' + str(len(seed_domains) + len(seed_ips)) + '''</div>
                    <div class="stat-label">''' + T('Seed Assets') + '''</div>
                </div>
            </div>

            <div class="card">
                <h2>''' + T('Seed Assets') + '''</h2>
                <div class="seed-list">
                    <div class="seed-col">
                        <div class="seed-title">''' + T('Domains') + '''</div>
                        <div class="seed-items">''' + ('<code>'+', '.join(seed_domains)+'</code>' if seed_domains else T('None')) + '''</div>
                    </div>
                    <div class="seed-col">
                        <div class="seed-title">''' + T('IP Addresses') + '''</div>
                        <div class="seed-items">''' + ('<code>'+', '.join(seed_ips)+'</code>' if seed_ips else T('None')) + '''</div>
                    </div>
                </div>
            </div>

            <div class="card">
                <h2>''' + T('Top Hosts') + '''</h2>
                <div class="table-wrapper">
                    <table>
                        <thead>
                            <tr><th>''' + T('Type') + '''</th><th>''' + T('Value') + '''</th><th>''' + T('Connections') + '''</th></tr>
                        </thead>
                        <tbody>
''')
            for host in top_hosts_data:
                f.write(f'''                            <tr>
                                <td><span class="type-badge type-{host['type']}">{host['type']}</span></td>
                                <td><span class="val-monospace">{host['value']}</span></td>
                                <td>{host['degree']}</td>
                            </tr>
''')
            if not top_hosts_data:
                f.write('''                            <tr><td colspan="3"><div class="empty-state"><div class="empty-icon">&#128269;</div>''' + T('No data') + '''</div></td></tr>
''')
            f.write('''                        </tbody>
                    </table>
                </div>
            </div>

            <div class="card">
                <h2>''' + T('Asset Type Distribution') + '''</h2>
                <div class="chart-container">
                    <div class="bar-chart">
''')
            # Bar chart
            max_count = max(asset_type_dist.values()) if asset_type_dist else 1
            for atype, count in sorted(asset_type_dist.items(), key=lambda x: x[1], reverse=True):
                pct = (count / max_count * 100) if max_count > 0 else 0
                color = type_colors.get(atype, '#6b7280')
                f.write(f'''                        <div class="bar-item">
                            <div class="bar-label">{atype}</div>
                            <div class="bar-track">
                                <div class="bar-fill" style="width:{pct}%;background:{color};">{count}</div>
                            </div>
                        </div>
''')
            f.write('''                    </div>
                    <div class="donut-legend">
''')
            # Donut chart legend
            legend_items = sorted(asset_type_dist.items(), key=lambda x: x[1], reverse=True)
            for atype, count in legend_items:
                color = type_colors.get(atype, '#6b7280')
                f.write(f'''                        <div class="legend-item">
                            <div class="legend-dot" style="background:{color};"></div>
                            <span>{atype}: {count}</span>
                        </div>
''')
            f.write('''                    </div>
                </div>
            </div>
        </div>

        <!-- Topology Tab -->
        <div id="tab-topology" class="tab-content">
            <div class="card">
                <div class="toolbar">
                    <h2 style="margin-bottom:0;">''' + T('Asset Topology') + '''</h2>
                    <span class="count-info">''' + T('Node count: {count}').replace('{count}', str(len(graph_nodes))) + ''' &middot; ''' + T('Edge count: {count}').replace('{count}', str(len(graph_edges))) + '''</span>
                </div>
                <div class="topo-wrap">
                    <canvas id="topology-canvas" class="topo-canvas"></canvas>
                </div>
                <div class="topo-legend" id="topology-legend"></div>
                <div class="topo-hint">''' + T('Hint: drag to pan, pin/scroll to zoom, builds auto-fit') + '''</div>
            </div>
        </div>

        <!-- Active Assets Tab -->
        <div id="tab-assets" class="tab-content">
            <div class="card">
                <div class="toolbar">
                    <h2 style="margin-bottom:0;">''' + T('Active Assets') + '''</h2>
                    <div style="display:flex;gap:8px;flex-wrap:wrap;width:100%;max-width:520px;">
                        <input type="text" class="search-box" placeholder="''' + T('Search assets...') + '''" oninput="applyFilters('active-table','active')" style="max-width:260px;">
                        <select class="filter-select" data-col="0" onchange="applyFilters('active-table','active')">
                            <option value="">''' + T('All types') + '''</option>
                            ''' + active_type_options + '''
                        </select>
                        <select class="filter-select" data-col="4" onchange="applyFilters('active-table','active')">
                            <option value="">''' + T('All states') + '''</option>
                            ''' + active_state_options + '''
                        </select>
                        <select class="filter-select" data-col="2" onchange="applyFilters('active-table','active')">
                            <option value="">''' + T('All depths') + '''</option>
                            ''' + active_depth_options + '''
                        </select>
                    </div>
                </div>
                <div class="count-info" id="active-count">''' + T('Showing {count} assets').replace('{count}', str(len(active_assets))) + '''</div>
                <div class="table-wrapper">
                    <table id="active-table">
                        <thead>
                            <tr>
                                <th>''' + T('Type') + '''</th>
                                <th>''' + T('Value') + '''</th>
                                <th>''' + T('Depth') + '''</th>
                                <th>''' + T('Discovery Time') + '''</th>
                                <th>''' + T('State') + '''</th>
                                <th>''' + T('Title / Note') + '''</th>
                                <th>''' + T('Fingerprints') + '''</th>
                                <th>''' + T('CMS') + '''</th>
                                <th>''' + T('Server') + '''</th>
                            </tr>
                        </thead>
                        <tbody>
''')
            for asset in active_assets:
                discovery_time = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(asset.properties.get('discovery_time', time.time())))
                title_or_note = self._get_asset_note(asset)
                fingerprints, cms, server = self._get_asset_fingerprint_info(asset)
                type_class = f"type-{asset.type}" if asset.type in type_colors else "type-domain"
                state_class = f"state-{asset.state}" if asset.state in ['scanned', 'eliminated', 'scanning', 'new', 'failed', 'excluded'] else "state-new"
                val_escaped = asset.value.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('"', '&quot;')
                title_escaped = title_or_note.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('"', '&quot;')
                fp_escaped = fingerprints.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('"', '&quot;')
                cms_escaped = cms.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('"', '&quot;')
                srv_escaped = server.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('"', '&quot;')
                f.write(f'''                            <tr>
                                <td><span class="type-badge {type_class}">{asset.type}</span></td>
                                <td><span class="val-monospace">{val_escaped}</span></td>
                                <td>{asset.depth}</td>
                                <td>{discovery_time}</td>
                                <td><span class="state-badge {state_class}">{asset.state}</span></td>
                                <td>{title_escaped}</td>
                                <td>{fp_escaped}</td>
                                <td>{cms_escaped}</td>
                                <td>{srv_escaped}</td>
                            </tr>
''')
            if not active_assets:
                f.write('''                            <tr><td colspan="9"><div class="empty-state"><div class="empty-icon">&#128269;</div>''' + T('No active assets found') + '''</div></td></tr>
''')
            f.write('''                        </tbody>
                    </table>
                </div>
            </div>
        </div>

        <!-- Eliminated Assets Tab -->
        <div id="tab-eliminated" class="tab-content">
            <div class="card">
                <div class="toolbar">
                    <h2 style="margin-bottom:0;">''' + T('Eliminated Assets') + '''</h2>
                    <div style="display:flex;gap:8px;flex-wrap:wrap;width:100%;max-width:520px;">
                        <input type="text" class="search-box" placeholder="''' + T('Search eliminated assets...') + '''" oninput="applyFilters('eliminated-table','eliminated')" style="max-width:260px;">
                        <select class="filter-select" data-col="0" onchange="applyFilters('eliminated-table','eliminated')">
                            <option value="">''' + T('All types') + '''</option>
                            ''' + eliminated_type_options + '''
                        </select>
                        <select class="filter-select" data-col="4" onchange="applyFilters('eliminated-table','eliminated')">
                            <option value="">''' + T('All states') + '''</option>
                            ''' + eliminated_state_options + '''
                        </select>
                        <select class="filter-select" data-col="2" onchange="applyFilters('eliminated-table','eliminated')">
                            <option value="">''' + T('All depths') + '''</option>
                            ''' + eliminated_depth_options + '''
                        </select>
                    </div>
                </div>
                <p style="font-size:0.85rem;color:var(--text-secondary);margin-bottom:12px;">''' + T('Eliminated assets are those that are not alive or responsive, including inaccessible ports, unreachable URLs, etc.') + '''</p>
                <div class="count-info" id="eliminated-count">''' + T('Showing {count} assets').replace('{count}', str(len(eliminated_assets))) + '''</div>
                <div class="table-wrapper">
                    <table id="eliminated-table">
                        <thead>
                            <tr>
                                <th>''' + T('Type') + '''</th>
                                <th>''' + T('Value') + '''</th>
                                <th>''' + T('Depth') + '''</th>
                                <th>''' + T('Discovery Time') + '''</th>
                                <th>''' + T('State') + '''</th>
                                <th>''' + T('Reason') + '''</th>
                                <th>''' + T('Fingerprints') + '''</th>
                                <th>''' + T('CMS') + '''</th>
                                <th>''' + T('Server') + '''</th>
                            </tr>
                        </thead>
                        <tbody>
''')
            if keep_eliminated:
                for asset in eliminated_assets:
                    discovery_time = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(asset.properties.get('discovery_time', time.time())))
                    reason = self._get_eliminated_reason(asset)
                    fingerprints, cms, server = self._get_asset_fingerprint_info(asset)
                    type_class = f"type-{asset.type}"
                    val_escaped = asset.value.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('"', '&quot;')
                    reason_escaped = reason.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('"', '&quot;')
                    fp_escaped = fingerprints.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('"', '&quot;')
                    cms_escaped = cms.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('"', '&quot;')
                    srv_escaped = server.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('"', '&quot;')
                    f.write(f'''                            <tr>
                                <td><span class="type-badge {type_class}">{asset.type}</span></td>
                                <td><span class="val-monospace">{val_escaped}</span></td>
                                <td>{asset.depth}</td>
                                <td>{discovery_time}</td>
                                <td><span class="state-badge state-eliminated">eliminated</span></td>
                                <td>{reason_escaped}</td>
                                <td>{fp_escaped}</td>
                                <td>{cms_escaped}</td>
                                <td>{srv_escaped}</td>
                            </tr>
''')
                if not eliminated_assets:
                    f.write('''                            <tr><td colspan="9"><div class="empty-state"><div class="empty-icon">&#9989;</div>''' + T('No eliminated assets found') + '''</div></td></tr>
''')
            else:
                f.write('''                            <tr><td colspan="9"><div class="empty-state"><div class="empty-icon">&#9888;</div>''' + T('Eliminated assets display is disabled in configuration. Set <code>keep_eliminated_assets: true</code> to enable.') + '''</div></td></tr>
''')
            f.write('''                        </tbody>
                    </table>
                </div>
            </div>
        </div>

        <!-- Metrics Tab -->
        <div id="tab-metrics" class="tab-content">
            <div class="card">
                <h2>''' + T('Execution Metrics') + '''</h2>
                <div class="table-wrapper">
                    <table class="metric-table">
                        <thead>
                            <tr><th>''' + T('Metric') + '''</th><th>''' + T('Value') + '''</th></tr>
                        </thead>
                        <tbody>
''')
            for key, value in sorted(metrics.items()):
                f.write(f'''                            <tr><td>{key.replace('_', ' ')}</td><td>{value}</td></tr>
''')
            f.write('''                        </tbody>
                    </table>
                </div>
            </div>

            <div class="card">
                <h2>''' + T('Port Distribution') + '''</h2>
                <div class="table-wrapper">
                    <table>
                        <thead>
                            <tr><th>''' + T('Service') + '''</th><th>''' + T('Count') + '''</th></tr>
                        </thead>
                        <tbody>
''')
            for service, count in port_dist.items():
                f.write(f'''                            <tr><td>{service}</td><td>{count}</td></tr>
''')
            if not port_dist:
                f.write('''                            <tr><td colspan="2"><div class="empty-state"><div class="empty-icon">&#128269;</div>''' + T('No data') + '''</div></td></tr>
''')
            f.write('''                        </tbody>
                    </table>
                </div>
            </div>

            <div class="card">
                <h2>''' + T('Elimination Reasons') + '''</h2>
                <div class="table-wrapper">
                    <table>
                        <thead>
                            <tr><th>''' + T('Reason') + '''</th><th>''' + T('Count') + '''</th></tr>
                        </thead>
                        <tbody>
''')
            for reason, count in eliminated_reasons.items():
                f.write(f'''                            <tr><td><span class="val-monospace">{reason}</span></td><td>{count}</td></tr>
''')
            if not eliminated_reasons:
                f.write('''                            <tr><td colspan="2"><div class="empty-state"><div class="empty-icon">&#128269;</div>''' + T('No data') + '''</div></td></tr>
''')
            f.write('''                        </tbody>
                    </table>
                </div>
            </div>
        </div>
    </div>

    <div class="footer">
        ''' + T('Z-Sans Asset Breeding Engine &mdash; Report generated at') + ''' ''' + gen_time + '''
    </div>

    <script>
        var I18N = ''' + js_i18n + ''';
        function switchTab(tabName) {
            document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
            document.querySelectorAll('.nav-tab').forEach(t => t.classList.remove('active'));
            document.getElementById('tab-' + tabName).classList.add('active');
            event.target.classList.add('active');
            if (tabName === 'topology' && window.__topoFit) {
                setTimeout(window.__topoFit, 0);
            }
        }
        function applyFilters(tableId, kind) {
            var table = document.getElementById(tableId);
            if (!table) return;
            var rows = table.querySelectorAll('tbody tr');
            var card = table.closest('.card');
            var box = card ? card.querySelector('input.search-box') : null;
            var selects = card ? card.querySelectorAll('select.filter-select') : [];
            var q = (box ? box.value : '').toLowerCase();
            var filters = [];
            Array.prototype.forEach.call(selects, function(sel) {
                if (sel.value) filters.push({ col: parseInt(sel.getAttribute('data-col'), 10), val: sel.value.toLowerCase() });
            });
            var anyFilter = q || filters.length;
            var visible = 0;
            rows.forEach(function(row) {
                var show = true;
                filters.forEach(function(f) {
                    var cell = row.cells[f.col];
                    var rowVal = cell ? cell.textContent.trim().toLowerCase() : '';
                    if (rowVal !== f.val) show = false;
                });
                if (show && q) {
                    var text = row.textContent.toLowerCase();
                    if (text.indexOf(q) === -1) show = false;
                }
                row.style.display = show ? '' : 'none';
                if (show) visible++;
            });
            var countEl = document.getElementById(tableId === 'active-table' ? 'active-count' : 'eliminated-count');
            if (countEl) {
                countEl.textContent = I18N.showing_assets.replace('{count}', visible) + (anyFilter ? I18N.filtered : '');
            }
        }

        var GRAPH = ''' + graph_data + ''';

        (function() {
            var TYPE_COLORS = {domain:'#4f46e5', ip:'#0891b2', url:'#059669', port:'#d97706', js:'#dc2626', cert:'#7c3aed'};
            var canvas = document.getElementById('topology-canvas');
            if (!canvas || !GRAPH || !GRAPH.nodes.length) return;
            var ctx = canvas.getContext('2d');

            var legend = document.getElementById('topology-legend');
            var seen = {};
            Object.keys(TYPE_COLORS).forEach(function(t) {
                if (seen[t]) return;
                var present = GRAPH.nodes.some(function(n) { return n.type === t; });
                if (present) {
                    seen[t] = true;
                    var item = document.createElement('span');
                    item.className = 'legend-item';
                    item.innerHTML = '<span class="legend-dot" style="background:' + TYPE_COLORS[t] + ';"></span> ' + t;
                    legend.appendChild(item);
                }
            });

            var degree = {};
            GRAPH.edges.forEach(function(e) {
                degree[e.source] = (degree[e.source] || 0) + 1;
                degree[e.target] = (degree[e.target] || 0) + 1;
            });
            var nodes = GRAPH.nodes.slice();
            if (nodes.length > 300) {
                nodes.sort(function(a, b) { return (degree[b.id] || 0) - (degree[a.id] || 0); });
                nodes = nodes.slice(0, 300);
            }
            var idSet = {};
            nodes.forEach(function(n) { idSet[n.id] = true; });
            var edges = GRAPH.edges.filter(function(e) { return idSet[e.source] && idSet[e.target]; });

            var R = 320;
            nodes.forEach(function(n, i) {
                var a = 2 * Math.PI * i / nodes.length;
                n.x = Math.cos(a) * R; n.y = Math.sin(a) * R; n.vx = 0; n.vy = 0;
            });
            var byId = {};
            nodes.forEach(function(n) { byId[n.id] = n; });

            function layout() {
                var repK = 1400, springK = 0.05, restL = 110, damp = 0.85, n = nodes.length;
                for (var it = 0; it < 500; it++) {
                    for (var i = 0; i < n; i++) {
                        for (var j = i + 1; j < n; j++) {
                            var a = nodes[i], b = nodes[j];
                            var dx = a.x - b.x, dy = a.y - b.y;
                            var d2 = dx * dx + dy * dy || 1;
                            var d = Math.sqrt(d2);
                            var f = repK / d2;
                            var fx = (dx / d) * f, fy = (dy / d) * f;
                            a.vx += fx; a.vy += fy; b.vx -= fx; b.vy -= fy;
                        }
                    }
                    for (var e = 0; e < edges.length; e++) {
                        var sa = byId[edges[e].source], ta = byId[edges[e].target];
                        if (!sa || !ta) continue;
                        var ex = ta.x - sa.x, ey = ta.y - sa.y;
                        var ed = Math.sqrt(ex * ex + ey * ey) || 1;
                        var pull = (ed - restL) * springK;
                        sa.vx += (ex / ed) * pull; sa.vy += (ey / ed) * pull;
                        ta.vx -= (ex / ed) * pull; ta.vy -= (ey / ed) * pull;
                    }
                    nodes.forEach(function(nd) {
                        nd.vx *= damp; nd.vy *= damp;
                        nd.x += nd.vx; nd.y += nd.vy;
                    });
                }
            }
            layout();

            var minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
            nodes.forEach(function(n) {
                minX = Math.min(minX, n.x); maxX = Math.max(maxX, n.x);
                minY = Math.min(minY, n.y); maxY = Math.max(maxY, n.y);
            });

            var baseS = 1, baseOx = 0, baseOy = 0, viewZoom = 1, viewTX = 0, viewTY = 0;

            function fitBase(w, h) {
                var pad = 60;
                var sx = (w - 2 * pad) / (maxX - minX || 1);
                var sy = (h - 2 * pad) / (maxY - minY || 1);
                baseS = Math.min(sx, sy);
                baseOx = (w - (maxX - minX) * baseS) / 2;
                baseOy = (h - (maxY - minY) * baseS) / 2;
                viewZoom = 1; viewTX = 0; viewTY = 0;
            }

            function draw() {
                var w = canvas.clientWidth || 800, h = canvas.clientHeight || 520;
                canvas.width = w; canvas.height = h;
                ctx.clearRect(0, 0, w, h);
                ctx.save();
                ctx.translate(viewTX, viewTY);
                ctx.scale(viewZoom, viewZoom);

                ctx.lineWidth = 1 / viewZoom;
                ctx.strokeStyle = 'rgba(100,116,139,0.35)';
                edges.forEach(function(e) {
                    var a = byId[e.source], b = byId[e.target];
                    if (!a || !b) return;
                    ctx.beginPath();
                    ctx.moveTo((a.x - minX) * baseS + baseOx, (a.y - minY) * baseS + baseOy);
                    ctx.lineTo((b.x - minX) * baseS + baseOx, (b.y - minY) * baseS + baseOy);
                    ctx.stroke();
                });
                nodes.forEach(function(n) {
                    var px = (n.x - minX) * baseS + baseOx, py = (n.y - minY) * baseS + baseOy;
                    var r = (5 + Math.min(10, (degree[n.id] || 0) * 0.7)) / viewZoom;
                    ctx.fillStyle = TYPE_COLORS[n.type] || '#6b7280';
                    ctx.beginPath();
                    ctx.arc(px, py, r, 0, 2 * Math.PI);
                    ctx.fill();
                    ctx.strokeStyle = '#ffffff'; ctx.lineWidth = 1.5 / viewZoom; ctx.stroke();
                    if (viewZoom > 1.6 && n.label) {
                        ctx.fillStyle = '#334155'; ctx.font = (11 / viewZoom) + 'px sans-serif';
                        ctx.fillText(String(n.label).substring(0, 28), px + r + 3, py + 4 / viewZoom);
                    }
                });
                ctx.restore();
            }

            function redraw() { draw(); }

            // --- interaction: zoom / pan / pinch ---
            var pointers = {};
            var dragStart = null, startTX = 0, startTY = 0;
            var pinch = null;

            canvas.addEventListener('pointerdown', function(e) {
                canvas.setPointerCapture(e.pointerId);
                pointers[e.pointerId] = { x: e.clientX, y: e.clientY };
                var keys = Object.keys(pointers);
                if (keys.length === 1) {
                    dragStart = { x: e.clientX, y: e.clientY };
                    startTX = viewTX; startTY = viewTY;
                } else if (keys.length === 2) {
                    var p1 = pointers[keys[0]], p2 = pointers[keys[1]];
                    pinch = {
                        d: Math.hypot(p2.x - p1.x, p2.y - p1.y),
                        z: viewZoom, tx: viewTX, ty: viewTY,
                        cx: (p1.x + p2.x) / 2, cy: (p1.y + p2.y) / 2
                    };
                }
            });

            canvas.addEventListener('pointermove', function(e) {
                if (!(e.pointerId in pointers)) return;
                pointers[e.pointerId] = { x: e.clientX, y: e.clientY };
                var keys = Object.keys(pointers);
                if (keys.length === 1 && dragStart) {
                    viewTX = startTX + (e.clientX - dragStart.x);
                    viewTY = startTY + (e.clientY - dragStart.y);
                    draw();
                } else if (keys.length === 2 && pinch) {
                    var p1 = pointers[keys[0]], p2 = pointers[keys[1]];
                    var nd = Math.hypot(p2.x - p1.x, p2.y - p1.y);
                    var ncx = (p1.x + p2.x) / 2, ncy = (p1.y + p2.y) / 2;
                    var nz = Math.max(0.1, Math.min(40, pinch.z * (nd / (pinch.d || 1))));
                    viewZoom = nz;
                    var f = nd / (pinch.d || 1);
                    viewTX = ncx - (pinch.cx - pinch.tx) * f;
                    viewTY = ncy - (pinch.cy - pinch.ty) * f;
                    draw();
                }
            });

            function endPointer(e) {
                delete pointers[e.pointerId];
                var keys = Object.keys(pointers);
                if (keys.length === 1) {
                    var p = pointers[keys[0]];
                    dragStart = { x: p.x, y: p.y };
                    startTX = viewTX; startTY = viewTY;
                } else {
                    dragStart = null;
                }
                if (keys.length < 2) pinch = null;
            }
            canvas.addEventListener('pointerup', endPointer);
            canvas.addEventListener('pointercancel', endPointer);

            canvas.addEventListener('wheel', function(e) {
                e.preventDefault();
                var rect = canvas.getBoundingClientRect();
                var mx = e.clientX - rect.left, my = e.clientY - rect.top;
                var factor = e.deltaY < 0 ? 1.12 : 0.89;
                var nz = Math.min(8, Math.max(0.05, viewZoom * factor));
                var f = nz / viewZoom;
                viewZoom = nz;
                viewTX = mx - (mx - viewTX) * f;
                viewTY = my - (my - viewTY) * f;
                draw();
            }, { passive: false });

            window.addEventListener('resize', function() {
                fit();
            });

            function fit() {
                var w = canvas.clientWidth || canvas.width || 800;
                var h = canvas.clientHeight || canvas.height || 520;
                fitBase(w, h);
                draw();
            }

            window.__topoFit = fit;
            fit();
        })();
    </script>
</body>
</html>''')
        
        logger.info(_("Generated HTML report: {filename}").format(filename=filename))
        return filename

    def _get_asset_note(self, asset):
        if asset.type == "url" and "title" in asset.properties:
            url_config = self.config.get('asset_types', {}).get('url', {})
            title_extraction_config = url_config.get('title_extraction', {})
            if title_extraction_config.get('enabled', True):
                title = asset.properties["title"]
                max_length = title_extraction_config.get('max_length', 50)
                if len(title) > max_length:
                    title = title[:max_length] + "..."
                return title
        elif asset.type == "port":
            return asset.properties.get("service", _("Unknown Service"))
        return ""

    def _get_asset_fingerprint_info(self, asset):
        fingerprints = ""
        cms = ""
        server = ""
        if asset.type == "url":
            if "fingerprints" in asset.properties and asset.properties["fingerprints"]:
                fingerprints = ",".join(asset.properties["fingerprints"])
            if "cms" in asset.properties and asset.properties["cms"]:
                cms = asset.properties["cms"]
            if "server" in asset.properties and asset.properties["server"]:
                server = asset.properties["server"]
        return fingerprints, cms, server

    def _get_eliminated_reason(self, asset):
        if "eliminated_reason" in asset.properties:
            return asset.properties["eliminated_reason"]
        elif asset.type == "url" and "status_code" in asset.properties:
            return _("HTTP status code: {code}").format(code=asset.properties['status_code'])
        elif asset.type == "port" and "reason" in asset.properties:
            return asset.properties["reason"]
        elif asset.type == "domain" and "reason" in asset.properties:
            return asset.properties["reason"]
        elif asset.type == "ip" and "reason" in asset.properties:
            return asset.properties["reason"]
        else:
            if asset.type == "url":
                return _("Inaccessible URL")
            elif asset.type == "port":
                return _("Closed port")
            elif asset.type == "domain":
                return _("Unresolvable domain")
            elif asset.type == "ip":
                return _("Unreachable IP")
        return _("Unknown")