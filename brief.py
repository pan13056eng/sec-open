"""生成每日中文简报（Markdown + 暗黑主题 HTML）

译文结构（由 translate.py 的提示词约定产出）：
    【一句话概要】   → 卡片主标题下的一行概要
    【要点摘要】     → 3-5 条要点（≤35 字/条）
    【正文译文】     → 压缩式全文，默认折叠
旧格式（一、中文要点摘要 / 二、正文译文）同样兼容解析。
"""
import os
import re
from datetime import datetime, timedelta

import db

# 正文译文默认折叠；超过该长度提示「长文」
# ---------- 暗黑主题样式 ----------
HTML_CSS = """
*{box-sizing:border-box;margin:0;padding:0}
:root{
  --bg:#0B0E13; --panel:#141A22; --panel2:#11171F; --line:#232B36;
  --fg:#E6EDF3; --fg2:#B6C2CF; --mut:#8B98A5; --dim:#6B7787;
  --link:#58A6FF; --accent:#58A6FF;
}
html,body{background:var(--bg)}
body{font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif;
color:var(--fg);line-height:1.72;padding:30px 18px 70px;-webkit-font-smoothing:antialiased}
.wrap{max-width:1000px;margin:0 auto}
header{margin-bottom:22px;padding-bottom:18px;border-bottom:1px solid var(--line)}
h1{font-size:25px;font-weight:650;letter-spacing:-.3px}
.sub{color:var(--mut);font-size:12.5px;margin-top:9px}
.sub b{color:var(--fg2);font-weight:500}

.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:11px;margin:20px 0}
.stat{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:14px 16px}
.stat .k{font-size:11.5px;color:var(--mut);letter-spacing:.3px}
.stat .v{font-size:23px;font-weight:650;margin-top:3px;color:var(--fg)}

.bar{position:sticky;top:0;z-index:20;background:rgba(11,14,19,.93);
backdrop-filter:blur(10px);padding:12px 0;margin-bottom:16px;
border-bottom:1px solid var(--line)}
.bar-in{display:flex;gap:9px;flex-wrap:wrap;align-items:center}
.spacer{flex:1}
#q{background:var(--panel);border:1px solid var(--line);border-radius:999px;padding:6px 14px;
font-size:12.5px;color:var(--fg);font-family:inherit;width:170px;outline:none}
#q:focus{border-color:var(--accent)}
#q::placeholder{color:var(--dim)}
.card{background:var(--panel);border:1px solid var(--line);border-radius:14px;
padding:17px 20px 15px;margin-bottom:13px;border-left:3px solid #2E3947;transition:.15s}
.card:hover{border-color:#3A4654;border-left-color:var(--accent)}
.chead{display:flex;align-items:baseline;gap:9px;flex-wrap:wrap}
.card h3{font-size:16.5px;font-weight:650;letter-spacing:-.2px}
.new{font-size:9.5px;font-weight:700;letter-spacing:.6px;color:#241C00;
background:#FFD33D;border-radius:4px;padding:1px 5px;vertical-align:2px;
font-family:-apple-system,sans-serif}
.card.isnew{border-left-color:#3FB950}
.card.isnew .meta span:first-child{border-color:#2E6B41;color:#7EE2A8}
.meta{font-size:11.5px;color:var(--dim);margin-top:5px;font-variant-numeric:tabular-nums}
.meta span{background:var(--panel2);border:1px solid var(--line);border-radius:5px;padding:1px 7px;margin-right:6px}

.headline{font-size:14.5px;font-weight:600;color:var(--fg);margin-top:11px;line-height:1.6}
.headline::before{content:"概要";font-size:10.5px;font-weight:600;color:var(--accent);
border:1px solid var(--accent);border-radius:4px;padding:1px 5px;margin-right:8px;
vertical-align:1.5px;letter-spacing:.5px;opacity:.9}
.pts{margin:9px 0 0;padding:0;list-style:none}
.pts li{font-size:13.5px;color:var(--fg2);padding:3px 0 3px 17px;position:relative;line-height:1.62}
.pts li::before{content:"";position:absolute;left:4px;top:12px;width:4px;height:4px;
border-radius:50%;background:var(--accent);opacity:.6}

details{margin-top:11px}
details summary{cursor:pointer;font-size:12.5px;color:var(--mut);user-select:none;
padding:5px 0;list-style:none;display:inline-flex;align-items:center;gap:7px}
details summary::-webkit-details-marker{display:none}
details summary::before{content:"▸";font-size:10px;transition:.15s;display:inline-block}
details[open] summary::before{transform:rotate(90deg)}
details summary:hover{color:var(--fg)}
.cnt{font-size:11px;color:var(--dim);background:var(--panel2);border:1px solid var(--line);
border-radius:5px;padding:1px 6px}
.src{margin-top:10px;font-size:11.5px;color:var(--dim)}
.src a{color:var(--link);text-decoration:none;word-break:break-all}
.src a:hover{text-decoration:underline}
.nosum{color:var(--dim);font-size:13px;margin-top:10px;font-style:italic}

.wl{background:var(--panel);border:1px solid var(--line);border-radius:12px;
padding:11px 16px;margin:14px 0 2px}
.wl summary{cursor:pointer;font-size:13px;color:var(--fg2);list-style:none;
display:flex;align-items:center;gap:8px;user-select:none}
.wl summary::-webkit-details-marker{display:none}
.wl summary::before{content:"▸";font-size:10px;transition:.15s;display:inline-block}
.wl[open] summary::before{transform:rotate(90deg)}
.wl summary:hover{color:var(--fg)}
.wl-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(190px,1fr));
gap:8px;margin-top:12px}
.wl-item{display:flex;align-items:center;gap:8px;background:var(--panel2);
border:1px solid var(--line);border-radius:9px;padding:8px 11px;cursor:pointer;
font-family:inherit;color:var(--fg);font-size:13px;text-align:left;transition:.15s}
.wl-item:hover{border-color:var(--accent)}
.wl-item b{font-weight:650;letter-spacing:.2px;font-size:12.5px}
.wl-item span{color:var(--mut);font-size:12px;flex:1;overflow:hidden;
text-overflow:ellipsis;white-space:nowrap}
.wl-item em{font-style:normal;font-size:11px;color:var(--fg2);background:#1B232E;
border:1px solid var(--line);border-radius:999px;padding:1px 7px}
.wl-item.zero{opacity:.45}

.empty{background:var(--panel);border:1px dashed var(--line);border-radius:14px;
padding:44px;text-align:center;color:var(--mut);font-size:14px}
footer{margin-top:34px;font-size:11.5px;color:var(--dim);text-align:center;line-height:1.9}

.macro{background:var(--panel);border:1px solid var(--line);border-radius:12px;
padding:11px 15px 12px;margin:0 0 14px}
.macro-t{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:7px}
.macro-t b{font-size:13px;font-weight:600;color:var(--fg)}
.macro-t span{font-size:11.5px;color:var(--dim)}
.macro-t .warn{color:#F0883E}
.macro-scroll{overflow-x:auto;-webkit-overflow-scrolling:touch}
table.mt{width:100%;border-collapse:collapse;font-size:12.5px;
font-variant-numeric:tabular-nums;min-width:380px}
table.mt th{font-weight:500;font-size:11.5px;color:var(--dim);text-align:right;
padding:3px 9px;border-bottom:1px solid var(--line);white-space:nowrap}
table.mt td{text-align:right;padding:5px 9px;color:var(--fg2);white-space:nowrap;
border-bottom:1px solid rgba(255,255,255,.035)}
table.mt th:first-child,table.mt td:first-child{text-align:left}
table.mt tr:last-child td{font-weight:600;color:var(--fg);border-bottom:0}
.pos{color:#3FB950}.neg{color:#F0883E}
.rv{font-style:normal;font-size:10.5px;font-weight:600;margin-left:4px;
padding:0 4px;border-radius:4px;background:#1B232E;border:1px solid var(--line);
color:var(--mut);vertical-align:1px}
"""

HTML_JS = """
var qEl = document.getElementById('q');
function applyFilter(){
  var kw = (qEl && qEl.value || '').trim().toLowerCase();
  var shown = 0;
  document.querySelectorAll('.card').forEach(function(c){
    var ok = !kw || (c.dataset.search || '').indexOf(kw) >= 0;
    c.style.display = ok ? '' : 'none';
    if (ok) shown++;
  });
  var e = document.getElementById('shown');
  if (e) e.textContent = '显示 ' + shown + ' 条';
}
if (qEl) qEl.oninput = applyFilter;
document.querySelectorAll('.wl-item').forEach(function(b){
  b.onclick = function(){
    if (!qEl) return;
    var tk = b.dataset.tk || '';
    qEl.value = (qEl.value === tk) ? '' : tk;   // 再点一次取消筛选
    applyFilter();
    window.scrollTo(0, 0);
  };
});
"""


# ---------- 译文解析 ----------
_HEAD_LINE = re.compile(
    r"^\s*(?:[【\[]\s*\*{0,2}(.{2,24}?)\*{0,2}\s*[】\]]"
    r"|#{1,4}\s*\*{0,2}(.{2,24}?)\*{0,2}"
    r"|([一二三四五六七八九十])、\s*\*{0,2}(.{2,16}?)\*{0,2})\s*[::]?\s*$"
)


def _head_key(line: str):
    """识别分节标题 → headline / points / body，非标题返回 None"""
    m = _HEAD_LINE.match(line)
    if not m:
        return None
    # 编号式标题分了两段捕获（"二" + "正文译文"），拼起来再判断
    s = "".join(g for g in m.groups() if g)
    s = s.strip()
    if "正文" in s or "全文" in s or "译文" in s:
        return "body"
    if "要点" in s or "摘要" in s:
        return "points"
    if "概要" in s:
        return "headline"
    return None


def parse_zh(text: str):
    """把 AI 输出拆成 (一句话概要, 要点列表)

    只取概要与要点；若输出里还带着【正文译文】（历史数据），直接丢弃不展示。
    """
    text = (text or "").replace("\r\n", "\n").strip()
    if not text:
        return "", []

    buckets = {"headline": [], "points": []}
    cur = None
    has_head = False
    for ln in text.split("\n"):
        k = _head_key(ln)
        if k:
            cur = k if k in buckets else None  # body 之类一概跳过
            has_head = True
            continue
        if cur:
            buckets[cur].append(ln)

    def joined(k):
        return "\n".join(buckets[k]).strip()

    headline = joined("headline")
    points_raw = joined("points")

    # 要点：逐行取，去掉序号/符号
    pts = []
    for ln in points_raw.split("\n"):
        s = re.sub(r"^\s*(\d+\s*[.、)．]|[-•·*]{1,2})\s*", "", ln).strip()
        if not s:
            continue
        s = re.sub(r"\s{2,}", " ", s)
        pts.append(s)
    # 兼容：要点挤在一行里用数字连排的情况
    if len(pts) == 1 and re.search(r"\d+\s*[.、]\s*\S", pts[0]):
        pts = [x.strip() for x in re.split(r"(?=\d+\s*[.、]\s*)", pts[0]) if x.strip()]
    pts = [p for p in pts if len(p) > 1][:6]

    # 概要：取第一行，去掉括号提示与 Markdown 粗体
    hl = ""
    for ln in headline.split("\n"):
        s = re.sub(r"^\s*(\d+\s*[.、)．]|[-•·*]{1,2})\s*", "", ln).strip()
        s = s.strip("*").strip()
        if s and not s.startswith("（此处"):
            hl = s
            break
    hl = re.sub(r"[（(]\s*(1\s*行|不超过\s*\d+\s*字|≤\s*\d+\s*字)[^）)]*[）)]\s*$", "", hl).strip()

    if not hl and pts:
        hl = pts[0]
    if not hl and not has_head:
        # 完全没按格式输出 → 取第一行像样的文字当概要
        for ln in text.split("\n"):
            s = re.sub(r"^[#*\s>]+", "", ln).strip()
            if len(s) > 8:
                hl = s
                break
    if len(hl) > 90:
        hl = hl[:88].rstrip("，,、；; ") + "…"

    return hl, pts


# 增删公司：管理页（manage.py）以 iframe 嵌入。
# 用 JSONP 探针（script 标签）检测服务是否开着 —— 静态页用 fetch 会被同源策略拦，script 不会。
MGR_JS = """
(function(){
  var API='http://127.0.0.1:8765';
  var box=document.getElementById('mgrBox'), st=document.getElementById('mgrState'),
      det=document.getElementById('mgrDet');
  if(!box||!st) return;
  window.__mgrPing=function(){};                       // JSONP 回调（空函数即可）

  var build=null;   // 折叠着时不渲染：iframe 在 display:none 里测高会测到 0，展开后会只剩一条缝
  function show(){ if(build && (!det || det.open)) build(); }
  if(det) det.addEventListener('toggle', show);

  function probe(cb){                                  // 用 script 标签探测，绕开同源策略
    var s=document.createElement('script'), done=false;
    s.src=API+'/api/ping?cb=__mgrPing&t='+Date.now();
    s.onload=function(){ if(!done){done=true;cb(true);} };
    s.onerror=function(){ if(!done){done=true;cb(false);} };
    document.head.appendChild(s);
    setTimeout(function(){ if(!done){done=true;cb(false);} },1500);
  }
  function online(){
    st.textContent='已连接'; st.style.color='#3FB950';
    build=function(){
      box.innerHTML="<iframe id='mgrFrame' src='"+API+"' "
        +"style='width:100%;height:900px;border:0;border-radius:10px;background:#141A22'></iframe>";
    };
    show();
  }
  function offline(tip){
    st.textContent='未启动'; st.style.color='';
    build=function(){
      box.innerHTML="<div style='padding:14px 16px;border:1px dashed #2c3746;border-radius:8px;"
        +"color:#8B98A5;font-size:13px;line-height:1.7'>"
        +"<div style='color:#E6EDF3;font-size:14px;margin-bottom:8px'>管理功能还没开启</div>"
        +"<button onclick='__mgrStart()' style='background:#1F6FEB;color:#fff;border:none;"
        +"border-radius:7px;padding:8px 15px;font-size:13px;cursor:pointer'>⚡ 一键开启</button>"
        +"<span style='margin-left:10px'>首次点击系统可能询问「是否允许打开」，选允许</span>"
        +(tip?"<div style='margin-top:9px;color:#F0883E'>"+tip+"</div>":"")
        +"</div>";
    };
    show();
  }
  window.__mgrStart=function(){
    build=function(){
      box.innerHTML="<div style='padding:14px 16px;color:#8B98A5;font-size:13px'>正在开启…"
        +"若系统弹窗询问，点「允许」</div>";
    };
    show();
    location.href='usstockintel://start';              // 唤起本机 App 起服务
    var n=0, t=setInterval(function(){
      n++;
      probe(function(ok){
        if(ok){ clearInterval(t); online(); }
        else if(n>=12){ clearInterval(t);
          offline('没起来。请双击桌面的「美股公告管理」一次，再刷新本页。'); }
      });
    },2000);
  };
  window.addEventListener('message',function(e){
    var f=document.getElementById('mgrFrame');
    if(e.data&&e.data.mgrH&&f&&e.data.mgrH>120) f.style.height=(e.data.mgrH+30)+'px';
  });
  st.textContent='检测中…';
  probe(function(ok){ ok?online():offline(''); });
})();
"""


def _recent_rows(days: int):
    cutoff = (datetime.now() - timedelta(days=days)).date().isoformat()
    return [r for r in db.recent(500) if (r.get("filing_date") or "") >= cutoff]


# ---------- 顶部宏观小表（CPI / 就业）----------
def _macro_cell(v, unit="%", down_good=True, signed=True, color=True, rev=None):
    """数值单元格。配色按「这个方向对经济是好是坏」来定：
    CPI 越低越好（跌=绿）；非农新增越高越好（涨=绿）。
    color=False 用于失业率这类「水平值」——它没有正负方向，上色会误导。
    rev 非 None 表示这次的数字是官方上修/下修过的，挂一个小角标。"""
    if v is None:
        return "<td>—</td>"
    cls = ""
    if color:
        if v > 0:
            cls = "neg" if down_good else "pos"
        elif v < 0:
            cls = "pos" if down_good else "neg"
    txt = f"{v:+.1f}{unit}" if signed else f"{v:.1f}{unit}"
    mark = ""
    if rev:
        mark = f'<i class="rv">{"↑" if rev > 0 else "↓"}{abs(rev):.1f}</i>'
    inner = f"{txt}{mark}"
    return f'<td class="{cls}">{inner}</td>' if cls else f"<td>{inner}</td>"


def _macro_html(rows, stale=False):
    if not rows:
        return ""
    head = "".join(
        f"<th>{h}</th>"
        for h in ("月份", "CPI 同比", "核心 CPI 同比", "失业率", "非农新增")
    )
    body = "".join(
        "<tr><td>" + r["month"] + "</td>"
        + _macro_cell(r.get("cpi"), rev=(r.get("rev") or {}).get("cpi"))
        + _macro_cell(r.get("core"), rev=(r.get("rev") or {}).get("core"))
        + _macro_cell(
            r.get("unemp"),
            signed=False,
            color=False,
            rev=(r.get("rev") or {}).get("unemp"),
        )
        + _macro_cell(
            r.get("nfp"),
            unit=" 万",
            down_good=False,
            rev=(r.get("rev") or {}).get("nfp"),
        )
        + "</tr>"
        for r in rows
    )
    n_rev = sum(len(r.get("rev") or {}) for r in rows)
    warn = '<span class="warn">· 本次未取到，显示上次缓存</span>' if stale else ""
    if n_rev:
        warn += f'<span>· 本次 {n_rev} 项为官方修订值（角标为修订幅度）</span>'
    return (
        '<div class="macro"><div class="macro-t"><b>美国宏观 · CPI 与就业</b>'
        f'<span>近 {len(rows)} 个月 ｜ 数据源 FRED（圣路易斯联储，原始出自 BLS）</span>{warn}</div>'
        f'<div class="macro-scroll"><table class="mt"><thead><tr>{head}</tr></thead>'
        f"<tbody>{body}</tbody></table></div></div>"
    )


def _macro_md(rows, stale=False):
    if not rows:
        return []
    out = [
        "## 美国宏观 · CPI 与就业",
        "",
        f"> 近 {len(rows)} 个月 ｜ 数据源 FRED（圣路易斯联储，原始出自 BLS）"
        + ("｜ **本次未取到，显示上次缓存**" if stale else ""),
        "",
        "| 月份 | CPI 同比 | 核心 CPI 同比 | 失业率 | 非农新增 |",
        "|---|---|---|---|---|",
    ]
    def cell(v, unit="%", signed=True):
        if v is None:
            return "—"
        return f"{v:+.1f}{unit}" if signed else f"{v:.1f}{unit}"

    for r in rows:
        rev = r.get("rev") or {}

        def mark(k):
            d = rev.get(k)
            return f"（修订 {'↑' if d > 0 else '↓'}{abs(d):.1f}）" if d else ""

        out.append(
            f"| {r['month']} | {cell(r.get('cpi'))}{mark('cpi')} "
            f"| {cell(r.get('core'))}{mark('core')} "
            f"| {cell(r.get('unemp'), signed=False)}{mark('unemp')} "
            f"| {cell(r.get('nfp'), ' 万')}{mark('nfp')} |"
        )
    if any(r.get("rev") for r in rows):
        out.append("")
        out.append("> 带「修订」字样的数字，是本次刷新时官方对历史数据的上修/下修")
    out.append("")
    return out


def generate_html(
    out_dir: str,
    days: int = 30,
    watch_count: int = 0,
    watchlist=None,
    macro=None,
    macro_stale: bool = False,
) -> str:
    import html as _h

    os.makedirs(out_dir, exist_ok=True)
    rows = _recent_rows(days)
    tick_counts = {}
    for r in rows:
        tk = r.get("ticker")
        if tk:
            tick_counts[tk] = tick_counts.get(tk, 0) + 1
    today = f"{datetime.now():%Y-%m-%d}"
    cut3 = (datetime.now().date() - timedelta(days=2)).isoformat()   # 近三日（含今天）
    now_str = f"{datetime.now():%Y-%m-%d %H:%M}"

    tickers = {r.get("ticker") for r in rows if r.get("ticker")}

    def esc(s):
        return _h.escape(s or "")

    cards = []
    n_new = 0
    for r in rows:
        headline, pts = parse_zh(r.get("summary_zh"))

        who = " · ".join(x for x in [r.get("ticker"), r.get("company")] if x)
        meta_bits = [
            x
            for x in [
                r.get("form"),
                f"Item {r['items']}" if r.get("items") else "",
                r.get("filing_date"),
            ]
            if x
        ]
        meta_html = "".join(f"<span>{esc(x)}</span>" for x in meta_bits)

        pts_html = (
            "<ul class='pts'>" + "".join(f"<li>{esc(p)}</li>" for p in pts) + "</ul>"
            if pts
            else ""
        )

        src = r.get("url") or ""
        src_html = (
            f'<div class="src">原文：<a href="{esc(src)}" target="_blank">{esc(src)}</a></div>'
            if src
            else ""
        )

        hl_html = (
            f'<div class="headline">{esc(headline)}</div>' if headline else ""
        ) or '<div class="nosum">（AI 未生成概要，请查看原文）</div>'

        search = " ".join([who, headline, " ".join(pts)]).lower()

        # 按公告日期分两层：近三日标绿（左边框），当日再多一个黄色 NEW
        fd = r.get("filing_date") or ""
        is_recent = bool(fd) and fd >= cut3
        is_today = fd == today
        new_tag = '<span class="new">NEW</span>' if is_today else ""
        if is_recent:
            n_new += 1

        cards.append(
            f'<div class="card{" isnew" if is_recent else ""}" data-search="{esc(search)}">'
            f'<div class="chead"><h3>{esc(who)}</h3>{new_tag}</div>'
            f'<div class="meta">{meta_html}</div>'
            f"{hl_html}{pts_html}{src_html}</div>"
        )

    watchlist = watchlist or []
    n_watch = len(watchlist) or watch_count

    # 发布到公开网页时（PUBLISH_MODE=1）隐藏「监控名单」——名单等于持仓，不适合公开
    publish = os.environ.get("PUBLISH_MODE") == "1"

    # 监控名单（可折叠；点公司名即筛选该公司的公告）
    if publish:
        watch_html = ""
    elif watchlist:
        items_html = []
        for w in watchlist:
            tk = (w.get("ticker") or "").strip()
            if not tk:
                continue
            nm = w.get("name", "")
            n = tick_counts.get(tk, 0)
            items_html.append(
                f'<button class="wl-item{" zero" if n == 0 else ""}" '
                f'data-tk="{esc(tk)}"><b>{esc(tk)}</b>'
                f'<span>{esc(nm)}</span><em>{n} 条</em></button>'
            )
        watch_html = (
            '<details class="wl"><summary>监控名单 · 点击公司可筛选'
            f'<span class="cnt">{len(watchlist)} 家</span></summary>'
            f'<div class="wl-grid">{"".join(items_html)}</div></details>'
        )
    else:
        watch_html = ""

    # 增删公司（管理页以 iframe 嵌入；服务没开时给出启动提示）
    # 发布版里这块要去掉：线上访问不到本机的 127.0.0.1:8765 服务，
    # 留着只会永远显示「管理功能还没开启」，看着像坏了。
    mgr_html = (
        ""
        if publish
        else (
            '<details class="wl" id="mgrDet"><summary>⚙ 增删监控公司'
            '<span class="cnt" id="mgrState">…</span></summary>'
            '<div id="mgrBox" style="margin-top:10px"></div>'
            '<div class="sub" style="margin:8px 0 2px;font-size:12px">'
            "改动保存在 config.yaml，会在<b>下次定时抓取</b>后出现在简报里；"
            "想马上看到就点下面的「立即跑一次抓取」，跑完刷新本页。</div>"
            "</details>"
        )
    )

    stats_html = "".join(
        f'<div class="stat"><div class="k">{k}</div><div class="v">{v}</div></div>'
        for k, v in [
            ("近三日", n_new),
            ("监控公司", n_watch),
            ("公告总数", len(rows)),
            ("涉及公司", len(tickers)),
            ("数据区间", f"{days}天"),
        ]
    )

    body_main = (
        "".join(cards)
        if cards
        else '<div class="empty">本区间暂无符合条件的公告</div>'
    )

    doc = (
        "<!DOCTYPE html><html lang='zh-CN'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<meta name='color-scheme' content='dark'>"
        f"<title>美股公告情报简报 · {today}</title>"
        f"<style>{HTML_CSS}</style></head><body><div class='wrap'>"
        f"<header><h1>美股公告情报简报</h1>"
        f"<div class='sub'>{now_str} ｜ 数据源 <b>SEC EDGAR</b> 官方一手 ｜ "
        f"AI 读英文原文后直接生成中文概括，关键决策请回看原文核对</div></header>"
        f"<div class='stats'>{stats_html}</div>"
        f"{_macro_html(macro or [], macro_stale)}"
        f"{watch_html}{mgr_html}"
        f"<div class='bar'><div class='bar-in'>"
        f"<div class='spacer'></div>"
        f"<input id='q' placeholder='搜索公司/关键词…' autocomplete='off'>"
        f"</div></div>"
        f"<div class='sub' id='shown' style='margin:-6px 0 14px'></div>"
        f"{body_main}"
        f"<footer>本地生成 · 数据来自 SEC EDGAR（免费公开）<br>"
        f"本页仅做信息聚合与翻译，不构成投资建议</footer>"
        f"</div><script>{HTML_JS}</script>"
        + ("" if publish else f"<script>{MGR_JS}</script>")
        + "</body></html>"
    )

    path = os.path.join(out_dir, f"简报-{today}.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(doc)
    _prune_out(out_dir)
    return path


def _prune_out(out_dir: str, keep_days: int = 7):
    """只保留最近 keep_days 天的简报，其余删掉。

    每天会生成 md + html 两份，不清理的话一个月就是 240 个文件。
    只认「简报-YYYY-MM-DD.*」这个命名，别的文件一律不碰。
    """
    pat = re.compile(r"^简报-(\d{4}-\d{2}-\d{2})\.(md|html)$")
    cutoff = (datetime.now().date() - timedelta(days=keep_days)).isoformat()
    try:
        for name in os.listdir(out_dir):
            m = pat.match(name)
            if m and m.group(1) < cutoff:
                os.remove(os.path.join(out_dir, name))
    except Exception:  # noqa: BLE001  清理失败无所谓，不影响出简报
        pass


def generate(
    out_dir: str,
    days: int = 7,
    watch_count: int = 0,
    watchlist=None,
    macro=None,
    macro_stale: bool = False,
) -> str:
    os.makedirs(out_dir, exist_ok=True)
    rows = _recent_rows(days)

    today = f"{datetime.now():%Y-%m-%d}"
    path = os.path.join(out_dir, f"简报-{today}.md")

    watchlist = watchlist or []
    tick_counts = {}
    for r in rows:
        tk = r.get("ticker")
        if tk:
            tick_counts[tk] = tick_counts.get(tk, 0) + 1

    lines = [
        f"# 美股公告情报简报 · {today}",
        "",
        f"> 生成时间：{datetime.now():%Y-%m-%d %H:%M} ｜ 监控 {len(watchlist) or watch_count} 家公司 ｜ "
        f"最近 {days} 天命中 {len(rows)} 条",
        ">",
        "> 数据源：SEC EDGAR（官方一手）｜ AI 读英文原文后直接生成中文概括，请按原文核对关键数据",
        "",
    ]

    lines += _macro_md(macro or [], macro_stale)

    if watchlist and os.environ.get("PUBLISH_MODE") != "1":
        lines.append("<details><summary>监控名单（点击展开）</summary>")
        lines.append("")
        lines.append("| 代码 | 公司 | 本区间公告 |")
        lines.append("|---|---|---|")
        for w in watchlist:
            tk = (w.get("ticker") or "").strip()
            if not tk:
                continue
            lines.append(
                f"| {tk} | {w.get('name','')} | {tick_counts.get(tk, 0)} 条 |"
            )
        lines.append("")
        lines.append("</details>")
        lines.append("")

    if not rows:
        lines.append("本次无新增公告。")
        lines.append("")
    else:
        rows.sort(key=lambda x: (x.get("filing_date") or ""), reverse=True)
        for it in rows:
            items_txt = f" · Item {it['items']}" if it.get("items") else ""
            # 按公告日期：当日标 NEW，近三日（非当日）标 3日内
            fd = it.get("filing_date") or ""
            if fd == today:
                tag = " `NEW`"
            elif fd and fd >= (datetime.now().date() - timedelta(days=2)).isoformat():
                tag = " `3日内`"
            else:
                tag = ""
            lines.append(
                f"### {it.get('ticker','')} {it.get('company','')} "
                f"· {it.get('form','')}{items_txt} · {it.get('filing_date','')}{tag}"
            )
            lines.append("")
            hl, pts = parse_zh(it.get("summary_zh"))
            if hl:
                lines.append(f"**概要**：{hl}")
                lines.append("")
            if pts:
                for p in pts:
                    lines.append(f"- {p}")
                lines.append("")
            lines.append(f"- 原文：{it.get('url','')}")
            lines.append("")
            lines.append("---")
            lines.append("")

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return path
