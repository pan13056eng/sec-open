// 美股公告情报系统 —— 云端「增删监控公司」后端
//
// 为什么需要它：
//   简报页是 GitHub Pages 上的静态文件，浏览器里不能放任何密钥。
//   所以增删改由这个 Worker 代劳：它保管 GitHub token（写成 Worker secret，
//   不会出现在代码里），顺便代理 SEC 的公司查询（SEC 不给 CORS 头，浏览器直连不了）。
//
// 数据存哪：直接改仓库里的 config.yaml。
//   好处是「唯一数据源」——GitHub Actions 本来就按 config.yaml 抓，
//   这边改完，下次定时跑自动生效，定时任务的代码不用动。

const GH = 'https://api.github.com';
const SEC_TICKERS = 'https://www.sec.gov/files/company_tickers.json';

const CORS = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
  'Access-Control-Allow-Headers': 'Content-Type, X-Admin-Token',
  'Access-Control-Max-Age': '86400',
};

function json(data, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { ...CORS, 'Content-Type': 'application/json; charset=utf-8' },
  });
}

function fail(msg, status = 400) {
  return json({ ok: false, error: String(msg) }, status);
}

// ---------- config.yaml 的名单段读写 ----------

// 定位 watchlist: 段的范围。段内容 = 它后面所有缩进行，
// 直到出现下一个顶格写的键为止。
// 注意结尾只算到「最后一个有内容的行」——段后面的空行原样留着，
// 否则每次改写都会多攒一个空行，diff 里全是噪音。
function sectionRange(text) {
  const lines = text.split('\n');
  const start = lines.findIndex((l) => /^watchlist:\s*$/.test(l));
  if (start < 0) return null;
  let last = start;
  for (let i = start + 1; i < lines.length; i++) {
    const l = lines[i];
    if (l.trim() === '') continue; // 空行先跳过，看后面还有没有段内容
    if (/^\s/.test(l)) {
      last = i;
      continue;
    }
    break; // 顶格 = 下一个顶层键，段在这里结束
  }
  return { start, end: last + 1 };
}

function parseWatchlist(text) {
  const r = sectionRange(text);
  if (!r) throw new Error('config.yaml 里找不到「watchlist:」段');
  const items = [];
  let cur = null;
  for (const l of text.split('\n').slice(r.start, r.end)) {
    const t = l.match(/^\s*-\s*ticker:\s*(\S+)/);
    if (t) {
      cur = { ticker: t[1], cik: '', name: '' };
      items.push(cur);
      continue;
    }
    if (!cur) continue;
    const c = l.match(/^\s*cik:\s*"?([^"\s]+)"?/);
    if (c) {
      cur.cik = c[1].replace(/"/g, '');
      continue;
    }
    const n = l.match(/^\s*name:\s*(.+?)\s*$/);
    if (n) {
      cur.name = n[1].replace(/^["']|["']$/g, '');
    }
  }
  return items;
}

function renderWatchlist(items) {
  if (!items.length) return ['watchlist:', '  []'];
  const out = ['watchlist:'];
  for (const it of items) {
    out.push(`  - ticker: ${it.ticker}`);
    out.push(`    cik: "${(it.cik || '').replace(/"/g, '')}"`);
    out.push(`    name: ${it.name || ''}`);
  }
  return out;
}

function replaceWatchlist(text, items) {
  const r = sectionRange(text);
  if (!r) throw new Error('config.yaml 里找不到「watchlist:」段');
  const lines = text.split('\n');
  return [...lines.slice(0, r.start), ...renderWatchlist(items), ...lines.slice(r.end)].join('\n');
}

// ---------- GitHub 读写 ----------

async function readConfig(env) {
  const url = `${GH}/repos/${env.GITHUB_OWNER}/${env.GITHUB_REPO}/contents/config.yaml?ref=${env.GITHUB_BRANCH}`;
  const r = await fetch(url, {
    headers: {
      Authorization: `Bearer ${env.GITHUB_TOKEN}`,
      Accept: 'application/vnd.github+json',
      'User-Agent': 'sec-admin-worker',
    },
  });
  if (!r.ok) throw new Error(`读 config.yaml 失败（HTTP ${r.status}）`);
  const j = await r.json();
  const bin = atob((j.content || '').replace(/\s/g, ''));
  const bytes = Uint8Array.from(bin, (c) => c.charCodeAt(0));
  return { text: new TextDecoder().decode(bytes), sha: j.sha };
}

async function writeConfig(env, text, sha, message) {
  const bytes = new TextEncoder().encode(text);
  let raw = '';
  for (const b of bytes) raw += String.fromCharCode(b);
  const r = await fetch(
    `${GH}/repos/${env.GITHUB_OWNER}/${env.GITHUB_REPO}/contents/config.yaml`,
    {
      method: 'PUT',
      headers: {
        Authorization: `Bearer ${env.GITHUB_TOKEN}`,
        Accept: 'application/vnd.github+json',
        'User-Agent': 'sec-admin-worker',
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ message, content: btoa(raw), sha, branch: env.GITHUB_BRANCH }),
    }
  );
  if (!r.ok) throw new Error(`提交 config.yaml 失败（HTTP ${r.status}）`);
  return r.json();
}

// ---------- SEC 公司查询（代理，解决 CORS） ----------

let tickerCache = null;

async function lookupTicker(env, ticker) {
  if (!tickerCache) {
    const r = await fetch(SEC_TICKERS, {
      headers: { 'User-Agent': env.SEC_USER_AGENT, Accept: 'application/json' },
    });
    if (!r.ok) throw new Error(`SEC 查询失败（HTTP ${r.status}）`);
    tickerCache = await r.json();
  }
  const want = String(ticker).trim().toUpperCase();
  for (const v of Object.values(tickerCache)) {
    if (String(v.ticker).toUpperCase() === want) {
      return {
        ticker: v.ticker,
        cik: String(v.cik_str).padStart(10, '0'),
        name: v.title,
      };
    }
  }
  return null;
}

// ---------- 路由 ----------

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const path = url.pathname;
    const method = request.method;

    if (method === 'OPTIONS') return new Response(null, { status: 204, headers: CORS });

    try {
      // 当前名单（公开信息，不用鉴权）
      if (method === 'GET' && path === '/api/state') {
        const { text } = await readConfig(env);
        return json({ ok: true, items: parseWatchlist(text) });
      }

      // 查公司代码 → 官方全名 + CIK（不用鉴权）
      if (method === 'POST' && path === '/api/lookup') {
        const { ticker } = await request.json();
        if (!ticker) return fail('缺少 ticker');
        const hit = await lookupTicker(env, ticker);
        if (!hit) return fail(`SEC 里查不到代码「${ticker}」`, 404);
        return json({ ok: true, item: hit });
      }

      // 以下都要密钥
      if (request.headers.get('X-Admin-Token') !== env.ADMIN_TOKEN) {
        return fail('密钥不对', 401);
      }

      if (method === 'POST' && path === '/api/add') {
        const body = await request.json();
        if (!body.ticker || !body.cik) return fail('缺少 ticker 或 cik');
        const { text, sha } = await readConfig(env);
        const items = parseWatchlist(text);
        if (items.some((i) => i.ticker.toUpperCase() === body.ticker.toUpperCase())) {
          return fail(`${body.ticker} 已经在名单里了`);
        }
        items.push({ ticker: body.ticker.toUpperCase(), cik: body.cik, name: body.name || '' });
        await writeConfig(env, replaceWatchlist(text, items), sha, `监控名单 +${body.ticker}`);
        return json({ ok: true, items });
      }

      if (method === 'POST' && path === '/api/remove') {
        const body = await request.json();
        if (!body.ticker) return fail('缺少 ticker');
        const { text, sha } = await readConfig(env);
        const items = parseWatchlist(text);
        const left = items.filter((i) => i.ticker.toUpperCase() !== body.ticker.toUpperCase());
        if (left.length === items.length) return fail(`名单里没有 ${body.ticker}`, 404);
        await writeConfig(env, replaceWatchlist(text, left), sha, `监控名单 -${body.ticker}`);
        return json({ ok: true, items: left });
      }

      return fail('没有这个接口', 404);
    } catch (e) {
      return fail(e.message || e, 500);
    }
  },
};
