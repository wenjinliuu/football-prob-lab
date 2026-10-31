// 通过 CloudBase CLI 调用 jc-collector（HTTP 函数），把原始响应存成带时间戳的 JSON 文件（只新增，不覆盖）。
// 用法：node scripts/collect.mjs <outDir> [calc] [results:YYYY-MM-DD:YYYY-MM-DD] [history:id1,id2,...]
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
  const i = out.indexOf('{"action"');
  if (i < 0) throw new Error('unexpected CLI output: ' + out.slice(0, 500));
  return JSON.parse(out.slice(i).trim());
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

for (const t of tasks) {
  const [kind, a, b] = t.split(':');
  if (kind === 'calc') {
    const { result } = invoke({ action: 'calc', pools: 'had,hhad' });
    const n = good(result) ? result.data.value.matchInfoList.flatMap((d) => d.subMatchList).length : 0;
    summary.push(`calc: status ${result.status}, ${n} matches → ${save('calc', 'calc', result)}`);
    if (!good(result)) process.exitCode = 1;
  } else if (kind === 'results') {
    let page = 1, pages = 1;
    do {
      const { result } = invoke({ action: 'results', begin: a, end: b, pageNo: page });
      if (!good(result)) { summary.push(`results ${a}..${b} p${page}: status ${result.status}, stopped`); process.exitCode = 1; break; }
      pages = Number(result.data.value.pages) || 1;
      summary.push(`results ${a}..${b} p${page}/${pages}: ${result.data.value.matchResult.length} rows → ${save('results', `${a}_${b}_p${page}`, result)}`);
      page += 1;
      if (page <= pages) await new Promise((r) => setTimeout(r, 2000));
    } while (page <= pages);
  } else if (kind === 'history') {
    const ids = (a || '').split(',').filter(Boolean);
    const { result } = invoke({ action: 'history', matchIds: ids });
    for (const it of result.items || []) save('history', String(it.matchId), it);
    summary.push(`history: ${(result.items || []).length}/${ids.length} fetched${result.stoppedEarly ? ' (stopped early)' : ''}`);
    if (result.stoppedEarly) process.exitCode = 1;
  } else {
    console.error('unknown task ' + t); process.exitCode = 2;
  }
}
console.log(summary.join('\n'));
if (process.env.GITHUB_STEP_SUMMARY) fs.appendFileSync(process.env.GITHUB_STEP_SUMMARY, '### 采集结果\n\n```\n' + summary.join('\n') + '\n```\n');
