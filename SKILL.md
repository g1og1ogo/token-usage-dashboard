---
name: token-usage-dashboard
description: 从本机 WorkBuddy/CodeBuddy 会话日志（~/.workbuddy/projects/**/*.jsonl + audit-log）统计 Token 消耗，生成单文件离线 HTML 看板——按业务主线归因、拆分思考模式 token 与缓存命中、给出工具调用分布与可参考的降本结论。适用于用户提出"看看我这段时间 token 花在哪了""统计一下 token 消耗""各条业务线分别用了多少""生成 token 看板/用量报表/消耗分析"等需求，也适用于需要按时间区间、按业务线复盘 AI 投入产出，或在决定是否切换低价模型前做依据核查的场景。内置 usage 字段多形态兼容、user_query 意图提取、业务归因投票与 PII 脱敏开关，并固化了 system-reminder 注入、统计静默变少等实测硬约束。
agent_created: true
---

# Token 消耗看板

## 用途

把散在会话日志里的请求级 usage，读成一张能回答三个问题的看板：

1. **钱花在哪条业务线**（业务主线归因，而不是只给一个总数）
2. **花得值不值**（缓存命中率、思考模式占比、流程搬运型 vs 深度调研型）
3. **哪里能省**（集中度告警、低投入主线、可切换低价模型的主线）

产出单文件离线 HTML（无 CDN、无外部字体），双击即开、可直接打印。同时可导出结构化 JSON 供二次分析或跨期对比。

## 何时使用

- 「看看这段时间 token 花了多少 / 花在哪」
- 「各条业务线分别消耗了多少」
- 「要不要换便宜点的模型」——用「思考占比 / 流程搬运型」两列做依据，而不是凭感觉
- 需要向团队或领导说明 AI 使用投入时的**数据底稿**
- 需要按周/按月复盘 AI 投入产出

## 与环境相关的前提

- 数据源默认：`~/.workbuddy/projects/**/*.jsonl`（请求级 usage）与 `~/.workbuddy/audit-log/*.jsonl`（工具调用类别）
- Python 只用标准库，**无需 pip install**。用托管环境解释器即可，形如
  `~/.workbuddy/binaries/python/envs/default/Scripts/python.exe`（Windows 下把 `~` 换成你的用户目录）
- 全程只读；除输出文件外不改动任何数据

## 工作流

设技能目录 `<SKILL>/`，Python 为 `<PYTHON>`。

### 第 0 步：确认有没有合适的业务词典

这一步决定看板有没有价值，**不要跳过**。

- 先看用户过去有没有整理过自己的业务线关键词表，有就直接用（通常放在技能目录之外，运行时 `--biz-config` 指定）
- 若手头没有，复制 `references/biz_rules.example.json` 到技能目录**之外**再改写；
  **不要就地改 `<SKILL>/references/` 里的模板**——那份随时可能被连同技能一起对外发布，
  见下面「公开发布前的脱敏检查」
- 把用户的业务线写成「专有名词 + 产物文件名词根」，通用词（「公司」「报告」「方案」）一律不放

词典质量直接决定归因准不准：写得好，抽验即对；写得糙，剩下全是噪声。

### 第 1 步：跑一次自检

```bash
<PYTHON> <SKILL>/scripts/dashboard.py --self-test -o "<WORK>/_selftest.html"
```

不碰真实数据，用内置假数据验证渲染链路。这一步失败就先别往下走。

### 第 2 步：生成真实看板

```bash
<PYTHON> <SKILL>/scripts/dashboard.py \
    --biz-config "<WORK>/biz_rules.json" \
    --json-out "<WORK>/dashboard.json" \
    -o "<成品路径>.html" \
    --title "Token 消耗看板 · <期间>" \
    --top-sessions 12
```

常用开关：

| 参数 | 用途 |
|---|---|
| `--since / --until` | 按 YYYY-MM-DD 限定区间，做周报/月报 |
| `--theme dark` | 大屏盯盘；**交付一律用默认 light**（可打印） |
| `--price-config` | 传入用户自己的单价表后才会出现「估算成本」列 |
| `--hide-intent` | **对外交付必开**，见下 |
| `--no-audit` | 跳过工具调用面板 |
| `--top N` | 业务主线只显示前 N 条 |

### 第 3 步：核对数字，别急着交付

用导出的 JSON 抽验 3–5 个会话，确认归因与用户实际在做的事对得上。
**归因是启发式投票，覆盖率 95% 不代表判对了**——这一步是唯一能把启发式结果变成可信结论的手段。

看不对时的调参顺序：`补词典 → 调 --kw-weight → 调 --min-score`。

### 第 4 步：外发前过一遍隐私开关

Top 会话表默认展示的是用户原话。实测本机数据里包含自然人姓名、18 位身份证号、
企业统一社会信用代码、内部绝对路径。

- **自用**：保持默认，要看的就是自己在做什么
- **任何对外交付（发群 / 邮件 / 贴进报告 / 截图外传）**：加 `--hide-intent`

该开关同时清空 JSON 导出里的 `first_msg`，避免从另一条路径泄漏。

## 公开发布前的脱敏检查

技能本身不含任何本机数据，**但词典和输出产物会**。把技能发到 GitHub / Gitee / 发给别人之前，
依次确认：

| 对象 | 能否公开 | 说明 |
|---|---|---|
| `scripts/dashboard.py` | 可 | 无个人信息、无硬编码用户路径 |
| `SKILL.md`、`references/pitfalls.md` | 可 | 已确认不含具体项目名与 PII |
| `references/price.example.json` | 视内容 | 若已替换成**你的真实合同价**，等于公开采购条件 |
| `references/biz_rules.example.json` | **不可** | 一旦填入你的真实主线，就是一份**业务布局清单** |
| 生成的看板 HTML / JSON | **绝对不可** | 含真实消耗数据与用户原话（见 pitfalls 第 8 条） |
| 早期一次性脚本（如 `token_dashboard_v2.py`） | **绝对不可** | 词典与路径通常是写死在里面的 |

**最常见的翻车方式**：为了省事直接把自己的词典写进 `references/biz_rules.example.json`，
然后整个技能推上去。词典里的每个关键词——客户名、尽调对象、交易对手方、未公开项目代号——
都会等于对外公告你在做什么。

正确做法：自己的词典放在技能目录**之外**（如 `~/.token-dashboard/biz_rules.json`），
运行时用 `--biz-config` 指向它；`references/` 下的同名文件始终保持中性模板。

推送前最后核一遍——**别手敲，直接跑脚本**：

```bash
bash scripts/check_private.sh .        # 干净会输出「可以发布」并以 0 退出
```

它会扫五类：本机用户名路径、业务专有名词、PII/凭据（邮箱、手机、身份证、信用代码、私钥、AK/SK、ghp_ token）、
本地工作路径、未清理的运行产物。命中会列出文件名与行内容，退出码非 0。

**业务专名与本机路径两张词表不在脚本里**，默认从仓库之外读取：

```
~/.token-dashboard/biz_names.txt      一行一个业务专名（客户、对手方、项目代号）
~/.token-dashboard/local_paths.txt    一行一个本机路径特征
```

模板见 `references/biz_names.example.txt`；可用 `BIZ_NAMES_FILE` / `LOCAL_PATHS_FILE` 覆盖路径。
**不要把这些词写回 `check_private.sh`**——那个脚本本身会随技能公开，写进去就是用防泄漏的工具制造泄漏。
词表缺失时脚本打印 `[SKIP]` 并给出创建命令，不会静默放过。

想手工核也行：

```bash
grep -rn -E "你的项目名|客户名|对手方" .     # 应无命中
grep -rn -o -E "[A-Za-z]:[\\/]Users[\\/][A-Za-z0-9_.-]+" .   # 应无命中
git status --porcelain                        # 确认没有把 HTML/JSON 产物加进来
```

另外：**推送到 GitHub 时不要用 `--global` 里写死的身份去判断**，commit 作者邮箱会永久留在公开历史里，
建议在目标仓库里单独设 `git config user.name/user.email`，或用 GitHub 提供的 noreply 邮箱。

## 关键约束

改造脚本或调试输出异常时，**先读 `references/pitfalls.md`**。

其中每条都来自本机真数据的实测：`<user_query>` 藏在 system-reminder 尾部、
用 `--text-limit` 去取用户意图会导致 59 个会话只剩 1 条、缓存与思考 token 藏在 details 数组里、
以及为什么脚本坚持不内置任何模型单价。

## 交付前自查

- [ ] `--self-test` 通过
- [ ] Top 会话表里的「首条用户意图」是**人说的话**，不是 `OS Version:` / `Below are` 之类的系统文本
- [ ] 归因覆盖率 > 60%，且抽验 3–5 个会话确认分类合理
- [ ] 业务主线数量与用户实际在推进的事基本一致（不是一堆「未归因」或一条独大）
- [ ] 无 `None` / `nan` / 乱码 / 半个 HTML 标签
- [ ] 若输出金额：能给出来价格来源；没配置单价表时表里**不应出现金额列**
- [ ] 对外交付已加 `--hide-intent`
- [ ] 若要打印：确认是 light 主题

## 资源

- `scripts/dashboard.py` — 单文件采集 + 归因 + 渲染，零第三方依赖
- `scripts/check_private.sh` — 发布/分享前的一键隐私自检，五类高危项扫描，命中则非零退出
- `references/biz_names.example.txt` — 隐私自检词表模板（**复制到 `~/.token-dashboard/` 再改写，别就地改**）
- `references/biz_rules.example.json` — 业务主线词典模板（**应复制改写，不要就地改**）
- `references/price.example.json` — 单价表模板，数值为占位样例，须替换为用户实际价格
- `references/pitfalls.md` — 实测坑位与硬约束（改脚本前必读）
