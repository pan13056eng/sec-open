# 美股公告情报系统（本地版）

自动监控你关注的美股公司 → 从 **SEC EDGAR 官方**抓公告 → AI 读英文原文后直接产出中文概括 → 展示在网页上。

> 只有三步：**抓取 → AI 概括 → 展示**。不翻译全文（全文既烧额度又没人看），
> AI 一次调用直接给中文「一句话概要 + 3-5 条要点」，想看细节点原文链接。
>
> 顶部还有一张 **美国宏观小表**（CPI 同比 / 核心 CPI / 失业率 / 非农新增，近 6 个月），
> 用来判断这些公告发生的利率与就业背景。
>
> 每天自动跑 4 次（每 6 小时）。

---

## 一、三步跑起来

### 1. 装依赖（用 uv，别用系统 Python）

```bash
cd ~/USStockIntel
uv python install 3.13          # 装一份独立 Python
uv venv .venv --python 3.13
uv pip install --python .venv/bin/python -r requirements.txt
```

> **为什么用 `uv` 而不是 `python3 -m venv`**：venv 的解释器是个指向外部 Python 的软链接，
> 外部那个一没，定时任务就直接 `No such file or directory` 静默死掉。这一天之内踩过两次：
> 一次是指向应用内置缓存目录（应用升级就没了），一次是 Homebrew 升级把 python 公式删了。
> `uv` 装的 Python 在 `~/.local/share/uv/python/`，不依赖 Homebrew 也不依赖任何应用缓存。

### 2. 改配置（config.yaml）

**必须改三处：**

| 项目 | 说明 |
|---|---|
| `sec.user_agent` | 填 `"你的名字 你的邮箱"`，**SEC 强制要求**，不填会被封 IP |
| `translate.glm.*` | 智谱 API 的 `base_url` / `model` / `api_key` |
| `watchlist` | 换成你自己关注的票（ticker + 10 位 CIK + 中文名） |

**设置 API key**：写在项目根目录的 `.env` 里（这个文件已被 `.gitignore` 排除，不会进 Git）：

```bash
cd ~/USStockIntel
echo 'GLM_API_KEY=你的智谱APIKey' > .env
```

`main.py` 启动时会自动读取它，所以 launchd 定时任务也拿得到（不需要改 `.zshrc`）。
`config.yaml` 里只留 `api_key_env: "GLM_API_KEY"` 这一行，不存明文。

> 拿 key：智谱开放平台 open.bigmodel.cn 注册 → 控制台 → API Keys
> 免费模型：`glm-4-flash`（完全免费，限并发 1）

**查 CIK**（10 位，不足补 0）：
- 单查：https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany
- 批量：https://www.sec.gov/files/company_tickers.json

### 增删公司（推荐用可视化页面）

**就在简报页里改**：`out/简报-日期.html` 顶部有「⚙ 增删监控公司」（默认折叠）。

- 服务没开 → 展开后显示「⚡ 一键开启」，**点一下**就唤起本机 App 起服务
  （首次点击系统可能弹「是否允许打开」，选允许）
- 服务开着 → 展开即载入管理面板，增删改查都在这一页完成，iframe 无边框、高度自适应

改完不用盯着 —— 下次定时抓取后自动出现在新简报里。
管理服务也可以单独启动（一般不用）：双击项目里的 `启动管理页.command`，或 `./.venv/bin/python manage.py`。

**代码怎么填？**

| 你输入的 | 页面识别为 | 说明 |
|---|---|---|
| `META` / `meta` | `META` | 最简单，直接填代码 |
| `NASDAQ: META` | `META` | 交易所前缀自动剥掉（NYSE / NYSEARCA / OTC 等同样） |
| `BRK.B` / `nyse: brk.b` / `BRK B` | `BRK-B` | 点、空格、横杠都会纠正成 SEC 官方写法 |
| 查不到的（如部分 ADR） | 手动填 10 位 CIK | 页面会给 SEC 公司搜索链接 |

**怎么确定填对了？** 添加前页面会真打一次 SEC，把**官方公司全名**（如 `Meta Platforms, Inc.`）、
交易所、行业、以及该公司最近的真实申报列出来 —— 名字对得上就是填对了。
抓取**只用 10 位 CIK** 定位，代码只是给你看的标签。

> 「最近 30 天命中 0 条」**不等于填错**，可能只是这段时间没发符合过滤条件的公告。
> 页面会额外列出该 CIK 最近的不限类型申报，用来确认公司没找错。
> BRK-A / BRK-B 这类 A/B 股共用一个 CIK（1067983）。

### 增删公司（手改 config.yaml 也行）

**时间范围**：只有 `sec.lookback_days` 一个开关，默认 30 天。
新增一家公司后，下次运行会**回溯它最近 30 天的公告**并逐条概括（已处理过的按 accession 去重）。
网页也只显示最近 30 天，与抓取窗口一致。

**删除一家公司**：从 `watchlist` 里删掉那一段就行，下次运行时会**自动清掉它的历史记录**。
日志会打印 `已移除公司清理：删除 N 条历史记录`。若想立刻生效，手动跑一次 `main.py`。

> 保护机制：`watchlist` 被清空（多半是配置写错了）时不会执行清理，避免把库洗白。

### 3. 手动跑一次

```bash
./.venv/bin/python main.py
```

跑完看 `out/简报-日期.html`（暗黑主题网页，双击就能看）或 `out/简报-日期.md`。

**网页上的三种标记：**

| 标记 | 含义 |
|---|---|
| 🟡 黄色 `NEW` | **公告日期是今天**的公告 |
| 🟢 绿色左边框 | **近三日**（今天及前两天）的公告 |
| 小角标 `↑3.6` | 这个数字被官方**上修/下修**过，角标是修订幅度 |

顶部可按关键词搜索；统计下方的「监控名单」可折叠，点公司名即筛选出它的公告。

---

## 二、文件都在哪（全部集中在 `~/USStockIntel/`）

```
~/USStockIntel/
├── main.py          主流程（抓取 → 过滤 → 去重 → 概括 → 出简报）
├── fetch_sec.py     SEC EDGAR 抓取 + 表单/item 过滤
├── translate.py     AI 概括（免费云端 API，可多家兜底）
├── brief.py         生成简报网页 + md（暗黑主题）
├── db.py            SQLite 去重（按 accession，避免重复调用 AI）
├── macro.py         顶部宏观小表（CPI / 就业，取 FRED）
├── manage.py        增删公司的可视化管理页
├── config.yaml      配置（监控名单、过滤规则、AI、宏观开关）
├── .env             本地 API key（不进 Git）
├── .github/workflows/daily.yml   可选的云端定时任务
├── data/intel.db    数据库（公告 + 中文概括）
├── data/macro.json  宏观数据缓存（取不到时顶上）
├── out/             生成的简报，只留最近 7 天
├── logs/run.log     运行日志
├── 美股公告管理.app   双击启动管理页
├── 启动管理页.command / 启用定时抓取.command
├── com.user.usstockintel.plist   定时任务配置
└── .venv/           Python 环境（uv 装的独立 Python）
```

两个「必须留在外面」的东西（系统规定）：

- `~/Library/LaunchAgents/com.user.usstockintel.plist` —— macOS 只认这个位置，里面指向上面的项目路径
- 桌面上只有一个**替身** `USStockIntel` 指向这个目录，方便打开

> ⚠️ **别把项目搬回桌面 / 下载 / 文稿**：macOS 会给这些目录的文件打 `com.apple.provenance` 标记，
> 打了标记的 SQLite 库读正常、一写就报 `disk I/O error`（清掉还会被重新打上）。
> 想换位置就放用户主目录下，或设环境变量 `USSTOCK_DB` 单独指定数据库路径。

---

## 三、装定时（每天 4 次）

**双击项目里的 `启用定时抓取.command`** 即可（改过 plist 后双击一次生效）。

plist 里已带 `RunAtLoad`：开机/重启后会**自动补跑一次**（前面 `sleep 20` 等网络就绪）。

```bash
launchctl list | grep usstockintel      # 确认已加载
tail -f logs/run.log                    # 看日志
```

> 06:05 那次是关键：覆盖美股盘后（北京时间凌晨 4-5 点收盘）的公告高峰。

---

## 四、抓什么（为什么这么设计）

你关心的是**回购注销**和**发债**，所以不是泛抓所有公告，而是两层过滤：

**第一层 · 表单类型**（`filters.form_types`）

| 表单 | 含义 |
|---|---|
| `8-K` | 重大事件临时报告（发债、回购、协议）★主力 |
| `424B2` / `424B5` / `FWP` | 债券/股票发行的定价补充文件 ★发债核心 |
| `SC TO-I` | 发行人要约回购 ★回购核心 |
| `10-Q` / `10-K` | 季报/年报，含**月度回购明细表** ★回购核心 |
| `6-K` / `20-F` | 外国发行人（台积电、SK 海力士这类）的临时报告与年报 |

**第二层 · 8-K 的 item 编号**（`filters.items_8k`）

| Item | 含义 |
|---|---|
| `2.03` | ★ 产生直接财务义务 → **举债/发债** |
| `1.01` | ★ 重大协议 → 债券契约 indenture、加速回购 ASR |
| `8.01` | ★ 其他重大事件 → 回购授权、注销股份 |
| `2.02` | 业绩公告（常附回购进展） |

> 已移除「融资/回购/营收」这类自动打标签的功能——关键词猜出来的标签不准，
> 判断依据交给 AI 的中文概要，你直接读结论即可。
> `S-3`/`S-3ASR`（货架注册）和 `3.02` 也已移除，常年挂着不代表真动作，纯噪音。

---

## 五、顶部宏观小表（CPI / 就业）

近 6 个月的四个数：**CPI 同比 · 核心 CPI 同比 · 失业率 · 非农新增（万人）**。

- 数据源：**FRED（圣路易斯联储）的公开 CSV**，免注册、免 API key，数字原始出自 BLS
- 一次请求拿全部序列（约 4 秒），取不到时用 `data/macro.json` 缓存顶上并标注「缓存」
- 开关：`config.yaml` 的 `macro.enabled` / `macro.months`

**关于官方修订**（重要）：

| 指标 | 会不会被改 | 说明 |
|---|---|---|
| CPI | **基本不会** | BLS 不修订 CPI 指数本身。这里用的是**未季调**序列（也是官方头条同比口径），几乎不受影响 |
| 非农 | **一定会** | 首次公布只回收部分问卷，之后两个月陆续修正；每年 2 月还有一次基准重算 |

每次刷新都会重拉全量重算，所以表里永远是**最新修订口径**。
数字被改过时，旁边会挂一个角标（如 `+21.4 万 ↑3.6`），表头提示「本次 N 项为官方修订值」——
你能一眼看出「这次刷新官方改了什么」。

> 想看「某个时点当时的数据」可以用 ALFRED（FRED 的归档版）：
> `https://alfred.stlouisfed.org/graph/alfredgraph.csv?id=PAYEMS&vintage_date=2026-04-15`

---

## 六、AI 引擎：免费 API 链

**只用免费云端 API，刻意不做本地模型兜底。**

> 早先版本有 Ollama 本地兜底，代价很实在：本地 8B 会吃满 CPU/GPU（打游戏时不能用），
> 在 512M 的低配 VPS 上更会直接撑爆内存、swap 抖动，最后被 OOM killer 杀掉代理进程，
> 整台机器假死。所以本地兜底已彻底删除——宁可这次不产概要，也不碰本机算力。

### 多家免费 API 自动兜底

主用接口挂了、没额度、没配 key，就自动按顺序试下一家。
在 `config.yaml` 的 `translate.providers` 里加一段即可（都是 OpenAI 兼容接口）：

```yaml
translate:
  primary: glm
  providers:
    - name: siliconflow
      base_url: "https://api.siliconflow.cn/v1"
      model: "Qwen/Qwen2.5-7B-Instruct"
      api_key_env: "SF_API_KEY"     # key 走环境变量，不写进配置文件
```

常见免费源（政策会变，以官网当时说明为准）：

| 平台 | 免费模型举例 | 备注 |
|---|---|---|
| 智谱 open.bigmodel.cn | `glm-4-flash` | 当前主用，国内直连 |
| 硅基流动 siliconflow.cn | `Qwen2.5-7B-Instruct` | 部分模型免费额度 |
| 阿里百炼（兼容模式） | `qwen-turbo` | 新用户免费额度 |
| 腾讯混元 | `hunyuan-turbo` | 有免费额度 |
| 火山方舟 | `doubao-lite-*` | 新用户免费额度 |
| OpenRouter | 模型名带 `:free` | 需国外网络 |

---

## 七、常见问题

**403 / 被 SEC 拒绝** → `config.yaml` 的 `user_agent` 没填或格式不对，必须是 `姓名 邮箱`。

**跑得慢** → GLM 免费档并发=1，是串行的。单条约 10 秒（只输出概括，比翻译全文快几倍）。
整体一轮（12 家公司）约 11 秒 + 宏观 4 秒，CPU 占用约 0.5 秒，峰值内存 78 MB。

**想看更长历史** → 改 `sec.lookback_days`（首次建议 30，之后靠 SQLite 自动去重）。

**噪音太多** → 把 `items_8k` 只保留 `2.03`（纯举债）和 `8.01`（回购授权/注销），
或从 `filters.form_types` 里删掉你不想看的表单类型。

**配额/网络异常** → 看 `logs/run.log`。AI 调用失败的条目不入库，下次会重试，不会丢数据。
同一条最多重试 3 次，之后放弃，避免反复烧额度。

**宏观数据没更新** → 表头会显示「缓存」二字，说明这次没取到、用的是上次的值。
取数失败不会影响简报生成。

**网站太亮/想换配色** → 改 `brief.py` 顶部的 `HTML_CSS`（`:root` 里的 `--bg / --panel / --fg` 等变量）。

**定时任务没在跑** → `launchctl list | grep usstockintel` 查不到就说明没装载，
双击一次 `启用定时抓取.command`。

---

## 八、可选：部署到 GitHub（零成本 + 手机能看）

本机跑有个前提：电脑得开着。想让它在云端自动跑、手机随时打开看，用 GitHub 免费方案。

**成本真的是 0**：SEC 不要钱、FRED 不要钱、GLM-4-Flash 免费额度，
GitHub Actions 私有仓每月 2000 分钟免费（本项目每月只用 30~40 分钟）。

### 已经准备好的东西

- `.github/workflows/daily.yml` —— 每 6 小时自动跑（UTC 4/10/16/22 点 = 北京 12/18/0/6 点）
- `.gitignore` —— 已经排除 `.env`、`.venv/`、`out/` 等
- **API key 已从 `config.yaml` 挪到 `.env`**（`.env` 不会被提交）
- `main.py` 启动时自动读 `.env`；CI 上直接用环境变量，没有 `.env` 也不影响
- `PUBLISH_MODE=1` 是「发布模式」：隐藏**监控名单**（名单等于持仓，别公开），
  同时去掉「增删监控公司」面板（线上访问不到你本机的服务，留着像是坏了）

### 部署步骤

1. **在 GitHub 建一个仓库**（公开或私有都行，见下方说明）

2. **把项目推上去**（`.env` 不会被推，放心）：
   ```bash
   cd ~/USStockIntel
   git init && git add -A
   git commit -m "init"
   git branch -M main
   git remote add origin git@github.com:你的用户名/仓库名.git
   git push -u origin main
   ```

3. **配 Secrets**：仓库 → Settings → Secrets and variables → Actions → New repository secret
   | 名称 | 值 |
   |---|---|
   | `GLM_API_KEY` | 你的智谱 key（`.env` 里那串） |
   | `SEC_USER_AGENT` | 可选。`"姓名 邮箱"`，不配就用 config.yaml 里那个 |

4. **开 Pages**：仓库 → Settings → Pages → Source 选 `Deploy from a branch`，
   Branch 选 `main`、目录选 `/docs`，保存。

5. **跑一次**：Actions 页面 → 「抓取美股公告」→ Run workflow。
   跑完 `https://你的用户名.github.io/仓库名/` 就是简报页，手机直接打开。

### 公开还是私有？

| | 公开仓库 | 私有仓库 |
|---|---|---|
| Actions 免费额度 | 无限 | 2000 分钟/月（够用） |
| **Pages 免费网址** | ✅ 支持 | ❌ 需要 GitHub Pro |
| 代码/名单曝光 | 会（`PUBLISH_MODE=1` 已遮名单） | 不会 |

**想用免费网址，就得是公开仓库。** 代码本身没什么可藏的，
敏感的是监控名单——workflow 里已经默认设了 `PUBLISH_MODE=1` 把它遮掉。

如果坚持要私有仓库又要网址，两条路：给 GitHub 升 Pro，或者改用 Cloudflare Pages /
Netlify 的免费档（都支持从私有仓部署，代价是多注册一个服务）。

### 云端跑的注意事项

- **去重靠 `data/intel.db`**，workflow 每次会把它和新简报一起提交回仓库。
  不提交的话每次都是全新环境，会把 30 天内的公告全部重新概括一遍（费额度但不要钱）。
- **cron 不准点**，高峰期可能延迟 5~30 分钟，本站无所谓。
- **60 天无提交会被自动禁用**：GitHub 官方规定，公开仓库的定时任务在
  「60 天没有任何仓库活动」时会被**静默禁用**（不报错，只是不再跑）。
  本项目有 12 家公司、通常每周都有公告，本来不太会触发；但为了保险，
  workflow 里加了**心跳机制**：`data/.last-run` 每 7 天至少更新一次，
  保证永远有活动记录。万一还是被禁用了，去 Actions 页面点一下
  「Enable workflow」就能恢复。
- 仓库开了分支保护的话，Actions 的 `git push` 会被拒——记得允许 bot 推送。
- 本机的 launchd 定时和云端可以同时存在，两边都用同一个 `data/intel.db` 就会打架。
  建议**二选一**：上云之后就把本机的 `~/Library/LaunchAgents/com.user.usstockintel.plist` 删掉。

---

## 九、注意

- 数据源为 SEC 官方公开数据 + FRED 公开数据，**免费、无需 key**
- 概要由 AI 生成，涉及具体金额、条款、到期日等关键决策，**请回看原文链接核对**
- 本工具只做信息聚合与概括，不含任何投资建议
