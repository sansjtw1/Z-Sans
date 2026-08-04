#!/usr/bin/env python3
# coding: utf-8

import csv
import json
import logging
import os
import time
from datetime import datetime
from core.i18n import _

logger = logging.getLogger('zsans.output')


class OutputHandler:
    def __init__(self, engine, config=None):
        self.engine = engine
        self.config = config or {}
        self.output_dir = self.config.get('dir', self.config.get('output_dir', 'output'))
        
        if not os.path.exists(self.output_dir):
            os.makedirs(self.output_dir)
    
    def generate_output(self, formats=None):
        if formats is None:
            formats = self.config.get('output_formats', ['json', 'csv', 'graphml', 'report'])
        
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        prefix = self.config.get('output_prefix', 'zsans')
        base_filename = f"{prefix}_{timestamp}"
        
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
            json.dump(self.engine.asset_graph.export_json(), f, ensure_ascii=False, indent=2)
        
        logger.info(_("Exported JSON asset graph: {filename}").format(filename=filename))
        return filename
    
    def _export_csv(self, base_filename):
        assets_filename = os.path.join(self.output_dir, f"{base_filename}_assets.csv")
        relations_filename = os.path.join(self.output_dir, f"{base_filename}_relations.csv")
        
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
            
            for uid, asset in self.engine.asset_graph.nodes.items():
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
            
            for (source_id, target_id), relation_type in self.engine.asset_graph.edges.items():
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
        
        for uid, asset in self.engine.asset_graph.nodes.items():
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
        
        with open(filename, 'w', encoding='utf-8') as f:
            f.write('''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Z-Sans Asset Breeding Engine Report</title>
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
            .stats-grid { grid-template-columns: repeat(2, 1fr); }
            .chart-container { flex-direction: column; }
            .donut-chart { flex: 0 0 auto; }
        }
    </style>
</head>
<body>
    <div class="header">
        <div class="header-inner">
            <div>
                <h1>Z-Sans Asset Breeding Engine</h1>
                <div class="subtitle">Scan Report &mdash; Generated: ''' + gen_time + '''</div>
            </div>
            <div>
                <span class="badge">v0.0.1</span>
            </div>
        </div>
    </div>
    <div class="container">
        <div class="nav-tabs">
            <button class="nav-tab active" onclick="switchTab('overview')">Overview</button>
            <button class="nav-tab" onclick="switchTab('assets')">Active Assets</button>
            <button class="nav-tab" onclick="switchTab('eliminated')">Eliminated</button>
            <button class="nav-tab" onclick="switchTab('metrics')">Metrics</button>
        </div>

        <!-- Overview Tab -->
        <div id="tab-overview" class="tab-content active">
            <div class="stats-grid">
                <div class="stat-card accent">
                    <div class="stat-value">''' + str(stats['total_assets']) + '''</div>
                    <div class="stat-label">Total Assets</div>
                </div>
                <div class="stat-card info">
                    <div class="stat-value">''' + str(stats['total_relations']) + '''</div>
                    <div class="stat-label">Relations</div>
                </div>
                <div class="stat-card success">
                    <div class="stat-value">''' + str(metrics.get('assets_processed', 0)) + '''</div>
                    <div class="stat-label">Processed</div>
                </div>
                <div class="stat-card warning">
                    <div class="stat-value">''' + str(metrics.get('new_assets_found', 0)) + '''</div>
                    <div class="stat-label">New Found</div>
                </div>
                <div class="stat-card danger">
                    <div class="stat-value">''' + str(metrics.get('depth_reached', 0)) + '''</div>
                    <div class="stat-label">Max Depth</div>
                </div>
            </div>

            <div class="card">
                <h2>Asset Type Distribution</h2>
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

        <!-- Active Assets Tab -->
        <div id="tab-assets" class="tab-content">
            <div class="card">
                <div class="toolbar">
                    <h2 style="margin-bottom:0;">Active Assets</h2>
                    <input type="text" class="search-box" placeholder="Search assets..." oninput="filterTable('active-table', this.value)">
                </div>
                <div class="count-info" id="active-count">Showing ''' + str(len(active_assets)) + ''' assets</div>
                <div class="table-wrapper">
                    <table id="active-table">
                        <thead>
                            <tr>
                                <th>Type</th>
                                <th>Value</th>
                                <th>Depth</th>
                                <th>Discovery Time</th>
                                <th>State</th>
                                <th>Title / Note</th>
                                <th>Fingerprints</th>
                                <th>CMS</th>
                                <th>Server</th>
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
                f.write('''                            <tr><td colspan="9"><div class="empty-state"><div class="empty-icon">&#128269;</div>No active assets found</div></td></tr>
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
                    <h2 style="margin-bottom:0;">Eliminated Assets</h2>
                    <input type="text" class="search-box" placeholder="Search eliminated assets..." oninput="filterTable('eliminated-table', this.value)">
                </div>
                <p style="font-size:0.85rem;color:var(--text-secondary);margin-bottom:12px;">Eliminated assets are those that are not alive or responsive, including inaccessible ports, unreachable URLs, etc.</p>
                <div class="count-info" id="eliminated-count">Showing ''' + str(len(eliminated_assets)) + ''' assets</div>
                <div class="table-wrapper">
                    <table id="eliminated-table">
                        <thead>
                            <tr>
                                <th>Type</th>
                                <th>Value</th>
                                <th>Depth</th>
                                <th>Discovery Time</th>
                                <th>State</th>
                                <th>Reason</th>
                                <th>Fingerprints</th>
                                <th>CMS</th>
                                <th>Server</th>
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
                    f.write('''                            <tr><td colspan="9"><div class="empty-state"><div class="empty-icon">&#9989;</div>No eliminated assets found</div></td></tr>
''')
            else:
                f.write('''                            <tr><td colspan="9"><div class="empty-state"><div class="empty-icon">&#9888;</div>Eliminated assets display is disabled in configuration. Set <code>keep_eliminated_assets: true</code> to enable.</div></td></tr>
''')
            f.write('''                        </tbody>
                    </table>
                </div>
            </div>
        </div>

        <!-- Metrics Tab -->
        <div id="tab-metrics" class="tab-content">
            <div class="card">
                <h2>Execution Metrics</h2>
                <div class="table-wrapper">
                    <table class="metric-table">
                        <thead>
                            <tr><th>Metric</th><th>Value</th></tr>
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
        </div>
    </div>

    <div class="footer">
        Z-Sans Asset Breeding Engine &mdash; Report generated at ''' + gen_time + '''
    </div>

    <script>
        function switchTab(tabName) {
            document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
            document.querySelectorAll('.nav-tab').forEach(t => t.classList.remove('active'));
            document.getElementById('tab-' + tabName).classList.add('active');
            event.target.classList.add('active');
        }
        function filterTable(tableId, query) {
            var table = document.getElementById(tableId);
            var rows = table.querySelectorAll('tbody tr');
            var visible = 0;
            var q = query.toLowerCase();
            rows.forEach(function(row) {
                var text = row.textContent.toLowerCase();
                if (text.indexOf(q) > -1) {
                    row.style.display = '';
                    visible++;
                } else {
                    row.style.display = 'none';
                }
            });
            var countId = tableId === 'active-table' ? 'active-count' : 'eliminated-count';
            var countEl = document.getElementById(countId);
            if (countEl) {
                countEl.textContent = 'Showing ' + visible + ' assets' + (query ? ' (filtered)' : '');
            }
        }
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