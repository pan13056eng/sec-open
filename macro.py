"""美国宏观关键数据（CPI / 就业）—— 取 FRED 免费 CSV，免注册、免 API key。

数据源：https://fred.stlouisfed.org/graph/fredgraph.csv?id=<序列ID>
    CPIAUCNS  CPI-U 全部项目（未季调）      ← 官方头条 CPI 同比用的就是未季调口径
    CPILFENS  核心 CPI（剔除食品能源，未季调）
    UNRATE    失业率（季调，%）
    PAYEMS    非农就业总人数（千人，季调）  ← 差分得到月度新增

两个关键取舍：
1. 用 FRED 而不是 BLS 官方 API —— api.bls.gov 在国内直连会被 Akamai 拦（403），
   FRED 的图形 CSV 端点一直公开可用且不要 key，数据本身同样源自 BLS。
2. 四个序列合并成「一次请求」而不是四次 —— 密集请求会被 FRED 短暂限流，
   并发四个实测会集体超时，合成一个既快又稳（约 4 秒 / 30KB）。

取不到时不抛异常：回落到 data/macro.json 的缓存，页面标注「缓存」。
宁可显示旧数据，也不让顶部栏目空掉或让主流程失败。
"""
import csv
import io
import json
import os
import time
import urllib.request
from datetime import datetime

BASE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(BASE, "data", "macro.json")
URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"

SERIES = {
    "CPIAUCNS": "cpi",
    "CPILFENS": "core",
    "UNRATE": "unemp",
    "PAYEMS": "nfp",
}
TIMEOUT = 30        # FRED 偶尔慢到 20 秒以上，给足
RETRY = 2           # 被限流时重试一次基本能过
DELAY = 1.5
# 关键 1：必须显式要 identity。带 gzip 的请求会被 FRED 的 CDN 挂住不返回，
#         表现为读超时（curl 默认不压缩，所以拿 curl 测一直都是好的，很误导人）。
# 关键 2：这里用标准库 urllib 而不是 requests —— 实测 requests 对这个域名稳定超时，
#         urllib 稳定 3~5 秒返回。本模块因此零第三方依赖。
HEADERS = {"User-Agent": "Mozilla/5.0 USStockIntel/1.0", "Accept-Encoding": "identity"}


def _month_now() -> str:
    return datetime.now().strftime("%Y-%m")


def _shift(month: str, delta: int) -> str:
    """月份加减，2026-01 减 1 → 2025-12"""
    y, m = int(month[:4]), int(month[5:7]) + delta
    y += (m - 1) // 12
    m = (m - 1) % 12 + 1
    return f"{y}-{m:02d}"


def _fetch_all() -> dict[str, dict[str, float]]:
    """一次请求拿全部序列。返回 {'cpi': {'2026-08': 334.98, ...}, ...}"""
    last = None
    txt = None
    for i in range(RETRY):
        if i:
            time.sleep(DELAY)
        try:
            req = urllib.request.Request(f"{URL}?id={','.join(SERIES)}", headers=HEADERS)
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                txt = resp.read().decode("utf-8", "replace")
            break
        except Exception as e:  # noqa: BLE001
            last = e
    if txt is None:
        raise last

    rd = csv.reader(io.StringIO(txt))
    head = next(rd, [])
    cols = {}
    for idx, name in enumerate(head[1:], start=1):
        key = SERIES.get(name.strip())
        if key:
            cols[key] = idx

    out: dict[str, dict[str, float]] = {k: {} for k in cols}
    for row in rd:
        if not row:
            continue
        month = row[0][:7]
        for key, idx in cols.items():
            v = row[idx] if idx < len(row) else ""
            if v not in ("", "."):
                out[key][month] = float(v)
    return out


def _yoy(series: dict, month: str):
    cur, year_ago = series.get(month), series.get(_shift(month, -12))
    if not cur or not year_ago:
        return None
    return round((cur / year_ago - 1) * 100, 1)


def _diff_wan(series: dict, month: str):
    """就业人数月度差分，千人 → 万人（中文习惯用「万」）"""
    cur, prev = series.get(month), series.get(_shift(month, -1))
    if cur is None or prev is None:
        return None
    return round((cur - prev) / 10, 1)


def _save(rows: list):
    try:
        os.makedirs(os.path.dirname(CACHE), exist_ok=True)
        with open(CACHE, "w", encoding="utf-8") as f:
            json.dump({"rows": rows, "saved": _month_now()}, f, ensure_ascii=False)
    except Exception:  # noqa: BLE001  缓存写失败无所谓
        pass


def _load() -> list:
    try:
        with open(CACHE, encoding="utf-8") as f:
            return json.load(f).get("rows") or []
    except Exception:  # noqa: BLE001
        return []


def _stale() -> list:
    """取不到新数据时用缓存，并抹掉上次的修订角标（不然会误以为这次又修了）"""
    rows = _load()
    for r in rows:
        r.pop("rev", None)
    return rows


def fetch(months: int = 6, log=print):
    """取最近 months 个月的宏观数据。

    返回 (rows, stale)：
      rows  由旧到新，每项 {month, cpi, core, unemp, nfp}，缺失值为 None
      stale True 表示这次没取到、用的是上次缓存
    """
    try:
        raw = _fetch_all()
    except Exception as e:  # noqa: BLE001
        log(f"宏观数据获取失败：{e}（改用缓存）")
        return _stale(), True

    cpi, unemp = raw.get("cpi") or {}, raw.get("unemp") or {}
    if not cpi or not unemp:
        log("宏观数据返回为空，改用缓存")
        return _stale(), True

    # 先取出上一版结果，用来算「官方有没有上修/下修历史数据」
    prev = {r["month"]: r for r in _load()}

    rows = []
    for m in sorted(set(cpi) & set(unemp))[-months:]:
        row = {
            "month": m,
            "cpi": _yoy(cpi, m),
            "core": _yoy(raw.get("core") or {}, m),
            "unemp": unemp.get(m),
            "nfp": _diff_wan(raw.get("nfp") or {}, m),
        }
        old = prev.get(m)
        if old:
            rev = {}
            for k in ("cpi", "core", "unemp", "nfp"):
                new_v, old_v = row.get(k), old.get(k)
                if (
                    new_v is not None
                    and old_v is not None
                    and abs(new_v - old_v) >= 0.05      # 小于 0.05 视为舍入噪声
                ):
                    rev[k] = round(new_v - old_v, 1)
            if rev:
                row["rev"] = rev
                log(f"数据修订 {m}：" + "、".join(
                    f"{k} {v:+.1f}" for k, v in rev.items()))
        rows.append(row)
    if not rows:
        return _stale(), True

    _save(rows)
    return rows, False


if __name__ == "__main__":
    rs, stale = fetch(6)
    print(f"{'月份':<10}{'CPI同比':>9}{'核心同比':>10}{'失业率':>8}{'非农(万)':>10}")
    for r in rs:
        f = lambda v, s="%": f"{v:+.1f}{s}" if v is not None else "—"  # noqa: E731
        print(f"{r['month']:<10}{f(r['cpi']):>9}{f(r['core']):>10}"
              f"{r['unemp'] if r['unemp'] is not None else '—':>8}{f(r['nfp'], ''):>10}")
    print("（缓存数据）" if stale else "（本次实时获取）")
