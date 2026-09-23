"""主流程：抓取 → 按 form/item 过滤 → 去重 → 翻译 → 生成中文简报

手动跑：  python3 main.py
每天自动：由 launchd 每 6 小时触发（见 com.user.usstockintel.plist）
"""
import json
import os
import sys
import time
import urllib.request
from datetime import datetime

import yaml

BASE = os.path.dirname(os.path.abspath(__file__))
MAX_ATTEMPTS = 3   # 同一条最多重试几次，超过就放弃，避免反复消耗额度
sys.path.insert(0, BASE)


def _load_env_file():
    """读项目根目录的 .env（每行 KEY=VALUE）。

    API key 不写进 config.yaml —— 那份文件要提交到 Git，写进去等于公开。
    本地跑（含 launchd 定时任务）从 .env 读；GitHub Actions 上直接用仓库 Secrets
    注入的环境变量，没有 .env 文件也不影响。
    """
    path = os.path.join(BASE, ".env")
    if not os.path.exists(path):
        return
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip("\"'"))
    except Exception:  # noqa: BLE001  读不到就算了，走环境变量
        pass


_load_env_file()

import db  # noqa: E402
import brief  # noqa: E402
import macro  # noqa: E402
from fetch_sec import SecClient, pick_filings, extract_relevant  # noqa: E402
from translate import Translator  # noqa: E402


def log(msg: str):
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def _remote_watchlist():
    """从云端后台（Cloudflare Worker）拉监控名单。

    网页上增删的公司存在 Worker 的 KV 里，所以定时跑要以那边为准，
    config.yaml 只当兜底。拉不到（没配 / 后台挂了 / 网络不通）就返回 None，
    让调用方继续用 config.yaml —— 后台出问题不该让整个抓取失败。
    """
    api = (os.environ.get("WATCHLIST_API") or "").strip()
    if not api:
        return None

    # 重试 3 次：一次网络抖动就静默退回旧名单的话，用户刚加的公司会「明明成功了却没生效」，
    # 很难查。多试几下的代价只是几十秒。
    # 用 urllib 而不是 requests：代理环境里 requests 会莫名读超时。
    # Accept-Encoding: identity 是同一个坑的另一半（压缩响应会被某些 CDN 挂住）。
    data = None
    last_err = None
    for attempt in range(3):
        try:
            req = urllib.request.Request(
                api.rstrip("/") + "/api/state",
                headers={
                    "User-Agent": "USStockIntel",
                    "Accept": "application/json",
                    "Accept-Encoding": "identity",
                },
            )
            with urllib.request.urlopen(req, timeout=15) as r:
                data = json.loads(r.read().decode("utf-8"))
            break
        except Exception as e:  # noqa: BLE001
            last_err = e
            if attempt < 2:
                log(f"云端名单第 {attempt + 1} 次没拉到（{e}），重试…")
                time.sleep(2)

    if data is None:
        log(f"云端名单拉不到（{last_err}），改用 config.yaml")
        return None

    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list) or not items:
        log("云端名单是空的，改用 config.yaml")
        return None

    out = []
    for it in items:
        if not isinstance(it, dict):
            continue
        tk = str(it.get("ticker") or "").strip().upper()
        if not tk:
            continue
        out.append(
            {
                "ticker": tk,
                "cik": str(it.get("cik") or "").strip(),
                "name": str(it.get("name") or "").strip(),
            }
        )
    if not out:
        return None
    log(f"云端名单：{len(out)} 家")
    return out


def main():
    with open(os.path.join(BASE, "config.yaml"), encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    sec_cfg, filt, tcfg = cfg["sec"], cfg["filters"], cfg["translate"]
    lookback = sec_cfg.get("lookback_days", 30)
    max_chars = tcfg.get("max_chars", 12000)

    # 仓库公开时邮箱会被人爬，允许用环境变量覆盖（GitHub Actions 走 Secrets）
    ua = os.environ.get("SEC_USER_AGENT") or sec_cfg["user_agent"]
    client = SecClient(ua, sec_cfg.get("request_delay", 0.15))
    tr = Translator(tcfg)

    wl = cfg.get("watchlist") or []

    # 名单优先从云端后台（Cloudflare Worker / KV）取。
    # 网页上增删的公司存在那里，config.yaml 只是兜底。
    remote = _remote_watchlist()
    if remote is not None:
        wl = remote

    # 从 watchlist 删掉的公司，历史记录一并清掉（删干净、不用碰数据库）
    removed = db.prune_tickers([w["ticker"] for w in wl])
    if removed:
        log(f"已移除公司清理：删除 {removed} 条历史记录")

    log(
        f"开始｜监控 {len(wl)} 家｜回溯 {lookback} 天｜"
        f"翻译链路 {' → '.join(tr.order()) or '（未配置）'}"
    )

    new_count = 0
    for w in wl:
        ticker = w["ticker"]
        cik = str(w["cik"]).zfill(10)
        name = w.get("name", "")

        try:
            sub = client.submissions(cik)
        except Exception as e:  # noqa: BLE001
            log(f"  {ticker} 申报列表获取失败：{e}")
            continue

        cands = pick_filings(sub, filt["form_types"], filt.get("items_8k"), lookback)
        if not cands:
            continue

        for c in cands:
            acc = c["accession"]
            if db.is_done(acc):
                continue  # 已翻译过，跳过省额度
            if db.attempts(acc) >= MAX_ATTEMPTS:
                log(f"  {ticker} {c["form"]} 已失败 {MAX_ATTEMPTS} 次，不再重试（省额度）")
                continue

            try:
                url = client.doc_url(cik, acc, c["primary_document"])
                raw = client.doc_text(url)
            except Exception as e:  # noqa: BLE001
                log(f"  {ticker} {c['form']} 正文获取失败：{e}")
                continue

            body = extract_relevant(raw, c["form"], max_chars)
            if not body.strip():
                continue

            log(f"  ★ {ticker} {c['form']} {c['filing_date']}")

            meta = {
                "ticker": ticker,
                "company": name,
                "form": c["form"],
                "items": c["items"],
                "filing_date": c["filing_date"],
            }
            zh, engine = tr.translate(body, meta)
            if zh:
                log(f"    AI 概括完成（{engine}）")
            else:
                log("    概括失败，本次不入库")

            db.save(
                {
                    "accession": acc,
                    "cik": cik,
                    "ticker": ticker,
                    "company": name,
                    "form": c["form"],
                    "items": c["items"],
                    "filing_date": c["filing_date"],
                    "url": url,
                    "title": f"{ticker} {c['form']}",
                    "summary_zh": zh or "",
                }
            )
            if zh:
                new_count += 1

    log(f"本次新增 {new_count} 条")

    # 顶部宏观小表（CPI / 就业）。取不到会自动用缓存，不影响出简报。
    mcfg = cfg.get("macro") or {}
    macro_rows, macro_stale = ([], False)
    if mcfg.get("enabled", True):
        macro_rows, macro_stale = macro.fetch(mcfg.get("months", 6), log)
        if macro_rows:
            log(
                f"宏观数据：{len(macro_rows)} 个月，最新 {macro_rows[-1]['month']}"
                f"{'（缓存）' if macro_stale else ''}"
            )

    out_dir = os.path.join(BASE, (cfg.get("output") or {}).get("dir", "out"))
    path = brief.generate(
        out_dir, days=lookback, watch_count=len(wl), watchlist=wl,
        macro=macro_rows, macro_stale=macro_stale,
    )
    page = brief.generate_html(
        out_dir, days=lookback, watch_count=len(wl), watchlist=wl,
        macro=macro_rows, macro_stale=macro_stale,
    )
    log(f"简报：{path}")
    log(f"网页：{page}")
    log(f"库存：{db.stats()}")


if __name__ == "__main__":
    main()
