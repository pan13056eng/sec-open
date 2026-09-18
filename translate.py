"""AI 层：读英文公告 → 直接输出中文概括（提炼 + 翻译一步完成）

不翻译全文，只产出「一句话概要 + 3-5 条要点」。
GLM 免费 API 主 + 多家 OpenAI 兼容免费源兜底。

【刻意不做本地模型兜底】早先版本有 Ollama 本地兜底，但它会吃满 CPU/GPU
（打游戏时不能用），更糟的是在 512M 的低配 VPS 上直接把内存撑爆、swap 抖动，
被 OOM killer 杀掉代理进程，整台机器假死。所以本地兜底已彻底移除，
宁可这次不出概要，也不碰本机/服务器的算力。

免费档并发=1，所以串行调用 + 间隔。
"""
import os
import re
import time

from openai import OpenAI

SYSTEM = """你是资深美股公告分析师，专精美国上市公司公告（SEC filing）。

任务：读英文公告，直接用简体中文输出概括（提炼与翻译一步完成，不翻译全文）。

术语必须统一（不要意译发挥）：
- notes / senior notes → 优先票据 / 高级票据；convertible notes → 可转债
- indenture → 债券契约；prospectus supplement → 招股说明书补充文件
- aggregate principal amount → 本金总额；maturity → 到期；coupon → 票息
- repurchase / buyback → 回购；repurchase program → 回购计划
- accelerated share repurchase (ASR) → 加速股份回购
- tender offer → 要约收购/要约回购
- retire / cancel / cancellation of shares → 注销股份
- revolving credit facility → 循环信贷额度；term loan → 定期贷款
- underwriter → 承销商；net proceeds → 募集资金净额

硬性要求：
1. 所有数字、金额、股数、百分比、日期、利率、代码必须与原文完全一致，不得换算或四舍五入
2. 不臆测、不补充原文没有的信息；原文含糊就照实说"原文未披露"
3. 语气客观，不点评、不给投资建议
4. 只输出中文概括，不要输出正文译文，也不要保留英文原文段落
5. 必须严格按用户指定的结构输出，标题文字一字不改
6. 【禁止编造数字】绝对不要自己造一个数字，也不要用 XX、xxx、???、N/A 这类
   无意义符号去顶替数字位置——那看起来像乱码，等于没写。
7. 如果原文这一项是**留空**的（常见于标注 subject to completion 的
   preliminary prospectus：金额、利率、到期日都还没填），就照实说
   "发行条款尚未确定，原文留空"。这是正确写法，比写 XX 有用得多。
   能确定的先写确定的，缺的那项单独说明缺什么。"""

# 只概括，不翻译全文：
#   公告正文动辄上万字，全量直译既烧额度又没人看。
#   一次调用直接产出中文「概要 + 要点」，链路最短。
USER_TPL = """请阅读这份英文公告，直接用中文概括（不要翻译全文）。

公司：{ticker} {company}
文件类型：{form}{items}
公告日期：{filing_date}

【输出结构】严格两段，标题文字照抄

【一句话概要】
一行，不超过 45 字：谁 + 做了什么 + 关键金额/股数/时间。只写结论，不要铺垫。

【要点摘要】
3-5 条，每条一行，每条不超过 35 字。只写有信息量的硬事实：
发生了什么 / 金额或股数 / 价格或票息 / 期限 / 对股东的影响。
不要复述概要，不要写套话。

【公告原文】
{text}
"""


class Translator:
    """免费云端 API 为主，多家按顺序兜底"""

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.primary = cfg.get("primary", "glm")
        self.providers = [
            p for p in (cfg.get("providers") or []) if p.get("enabled", True)
        ]
        self.delay = cfg.get("request_delay", 1.0)
        self.timeout = cfg.get("timeout", 180)
        self.max_chars = cfg.get("max_chars", 12000)
        # 统一索引：glm / 各家 provider
        self._specs = {}
        if cfg.get("glm"):
            self._specs["glm"] = cfg["glm"]
        for p in self.providers:
            if p.get("name"):
                self._specs[p["name"]] = p
        # 输出额度给足，否则长公告译文会被截断
        self.max_tokens = cfg.get("max_tokens", 4000)
        # thinking_level: none(不传，推荐) / low / high / max
        # ⚠️ glm-5.x 是「始终思考」模型，不能关闭（传 disabled 会 400），只能给 low 降低思考量
        self.thinking_level = (cfg.get("thinking_level") or "none").lower()

    def _opt(self, kind: str, key: str, default=None):
        return (self._specs.get(kind) or {}).get(key, default)

    def _api_key(self, kind: str) -> str:
        """优先用配置里直接填的 key，其次读环境变量"""
        c = self._specs.get(kind) or {}
        return c.get("api_key") or os.environ.get(
            c.get("api_key_env", "GLM_API_KEY" if kind == "glm" else ""), ""
        )

    def _client(self, kind: str):
        c = self._specs.get(kind)
        if not c:
            return None, None
        base, model = c.get("base_url"), c.get("model")
        if not base or not model:
            return None, None
        key = self._api_key(kind)
        if not key:
            return None, None  # 没配 key 的接口直接跳过
        return (
            OpenAI(
                api_key=key,
                base_url=base,
                timeout=self._opt(kind, "timeout", self.timeout),
                max_retries=0,
            ),
            model,
        )

    def _call(self, kind: str, client, model: str, text: str, meta: dict) -> str:
        items = f"（Item {meta['items']}）" if meta.get("items") else ""
        user = USER_TPL.format(
            ticker=meta.get("ticker", ""),
            company=meta.get("company", ""),
            form=meta.get("form", ""),
            items=items,
            filing_date=meta.get("filing_date", ""),
            text=text[: self.max_chars],
        )
        level = (self._opt(kind, "thinking_level", self.thinking_level) or "none").lower()
        kwargs = dict(
            model=model,
            messages=[
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": user},
            ],
            temperature=0.2,
            max_tokens=self._opt(kind, "max_tokens", self.max_tokens),
        )
        if level and level != "none":
            kwargs["extra_body"] = {"thinking": {"type": level}}
        try:
            resp = client.chat.completions.create(**kwargs)
        except Exception:  # noqa: BLE001
            # 模型不支持 thinking 参数（如 glm-4-flash），去掉重试
            kwargs.pop("extra_body", None)
            resp = client.chat.completions.create(**kwargs)
        return (resp.choices[0].message.content or "").strip()

    def order(self):
        """翻译引擎尝试顺序：主用 → 其它备用免费 API"""
        out = [self.primary] if self.primary else []
        for p in self.providers:
            n = p.get("name")
            if n and n not in out:
                out.append(n)
        return out

    def translate(self, text: str, meta: dict):
        """返回 (译文, 使用的引擎)；全部失败返回 (None, None)"""
        order = self.order()

        for kind in order:
            try:
                client, model = self._client(kind)
                if client is None:
                    print(f"  [翻译] {kind} 未配置 API key / 未启用，跳过")
                    continue
                out = self._call(kind, client, model, text, meta)
                time.sleep(self.delay)
                if out:
                    if re.search(r"[Xx]{2,}", out):
                        print(
                            f"  [翻译] {kind} 输出里出现 XX 占位符"
                            "（多半是原文该项留空），建议人工看一眼原文"
                        )
                    return out, kind
            except Exception as e:  # noqa: BLE001
                print(f"  [翻译] {kind} 失败：{type(e).__name__}: {e}")
                continue
        return None, None
