"""SEC EDGAR 抓取层

精准定位「回购注销 / 发债」类官方公告：
  1. 按 form 类型过滤（8-K / 424B2 / FWP / SC TO-I / 10-Q / 10-K ...）
  2. 8-K 再按 item 编号过滤（2.03 举债 / 1.01 重大协议 / 8.01 回购授权 ...）
  3. 正文关键词二次确认（避免噪音）
"""
import re
import time
from datetime import date, timedelta

import requests
from bs4 import BeautifulSoup

API = "https://data.sec.gov"
ARCH = "https://www.sec.gov/Archives/edgar/data"

# 10-Q / 10-K 里回购明细表所在的小节
BUYBACK_ANCHOR = "Issuer Purchases of Equity Securities"
# 没有回购表时退而求其次：找真正带数字的章节。
# 不能退回「取正文开头」——SEC 的 10-Q 是 inline XBRL，开头一万多字全是
# 给机器看的元数据（us-gaap:xxx、context 日期），拿去问 AI 只会得到 XX 占位符。
FALLBACK_ANCHORS = (
    "Item 2. Management's Discussion and Analysis",
    "Management's Discussion and Analysis",
    "Condensed Consolidated Statements of Operations",
    "Consolidated Statements of Operations",
)


class SecClient:
    def __init__(self, user_agent: str, delay: float = 0.15):
        if not user_agent or "@" not in user_agent:
            raise ValueError(
                "SEC 强制要求 User-Agent（格式 '姓名 邮箱'），请在 config.yaml 的 sec.user_agent 填写"
            )
        self.ua = user_agent
        self.delay = delay
        self.s = requests.Session()
        self.s.headers.update(
            {
                "User-Agent": self.ua,
                "Accept-Encoding": "gzip, deflate",
                "Accept": "application/json, text/html;q=0.9,*/*;q=0.8",
            }
        )

    # ---------- 基础请求（带限速） ----------
    def _get(self, url, **kw):
        time.sleep(self.delay)  # 官方限速 ≤10 req/s
        r = self.s.get(url, timeout=30, **kw)
        r.raise_for_status()
        return r

    def submissions(self, cik: str) -> dict:
        return self._get(f"{API}/submissions/CIK{cik}.json").json()

    def doc_url(self, cik: str, accession: str, primary_doc: str) -> str:
        acc = accession.replace("-", "")
        return f"{ARCH}/{int(cik)}/{acc}/{primary_doc}"

    def doc_text(self, url: str) -> str:
        return self._html_to_text(self._get(url).text)

    @staticmethod
    def _html_to_text(html: str) -> str:
        soup = BeautifulSoup(html, "html.parser")
        # ix:header / ix:hidden 是 inline XBRL 给机器看的元数据，必须剥掉：
        # 不剥的话它们会排在正文最前面，被当成「正文开头」抓走。
        for t in soup(["script", "style", "ix:header", "ix:hidden"]):
            t.decompose()
        text = soup.get_text("\n")
        text = re.sub(r"\n{3,}", "\n\n", text)
        text = re.sub(r"[ \t]{2,}", " ", text)
        return text.strip()


# ---------- 筛选 ----------
def pick_filings(sub: dict, form_types, items_8k, lookback_days: int):
    """从 submissions 里挑出符合条件的新公告"""
    data = (sub.get("filings") or {}).get("recent") or sub.get("recent")
    if not data:
        return []

    cutoff = (date.today() - timedelta(days=lookback_days)).isoformat()
    forms = data.get("form", [])
    dates = data.get("filingDate", [])
    accs = data.get("accessionNumber", [])
    docs = data.get("primaryDocument", [])
    items_list = data.get("items", [])

    out = []
    for i, form in enumerate(forms):
        fdate = dates[i] if i < len(dates) else ""
        if fdate < cutoff:
            continue
        if form not in form_types:
            continue

        items = items_list[i] if i < len(items_list) else ""
        if form == "8-K" and items_8k:
            have = [x.strip() for x in (items or "").split(",") if x.strip()]
            if have and not any(h in have for h in items_8k):
                continue

        out.append(
            {
                "form": form,
                "filing_date": fdate,
                "accession": accs[i] if i < len(accs) else "",
                "primary_document": docs[i] if i < len(docs) else "",
                "items": items or "",
            }
        )
    return out


# ---------- 正文处理 ----------
def _locate(text: str, low: str, phrase: str) -> int:
    """定位「真正的那一节」，返回起始下标；找不到返回 -1。

    不能只取第一次出现：目录页里会列出所有小节标题，第一次命中往往是目录。
    两者的区别很稳定——目录条目的标题后面紧跟页码（`... / 23 / Item 3.`），
    而真正的章节标题后面接的是正文。所以：跳过「标题后 60 字内出现数字」的位置，
    取第一个剩下的。全都像目录时才退回第一个。兼容直引号 ' 和排版弯引号 ’。
    """
    positions = []
    for variant in (phrase, phrase.replace("'", "\u2019")):
        p = variant.lower()
        start = 0
        while True:
            i = low.find(p, start)
            if i < 0:
                break
            positions.append((i, len(p)))
            start = i + 1
    if not positions:
        return -1
    positions.sort()
    for i, plen in positions:
        tail = text[i + plen : i + plen + 60]
        if not any(ch.isdigit() for ch in tail):
            return i
    return positions[0][0]


def extract_relevant(text: str, form: str, max_chars: int) -> str:
    """10-Q/10-K 优先取回购表附近；没有就退到 MD&A / 利润表；都没有才取开头。

    关键是不要轻易退回「取正文开头」——那一段很可能是 XBRL 元数据残留，
    不含任何财务数字，AI 拿到后只能编造并用 XX 占位。
    """
    if form in ("10-Q", "10-K"):
        low = text.lower()
        i = _locate(text, low, BUYBACK_ANCHOR)
        if i >= 0:  # 回购表前后都要留一点上下文
            return text[max(0, i - 1500) : i + 6000][:max_chars]
        for anchor in FALLBACK_ANCHORS:
            i = _locate(text, low, anchor)
            if i >= 0:
                return text[i : i + max_chars]
    return text[:max_chars]
