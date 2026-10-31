// 通过 CloudBase CLI 调用 jc-collector（HTTP 函数），把原始响应存成带时间戳的 JSON 文件（只新增，不覆盖）。
// 用法：node scripts/collect.mjs <outDir> [calc] [results:YYYY-MM-DD:YYYY-MM-DD] [results-range:开始:结束] [history:id1,id2,...]
// results 单次 ≤30 天；results-range 自动切成 30 天一段，用于历史回补。
// history-file:名单文件 按名单拉（跳过已完成）。history-pending:N 从仓库已存的赛果里挑出重点联赛中还没有变赔历史的场次（新到旧），拉 N 场，每次调用 5 场。
import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';

const FN = 'jc-collector';
const ENV = process.env.CLOUDBASE_ENV_ID;
const KEY = process.env.CLOUDBASE_API_KEY;
const [outDir, ...tasks] = process.argv.slice(2);
if (!outDir || !ENV || !KEY) { console.error('need outDir, CLOUDBASE_ENV_ID, CLOUDBASE_API_KEY'); process.exit(2); }

function invoke(payload) {
  const out = execFileSync('tcb', ['fn', 'invoke', FN, '-e', ENV, '-X', 'POST', '-H', 'Content-Type: application/json', '-d', JSON.stringify(payload), '--api-key', KEY], { encoding: 'utf8', maxBuffer: 64 * 1024 * 1024 });
  const i = out.search(/^\{/m); // CLI 先打印提示行，再打印格式化的 JSON
  if (i < 0) throw new Error('unexpected CLI output: ' + out.slice(0, 500));
  const rest = out.slice(i);
  const end = rest.search(/^\}\s*$/m); // 顶层对象以第 0 列的 } 结束，其后还有 CLI 提示
  if (end < 0) throw new Error('no closing brace in CLI output');
  return JSON.parse(rest.slice(0, end + 1));
}

const stamp = new Date().toISOString().replace(/[:.]/g, '-');
function save(kind, name, obj) {
  const dir = path.join(outDir, kind);
  fs.mkdirSync(dir, { recursive: true });
  const file = path.join(dir, `${name}__${stamp}.json`);
  if (fs.existsSync(file)) throw new Error('refuse to overwrite ' + file);
  fs.writeFileSync(file, JSON.stringify(obj));
  return file;
}
const good = (r) => r && r.status === 200 && r.data && r.data.errorCode === '0';
const summary = [];

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let halted = false; // 任何请求异常：立即停止整次运行，不重试

// 拉一个日期区间（≤30 天）的全部分页赛果
async function results(a, b) {
  let page = 1, pages = 1;
  do {
    const { result } = invoke({ action: 'results', begin: a, end: b, pageNo: page });
    if (!good(result)) { summary.push(`results ${a}..${b} p${page}: status ${result.status}, stopped`); halted = true; return; }
    pages = Number(result.data.value.pages) || 1;
    summary.push(`results ${a}..${b} p${page}/${pages}: ${result.data.value.matchResult.length} rows → ${save('results', `${a}_${b}_p${page}`, result)}`);
    page += 1;
    await sleep(2000);
  } while (page <= pages);
}

// 把长区间切成 30 天一段（含首尾）
function windows(a, b) {
  const day = 86400000, out = [];
  for (let t = Date.parse(a + 'T00:00:00Z'); t <= Date.parse(b + 'T00:00:00Z'); t += 30 * day) {
    const e = Math.min(t + 29 * day, Date.parse(b + 'T00:00:00Z'));
    out.push([new Date(t).toISOString().slice(0, 10), new Date(e).toISOString().slice(0, 10)]);
  }
  return out;
}


function history(ids) {
  let result, raw;
  try {
    raw = invoke({ action: 'history', matchIds: ids });
    ({ result } = raw);
  } catch (e) {
    summary.push(`history: invoke failed (${String(e.message || e).split('\n').find((l) => l.includes('✖')) || 'error'}), stopped`);
    halted = true;
    return;
  }
  if (!result || !Array.isArray(result.items)) {
    summary.push(`history: unexpected response ${JSON.stringify(raw).slice(0, 300)}, stopped`);
    halted = true;
    return;
  }
  const ok = (result.items || []).filter((it) => good(it));
  for (const it of result.items || []) save('history', String(it.matchId), it);
  summary.push(`history: ${ok.length}/${ids.length} ok${result.stoppedEarly ? ' (stopped early)' : ''}`);
  if (result.stoppedEarly || ok.length < ids.length) halted = true;
}

// 五大联赛 + 欧冠、欧罗巴、欧协联（体彩 leagueId）
const HISTORY_BATCH = 5;
const FOCUS_LEAGUES = new Set([25, 62, 40, 37, 32, 69, 70, 1033103]);
const ARCHIVE = 'data/raw/sporttery';
function doneHistory() {
  const done = new Set();
  const hdir = path.join(ARCHIVE, 'history');
  if (fs.existsSync(hdir)) {
    for (const f of fs.readdirSync(hdir)) {
      const it = JSON.parse(fs.readFileSync(path.join(hdir, f), 'utf8'));
      // 正常数据，或 200 但接口明确返回空（延期/取消场次没有变赔记录），都算已处理
      if (good(it) || (it.status === 200 && it.data && it.data.success === false && it.data.errorCode === '')) done.add(Number(it.matchId));
    }
  }
  return done;
}

function pendingHistory(n) {
  const done = doneHistory();
  const matches = new Map();
  const rdir = path.join(ARCHIVE, 'results');
  for (const f of fs.existsSync(rdir) ? fs.readdirSync(rdir) : []) {
    const r = JSON.parse(fs.readFileSync(path.join(rdir, f), 'utf8'));
    for (const m of r.data?.value?.matchResult || []) {
      const played = /^\d+:\d+$/.test(m.sectionsNo999 || ''); // 没有比分的（延期/取消）没有变赔记录，跳过
      if (played && FOCUS_LEAGUES.has(Number(m.leagueId)) && !done.has(Number(m.matchId))) matches.set(Number(m.matchId), m.matchDate);
    }
  }
  return [...matches].sort((x, y) => (x[1] < y[1] ? 1 : x[1] > y[1] ? -1 : y[0] - x[0])).slice(0, n).map(([id]) => id);
}

for (const t of tasks) {
  if (halted) break;
  const [kind, a, b] = t.split(':');
  if (kind === 'calc') {
    const { result } = invoke({ action: 'calc', pools: 'had,hhad' });
    const n = good(result) ? result.data.value.matchInfoList.flatMap((d) => d.subMatchList).length : 0;
    summary.push(`calc: status ${result.status}, ${n} matches → ${save('calc', 'calc', result)}`);
    if (!good(result)) halted = true;
  } else if (kind === 'results') {
    await results(a, b);
  } else if (kind === 'results-range') {
    for (const [x, y] of windows(a, b)) { if (halted) break; await results(x, y); }
  } else if (kind === 'history') {
    history((a || '').split(',').filter(Boolean));
  } else if (kind === 'history-file') {
    // 按名单文件拉（如 data/backfill/shard_lighthouse.txt），跳过已完成的场次
    const done = doneHistory();
    const ids = fs.readFileSync(a, 'utf8').split(/\s+/).filter(Boolean).map(Number).filter((id) => !done.has(id));
    summary.push(`history-file ${a}: ${ids.length} to fetch`);
    for (let i = 0; i < ids.length && !halted; i += HISTORY_BATCH) {
      history(ids.slice(i, i + HISTORY_BATCH));
      if (i + HISTORY_BATCH < ids.length) await sleep(1500);
    }
  } else if (kind === 'history-pending') {
    const ids = pendingHistory(Number(a) || 200);
    summary.push(`history-pending: ${ids.length} selected`);
    // 每次调用 5 场（函数内每场间隔 1.5 秒，约 9 秒），避免 CLI 等待 HTTP 函数超时
    for (let i = 0; i < ids.length && !halted; i += HISTORY_BATCH) {
      history(ids.slice(i, i + HISTORY_BATCH));
      if (i + HISTORY_BATCH < ids.length) await sleep(1500);
    }
  } else {
    console.error('unknown task ' + t); process.exitCode = 2;
  }
}
if (halted) { summary.push('HALTED: 请求异常，已停止后续任务'); process.exitCode = 1; }
console.log(summary.join('\n'));
if (process.env.GITHUB_STEP_SUMMARY) fs.appendFileSync(process.env.GITHUB_STEP_SUMMARY, '### 采集结果\n\n```\n' + summary.join('\n') + '\n```\n');
