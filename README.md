# token-usage-dashboard

从本机 WorkBuddy / CodeBuddy 会话日志统计 Token 消耗，生成一页式的离线 HTML 看板。

它回答三个问题：

1. **消耗花在哪条业务线** —— 会话级加权投票做业务主线归因，而不是只给一个总数
2. **花得值不值** —— 拆出缓存命中率、思考模式（reasoning）占比，区分「流程搬运型」与「深度调研型」
3. **哪里能省** —— 集中度告警、低投入主线告警、可切换低价模型的主线清单

单文件输出，无 CDN、无外部字体，双击即开、可直接打印。零第三方依赖，只用 Python 标准库。

---

## 安装

```bash
# 用户级安装：放到 skills 目录即可
cp -r token-usage-dashboard ~/.workbuddy/skills/
```

环境要求：Python 3.9+（用到 `X | None` 类型语法与 dict 保序）。依赖：无。

## 快速开始

```bash
cd ~/.workbuddy/skills/token-usage-dashboard

# 1) 自检（不读真实数据，用内置假数据验证渲染链路）
python scripts/dashboard.py --self-test -o ./_selftest.html

# 2) 复制词典模板到技能目录之外，按你自己的业务线改写
cp references/biz_rules.example.json ~/.token-dashboard/biz_rules.json
#   编辑 ~/.token-dashboard/biz_rules.json

# 3) 生成真实看板
python scripts/dashboard.py \
    --biz-config ~/.token-dashboard/biz_rules.json \
    --json-out   ./dashboard.json \
    -o           ./token-dashboard.html
```

## 数据来源

| 数据源 | 内容 |
|---|---|
| `~/.workbuddy/projects/**/*.jsonl` | 请求级 usage（输入/输出/缓存/思考 token） |
| `~/.workbuddy/audit-log/*.jsonl` | 工具调用类别分布 |

全程只读，除输出文件外不改动任何数据。

## 主要参数

| 参数 | 说明 |
|---|---|
| `-o, --out` | 输出 HTML 路径（默认当前目录 `token-dashboard.html`） |
| `--json-out` | 同时导出结构化 JSON，便于二次分析或跨期对比 |
| `--biz-config` | 业务主线词典 JSON |
| `--price-config` | 模型单价 JSON；**不传则不输出任何金额** |
| `--since / --until` | 按 `YYYY-MM-DD` 限定区间 |
| `--theme light\|dark` | 默认 light，可直接打印 |
| `--hide-intent` | **对外分享前务必开启**，隐藏 Top 会话表里的用户原话 |
| `--top-sessions N` | 高消耗会话显示条数 |
| `--self-test` | 内置假数据自检 |

归因权重（`--filename-weight` / `--kw-weight` / `--user-boost` / `--min-score`）与
告警阈值（`--concentration-threshold` 等）均可调，见 `--help`。

## 隐私：发布与分享的边界

**技能本身不含任何本机数据，但词典和产物会。**

| 对象 | 能否公开 |
|---|---|
| `scripts/dashboard.py` | 可以 |
| `SKILL.md` / `references/pitfalls.md` | 可以 |
| `references/biz_rules.example.json` | **不可以**（一旦填入你的真实主线，就是一份业务布局清单） |
| `references/price.example.json` | 视情况（换成真实合同价后即等于公开采购条件） |
| 生成的看板 HTML / JSON | **绝对不可以**（含真实消耗数据与用户原话） |

实测本机日志里，用户首条发言可能包含自然人姓名、18 位身份证号、企业统一社会信用代码。
因此 Top 会话表对外展示前请加 `--hide-intent`。

仓库已内置 `.gitignore` 兜底：`*.html` 与 `*.json` 默认忽略，只放行 `references/` 下的两个模板。

## License

未附带 License 文件，默认保留所有权利。如需以 MIT 等协议开源，请自行添加 `LICENSE`
并把作者名改成你自己——本仓库不含任何可识别的个人信息，但也不替你做许可决定。
