#!/usr/bin/env python3
# coding: utf-8

INDEX_HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Z-Sans Web Console</title>
<script src="/static/vue.global.prod.js"></script>
<style>
:root {
  --bg: #0f172a; --bg2: #1e293b; --card: #1e293b; --border: #334155;
  --text: #e2e8f0; --text2: #94a3b8; --accent: #38bdf8; --accent2: #0ea5e9;
  --green: #4ade80; --red: #f87171; --yellow: #fbbf24; --purple: #a78bfa;
}
* { margin:0; padding:0; box-sizing:border-box; }
body { background:var(--bg); color:var(--text); font-family:-apple-system,'Segoe UI',Roboto,'PingFang SC','Microsoft YaHei',sans-serif; }
a { color:var(--accent); text-decoration:none; }
.topbar { display:flex; align-items:center; gap:16px; padding:14px 24px; background:var(--bg2); border-bottom:1px solid var(--border); position:sticky; top:0; z-index:50; }
.topbar h1 { font-size:1.1rem; font-weight:600; color:var(--text); }
.topbar .ver { color:var(--text2); font-size:.8rem; }
.topbar .nav { display:flex; gap:4px; margin-left:auto; }
.topbar .nav button { background:transparent; border:1px solid transparent; color:var(--text2); padding:6px 12px; border-radius:6px; cursor:pointer; font-size:.85rem; }
.topbar .nav button:hover { color:var(--text); background:var(--bg); }
.topbar .nav button.active { color:var(--accent); background:var(--bg); border-color:var(--border); }
.container { max-width:1280px; margin:0 auto; padding:24px; }
.grid { display:grid; gap:16px; }
.card { background:var(--card); border:1px solid var(--border); border-radius:10px; padding:20px; }
.card h3 { font-size:.95rem; margin-bottom:12px; color:var(--text); }
.row { display:flex; gap:12px; flex-wrap:wrap; align-items:center; }
.stat { flex:1; min-width:140px; background:var(--bg); border:1px solid var(--border); border-radius:8px; padding:14px 16px; }
.stat .n { font-size:1.4rem; font-weight:700; color:var(--accent); }
.stat .l { font-size:.75rem; color:var(--text2); margin-top:2px; }
table { width:100%; border-collapse:collapse; font-size:.85rem; }
th,td { text-align:left; padding:8px 10px; border-bottom:1px solid var(--border); }
th { color:var(--text2); font-weight:600; font-size:.75rem; text-transform:uppercase; }
tr:hover td { background:rgba(56,189,248,.05); }
.badge { display:inline-block; padding:2px 8px; border-radius:999px; font-size:.7rem; font-weight:600; }
.badge-running { background:rgba(56,189,248,.15); color:var(--accent); }
.badge-completed { background:rgba(74,222,128,.15); color:var(--green); }
.badge-failed { background:rgba(248,113,113,.15); color:var(--red); }
.badge-stopped { background:rgba(251,191,36,.15); color:var(--yellow); }
.badge-loaded { background:rgba(74,222,128,.15); color:var(--green); }
.badge-disabled { background:rgba(148,163,184,.15); color:var(--text2); }
.badge-failed-p { background:rgba(248,113,113,.15); color:var(--red); }
input,select,textarea { background:var(--bg); border:1px solid var(--border); color:var(--text); border-radius:6px; padding:8px 10px; font-size:.85rem; }
input:focus,select:focus,textarea:focus { outline:none; border-color:var(--accent); }
textarea { font-family:'SF Mono',Consolas,monospace; font-size:.8rem; line-height:1.5; width:100%; min-height:320px; }
.btn { background:var(--accent2); color:#fff; border:none; padding:8px 16px; border-radius:6px; cursor:pointer; font-size:.85rem; font-weight:600; }
.btn:hover { background:var(--accent); }
.btn-danger { background:var(--red); }
.btn-ghost { background:transparent; color:var(--text2); border:1px solid var(--border); }
.btn-sm { padding:4px 10px; font-size:.75rem; }
.empty { text-align:center; color:var(--text2); padding:40px 0; }
.progress { height:8px; background:var(--bg); border-radius:4px; overflow:hidden; border:1px solid var(--border); }
.progress>div { height:100%; background:linear-gradient(90deg,var(--accent2),var(--accent)); transition:width .4s; }
.logbox { background:#0b1120; border:1px solid var(--border); border-radius:8px; padding:12px; font-family:'SF Mono',Consolas,monospace; font-size:.75rem; max-height:420px; overflow-y:auto; color:#94a3b8; line-height:1.6; }
.logbox .ts { color:var(--purple); margin-right:6px; }
.tracebox { background:#0b1120; border:1px solid var(--red); border-radius:6px; padding:10px; font-family:'SF Mono',Consolas,monospace; font-size:.72rem; color:var(--red); overflow-x:auto; max-height:260px; overflow-y:auto; white-space:pre; }
.tabs { display:flex; gap:2px; border-bottom:1px solid var(--border); margin-bottom:16px; }
.tabs button { background:transparent; border:none; color:var(--text2); padding:8px 16px; cursor:pointer; font-size:.85rem; border-bottom:2px solid transparent; }
.tabs button.active { color:var(--accent); border-bottom-color:var(--accent); }
.type-badge { display:inline-block; padding:1px 8px; border-radius:4px; font-size:.7rem; background:var(--bg); border:1px solid var(--border); color:var(--accent); }
.asset-row { display:flex; gap:10px; align-items:center; padding:6px 0; border-bottom:1px dashed var(--border); font-size:.82rem; }
.asset-row .val { font-family:'SF Mono',Consolas,monospace; color:var(--text); word-break:break-all; }
.hidden { display:none; }
.topo-canvas { width:100%; height:480px; background:#0b1120; border:1px solid var(--border); border-radius:8px; display:block; }
.modal-mask { position:fixed; inset:0; background:rgba(0,0,0,.6); display:flex; align-items:center; justify-content:center; z-index:100; }
.modal { background:var(--bg2); border:1px solid var(--border); border-radius:12px; padding:20px; max-width:620px; width:92%; max-height:80vh; overflow-y:auto; }
.prop-grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(240px,1fr)); gap:8px; }
.prop-item { background:var(--bg); border:1px solid var(--border); border-radius:6px; padding:8px 10px; }
.prop-key { font-size:.7rem; color:var(--accent); margin-bottom:3px; word-break:break-all; }
.prop-val { font-size:.8rem; color:var(--text); font-family:'SF Mono',Consolas,monospace; word-break:break-all; white-space:pre-wrap; max-height:120px; overflow-y:auto; }

/* ===== 响应式 ===== */
@media (max-width: 768px) {
  body { font-size:14px; }
  .topbar { flex-wrap:wrap; gap:8px; padding:10px 14px; }
  .topbar .nav { margin-left:0; width:100%; overflow-x:auto; flex-wrap:nowrap; -webkit-overflow-scrolling:touch; }
  .topbar .nav button { flex:1 0 auto; padding:6px 8px; font-size:.78rem; white-space:nowrap; }
  .container { padding:14px; }
  .card { padding:14px; }
  .row { gap:8px; }
  .stat { flex:1 1 45%; min-width:0; padding:10px 12px; }
  .stat .n { font-size:1.15rem; }
  .table-wrapper { overflow-x:auto; -webkit-overflow-scrolling:touch; }
  table { font-size:.78rem; min-width:520px; }
  th,td { padding:6px 8px; }
  /* 隐藏部分次要列以适配窄屏 */
  .hide-mobile { display:none; }
  .btn { padding:8px 12px; font-size:.8rem; }
  .btn-sm { padding:4px 8px; font-size:.7rem; }
  input,select,textarea { font-size:.8rem; }
  textarea { min-height:200px; }
  .tabs { overflow-x:auto; -webkit-overflow-scrolling:touch; flex-wrap:nowrap; }
  .tabs button { flex:1 0 auto; padding:8px 12px; font-size:.8rem; white-space:nowrap; }
  .topo-canvas { height:300px; }
  .modal { max-width:96%; padding:14px; }
  .prop-grid { grid-template-columns:1fr; }
  .logbox { max-height:300px; font-size:.7rem; }
  .tracebox { font-size:.65rem; max-height:180px; }
  .grid { gap:10px; }
}

@media (max-width: 480px) {
  .topbar h1 { font-size:.95rem; }
  .topbar .ver { font-size:.7rem; }
  .stat { flex:1 1 100%; }
  .container { padding:10px; }
  .card { padding:12px; }
}
</style>
</head>
<body>
<div id="app">
  <div class="topbar">
    <h1>Z-Sans Web Console</h1>
    <span class="ver">v{{ version }}</span>
    <div class="nav">
      <button :class="{active: page==='projects'}" @click="go('projects')">{{ t('scan_projects') }}</button>
      <button :class="{active: page==='newscan'}" @click="go('newscan')">{{ t('new_task') }}</button>
      <button :class="{active: page==='tasks'}" @click="go('tasks')">{{ t('tasks') }}</button>
      <button :class="{active: page==='config'}" @click="go('config')">{{ t('config') }}</button>
      <button :class="{active: page==='plugins'}" @click="go('plugins')">{{ t('plugins') }}</button>
    </div>
  </div>
  <div class="container">

    <!-- 扫描项目列表 -->
    <div v-if="page==='projects'">
      <div class="row" style="margin-bottom:16px;justify-content:space-between">
        <h2 style="font-size:1.1rem">{{ t('history_projects') }}</h2>
        <div class="row">
          <button class="btn btn-ghost" @click="loadProjects">{{ t('refresh') }}</button>
          <button class="btn btn-danger" @click="deleteSelected" :disabled="!compareIds.length">{{ t('delete_selected') }} ({{ compareIds.length }})</button>
          <button class="btn" @click="compareSelected" :disabled="compareIds.length<2">{{ t('compare_selected') }}</button>
        </div>
      </div>
      <div class="card" v-if="projects.length">
        <div class="table-wrapper">
        <table>
          <thead><tr>
            <th style="width:36px"><input type="checkbox" :checked="allSelected" @change="toggleAll"></th>
            <th>ID</th><th>{{ t('output') }}</th><th>{{ t('data') }}</th><th>{{ t('actions') }}</th>
          </tr></thead>
          <tbody>
            <tr v-for="p in projects" :key="p.id">
              <td><input type="checkbox" :value="p.id" v-model="compareIds"></td>
              <td><a href="#" @click.prevent="viewProject(p.id)">{{ p.id }}</a></td>
              <td style="font-family:monospace">{{ p.path }}</td>
              <td>{{ p.has_json?'JSON ':'' }}{{ p.has_csv?'CSV ':'' }}{{ p.has_graphml?'GraphML':'' }}</td>
              <td><button class="btn btn-sm btn-ghost" @click="viewProject(p.id)">{{ t('view') }}</button></td>
            </tr>
          </tbody>
        </table>
        </div>
      </div>
      <div v-else class="card"><div class="empty">{{ t('no_projects') }}</div></div>
    </div>

    <!-- 项目对比 -->
    <div v-if="page==='compare'">
      <div class="row" style="margin-bottom:16px">
        <button class="btn btn-ghost" @click="go('projects')">{{ t('back') }}</button>
        <h2 style="font-size:1.1rem">{{ t('project_compare') }}</h2>
      </div>
      <div v-if="compareData">
        <div class="row" style="display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin-bottom:16px">
          <div class="stat"><div class="n">{{ compareData.total_base }}</div><div class="l">{{ t('base_assets') }}</div></div>
          <div class="stat" v-if="compareData.total_others!==undefined"><div class="n">{{ compareData.total_others }}</div><div class="l">{{ t('other_assets') }}</div></div>
          <div class="stat"><div class="n">{{ compareData.only_in_base.length }}</div><div class="l">{{ t('only_base') }}</div></div>
          <div class="stat" v-if="compareData.only_in_others!==undefined"><div class="n">{{ compareData.only_in_others.length }}</div><div class="l">{{ t('only_others') }}</div></div>
          <div class="stat" v-if="compareData.common!==undefined"><div class="n">{{ compareData.common.length }}</div><div class="l">{{ t('common') }}</div></div>
        </div>
        <div class="tabs">
          <button :class="{active: compareTab==='onlybase'}" @click="compareTab='onlybase'">{{ t('only_base_has') }} ({{ compareData.only_in_base.length }})</button>
          <button v-if="compareData.only_in_others" :class="{active: compareTab==='onlyothers'}" @click="compareTab='onlyothers'">{{ t('only_others_has') }} ({{ compareData.only_in_others.length }})</button>
          <button v-if="compareData.common" :class="{active: compareTab==='common'}" @click="compareTab='common'">{{ t('common_has') }} ({{ compareData.common.length }})</button>
        </div>
        <div class="card">
          <div class="asset-row" v-for="uid in compareList" :key="uid">
            <span class="val">{{ uid }}</span>
          </div>
          <div v-if="!compareList.length" class="empty">{{ t('no_assets') }}</div>
        </div>
      </div>
      <div v-else class="card"><div class="empty">{{ t('compare_empty') }}</div></div>
    </div>

    <!-- 项目详情 -->
    <div v-if="page==='project-detail'">
      <div class="row" style="margin-bottom:16px">
        <button class="btn btn-ghost" @click="go('projects')">{{ t('back') }}</button>
        <h2 style="font-size:1.1rem">{{ currentProjectId }}</h2>
      </div>
      <div class="grid" v-if="projectData">
        <div class="row" style="display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px">
          <div class="stat"><div class="n">{{ stats.nodes }}</div><div class="l">{{ t('nodes') }}</div></div>
          <div class="stat"><div class="n">{{ stats.edges }}</div><div class="l">{{ t('edges') }}</div></div>
          <div class="stat"><div class="n">{{ typeCount }}</div><div class="l">{{ t('type_count') }}</div></div>
          <div class="stat"><div class="n">{{ maxDepth }}</div><div class="l">{{ t('max_depth_l') }}</div></div>
        </div>
        <div class="tabs">
          <button :class="{active: detailTab==='assets'}" @click="detailTab='assets'">{{ t('assets') }}</button>
          <button :class="{active: detailTab==='analysis'}" @click="detailTab='analysis'">{{ t('analysis') }}</button>
          <button :class="{active: detailTab==='topology'}" @click="detailTab='topology'">{{ t('topology') }}</button>
          <button :class="{active: detailTab==='raw'}" @click="detailTab='raw'">{{ t('raw_json') }}</button>
        </div>
        <div class="card" v-if="detailTab==='assets'">
          <div class="row" style="margin-bottom:10px">
            <input v-model="assetFilter" :placeholder="t('search_assets')" style="flex:1">
            <select v-model="typeFilter">
              <option value="">{{ t('all_types') }}</option>
              <option v-for="t in typeList" :key="t" :value="t">{{ t }}</option>
            </select>
          </div>
          <div v-if="filteredAssets.length">
            <div class="asset-row" style="cursor:pointer" @click="openAssetDetail(a)" v-for="a in filteredAssets" :key="a.uid">
              <span class="type-badge">{{ a.type }}</span>
              <span class="val">{{ a.value }}</span>
              <span style="color:var(--text2);margin-left:auto">d{{ a.depth }} · {{ a.state }}</span>
            </div>
          </div>
          <div v-else class="empty">{{ t('no_match') }}</div>
        </div>
        <div class="card" v-if="detailTab==='analysis'">
          <div class="row" style="display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:16px">
            <div>
              <h3 style="font-size:.85rem;color:var(--text2);margin-bottom:10px">{{ t('type_dist') }}</h3>
              <div v-for="item in typeDist" :key="item.type" style="margin-bottom:8px">
                <div class="row" style="justify-content:space-between;font-size:.8rem;margin-bottom:3px">
                  <span>{{ item.type }}</span><span style="color:var(--text2)">{{ item.count }}</span>
                </div>
                <div class="progress"><div :style="{width: pct(item.count, typeDistTotal) + '%', background: typeColor(item.type)}"></div></div>
              </div>
              <div v-if="!typeDist.length" class="empty">{{ t('no_data') }}</div>
            </div>
            <div>
              <h3 style="font-size:.85rem;color:var(--text2);margin-bottom:10px">{{ t('state_dist') }}</h3>
              <div v-for="item in stateDist" :key="item.state" style="margin-bottom:8px">
                <div class="row" style="justify-content:space-between;font-size:.8rem;margin-bottom:3px">
                  <span>{{ item.state }}</span><span style="color:var(--text2)">{{ item.count }}</span>
                </div>
                <div class="progress"><div :style="{width: pct(item.count, stateDistTotal) + '%'}"></div></div>
              </div>
              <div v-if="!stateDist.length" class="empty">{{ t('no_data') }}</div>
            </div>
            <div>
              <h3 style="font-size:.85rem;color:var(--text2);margin-bottom:10px">{{ t('depth_dist') }}</h3>
              <div v-for="item in depthDist" :key="'d'+item.depth" style="margin-bottom:8px">
                <div class="row" style="justify-content:space-between;font-size:.8rem;margin-bottom:3px">
                  <span>{{ t('depth') }} {{ item.depth }}</span><span style="color:var(--text2)">{{ item.count }}</span>
                </div>
                <div class="progress"><div :style="{width: pct(item.count, depthDistTotal) + '%', background: 'var(--purple)'}"></div></div>
              </div>
              <div v-if="!depthDist.length" class="empty">{{ t('no_data') }}</div>
            </div>
          </div>
          <div class="row" style="margin-top:20px">
            <div class="stat"><div class="n">{{ topRelated.length }}</div><div class="l">{{ t('high_related') }}</div></div>
            <div class="stat"><div class="n">{{ seedCount }}</div><div class="l">{{ t('seed_domains') }}</div></div>
          </div>
        </div>
        <div class="card" v-if="detailTab==='topology'">
          <div class="topo-wrap">
            <canvas id="topo-canvas" class="topo-canvas"></canvas>
          </div>
          <div class="topo-legend" id="topo-legend" style="margin-top:10px;display:flex;gap:14px;flex-wrap:wrap"></div>
          <div class="topo-hint" style="margin-top:8px;color:var(--text2);font-size:.75rem">{{ t('drag_hint') }}</div>
        </div>
        <div class="card" v-if="detailTab==='raw'">
          <textarea readonly v-model="rawJson"></textarea>
        </div>
      </div>
      <div v-else-if="projectPending" class="card">
        <div class="empty" style="color:var(--yellow)">{{ t('scan_running') }}<br><span style="font-size:.8rem;color:var(--text2)">{{ t('scan_after_done') }}</span></div>
      </div>
      <div v-else class="card"><div class="empty">{{ t('data_not_found') }}</div></div>
    </div>

    <!-- 资产详情弹窗 -->
    <div v-if="assetModal" class="modal-mask" @click.self="assetModal=null">
      <div class="modal">
        <div class="row" style="justify-content:space-between;margin-bottom:12px">
          <h3 style="font-size:1rem"><span class="type-badge">{{ assetModal.type }}</span> {{ assetModal.value }}</h3>
          <button class="btn btn-sm btn-ghost" @click="assetModal=null">{{ t('close') }}</button>
        </div>
        <table>
          <tbody>
            <tr><th style="width:120px">UID</th><td>{{ assetModal.uid }}</td></tr>
            <tr><th>{{ t('type') }}</th><td>{{ assetModal.type }}</td></tr>
            <tr><th>{{ t('depth') }}</th><td>{{ assetModal.depth }}</td></tr>
            <tr><th>{{ t('status') }}</th><td>{{ assetModal.state }}</td></tr>
            <tr v-if="assetModal.source"><th>{{ t('source') }}</th><td>{{ assetModal.source }}</td></tr>
          </tbody>
        </table>
        <h3 style="font-size:.85rem;color:var(--text2);margin:14px 0 8px">{{ t('props') }}</h3>
        <div v-if="propKeys.length" class="prop-grid">
          <div class="prop-item" v-for="k in propKeys" :key="k">
            <div class="prop-key">{{ k }}</div>
            <div class="prop-val">{{ propValue(k) }}</div>
          </div>
        </div>
        <div v-else class="empty" style="padding:12px 0">{{ t('no_props') }}</div>
      </div>
    </div>

    <!-- 新建任务 -->
    <div v-if="page==='newscan'">
      <h2 style="font-size:1.1rem;margin-bottom:16px">{{ t('new_scan_task') }}</h2>
      <div class="card">
        <div style="margin-bottom:14px">
          <label style="display:block;font-size:.8rem;color:var(--text2);margin-bottom:6px">{{ t('domain_seed') }}</label>
          <textarea v-model="newScan.domains" placeholder="example.com&#10;www.example.com" style="min-height:90px"></textarea>
        </div>
        <div style="margin-bottom:14px">
          <label style="display:block;font-size:.8rem;color:var(--text2);margin-bottom:6px">{{ t('url_seed') }}</label>
          <textarea v-model="newScan.urls" placeholder="https://example.com/path" style="min-height:60px"></textarea>
        </div>
        <div style="margin-bottom:14px">
          <label style="display:block;font-size:.8rem;color:var(--text2);margin-bottom:6px">{{ t('ip_seed') }}</label>
          <textarea v-model="newScan.ips" placeholder="203.0.113.9" style="min-height:60px"></textarea>
        </div>
        <div class="row" style="margin-bottom:14px">
          <div><label style="display:block;font-size:.8rem;color:var(--text2);margin-bottom:4px">{{ t('max_depth') }}</label><input type="number" v-model.number="newScan.maxDepth" min="1" max="10" style="width:100px"></div>
          <div><label style="display:block;font-size:.8rem;color:var(--text2);margin-bottom:4px">{{ t('strategy') }}</label>
            <select v-model="newScan.strategy" style="min-width:160px">
              <option value="priority_based">priority_based</option>
              <option value="depth_first">depth_first</option>
              <option value="breadth_first">breadth_first</option>
              <option value="time_based">time_based</option>
            </select>
          </div>
          <div><label style="display:block;font-size:.8rem;color:var(--text2);margin-bottom:4px">{{ t('concurrency') }}</label><input type="number" v-model.number="newScan.concurrency" min="1" max="50" style="width:100px"></div>
        </div>
        <div class="row" style="margin-top:16px">
          <button class="btn" @click="startScan" :disabled="scanStarting">{{ scanStarting ? t('starting') : t('start_scan') }}</button>
          <span v-if="lastTaskId" style="color:var(--green);font-size:.85rem">{{ t('task_started') }} <a href="#" @click.prevent="go('tasks')">{{ lastTaskId }}</a></span>
        </div>
      </div>
    </div>

    <!-- 任务进度 -->
    <div v-if="page==='tasks'">
      <div class="row" style="margin-bottom:16px;justify-content:space-between">
        <h2 style="font-size:1.1rem">{{ t('scan_tasks') }}</h2>
        <button class="btn btn-ghost" @click="pollTasks">{{ t('refresh') }}</button>
      </div>
      <div class="card" v-if="tasks.length">
        <div class="table-wrapper">
        <table>
          <thead><tr><th>ID</th><th>{{ t('seed') }}</th><th>{{ t('status') }}</th><th>{{ t('asset_count') }}</th><th>{{ t('depth') }}</th><th>{{ t('actions') }}</th></tr></thead>
          <tbody>
            <tr v-for="t in tasks" :key="t.id">
              <td>{{ t.id }}</td>
              <td style="max-width:280px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">{{ seedSummary(t.seeds) }}</td>
              <td><span class="badge" :class="'badge-'+t.status">{{ t.status }}</span></td>
              <td>{{ t.metrics.assets_processed || 0 }}</td>
              <td>{{ t.metrics.depth_reached || 0 }}</td>
              <td>
                <button class="btn btn-sm btn-ghost" @click="viewTask(t.id)">{{ t('detail') }}</button>
                <button v-if="t.status==='running'" class="btn btn-sm btn-danger" @click="stopTask(t.id)">{{ t('stop') }}</button>
                <button v-if="t.status!=='running'" class="btn btn-sm btn-ghost" @click="rescanTask(t.id)">{{ t('rescan') }}</button>
              </td>
            </tr>
          </tbody>
        </table>
        </div>
      </div>
      <div v-else class="card"><div class="empty">{{ t('no_tasks') }}</div></div>

      <!-- 任务详情 -->
      <div v-if="activeTask" class="card" style="margin-top:16px">
        <div class="row" style="margin-bottom:12px">
          <h3>{{ t('task_detail') }} {{ activeTask.id }} · {{ activeTask.status }}</h3>
          <button class="btn btn-sm btn-ghost" @click="closeTask">{{ t('close') }}</button>
        </div>
        <div v-if="activeTask.run_dir" class="row" style="margin-bottom:12px">
          <a :href="'/api/projects/' + activeTask.run_dir.split('/').pop()" target="_blank">{{ t('view_output') }}</a>
        </div>
        <div v-if="activeTask.status==='failed' && activeTask.error" class="card" style="margin-bottom:12px;border-color:var(--red)">
          <h3 style="font-size:.85rem;color:var(--red);margin-bottom:8px">{{ t('task_failed') }}: {{ activeTask.error }}</h3>
          <pre v-if="activeTask.traceback" class="tracebox">{{ activeTask.traceback }}</pre>
        </div>
        <div class="progress" v-if="activeTask.status==='running'" style="margin-bottom:12px"><div :style="{width: activeTask.metrics.assets_processed ? '40%' : '5%'}"></div></div>
        <div class="row" style="margin-bottom:6px">
          <span style="font-size:.8rem;color:var(--text2)">{{ t('real_time_log') }}</span>
          <label style="font-size:.8rem;color:var(--text2);cursor:pointer"><input type="checkbox" v-model="logFollow"> {{ t('follow_log') }}</label>
        </div>
        <div class="logbox" ref="logbox">{{ activeTaskLogText }}</div>
      </div>
    </div>

    <!-- 配置编辑 -->
    <div v-if="page==='config'">
      <div class="row" style="margin-bottom:16px;justify-content:space-between">
        <h2 style="font-size:1.1rem">{{ t('config_file') }}</h2>
        <div class="row">
          <button class="btn btn-ghost" @click="loadConfig">{{ t('reload') }}</button>
          <button class="btn" @click="saveConfig">{{ t('save') }}</button>
        </div>
      </div>
      <div class="card">
        <textarea v-model="configText" spellcheck="false"></textarea>
        <div v-if="configSaved" style="color:var(--green);font-size:.85rem;margin-top:8px">{{ t('config_saved') }}</div>
        <div v-if="configError" style="color:var(--red);font-size:.85rem;margin-top:8px">{{ configError }}</div>
      </div>
    </div>

    <!-- 插件管理 -->
    <div v-if="page==='plugins'">
      <div class="row" style="margin-bottom:16px;justify-content:space-between">
        <h2 style="font-size:1.1rem">{{ t('plugins') }}</h2>
        <button class="btn btn-ghost" @click="loadPlugins">{{ t('refresh') }}</button>
      </div>
      <div class="card" v-if="plugins.length">
        <div class="table-wrapper">
        <table>
          <thead><tr><th>{{ t('name') }}</th><th>{{ t('version') }}</th><th>{{ t('status') }}</th><th>{{ t('handlers') }}</th><th>{{ t('events') }}</th><th>{{ t('description') }}</th><th>{{ t('actions') }}</th></tr></thead>
          <tbody>
            <tr v-for="p in plugins" :key="p.name">
              <td><b>{{ p.name }}</b></td>
              <td>{{ p.version }}</td>
              <td><span class="badge" :class="'badge-'+p.status">{{ p.status }}</span></td>
              <td>{{ p.handlers }}</td>
              <td style="font-size:.72rem;color:var(--text2)">{{ (p.events||[]).join(', ') }}</td>
              <td style="color:var(--text2)">{{ p.description }}</td>
              <td>
                <button v-if="p.status==='disabled'" class="btn btn-sm" @click="togglePlugin(p.name)">{{ t('enable') }}</button>
                <button v-else class="btn btn-sm btn-ghost" @click="togglePlugin(p.name)">{{ t('disable') }}</button>
              </td>
            </tr>
          </tbody>
        </table>
        </div>
      </div>
      <div v-else class="card"><div class="empty">{{ t('no_plugins') }}</div></div>
    </div>

  </div>
</div>

<script>
const { createApp } = Vue;
createApp({
  data() {
    return {
      version: '',
      lang: 'zh',
      langDict: {},
      page: 'projects',
      projects: [],
      compareIds: [],
      compareData: null,
      compareTab: 'onlybase',
      projectPending: false,
      assetModal: null,
      _topoHits: [],
      projectData: null,
      currentProjectId: null,
      detailTab: 'assets',
      assetFilter: '',
      typeFilter: '',
      rawJson: '',
      tasks: [],
      activeTask: null,
      taskPollTimer: null,
      _logSource: null,
      logFollow: true,
      newScan: { domains:'', urls:'', ips:'', maxDepth:4, strategy:'priority_based', concurrency:20 },
      scanStarting: false,
      lastTaskId: null,
      configText: '',
      configSaved: false,
      configError: null,
      plugins: [],
    };
  },
  computed: {
    stats() {
      const m = this.projectData && this.projectData.stats || {};
      const nodes = Array.isArray(this.projectData && this.projectData.nodes) ? this.projectData.nodes.length : 0;
      const edges = Array.isArray(this.projectData && this.projectData.edges) ? this.projectData.edges.length : 0;
      return { nodes, edges };
    },
    typeCount() {
      if (!this.projectData || !Array.isArray(this.projectData.nodes)) return 0;
      return new Set(this.projectData.nodes.map(n => n.type)).size;
    },
    maxDepth() {
      if (!this.projectData || !Array.isArray(this.projectData.nodes)) return 0;
      return Math.max(0, ...this.projectData.nodes.map(n => n.depth || 0));
    },
    typeList() {
      if (!this.projectData || !Array.isArray(this.projectData.nodes)) return [];
      return [...new Set(this.projectData.nodes.map(n => n.type))];
    },
    filteredAssets() {
      if (!this.projectData || !Array.isArray(this.projectData.nodes)) return [];
      const f = this.assetFilter.toLowerCase();
      return this.projectData.nodes.filter(n => {
        const okType = !this.typeFilter || n.type === this.typeFilter;
        const okFilter = !f || (n.value||'').toLowerCase().includes(f) || (n.uid||'').toLowerCase().includes(f);
        return okType && okFilter;
      });
    },
    activeTaskLogText() {
      if (!this.activeTask) return '';
      return this.activeTask._logs ? this.activeTask._logs.join('\n') : '暂无日志';
    },
    compareList() {
      if (!this.compareData) return [];
      if (this.compareTab === 'onlybase') return this.compareData.only_in_base || [];
      if (this.compareTab === 'onlyothers') return this.compareData.only_in_others || [];
      if (this.compareTab === 'common') return this.compareData.common || [];
      return [];
    },
    allSelected() {
      return this.projects.length > 0 && this.compareIds.length === this.projects.length;
    },
    _nodes() { return (this.projectData && Array.isArray(this.projectData.nodes)) ? this.projectData.nodes : []; },
    typeDist() {
      const m = {};
      this._nodes.forEach(n => { m[n.type] = (m[n.type]||0) + 1; });
      return Object.entries(m).map(([type, count]) => ({type, count})).sort((a,b)=>b.count-a.count);
    },
    typeDistTotal() { return this._nodes.length || 1; },
    stateDist() {
      const m = {};
      this._nodes.forEach(n => { m[n.state||'unknown'] = (m[n.state||'unknown']||0) + 1; });
      return Object.entries(m).map(([state, count]) => ({state, count})).sort((a,b)=>b.count-a.count);
    },
    stateDistTotal() { return this._nodes.length || 1; },
    depthDist() {
      const m = {};
      this._nodes.forEach(n => { m[n.depth||0] = (m[n.depth||0]||0) + 1; });
      return Object.entries(m).map(([depth, count]) => ({depth: Number(depth), count})).sort((a,b)=>a.depth-b.depth);
    },
    depthDistTotal() { return this._nodes.length || 1; },
    topRelated() {
      const deg = {};
      (this.projectData && this.projectData.edges||[]).forEach(e => {
        deg[e.source]=(deg[e.source]||0)+1; deg[e.target]=(deg[e.target]||0)+1;
      });
      return Object.entries(deg).filter(([,c])=>c>=3).sort((a,b)=>b[1]-a[1]);
    },
    seedCount() {
      const seeds = (this.projectData && this.projectData.seed_domains) || [];
      return Array.isArray(seeds) ? seeds.length : 0;
    },
    propKeys() {
      if (!this.assetModal) return [];
      const p = this.assetModal.properties || {};
      return Object.keys(p).sort();
    },
  },
  methods: {
    t(key) { return (this.langDict && this.langDict[key]) || key; },
    pct(v, total) { return Math.round((v / (total||1)) * 100); },
    typeColor(t) {
      const c = {domain:'#4f46e5', ip:'#0891b2', url:'#059669', port:'#d97706', js:'#dc2626', cert:'#7c3aed'};
      return c[t] || '#6b7280';
    },
    openAssetDetail(a) { this.assetModal = a; },
    propValue(k) {
      if (!this.assetModal) return '';
      const v = (this.assetModal.properties||{})[k];
      if (v === null || v === undefined) return '';
      return typeof v === 'object' ? JSON.stringify(v, null, 2) : String(v);
    },
    go(p) { this.page = p; if (p==='projects') this.loadProjects(); if (p==='tasks') this.loadTasks(); if (p==='config') this.loadConfig(); if (p==='plugins') this.loadPlugins(); },
    api(path, opts) { return fetch(path, opts).then(r => r.json()); },
    loadProjects() { this.api('/api/projects').then(d => { if (Array.isArray(d)) this.projects = d; }); },
    compareSelected() {
      if (this.compareIds.length < 2) { alert('请至少选择两个项目'); return; }
      this.compareTab = 'onlybase';
      this.api('/api/compare?ids=' + encodeURIComponent(this.compareIds.join(','))).then(d => {
        this.compareData = d; this.page = 'compare';
      });
    },
    toggleAll() {
      if (this.allSelected) this.compareIds = [];
      else this.compareIds = this.projects.map(p => p.id);
    },
    deleteSelected() {
      if (!this.compareIds.length) return;
      if (!confirm('确定删除所选 ' + this.compareIds.length + ' 个项目？此操作不可恢复。')) return;
      this.api('/api/projects/delete', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({ids: this.compareIds})}).then(d => {
        if (d.ok) {
          this.compareIds = [];
          this.loadProjects();
          alert('已删除 ' + d.deleted.length + ' 个，失败 ' + (d.failed||[]).length + ' 个');
        } else { alert('删除失败: ' + (d.error||'')); }
      });
    },
    viewProject(id) {
      this.currentProjectId = id; this.page = 'project-detail'; this.detailTab='assets'; this.assetFilter=''; this.typeFilter='';
      this.api('/api/projects/' + id).then(d => {
        if (d && d.nodes) { this.projectData = d; this.rawJson = JSON.stringify(d, null, 2); this.$nextTick(() => this.renderTopo()); }
        else if (d && d.status === 'pending') { this.projectData = null; this.projectPending = true; }
        else { this.projectData = null; this.projectPending = false; }
      });
    },
    renderTopo() {
      const canvas = document.getElementById('topo-canvas');
      if (!canvas || !this.projectData) return;
      const GRAPH = this.projectData;
      const ctx = canvas.getContext('2d');
      if (!GRAPH.nodes || !GRAPH.nodes.length) { ctx.clearRect(0,0,canvas.width,canvas.height); return; }
      const TYPE_COLORS = {domain:'#4f46e5', ip:'#0891b2', url:'#059669', port:'#d97706', js:'#dc2626', cert:'#7c3aed'};
      const legend = document.getElementById('topo-legend');
      if (legend) {
        legend.innerHTML = '';
        Object.keys(TYPE_COLORS).forEach(t => {
          if (GRAPH.nodes.some(n => n.type === t)) {
            const item = document.createElement('span');
            item.innerHTML = '<span style="display:inline-block;width:10px;height:10px;border-radius:50%;background:' + TYPE_COLORS[t] + ';margin-right:5px"></span>' + t;
            legend.appendChild(item);
          }
        });
      }
      const degree = {};
      // 归一化节点 id：JSON 导出节点只有 uid 无 id，统一用 uid 作为拓扑 id
      GRAPH.nodes.forEach(n => { n.id = n.uid || n.id; });
      (GRAPH.edges||[]).forEach(e => { degree[e.source]=(degree[e.source]||0)+1; degree[e.target]=(degree[e.target]||0)+1; });
      let nodes = GRAPH.nodes.slice();
      if (nodes.length > 300) {
        nodes.sort((a,b) => (degree[b.id]||0) - (degree[a.id]||0));
        nodes = nodes.slice(0, 300);
      }
      const idSet = {};
      nodes.forEach(n => idSet[n.id] = true);
      const edges = (GRAPH.edges||[]).filter(e => idSet[e.source] && idSet[e.target]);
      const R = 320;
      nodes.forEach((n,i) => { const a = 2*Math.PI*i/nodes.length; n.x=Math.cos(a)*R; n.y=Math.sin(a)*R; n.vx=0; n.vy=0; });
      const byId = {};
      nodes.forEach(n => byId[n.id] = n);
      (function layout() {
        const repK=1400, springK=0.05, restL=110, damp=0.85, n=nodes.length;
        for (let it=0; it<300; it++) {
          for (let i=0;i<n;i++) for (let j=i+1;j<n;j++) {
            const a=nodes[i], b=nodes[j];
            const dx=a.x-b.x, dy=a.y-b.y, d2=dx*dx+dy*dy||1, d=Math.sqrt(d2), f=repK/d2;
            const fx=(dx/d)*f, fy=(dy/d)*f;
            a.vx+=fx; a.vy+=fy; b.vx-=fx; b.vy-=fy;
          }
          for (let e=0;e<edges.length;e++) {
            const sa=byId[edges[e].source], ta=byId[edges[e].target];
            if (!sa||!ta) continue;
            const ex=ta.x-sa.x, ey=ta.y-sa.y, ed=Math.sqrt(ex*ex+ey*ey)||1, pull=(ed-restL)*springK;
            sa.vx+=(ex/ed)*pull; sa.vy+=(ey/ed)*pull; ta.vx-=(ex/ed)*pull; ta.vy-=(ey/ed)*pull;
          }
          nodes.forEach(nd => { nd.vx*=damp; nd.vy*=damp; nd.x+=nd.vx; nd.y+=nd.vy; });
        }
      })();
      let minX=Infinity,minY=Infinity,maxX=-Infinity,maxY=-Infinity;
      nodes.forEach(n => { minX=Math.min(minX,n.x); maxX=Math.max(maxX,n.x); minY=Math.min(minY,n.y); maxY=Math.max(maxY,n.y); });
      const w=canvas.clientWidth||800, h=canvas.clientHeight||480;
      canvas.width=w; canvas.height=h;
      const pad=60, sx=(w-2*pad)/(maxX-minX||1), sy=(h-2*pad)/(maxY-minY||1), baseS=Math.min(sx,sy);
      const baseOx=(w-(maxX-minX)*baseS)/2, baseOy=(h-(maxY-minY)*baseS)/2;
      ctx.clearRect(0,0,w,h);
      ctx.strokeStyle='rgba(148,163,184,0.7)';
      ctx.lineWidth=1.2;
      edges.forEach(e => {
        const a=byId[e.source], b=byId[e.target];
        if (!a||!b) return;
        ctx.beginPath();
        ctx.moveTo((a.x-minX)*baseS+baseOx,(a.y-minY)*baseS+baseOy);
        ctx.lineTo((b.x-minX)*baseS+baseOx,(b.y-minY)*baseS+baseOy);
        ctx.stroke();
      });
      nodes.forEach(n => {
        const px=(n.x-minX)*baseS+baseOx, py=(n.y-minY)*baseS+baseOy;
        const r=5+Math.min(10,(degree[n.id]||0)*0.7);
        ctx.fillStyle=TYPE_COLORS[n.type]||'#6b7280';
        ctx.beginPath(); ctx.arc(px,py,r,0,2*Math.PI); ctx.fill();
        ctx.strokeStyle='#fff'; ctx.lineWidth=1.5; ctx.stroke();
      });
      // 记录节点命中区域，供点击弹窗
      const hits = nodes.map(n => {
        const px=(n.x-minX)*baseS+baseOx, py=(n.y-minY)*baseS+baseOy;
        return { uid: n.uid, x: px, y: py, r: 5+Math.min(10,(degree[n.id]||0)*0.7) };
      });
      this._topoHits = hits;
      canvas.onclick = (ev) => {
        const rect = canvas.getBoundingClientRect();
        const mx = ev.clientX - rect.left, my = ev.clientY - rect.top;
        // 从后往前找，命中最近的节点
        let hit = null, bestD = Infinity;
        hits.forEach(h => {
          const d = Math.hypot(mx - h.x, my - h.y);
          if (d <= h.r + 6 && d < bestD) { bestD = d; hit = h; }
        });
        if (hit) {
          const asset = this._nodes.find(n => n.uid === hit.uid);
          if (asset) this.openAssetDetail(asset);
        }
      };
    },
    seedSummary(seeds) { if (!seeds) return ''; return seeds.map(s => (s.type==='domain'?'':'') + s.value).join(', '); },
    closeTask() {
      if (this._logSource) { this._logSource.close(); this._logSource = null; }
      clearTimeout(this.taskPollTimer);
      this.activeTask = null;
    },
    loadTasks() { this.api('/api/tasks').then(d => { if (Array.isArray(d)) this.tasks = d; }); },
    viewTask(id) {
      // 关闭旧的 SSE/轮询
      if (this._logSource) { this._logSource.close(); this._logSource = null; }
      clearTimeout(this.taskPollTimer);
      this.api('/api/tasks/' + id).then(t => { this.activeTask = t; });

      // SSE 实时日志流（优先）；不支持 EventSource 时回退轮询
      const useSSE = typeof EventSource !== 'undefined';
      if (useSSE) {
        const es = new EventSource('/api/tasks/' + id + '/logs/stream');
        this._logSource = es;
        es.onmessage = (ev) => {
          try {
            const d = JSON.parse(ev.data);
            if (d.logs && Array.isArray(d.logs)) {
              if (!this.activeTask || this.activeTask.id !== id) return;
              this.activeTask._logs = (this.activeTask._logs || []).concat(d.logs);
              this.$nextTick(() => { const box = this.$refs.logbox; if (box && this.logFollow) box.scrollTop = box.scrollHeight; });
            }
          } catch(e) {}
        };
        es.addEventListener('done', () => {
          es.close(); this._logSource = null;
          if (this.activeTask && this.activeTask.id === id) this.loadTasks();
        });
        es.onerror = () => { es.close(); this._logSource = null; };
      } else {
        // 回退：1s 轮询
        const poll = () => {
          Promise.all([
            this.api('/api/tasks/' + id),
            this.api('/api/tasks/' + id + '/logs?tail=1000'),
          ]).then(([t, lg]) => {
            if (!this.activeTask || this.activeTask.id !== id) return;
            t._logs = lg.logs || [];
            this.activeTask = t;
            this.$nextTick(() => { const box = this.$refs.logbox; if (box && this.logFollow) box.scrollTop = box.scrollHeight; });
            if (t.status === 'running') this.taskPollTimer = setTimeout(poll, 1000);
          });
        };
        poll();
      }
    },
    stopTask(id) { this.api('/api/tasks/stop', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({id})}).then(() => this.loadTasks()); },
    rescanTask(id) {
      this.api('/api/tasks/rescan', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({id})}).then(d => {
        if (d.ok) { this.lastTaskId = d.id; this.loadTasks(); alert('已用相同种子重新发起扫描: ' + d.id); }
        else { alert('重扫失败: ' + (d.error||'')); }
      });
    },
    pollTasks() { this.loadTasks(); },
    startScan() {
      const seeds = [];
      (this.newScan.domains||'').split('\n').map(s=>s.trim()).filter(Boolean).forEach(v => seeds.push({type:'domain', value:v}));
      (this.newScan.urls||'').split('\n').map(s=>s.trim()).filter(Boolean).forEach(v => seeds.push({type:'url', value:v}));
      (this.newScan.ips||'').split('\n').map(s=>s.trim()).filter(Boolean).forEach(v => seeds.push({type:'ip', value:v}));
      if (!seeds.length) { alert('请至少填写一个种子'); return; }
      this.scanStarting = true;
      const config = { max_depth: this.newScan.maxDepth, strategy: this.newScan.strategy };
      if (this.newScan.concurrency) config.concurrency = { max_tasks: this.newScan.concurrency };
      this.api('/api/scan/start', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({seeds, config})}).then(d => {
        this.lastTaskId = d.id;
        this.scanStarting = false;
        this.go('tasks');
      });
    },
    loadConfig() { this.api('/api/config').then(d => { this.configText = d.yaml || ''; this.configSaved=false; }); },
    saveConfig() {
      const body = this.configText;
      this.api('/api/config', {method:'POST', headers:{'Content-Type':'text/yaml'}, body}).then(d => {
        if (d.ok) { this.configSaved = true; this.configError = null; } else { this.configError = d.error; }
      });
    },
    loadPlugins() { this.api('/api/plugins').then(d => { this.plugins = d.plugins || []; }); },
    togglePlugin(name) {
      this.api('/api/plugins/' + name + '/toggle', {method:'POST'}).then(d => {
        if (d.ok) { this.loadPlugins(); } else { alert('操作失败: ' + (d.error||'')); }
      });
    },
  },
  watch: {
    detailTab() { if (this.detailTab === 'topology') this.$nextTick(() => this.renderTopo()); },
  },
  mounted() {
    this.api('/api/health').then(d => {
      this.version = d.version || '';
      this.lang = d.lang || 'zh_CN';
      this.api('/api/i18n').then(i => { this.langDict = i.dict || {}; });
    });
    this.loadProjects();
    setInterval(() => { if (this.page==='tasks') this.loadTasks(); }, 3000);
  },
}).mount('#app');
</script>
</body>
</html>
"""
