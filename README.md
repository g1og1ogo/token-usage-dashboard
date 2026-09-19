# token-usage-dashboard · WorkBuddy / CodeBuddy Token 消耗看板

> 扫描本机会话日志，把 AI 编码助手的 token 花销摊开成一张**单文件离线 HTML 看板**：
> 钱花在哪条业务线、缓存命中省了多少、思考 token 占多高、哪些会话该换便宜模型。
> 零第三方依赖 · 只读 · 无 CDN · 双击即开 · 可直接打印。

**给谁用**：每天用 WorkBuddy / CodeBuddy / Claude Code / Codex 超过两小时、开始关心"这个月烧了多少、烧在哪"的人；需要向团队或领导交一份 AI 投入产出底稿的人；在决定要不要切换低价模型之前想先看数据的人。

**关键词**：WorkBuddy token 看板 · CodeBuddy 用量统计 · Claude Code token 用量 · AI 编码助手成本分析 · token 消耗归因 · 缓存命中率 · 离线 HTML 报表 · 零依赖 Python

```
┌─ Token 消耗看板 · 2026-09（示意图，非真实数据）──────────┐
│ 总 token 48.7M   日均 1.6M   缓存命中 71.3%           │
│ 思考 token 占比 12.4%   工具调用 2,183 次              │
│                                                        │
│ 业务主线 TOP                 占比   思考占比 可优化     │
│ ① 客户尽调与合同评审         31.2%   24.1%  保留        │
│ ② 调研写作                   18.7%    8.3%  可降档      │
│ ③ 代码重构                   12.1%   19.6%  保留        │
│ ④ 台账与数据整理              6.4%    1.2%  → 小模型    │
└────────────────────────────────────────────────────────┘
```

## 它回答三个问题

| 问题 | 别的看板给的 | 这里给的 |
|---|---|---|
| **钱花在哪条业务线** | 一个总数、一个按天曲线 | 会话级加权投票做**业务主线归因**，输出每条主线的 token、会话数、思考占比 |
| **花得值不值** | 只有 token 数 | 拆出**缓存命中率**与**思考模式（reasoning）token 占比**，区分"流程搬运型"与"深度调研型"消耗 |
| **哪里能省** | 不提 | **集中度告警**、低投入主线告警、可切换低价模型的候选主线清单 |

## 为什么不直接用现成的 token 统计工具

| 维度 | 常见通用方案 | 本技能 |
|---|---|---|
| 价格口径 | 内置一张模型价目表去估算金额 | **不内置任何单价**：不传 `--price-config` 就完全不输出金额，绝不给你一个来路不明的价格 |
| 归因 | 按项目目录 / 会话分组 | **按业务主线归因**，且提供"抽验 3–5 个会话核对"的强制步骤，把启发式结果变成可信结论 |
| 质量维度 | 只数 token | 缓存命中 / 思考 token / 工具调用三类分布同时给 |
| 对外分享 | 无处理 | `--hide-intent` 一键清空用户原话（本机实测日志里首条发言可能含身份证号、统一社会信用代码） |
| 改脚本 | 需读源码猜 | 附 `references/pitfalls.md`，把实测踩过的坑（usage 字段多形态、`<user_query>` 藏在 system-reminder 尾部、缓存 token 藏在 details 数组）写死成硬约束 |

> 生态里同时存在其他 WorkBuddy token 看板项目（例如 [`fuyi-git/token-dashboard`](https://github.com/fuyi-git/token-dashboard)），
> 差异主要在**价格口径与归因深度**上——本技能选择"不猜价格、按业务线归因、并强制抽验"。选哪个取决于你想要"一个总数"还是"一份可拿去汇报的底稿"。

## 安装

```bash
# 放进用户级技能目录（三选一）
git clone https://github.com/g1og1ogo/token-usage-dashboard.git ~/.workbuddy/skills/token-usage-dashboard   # GitHub（主仓库）
git clone https://gitee.com/myworkbuddy/token-usage-dashboard.git  ~/.workbuddy/skills/token-usage-dashboard  # Gitee（只读镜像）
cp -r token-usage-dashboard ~/.workbuddy/skills/                                                              # 已下载的目录
```

> **主仓库在 GitHub，Gitee 是只读镜像**：Issue / PR / Release 只发生在 GitHub 一侧；Gitee 只为国内 clone 提速，不提 PR。
> 同一提交的 tree SHA 两边逐一相同，从哪个 clone 都是同一份内容——差异只存在于两次同步之间的时间窗。

环境要求：**Python 3.9+，无任何第三方依赖**（不需要 `pip install`）。WorkBuddy 自带的托管解释器即可：

```bash
~/.workbuddy/binaries/python/envs/default/Scripts/python.exe   # Windows
~/.workbuddy/binaries/python/envs/default/bin/python           # macOS / Linux
```

> 技能清单在会话启动时注入，**装完需重开会话才生效**。然后直接说"看看我这段时间 token 花在哪了"即可触发。

## 快速开始

```bash
cd ~/.workbuddy/skills/token-usage-dashboard

# 1) 自检：用内置假数据验证渲染链路，不碰真实数据
python scripts/dashboard.py --self-test -o ./_selftest.html

# 2) 把业务词典模板复制到技能目录之外，按你自己的业务线改写
cp references/biz_rules.example.json ~/.token-dashboard/biz_rules.json

# 3) 生成真实看板
python scripts/dashboard.py \
    --biz-config ~/.token-dashboard/biz_rules.json \
    --json-out   ./dashboard.json \
    -o           ./token-dashboard.html
```

> **词典质量决定归因准不准**：写「专有名词 + 产物文件名词根」，别放"公司""报告""方案"这类通用词。
> 词典必须放在技能目录**之外**，`references/` 下的模板保持中性——否则一份业务布局清单就跟着技能公开了。

## 数据来源

| 数据源 | 内容 |
|---|---|
| `~/.workbuddy/projects/**/*.jsonl` | 请求级 usage（输入 / 输出 / 缓存 / 思考 token） |
| `~/.workbuddy/audit-log/*.jsonl` | 工具调用类别分布 |

**全程只读**，除你指定的输出文件外不改动任何数据；不联网。

## 主要参数

| 参数 | 说明 |
|---|---|
| `-o, --out` | 输出 HTML 路径（默认当前目录 `token-dashboard.html`） |
| `--json-out` | 同时导出结构化 JSON，便于二次分析或跨期对比 |
| `--biz-config` | 业务主线词典 JSON |
| `--price-config` | 模型单价 JSON；**不传则不输出任何金额** |
| `--since / --until` | 按 `YYYY-MM-DD` 限定区间，做周报 / 月报 |
| `--theme light\|dark` | 默认 light，可直接打印 |
| `--hide-intent` | **对外分享前务必开启**，隐藏 Top 会话表里的用户原话 |
| `--title` | 看板标题，例如 `--title "Token 消耗看板 · 2026-09"` |
| `--top-sessions N` | 高消耗会话显示条数（默认 10） |
| `--top N` | 业务主线只显示前 N 条（0 = 全部） |
| `--no-audit` | 跳过工具调用面板 |
| `--projects-root` / `--audit-dir` | 覆盖默认日志目录（换宿主或做归档分析时用） |
| `--include-root` | 一并扫描 projects 根目录下的 jsonl |
| `--quiet` | 只在结尾输出成品路径，适合挂进脚本 |
| `--self-test` | 内置假数据自检 |

**归因与告警阈值全部可调**（默认值取自实测调优）：

| 参数 | 默认 | 作用 |
|---|---|---|
| `--filename-weight` | 1.0 | 文件名命中权重（强信号） |
| `--kw-weight` | 0.3 | 关键词命中权重（弱信号） |
| `--user-boost` | 1.8 | 用户消息命中的倍数 |
| `--min-score` | 0.6 | 会话归因的最低得分，低于则记「未归因」 |
| `--text-limit` | 4000 | 单条消息参与归因的文本长度上限 |
| `--concentration-threshold` | 40.0 | 集中度告警阈值（%） |
| `--low-input-threshold` | 30000 | 低投入业务线告警阈值（token） |
| `--coverage-threshold` | 60.0 | 归因覆盖率告警阈值（%） |
| `--reason-threshold` | 20.0 | 思考占比关注阈值（%） |

## 常见问题

**Q：会把我本地的日志或业务信息传出去吗？**
不会。脚本只用 Python 标准库，不联网，只读你的日志文件，产出写在你指定的路径。唯一的风险点是**产物本身**——见下面的边界表。

**Q：为什么不直接内置模型价格？**
因为内置价格必然过期、且与你实际计费口径（积分倍率、合同价）不一致，最后会得到一张"看起来精确但其实错"的账单。宁可不给金额，也不给错金额。

**Q：归因覆盖率只有 40%、或者一堆"未归因"怎么办？**
按 `补词典 → 调 --kw-weight → 调 --min-score` 的顺序调。别直接改脚本。

**Q：换机器 / 换版本后统计数字变小了？**
先读 `references/pitfalls.md`：`<user_query>` 藏在 system-reminder 尾部、缓存与思考 token 藏在 `details` 数组里，这两处最容易把统计做漏。

**Q：支持 Claude Code / Codex 的日志格式吗？**
当前默认路径是 WorkBuddy / CodeBuddy 的 `~/.workbuddy/`。其他宿主的目录结构不同，用 `--projects-root` / `--audit-dir` 指过去、并按需调整解析分支即可，欢迎提 PR。

## 隐私：发布与分享的边界

**技能本身不含任何本机数据，但词典和产物会。**

| 对象 | 能否公开 |
|---|---|
| `scripts/dashboard.py` | 可以 |
| `SKILL.md` / `references/pitfalls.md` | 可以 |
| `references/biz_rules.example.json` | **不可以**（一旦填入你的真实主线，就是一份业务布局清单） |
| `references/price.example.json` | 视情况（换成真实合同价后即等于公开采购条件） |
| 生成的看板 HTML / JSON | **绝对不可以**（含真实消耗数据与用户原话） |

实测本机日志里，用户首条发言可能包含自然人姓名、18 位身份证号、企业统一社会信用代码。因此 Top 会话表对外展示前请加 `--hide-intent`。

仓库已内置 `.gitignore` 兜底：`*.html` 与 `*.json` 默认忽略，只放行 `references/` 下的两个模板。推送前请跑一键自检，别靠肉眼：

```bash
bash scripts/check_private.sh .     # 干净则输出「可以发布」并以 0 退出
```

扫五类高危项：本机用户名路径、业务专有名词、PII 与凭据（邮箱 / 手机 / 身份证 / 信用代码 / 私钥 / AK·SK / `ghp_` token）、本地工作路径、未清理产物。

> **第 5 类（未清理产物）的判定有三个边界**：
> ① 非 git 仓库时「入库」无判定语义，只列候选、不影响退出码；
> ② 随技能发布的模板/示例（如中性 HTML）可在仓库根目录建 `.privacy-allow`（一行一个相对路径或 glob）显式豁免；
> ③ 机器上找不到 `git` 命令时（未安装 / 不在 PATH）无法判定会否入库——脚本按**未通过**处理并告警，可设 `GIT=/path/to/git` 覆盖。原则：**无法判定 ≠ 通过**。

> **两张词表刻意不放在脚本里。** 业务专名与本机路径都写到仓库**之外**：
>
> ```bash
> mkdir -p ~/.token-dashboard
> cp references/biz_names.example.txt ~/.token-dashboard/biz_names.txt      # 一行一个业务专名
> cp references/biz_names.example.txt ~/.token-dashboard/local_paths.txt    # 一行一个路径特征
> ```
>
> 原因是 `check_private.sh` 本身会跟着技能一起公开——**把一个真实客户名清单写进一个公开脚本，
> 等于用防泄漏的工具制造泄漏**。词表缺失时脚本会 `[SKIP]` 并提示路径，不会静默放过。

## 相关项目

- [**project-interrogation**](https://github.com/g1og1ogo/project-interrogation) —— 同作者的立项反问技能：在新项目启动前用六问把"需求是不是真的"问到有证据为止。

## License

MIT，见 [`LICENSE`](LICENSE)。

> 注意：Git 的 commit 作者邮箱会永久留在公开历史中，建议在目标仓库单独设置身份，或使用 GitHub 提供的 noreply 邮箱（`<id>+<login>@users.noreply.github.com`）。

---

<details>
<summary><b>English</b></summary>

**token-usage-dashboard** — a Skill for WorkBuddy / CodeBuddy / Claude Code that turns local session logs into a single-file, offline HTML dashboard of token spend. It attributes cost to *business lines* (not just directories), breaks out cache-hit and reasoning-token ratios, flags concentration risk, and lists which lines could move to a cheaper model.

Zero third-party dependencies (Python stdlib only), read-only, no CDN, no network. Ships no built-in price table on purpose: if you do not pass `--price-config`, no monetary column is rendered at all. A `--hide-intent` flag strips raw user utterances before any dashboard is shared externally.

Install: `git clone <repo> ~/.workbuddy/skills/token-usage-dashboard`. The primary repo is GitHub; Gitee is a read-only mirror (no PRs there).
</details>
