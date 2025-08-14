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
        self.output_dir = self.config.get('output_dir', 'output')
        
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
                    asset.value,
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
        
        with open(filename, 'w', encoding='utf-8') as f:
            f.write(f"""
            <!DOCTYPE html>
            <html>
            <head>
                <title>{_("Z-Sans Asset Breeding Engine Report")}</title>
                <meta charset="utf-8">
                <meta http-equiv="Content-Type" content="text/html; charset=utf-8">
                <meta name="viewport" content="width=device-width, initial-scale=1">
                <style>
                    body {{ font-family: Arial, sans-serif; margin: 0; padding: 20px; line-height: 1.6; }}
                    h1, h2, h3 {{ color: #333; }}
                    .container {{ max-width: 1200px; margin: 0 auto; }}
                    .card {{ background: #f9f9f9; border-radius: 5px; padding: 15px; margin-bottom: 20px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }}
                    .stat {{ display: inline-block; margin-right: 20px; margin-bottom: 10px; }}
                    .stat-value {{ font-size: 24px; font-weight: bold; }}
                    .stat-label {{ font-size: 14px; color: #666; }}
                    table {{ width: 100%; border-collapse: collapse; margin-bottom: 20px; }}
                    th, td {{ padding: 8px; text-align: left; border-bottom: 1px solid #ddd; }}
                    th {{ background-color: #f2f2f2; }}
                    tr:hover {{ background-color: #f5f5f5; }}
                </style>
            </head>
            <body>
                <div class="container">
                    <h1>{_("Z-Sans Asset Breeding Engine Report")}</h1>
                    <p>{_("Generated at:")} {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
                    
                    <div class="card">
                        <h2>{_("Summary")}</h2>
                        <div class="stat">
                            <div class="stat-value">{stats['total_assets']}</div>
                            <div class="stat-label">{_("Total Assets")}</div>
                        </div>
                        <div class="stat">
                            <div class="stat-value">{stats['total_relations']}</div>
                            <div class="stat-label">{_("Relations")}</div>
                        </div>
                        <div class="stat">
                            <div class="stat-value">{metrics.get('assets_processed', 0)}</div>
                            <div class="stat-label">{_("Assets Processed")}</div>
                        </div>
                        <div class="stat">
                            <div class="stat-value">{metrics.get('new_assets_found', 0)}</div>
                            <div class="stat-label">{_("New Assets Found")}</div>
                        </div>
                        <div class="stat">
                            <div class="stat-value">{metrics.get('depth_reached', 0)}</div>
                            <div class="stat-label">{_("Max Depth")}</div>
                        </div>
                    </div>
                    
                    <div class="card">
                        <h2>{_("Asset Type Distribution")}</h2>
                        <div>
            """)
            
            for asset_type, count in stats.get('asset_types', {}).items():
                f.write(f"""
                            <div class="stat">
                                <div class="stat-value">{count}</div>
                                <div class="stat-label">{asset_type}</div>
                            </div>
                """)
            
            f.write(f"""
                        </div>
                    </div>
                    
                    <div class="card">
                        <h2>{_("Asset List")}</h2>
                        <table>
                            <thead>
                                <tr>
                                    <th>{_("Type")}</th>
                                    <th>{_("Value")}</th>
                                    <th>{_("Depth")}</th>
                                    <th>{_("Discovery Time")}</th>
                                    <th>{_("State")}</th>
                                    <th>{_("Title/Note")}</th>
                                    <th>{_("Fingerprints")}</th>
                                    <th>{_("CMS")}</th>
                                    <th>{_("Server")}</th>
                                </tr>
                            </thead>
                            <tbody>
            """)
            
            keep_eliminated = self.config.get('keep_eliminated_assets', False)
            
            active_assets = []
            eliminated_assets = []
            
            logger.info(_("Processing HTML report, total assets: {count}").format(count=len(self.engine.asset_graph.nodes)))
            
            for uid, asset in self.engine.asset_graph.nodes.items():
                logger.debug(_("Processing asset: {uid}, state: {state}").format(uid=asset.uid, state=asset.state))
                if asset.state == "eliminated":
                    eliminated_assets.append(asset)
                    logger.debug(_("Added to eliminated assets: {uid}").format(uid=asset.uid))
                else:
                    active_assets.append(asset)
            
            logger.info(_("Collected eliminated assets: {count}").format(count=len(eliminated_assets)))
            
            active_assets.sort(key=lambda x: x.type)
            eliminated_assets.sort(key=lambda x: x.type)
            
            for asset in active_assets:
                discovery_time = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(asset.properties.get('discovery_time', time.time())))
                
                title_or_note = ""
                if asset.type == "url" and "title" in asset.properties:
                    url_config = self.config.get('asset_types', {}).get('url', {})
                    title_extraction_config = url_config.get('title_extraction', {})
                    
                    if title_extraction_config.get('enabled', True):
                        title = asset.properties["title"]
                        max_length = title_extraction_config.get('max_length', 50)
                        if len(title) > max_length:
                            title = title[:max_length] + "..."
                        title_or_note = title
                elif asset.type == "port":
                    title_or_note = asset.properties.get("service", _("Unknown Service"))
                
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
                
                f.write(f"""
                                <tr>
                                    <td>{asset.type}</td>
                                    <td>{asset.value}</td>
                                    <td>{asset.depth}</td>
                                    <td>{discovery_time}</td>
                                    <td>{asset.state}</td>
                                    <td>{title_or_note}</td>
                                    <td>{fingerprints}</td>
                                    <td>{cms}</td>
                                    <td>{server}</td>
                                </tr>
                """)
            
            f.write(f"""
                            </tbody>
                        </table>
                    </div>
                    
                    <div class="card">
                        <h2>{_("Eliminated Assets List")}</h2>
                        <p>{_("Eliminated assets are those that are not alive or responsive, including inaccessible ports, unreachable URLs, etc.")}</p>
                        <table>
                            <thead>
                                <tr>
                                    <th>{_("Type")}</th>
                                    <th>{_("Value")}</th>
                                    <th>{_("Depth")}</th>
                                    <th>{_("Discovery Time")}</th>
                                    <th>{_("State")}</th>
                                    <th>{_("Note")}</th>
                                    <th>{_("Fingerprints")}</th>
                                    <th>{_("CMS")}</th>
                                    <th>{_("Server")}</th>
                                </tr>
                            </thead>
                            <tbody>
            """)
            
            if keep_eliminated:
                logger.info(_("Preparing to display eliminated assets, count: {count}").format(count=len(eliminated_assets)))
                
                if not eliminated_assets:
                    f.write(f"""
                                <tr>
                                    <td colspan="9" style="text-align: center;">{_("No eliminated assets found. This may be because all assets are normal, or eliminated assets were not properly marked.")}</td>
                                </tr>
                    """)
                else:
                    for asset in eliminated_assets:
                        discovery_time = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(asset.properties.get('discovery_time', time.time())))
                        
                        reason = ""
                        if "eliminated_reason" in asset.properties:
                            reason = asset.properties["eliminated_reason"]
                            logger.debug(_("Got elimination reason from eliminated_reason: {reason}").format(reason=reason))
                        elif asset.type == "url" and "status_code" in asset.properties:
                            reason = _("HTTP status code: {code}").format(code=asset.properties['status_code'])
                        elif asset.type == "port" and "reason" in asset.properties:
                            reason = asset.properties["reason"]
                        elif asset.type == "domain" and "reason" in asset.properties:
                            reason = asset.properties["reason"]
                        elif asset.type == "ip" and "reason" in asset.properties:
                            reason = asset.properties["reason"]
                        else:
                            if asset.type == "url":
                                reason = _("Inaccessible URL")
                            elif asset.type == "port":
                                reason = _("Closed port")
                            elif asset.type == "domain":
                                reason = _("Unresolvable domain")
                            elif asset.type == "ip":
                                reason = _("Unreachable IP")
                        
                        logger.debug(_("Adding eliminated asset to HTML report: {type} - {value} - {state} - {reason}").format(
                            type=asset.type, value=asset.value, state=asset.state, reason=reason))
                        
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
                        
                        f.write(f"""
                                <tr>
                                    <td>{asset.type}</td>
                                    <td>{asset.value}</td>
                                    <td>{asset.depth}</td>
                                    <td>{discovery_time}</td>
                                    <td>{asset.state}</td>
                                    <td>{reason}</td>
                                    <td>{fingerprints}</td>
                                    <td>{cms}</td>
                                    <td>{server}</td>
                                </tr>
                        """)
            else:
                f.write(f"""
                                <tr>
                                    <td colspan="9" style="text-align: center;">{_("According to configuration, eliminated assets are not displayed. You can set keep_eliminated_assets: true to display them.")}</td>
                                </tr>
                """)
            
            f.write(f"""
                            </tbody>
                        </table>
                    </div>
                    
                    <div class="card">
                        <h2>{_("Execution Metrics")}</h2>
                        <table>
                            <thead>
                                <tr>
                                    <th>{_("Metric")}</th>
                                    <th>{_("Value")}</th>
                                </tr>
                            </thead>
                            <tbody>
            """)
            
            for key, value in metrics.items():
                f.write(f"""
                                <tr>
                                    <td>{key}</td>
                                    <td>{value}</td>
                                </tr>
                """)
            
            f.write(f"""
                            </tbody>
                        </table>
                    </div>
                </div>
            </body>
            </html>
            """)
        
        logger.info(_("Generated HTML report: {filename}").format(filename=filename))
        return filename