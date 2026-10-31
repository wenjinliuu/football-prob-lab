// 竞彩数据采集 HTTP 云函数（部署在腾讯云开发，国内出口）。
// 只做"代取 + 原样返回"，不做解析；调用方（GitHub Actions）负责存档与处理。
// 访问规范：普通浏览器请求头；单次调用请求数有上限；遇到非 200 立即停止，不重试、不绕过。
const http = require('http');
const https = require('https');

const BASE = 'https://webapi.sporttery.cn/gateway';
const UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0 Safari/537.36';
const MAX_HISTORY_PER_CALL = 20;
const HISTORY_GAP_MS = 1500;

function fetchJson(url, referer) {
  return new Promise((resolve) => {
    const t0 = Date.now();
    const req = https.get(url, {
      headers: { 'User-Agent': UA, Referer: referer, Accept: 'application/json, text/plain, */*', 'Accept-Language': 'zh-CN,zh;q=0.9' },
      timeout: 15000,
    }, (res) => {
      let body = '';
      res.setEncoding('utf8');
      res.on('data', (c) => { body += c; });
      res.on('end', () => {
        let data = null;
        try { data = JSON.parse(body); } catch (_) { /* 非 JSON（可能是拦截页） */ }
        resolve({ url, status: res.statusCode, ms: Date.now() - t0, fetchedAt: new Date().toISOString(), data, snippet: data ? undefined : body.slice(0, 300) });
      });
    });
    req.on('timeout', () => { req.destroy(); resolve({ url, status: 'timeout', ms: Date.now() - t0, fetchedAt: new Date().toISOString(), data: null }); });
    req.on('error', (e) => resolve({ url, status: 'error', error: String(e), ms: Date.now() - t0, fetchedAt: new Date().toISOString(), data: null }));
  });
}

const ok = (r) => r.status === 200 && r.data && r.data.errorCode === '0';
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const isDate = (s) => /^\d{4}-\d{2}-\d{2}$/.test(String(s || ''));

async function handle(body) {
  const action = body.action;
  if (action === 'calc') {
    const pools = /^[a-z,]+$/.test(body.pools || '') ? body.pools : 'had,hhad';
    return fetchJson(`${BASE}/jc/football/getMatchCalculatorV1.qry?poolCode=${pools}&channel=c`, 'https://www.sporttery.cn/');
  }
  if (action === 'results') {
    if (!isDate(body.begin) || !isDate(body.end)) return { error: 'begin/end must be YYYY-MM-DD' };
    const pageNo = Math.max(1, Number(body.pageNo) || 1);
    const qs = new URLSearchParams({ matchBeginDate: body.begin, matchEndDate: body.end, leagueId: String(body.leagueId || ''), pageSize: '100', pageNo: String(pageNo), isFix: '0', matchPage: '1', pcOrWap: '1' });
    return fetchJson(`${BASE}/uniform/football/getUniformMatchResultV1.qry?${qs}`, 'https://www.lottery.gov.cn/jc/zqsgkj/');
  }
  if (action === 'history') {
    const ids = (Array.isArray(body.matchIds) ? body.matchIds : []).map(Number).filter((n) => Number.isInteger(n) && n > 0).slice(0, MAX_HISTORY_PER_CALL);
    const out = [];
    for (const id of ids) {
      const r = await fetchJson(`${BASE}/uniform/football/getOddsHistoryV1.qry?matchId=${id}`, 'https://www.sporttery.cn/');
      out.push({ matchId: id, ...r });
      if (!ok(r)) break; // 任何异常立即停止
      await sleep(HISTORY_GAP_MS);
    }
    return { items: out, stoppedEarly: out.length < ids.length };
  }
  return { error: 'unknown action; use calc | results | history' };
}

http.createServer((req, res) => {
  let raw = '';
  req.on('data', (c) => { raw += c; if (raw.length > 10000) req.destroy(); });
  req.on('end', async () => {
    let body = {};
    try { body = raw ? JSON.parse(raw) : {}; } catch (_) { body = {}; }
    let result;
    try { result = await handle(body); } catch (e) { result = { error: String(e) }; }
    res.writeHead(200, { 'Content-Type': 'application/json; charset=utf-8' });
    res.end(JSON.stringify({ action: body.action || null, result }));
  });
}).listen(Number(process.env.PORT || 9000), '0.0.0.0', () => console.log('jc-collector listening'));
