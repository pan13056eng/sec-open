"""可视化增删公司（本地管理页）

启动：  ./.venv/bin/python manage.py
然后浏览器会自动打开 http://127.0.0.1:8765

只做三件事：
  1. 列出当前监控的公司 + 每家抓到几条
  2. 添加：输入代码 → 自动查 SEC 官方对照表 → 显示官方公司名让你确认 → 写入 config.yaml
  3. 删除：一点就删，同时清掉它的历史记录
改的是 config.yaml（原文件自动备份到 config.yaml.bak），不碰其它配置。
"""
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

import yaml  # noqa: E402

import db  # noqa: E402
from fetch_sec import SecClient, pick_filings  # noqa: E402

PORT = 8765
TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
CACHE = os.path.join(BASE, "data", "company_tickers.json")
CACHE_TTL = 24 * 3600
RUN_LOG = os.path.join(BASE, "logs", "manual.log")

_runs = {}  # 记录手动运行状态


# ---------- 配置读写 ----------
def load_cfg():
    with open(os.path.join(BASE, "config.yaml"), encoding="utf-8") as f:
        return yaml.safe_load(f)


def save_watchlist(items):
    """只替换 config.yaml 里的 watchlist 区块，其余内容和注释原样保留"""
    path = os.path.join(BASE, "config.yaml")
    text = open(path, encoding="utf-8").read()
    lines = text.split("\n")

    start = None
    for i, ln in enumerate(lines):
        if re.match(r"^watchlist:\s*(#.*)?$", ln):
            start = i
            break
    if start is None:
        raise RuntimeError("config.yaml 里找不到 watchlist 段")

    end = len(lines)
    for i in range(start + 1, len(lines)):
        if re.match(r"^[A-Za-z_][\w-]*:\s*(#.*)?$", lines[i]):
            end = i
            break

    block = ["watchlist:"]
    if not items:
        block.append("  []   # 名单为空，运行时不会抓取任何公司")
    for it in items:
        block.append(f"  - ticker: {it['ticker']}")
        block.append(f'    cik: "{str(it["cik"]).zfill(10)}"')
        block.append(f'    name: {it.get("name", "") or it["ticker"]}')

    shutil.copy(path, path + ".bak")
    open(path, "w", encoding="utf-8").write("\n".join(lines[:start] + block + lines[end:]))


# ---------- SEC 对照表 ----------
def ticker_table(force=False):
    """SEC 官方 ticker → (CIK, 公司全名)，本地缓存 24 小时"""
    if not force and os.path.exists(CACHE):
        if time.time() - os.path.getmtime(CACHE) < CACHE_TTL:
            with open(CACHE, encoding="utf-8") as f:
                return json.load(f)
    cfg = load_cfg()
    import urllib.request

    req = urllib.request.Request(
        TICKERS_URL, headers={"User-Agent": cfg["sec"]["user_agent"]}
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        raw = json.load(r)
    table = {
        v["ticker"].upper(): {
            "ticker": v["ticker"],
            "cik": str(v["cik_str"]).zfill(10),
            "title": v["title"],
        }
        for v in raw.values()
    }
    os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    with open(CACHE, "w", encoding="utf-8") as f:
        json.dump(table, f, ensure_ascii=False)
    return table


def norm_ticker(s):
    """把用户各种写法洗成 SEC 风格：
    'NASDAQ: META' / 'nyse:brk.b' / 'BRK B' / ' brk-b '  →  META / BRK-B
    """
    s = (s or "").strip().upper()
    s = re.sub(
        r"^(NASDAQ|NYSE|NYSEARCA|ARCA|AMEX|NYSEMKT|OTC|OTCMKTS|BATS|LSE|HKEX|TSE)\s*:\s*",
        "",
        s,
    )
    s = re.sub(r"\s+", "-", s)  # "BRK B" → "BRK-B"（SEC 用横杠表示 A/B 股）
    return s


def lookup(ticker):
    """返回候选（先精确，再试试 . ↔ - 互换，最后模糊匹配）"""
    t = norm_ticker(ticker)
    if not t:
        return []
    table = ticker_table()
    hit = table.get(t)
    if hit:
        return [hit]
    alt = t.replace(".", "-") if "." in t else t.replace("-", ".")
    hit = table.get(alt)
    if hit:
        return [hit]
    # 模糊：代码前缀匹配（BRK → BRK-A / BRK-B）
    cands = [v for k, v in table.items() if k.startswith(t)]
    return sorted(cands, key=lambda v: v["ticker"])[:8]


def preview(cik, name_hint=""):
    """真打一次 SEC，确认这个 CIK 是谁、最近有没有符合过滤条件的公告"""
    cfg = load_cfg()
    cik = str(cik).zfill(10)
    client = SecClient(cfg["sec"]["user_agent"], 0.15)
    sub = client.submissions(cik)
    lookback = cfg["sec"].get("lookback_days", 30)
    cands = pick_filings(
        sub, cfg["filters"]["form_types"], cfg["filters"].get("items_8k"), lookback
    )
    # 最近申报（不限类型）：用来证明这个 CIK 是活的、确实对应这家公司
    rd = (sub.get("filings") or {}).get("recent") or {}
    allrec = [
        {"form": f, "date": d}
        for f, d in zip(rd.get("form", [])[:5], rd.get("filingDate", [])[:5])
    ]
    return {
        "cik": cik,
        "sec_name": sub.get("name", ""),
        "sic": sub.get("sicDescription", ""),
        "exchanges": sub.get("exchanges") or [],
        "all_recent": allrec,
        "recent": [
            {"form": c["form"], "date": c["filing_date"], "items": c["items"]}
            for c in cands[:6]
        ],
        "in_window": len(cands),
        "lookback": lookback,
    }


# ---------- 页面 ----------
PAGE = r"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>美股公告情报 · 公司管理</title>
<style>
:root{--bg:#0B0E13;--panel:#141A22;--panel2:#11171F;--line:#232B36;--fg:#E6EDF3;
--fg2:#B6C2CF;--mut:#8B98A5;--dim:#6B7787;--link:#58A6FF;--ok:#3FB950;--warn:#D29922;--bad:#F85149}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.6 -apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif}
.wrap{max-width:980px;margin:0 auto;padding:28px 20px 60px}
h1{font-size:20px;margin:0 0 4px}
h2{font-size:15px;margin:26px 0 10px;color:var(--fg2);border-left:3px solid var(--link);padding-left:9px}
.sub{color:var(--mut);font-size:13px;margin-bottom:18px}
.panel{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:16px}
table{width:100%;border-collapse:collapse}
th,td{padding:9px 10px;text-align:left;border-bottom:1px solid var(--line);font-size:13px}
th{color:var(--mut);font-weight:500;font-size:12px}
tr:last-child td{border-bottom:none}
.code{font-weight:600;letter-spacing:.3px}
.mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;color:var(--fg2);font-size:12px}
.mut{color:var(--mut)}.dim{color:var(--dim)}
.zero{color:var(--dim)}
input[type=text]{background:var(--panel2);border:1px solid var(--line);color:var(--fg);
border-radius:7px;padding:9px 11px;font-size:14px;width:230px;outline:none}
input[type=text]:focus{border-color:var(--link)}
button{background:#1F6FEB;color:#fff;border:none;border-radius:7px;padding:9px 15px;
font-size:13px;cursor:pointer;font-weight:500}
button:hover{background:#388BFD}
button.ghost{background:transparent;border:1px solid var(--line);color:var(--fg2)}
button.ghost:hover{border-color:var(--link);color:var(--link)}
button.del{background:transparent;border:1px solid #4a2b2b;color:#F0857D;padding:5px 11px;font-size:12px}
button.del:hover{background:#3a1f1f;border-color:var(--bad)}
.row{display:flex;gap:9px;align-items:center;flex-wrap:wrap}
.hint{background:var(--panel2);border:1px solid var(--line);border-left:3px solid var(--warn);
border-radius:7px;padding:11px 13px;color:var(--fg2);font-size:13px;margin-top:12px}
.hint b{color:var(--fg)}
.card{background:var(--panel2);border:1px solid var(--line);border-radius:9px;padding:14px;margin-top:13px}
.card .nm{font-size:15px;font-weight:600;margin-bottom:3px}
.ok{color:var(--ok)}.bad{color:var(--bad)}.warn{color:var(--warn)}
ul{margin:8px 0 0;padding-left:18px;color:var(--fg2);font-size:13px}
.tag{display:inline-block;background:#1b2531;border:1px solid var(--line);border-radius:5px;
padding:1px 7px;font-size:11px;color:var(--mut);margin-left:6px}
#msg{margin-top:12px;font-size:13px;min-height:20px}
#log{margin:12px 0 0;padding:11px 13px;background:var(--panel2);border:1px solid var(--line);
border-radius:7px;max-height:260px;overflow:auto;white-space:pre-wrap;
font-size:12px;color:var(--fg2);min-height:42px}
.sp{color:var(--dim)}
</style></head><body><div class="wrap">

<h1 class="solo">监控公司管理</h1>
<div class="sub solo">改的是 <span class="mono">config.yaml</span>（每次保存自动备份为 <span class="mono">config.yaml.bak</span>）· 增删后下次运行生效</div>

<h2>当前监控 <span id="cnt" class="mut"></span></h2>
<div class="panel"><table id="tb"></table></div>
<div id="extra"></div>

<h2>添加公司</h2>
<div class="panel">
  <div class="row">
    <input type="text" id="q" placeholder="股票代码，如 META / BRK.B / TSM" autofocus>
    <button onclick="doLookup()">查找</button>
    <span class="mut" style="font-size:12px">回车也行</span>
  </div>
  <div class="hint">
    <b>填什么？</b>直接填股票代码就行 —— <span class="mono">NASDAQ: META</span>、<span class="mono">nyse: brk.b</span>
    这种带交易所前缀的写法会自动剥掉，大小写、空格、点/横杠也都会自动纠正。<br>
    <b>为什么填代码还要确认？</b>系统抓取时其实<b>只用 10 位 CIK</b> 定位公司，代码只是给你看的标签。
    所以只要 CIK 对就一定抓得到；页面会把 SEC 返回的<b>官方公司全名</b>显示出来，你确认名字对得上就是填对了。<br>
    <b>BRK.B 这类：</b>SEC 官方写法是 <span class="mono">BRK-B</span>（A/B 股同一个 CIK 1067983），输入 BRK.B 会自动纠正。
  </div>
  <div id="found"></div>
</div>

<h2>运行</h2>
<div class="panel">
  <div class="row">
    <button onclick="runNow()">立即跑一次抓取</button>
    <button class="ghost" onclick="stopRun()">停止</button>
    <button class="ghost" onclick="openOut()">打开简报目录</button>
    <button class="ghost" onclick="refreshTable()">刷新列表</button>
    <span style="flex:1"></span>
    <button class="del" onclick="quitSrv()">停止服务</button>
    <span id="runtip" class="mut" style="font-size:12px"></span>
  </div>
  <pre id="log" class="mono">（暂无运行记录）</pre>
</div>

<div class="sub solo" style="margin-top:22px">
  这个页面本身是个本地小服务（只监听 127.0.0.1）。关掉启动它的终端窗口就会停止，
  下次双击「美股公告管理」或运行 <span class="mono">./.venv/bin/python manage.py</span> 即可重新打开。
</div>

<div id="msg"></div>
</div>
<script>
const $=id=>document.getElementById(id);
const esc=s=>String(s==null?'':s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
let PENDING=null;

function msg(t,cls){$('msg').innerHTML=t?`<span class="${cls||''}">${t}</span>`:'';}

$('q').addEventListener('keydown',e=>{if(e.key==='Enter')doLookup();});

async function api(u,body){
  const r=await fetch(u,body?{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify(body)}:{});
  return await r.json();
}

async function refreshTable(){
  const s=await api('/api/state');
  $('cnt').textContent=`· ${s.watchlist.length} 家`;
  let h=`<tr><th>代码</th><th>名称</th><th>CIK</th><th>已抓</th><th>最近一条</th><th></th></tr>`;
  if(!s.watchlist.length) h+=`<tr><td colspan="6" class="dim">名单为空，运行时不会抓任何公司</td></tr>`;
  for(const w of s.watchlist){
    h+=`<tr><td class="code">${esc(w.ticker)}</td><td>${esc(w.name)}</td>
      <td class="mono">${esc(w.cik)}</td>
      <td class="${w.count?'':'zero'}">${w.count}</td>
      <td class="mono">${w.last?esc(w.last):'<span class="dim">—</span>'}</td>
      <td><button class="del" onclick="del('${esc(w.ticker)}')">删除</button></td></tr>`;
  }
  $('tb').innerHTML=h;
  $('extra').innerHTML=s.extra&&s.extra.length
    ? `<div class="hint"><b>库里有 ${s.extra.length} 家已不在名单：</b><span class="mono">${esc(s.extra.join('、'))}</span>
       它们的记录还没清掉（不影响网页显示，网页只显示最近 ${s.lookback} 天且已概括的）。
       下次运行会自动清理。</div>`:'';
}

async function doLookup(){
  msg('查询中…');$('found').innerHTML='';PENDING=null;
  const r=await api('/api/lookup',{ticker:$('q').value});
  if(!r.ok){msg(r.error||'查找失败','bad');return;}
  if(!r.cands.length){
    $('found').innerHTML=`<div class="card"><div class="nm warn">SEC 官方对照表里没找到「${esc(r.input)}」</div>
      <div class="mut" style="font-size:13px">可能是没在美股上市、代码太新，或拼写不同。可以手动填 10 位 CIK：</div>
      <div class="row" style="margin-top:10px"><input type="text" id="mcik" placeholder="10 位 CIK，如 0001326801">
      <button onclick="byCik()">用这个 CIK 验证</button></div>
      <div class="mut" style="font-size:12px;margin-top:8px">查 CIK：
      <a href="https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany" target="_blank" style="color:var(--link)">SEC 公司搜索</a></div></div>`;
    msg('');return;
  }
  msg('');
  for(const c of r.cands) showCard(c);
}

async function showCard(c){
  const p=await api('/api/preview',{cik:c.cik});
  if(!p.ok){
    $('found').innerHTML+=`<div class="card"><div class="nm bad">${esc(c.ticker)} 验证失败</div>
      <div class="mut">${esc(p.error)}</div></div>`;return;
  }
  const li=p.recent.length?`<ul>${p.recent.map(x=>
    `<li><span class="mono">${esc(x.form)}</span> · ${esc(x.date)}${x.items?' <span class="tag">'+esc(x.items)+'</span>':''}</li>`).join('')}</ul>`
    :`<div class="mut" style="margin-top:6px">最近 ${p.lookback} 天没有符合过滤条件的公告</div>`;
  const all=p.all_recent&&p.all_recent.length?`<div style="margin-top:8px" class="mut">
    该 CIK 最近申报（不限类型，证明公司找对了）：<ul>${p.all_recent.map(x=>
    `<li><span class="mono">${esc(x.form)}</span> · ${esc(x.date)}</li>`).join('')}</ul></div>`:'';
  const cls=p.in_window?'ok':'warn';
  $('found').innerHTML+=`<div class="card">
    <div class="nm">${esc(p.sec_name)} <span class="tag">${esc(c.ticker)}</span></div>
    <div class="mono dim">CIK ${esc(p.cik)}${p.sic?' · '+esc(p.sic):''}${p.exchanges&&p.exchanges.length?' · '+esc(p.exchanges.join(',')):''}</div>
    <div style="margin-top:8px" class="${cls}">最近 ${p.lookback} 天命中 ${p.in_window} 条${p.in_window?'':'（0 条不代表填错，可能只是这段时间没发公告）'}</div>
    ${li}${all}
    <div class="row" style="margin-top:12px">
      <input type="text" id="nm_${esc(c.ticker)}" placeholder="中文备注名（可选）" value="${esc(c.ticker)}">
      <button onclick="add('${esc(c.ticker)}','${esc(p.cik)}')">加入名单</button></div></div>`;
}

async function byCik(){
  const cik=($('mcik').value||'').replace(/\D/g,'');
  if(cik.length===0){msg('请填 CIK','bad');return;}
  msg('验证中…');
  const p=await api('/api/preview',{cik:cik});
  if(!p.ok){msg(p.error||'验证失败','bad');return;}
  msg('');
  showCard({ticker:p.sec_name.slice(0,12).toUpperCase().replace(/[^A-Z0-9]/g,''),cik:p.cik});
}

async function add(ticker,cik){
  const nm=$(`nm_${ticker}`)?$(`nm_${ticker}`).value:ticker;
  const r=await api('/api/add',{ticker:ticker,cik:cik,name:nm});
  if(!r.ok){msg(r.error||'添加失败','bad');return;}
  msg(`已添加 ${ticker} ✓ 下次运行生效（立即跑一次可马上看到）`,'ok');
  $('q').value='';$('found').innerHTML='';refreshTable();
}

async function del(ticker){
  if(!confirm(`确定删除 ${ticker}？它在库里的历史记录会一并清掉。`))return;
  const r=await api('/api/remove',{ticker:ticker});
  if(!r.ok){msg(r.error||'删除失败','bad');return;}
  msg(`已删除 ${ticker}，清掉 ${r.removed} 条历史记录`,'ok');
  refreshTable();
}

let logTimer=null;
async function loadLog(){
  const r=await api('/api/run_log');
  $('log').textContent=r.log||'（暂无运行记录）';
  $('log').scrollTop=$('log').scrollHeight;
  return r.running;
}
function pollLog(){
  clearInterval(logTimer);
  logTimer=setInterval(async()=>{
    const running=await loadLog();
    $('runtip').textContent=running?'抓取中…（日志实时刷新）':'空闲';
    if(!running){clearInterval(logTimer);refreshTable();}
  },1500);
}
async function runNow(){
  const r=await api('/api/run',{});
  if(!r.ok){$('runtip').textContent=r.error||'启动失败';return;}
  $('runtip').textContent='抓取中…（日志实时刷新）';
  pollLog();
}
async function stopRun(){
  const r=await api('/api/stop',{});
  if(!r.ok){$('runtip').textContent=r.error;return;}
  clearInterval(logTimer);$('runtip').textContent='已停止';
}
async function openOut(){await api('/api/open_out',{});}
async function quitSrv(){
  if(!confirm('停止管理页服务？正在跑的抓取也会一起停。\n下次双击「美股公告管理」即可重新打开。'))return;
  await api('/api/quit',{});
  document.body.innerHTML='<div class="wrap"><h1>服务已停止</h1>'
    +'<div class="sub">双击桌面的「美股公告管理」App 可重新打开。</div></div>';
}

refreshTable();loadLog();

// 被简报页以 iframe 嵌入时：藏掉标题等"独立页面"元素，视觉上融进简报页
if(window.self!==window.top){
  document.querySelectorAll('.solo').forEach(function(e){e.style.display='none';});
  var w=document.querySelector('.wrap');
  if(w) w.style.padding='10px 0 24px';
}

// 被简报页以 iframe 嵌入时，把自己的高度告诉父页面，避免滚动条套滚动条
function tellHeight(){try{parent.postMessage({mgrH:document.body.scrollHeight},'*');}catch(e){}}
new ResizeObserver(tellHeight).observe(document.body);
tellHeight();
</script></body></html>"""


# ---------- 服务 ----------
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, obj, code=200):
        b = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        if self.path.startswith("/api/state"):
            self._json(state())
        elif self.path.startswith("/api/ping"):
            # JSONP 探针：简报页（file:// 静态页）用它判断管理页有没有开着，
            # script 标签不受同源策略限制，比 fetch 更稳
            m = re.search(r"cb=([\w.$]+)", self.path)
            cb = m.group(1) if m else "cb"
            b = f'{cb}({{"ok":1,"port":{PORT}}});'.encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/javascript; charset=utf-8")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)
        elif self.path == "/api/run_log":
            self._json({"log": tail_log(), "running": is_running()})
        elif self.path in ("/", "/index.html"):
            b = PAGE.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)
        else:
            self._json({"ok": False, "error": "not found"}, 404)

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        try:
            req = json.loads(self.rfile.read(n) or b"{}")
        except Exception:  # noqa: BLE001
            req = {}
        p = self.path
        try:
            if p == "/api/lookup":
                t = norm_ticker(req.get("ticker", ""))
                self._json({"ok": True, "input": t, "cands": lookup(t)})
            elif p == "/api/preview":
                self._json({"ok": True, **preview(req.get("cik", ""))})
            elif p == "/api/add":
                self._json(add_one(req))
            elif p == "/api/remove":
                self._json(remove_one(req.get("ticker", "")))
            elif p == "/api/run":
                self._json(run_now())
            elif p == "/api/stop":
                self._json(stop_now())
            elif p == "/api/quit":
                self._json(quit_now())
            elif p == "/api/open_out":
                cfg = load_cfg()
                out = os.path.join(BASE, (cfg.get("output") or {}).get("dir", "out"))
                subprocess.Popen(["open", out])
                self._json({"ok": True})
            else:
                self._json({"ok": False, "error": "not found"}, 404)
        except Exception as e:  # noqa: BLE001
            self._json({"ok": False, "error": f"{type(e).__name__}: {e}"})


def state():
    cfg = load_cfg()
    wl = cfg.get("watchlist") or []
    rows = db.recent(3000)
    agg = {}
    for r in rows:
        a = agg.setdefault(r["ticker"], {"count": 0, "last": ""})
        a["count"] += 1
        if (r.get("filing_date") or "") > a["last"]:
            a["last"] = r.get("filing_date", "")
    out = []
    for w in wl:
        a = agg.get(w["ticker"], {"count": 0, "last": ""})
        out.append(
            {
                "ticker": w["ticker"],
                "name": w.get("name", ""),
                "cik": str(w["cik"]).zfill(10),
                "count": a["count"],
                "last": a["last"],
            }
        )
    keep = {w["ticker"].upper() for w in wl}
    extra = sorted({t for t in agg if t and t.upper() not in keep})
    return {
        "ok": True,
        "watchlist": out,
        "extra": extra,
        "lookback": cfg["sec"].get("lookback_days", 30),
    }


def add_one(req):
    cfg = load_cfg()
    wl = cfg.get("watchlist") or []
    tk = norm_ticker(req.get("ticker", ""))
    cik = str(req.get("cik", "")).zfill(10)
    if not tk or not cik or cik == "0000000000":
        return {"ok": False, "error": "缺少代码或 CIK"}
    if any(w["ticker"].upper() == tk for w in wl):
        return {"ok": False, "error": f"{tk} 已在名单里"}
    wl.append({"ticker": tk, "cik": cik, "name": req.get("name") or tk})
    save_watchlist(wl)
    db.prune_tickers([w["ticker"] for w in wl])
    return {"ok": True, "watchlist": wl}


def remove_one(ticker):
    cfg = load_cfg()
    wl = cfg.get("watchlist") or []
    tk = norm_ticker(ticker)
    left = [w for w in wl if w["ticker"].upper() != tk]
    if len(left) == len(wl):
        return {"ok": False, "error": f"名单里没有 {tk}"}
    save_watchlist(left)
    removed = db.prune_tickers([w["ticker"] for w in left])
    return {"ok": True, "removed": removed}


def is_running():
    p = _runs.get("proc")
    return bool(p and p.poll() is None)


def stop_now():
    p = _runs.get("proc")
    if not is_running():
        return {"ok": False, "error": "当前没有在跑"}
    p.terminate()
    return {"ok": True}


def quit_now():
    """从页面上关掉这个管理服务本身"""
    if is_running():
        stop_now()
    srv = _runs.get("srv")
    if not srv:
        return {"ok": False, "error": "服务未在运行"}
    threading.Thread(target=srv.shutdown, daemon=True).start()
    return {"ok": True}


def run_now():
    if is_running():
        return {"ok": False, "error": "上一次还在跑"}
    os.makedirs(os.path.dirname(RUN_LOG), exist_ok=True)
    f = open(RUN_LOG, "a", encoding="utf-8")
    f.write(f"\n===== {datetime.now():%Y-%m-%d %H:%M:%S} 手动运行 =====\n")
    p = subprocess.Popen(
        [sys.executable, "main.py"], cwd=BASE, stdout=f, stderr=subprocess.STDOUT
    )
    _runs["proc"] = p
    threading.Thread(target=p.wait, daemon=True).start()
    return {"ok": True}


def tail_log():
    if not os.path.exists(RUN_LOG):
        return ""
    with open(RUN_LOG, encoding="utf-8", errors="replace") as f:
        return "".join(f.readlines()[-15:])


def main():
    url = f"http://127.0.0.1:{PORT}"
    try:
        srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    except OSError:
        print(f"端口 {PORT} 被占用，可能管理页已经开着：{url}")
        webbrowser.open(url)
        return
    _runs["srv"] = srv
    print(f"管理页已启动：{url}   （关掉这个窗口即停止）")
    threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n已关闭")


if __name__ == "__main__":
    main()
