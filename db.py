"""SQLite 存储：公告去重 + 译文缓存
避免重复抓取和重复翻译（翻译是全流程最慢/最耗额度的一环）
"""
import os
import shutil
import sqlite3
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("USSTOCK_DB") or os.path.join(BASE_DIR, "data", "intel.db")

# 库搬过家，万一项目里没有，就从旧位置自动找回来（不丢历史数据）
LEGACY_DBS = (
    os.path.expanduser("~/Library/Application Support/USStockIntel/intel.db"),
    os.path.expanduser("~/Desktop/USStockIntel/data/intel.db"),
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS filings (
    accession   TEXT PRIMARY KEY,
    cik         TEXT,
    ticker      TEXT,
    company     TEXT,
    form        TEXT,
    items       TEXT,
    filing_date TEXT,
    url         TEXT,
    title       TEXT,
    summary_zh  TEXT,
    translated  INTEGER DEFAULT 0,
    attempts    INTEGER DEFAULT 0,
    created_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_filings_date ON filings(filing_date);
CREATE INDEX IF NOT EXISTS idx_filings_ticker ON filings(ticker);
"""


def _clear_provenance() -> bool:
    """macOS 会给文件打上 com.apple.provenance 扩展属性，它让 SQLite 的写事务
    直接报 "disk I/O error"（读正常、一写就挂）。碰上就自动清掉 —— 定时任务半夜
    自己跑的时候没人能手动修，必须自愈。"""
    try:
        os.removexattr(DB_PATH, "com.apple.provenance")
        return True
    except Exception:  # noqa: BLE001
        return False


def get_conn():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    _clear_provenance()  # 系统会反复打这个标记，每次连接都清一次（成本可忽略）
    # 库不在（比如刚搬家）→ 从旧位置找回来，不丢历史数据
    if not os.path.exists(DB_PATH):
        for old in LEGACY_DBS:
            if os.path.exists(old):
                try:
                    shutil.copy(old, DB_PATH)
                except Exception:  # noqa: BLE001
                    pass
                break

    def _open():
        c = sqlite3.connect(DB_PATH)
        c.row_factory = sqlite3.Row
        c.executescript(SCHEMA)
        return c

    try:
        conn = _open()
    except sqlite3.OperationalError:
        if not _clear_provenance():
            raise
        conn = _open()  # 清掉属性后重试一次
    return conn


def is_done(accession: str) -> bool:
    """已翻译过的不再处理"""
    conn = get_conn()
    try:
        cur = conn.execute(
            "SELECT 1 FROM filings WHERE accession=? AND translated=1", (accession,)
        )
        return cur.fetchone() is not None
    finally:
        conn.close()


def attempts(accession: str) -> int:
    """已尝试次数，用于限制重试、避免反复消耗额度"""
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT attempts FROM filings WHERE accession=?", (accession,)
        ).fetchone()
        return (row["attempts"] or 0) if row else 0
    finally:
        conn.close()


def save(rec: dict):
    conn = get_conn()
    try:
        old = conn.execute(
            "SELECT attempts FROM filings WHERE accession=?", (rec["accession"],)
        ).fetchone()
        att = (old["attempts"] or 0 if old else 0) + 1
        conn.execute(
            """INSERT OR REPLACE INTO filings
               (accession,cik,ticker,company,form,items,filing_date,
                url,title,summary_zh,translated,attempts,created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                rec["accession"],
                rec.get("cik", ""),
                rec.get("ticker", ""),
                rec.get("company", ""),
                rec.get("form", ""),
                rec.get("items", ""),
                rec.get("filing_date", ""),
                rec.get("url", ""),
                rec.get("title", ""),
                rec.get("summary_zh", ""),
                1 if rec.get("summary_zh") else 0,
                att,
                datetime.now().isoformat(timespec="seconds"),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def recent(limit: int = 500):
    """取最近记录，供简报生成"""
    conn = get_conn()
    try:
        cur = conn.execute(
            """SELECT * FROM filings
               WHERE translated=1
               ORDER BY filing_date DESC, created_at DESC
               LIMIT ?""",
            (limit,),
        )
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def prune_tickers(keep) -> int:
    """删除不在监控名单里的公司记录

    从 config.yaml 的 watchlist 删掉一家 → 下次运行时它的历史公告一并清掉，
    数据库和网页都不会再出现（不用手动删库、不用改代码）。
    返回删除条数。
    """
    keep = {str(t).strip().upper() for t in keep if str(t).strip()}
    if not keep:
        return 0  # 名单为空多半是配置写错了，别把库清空
    conn = get_conn()
    try:
        drop = [
            (r["ticker"] or "").upper()
            for r in conn.execute("SELECT DISTINCT ticker FROM filings")
        ]
        drop = [t for t in drop if t and t not in keep]
        if not drop:
            return 0
        marks = ",".join("?" * len(drop))
        n = conn.execute(
            f"DELETE FROM filings WHERE UPPER(ticker) IN ({marks})", drop
        ).rowcount
        conn.commit()
        return n or 0
    finally:
        conn.close()


def stats():
    conn = get_conn()
    try:
        total = conn.execute("SELECT COUNT(*) c FROM filings").fetchone()["c"]
        done = conn.execute(
            "SELECT COUNT(*) c FROM filings WHERE translated=1"
        ).fetchone()["c"]
        return {"total": total, "translated": done}
    finally:
        conn.close()
