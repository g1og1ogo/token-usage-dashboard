#!/usr/bin/env bash
# 推送/分享前的隐私自检
# 用法:  bash scripts/check_private.sh [要检查的目录，默认当前目录]
#
# 扫五类高危泄露：本机用户名路径 / 业务专有名词 / PII 与凭据 / 本地工作路径 / 运行产物
# 退出码 0 = 干净；非 0 = 有命中，禁止发布
#
# ★ 业务专名与本机路径两张词表**不在本文件里**，必须放到仓库之外：
#     ~/.token-dashboard/biz_names.txt      一行一个业务专名（客户、对手方、项目代号）
#     ~/.token-dashboard/local_paths.txt    一行一个本机路径特征
#   本文件会随技能一起公开，把真实名字写进来等于公告自己的业务布局。
#   模板见 references/biz_names.example.txt；可用环境变量 BIZ_NAMES_FILE / LOCAL_PATHS_FILE 覆盖。
set -uo pipefail

TARGET="${1:-.}"
cd "$TARGET" || { echo "目录不存在: $TARGET"; exit 2; }

echo "检查目标: $(pwd)"
echo "======================================"

flag=0
# git 可用环境变量：GIT=/path/to/git 后所有 git 调用改用它（不依赖 PATH）
GITBIN="${GIT:-git}"

check() {
  local name="$1" pat="$2" flags="${3:--E}"
  local out
  # 排除 .git（内部 objects/logs 里的 SHA 与作者邮箱会造成大量误报）
  out=$(grep -rn $flags --exclude-dir=.git --exclude-dir=node_modules \
        --exclude=check_private.sh "$pat" . 2>/dev/null)
  if [ -z "$out" ]; then
    printf "  [ OK ]  %s\n" "$name"
  else
    printf "  [HIT ]  %s —— %s 处命中\n" "$name" "$(printf '%s\n' "$out" | wc -l)"
    printf '%s\n' "$out" | head -10 | sed 's/^/           /'
    flag=1
  fi
}

echo "---- 1. 本机用户名绝对路径 ----"
check "Windows/Linux 用户目录路径" '[A-Za-z]:[\\/]Users[\\/][A-Za-z0-9_.-]+|/home/[A-Za-z0-9_.-]+|/Users/[A-Za-z0-9_.-]+'

echo "---- 2. 业务专有名词（客户/对手方/未公开代号） ----"
# 词表必须放在仓库之外。理由：这份脚本会随技能一起公开，
# 一旦把真实客户名/项目代号写进本文件，等于把业务布局公告出去。
BIZ_NAMES_FILE="${BIZ_NAMES_FILE:-$HOME/.token-dashboard/biz_names.txt}"
if [ -f "$BIZ_NAMES_FILE" ]; then
  biz_pat=$(grep -v -e '^[[:space:]]*#' -e '^[[:space:]]*$' "$BIZ_NAMES_FILE" | tr '\n' '|' | sed 's/|$//')
  if [ -n "$biz_pat" ]; then
    check "疑似业务专名（词表: $BIZ_NAMES_FILE）" "$biz_pat"
  else
    printf "  [ OK ]  业务词表为空\n"
  fi
else
  printf "  [SKIP]  未找到业务词表 %s\n" "$BIZ_NAMES_FILE"
  printf "           自定义清单不放仓库，放技能目录之外，一行一个词：\n"
  printf "           mkdir -p ~/.token-dashboard && cp references/biz_names.example.txt ~/.token-dashboard/biz_names.txt\n"
fi

echo "---- 3. PII 与凭据 ----"
check "邮箱" '[A-Za-z0-9._%-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}'
check "手机号" '1[3-9][0-9]{9}'
check "身份证号" '[0-9]{17}[0-9Xx]'
check "统一社会信用代码" '9[0-9A-Z]{17}'
check "私钥/AK/SK" 'BEGIN [A-Z ]*PRIVATE KEY|AKIA[0-9A-Z]{16}|sk-[A-Za-z0-9]{20,}|ghp_[A-Za-z0-9]{30,}'

echo "---- 4. 本地工作路径 ----"
# 你的本机工作目录同样属于不该公开的信息，和业务词表放同一份清单里（前缀 local:）
LOCAL_PATHS_FILE="${LOCAL_PATHS_FILE:-$HOME/.token-dashboard/local_paths.txt}"
if [ -f "$LOCAL_PATHS_FILE" ]; then
  local_pat=$(grep -v -e '^[[:space:]]*#' -e '^[[:space:]]*$' "$LOCAL_PATHS_FILE" | tr -d '\r' | tr '\n' '|' | sed 's/|$//')
  if [ -n "$local_pat" ]; then
    check "本机工作路径（词表: $LOCAL_PATHS_FILE）" "$local_pat"
  else
    printf "  [ OK ]  路径词表为空\n"
  fi
else
  printf "  [SKIP]  未找到路径词表 %s（格式见 references/biz_names.example.txt）\n" "$LOCAL_PATHS_FILE"
fi

echo "---- 5. 运行产物（应被 .gitignore 拦住） ----"
# ★ 判定前提：目标必须是 git 仓库，且机器上找得到 git 命令。
#   - 非仓库时 git check-ignore 一律返回非 0，会把「不在仓库内」误判成「会入库」——
#     于是任何合法 html（例如技能自带的 HTML 模板）都会把闸门打成红。
#     闸门长期误红 ⇒ 使用者开始忽略它 ⇒ 真泄漏时也没人看。这类假红必须消灭。
#   - 找不到 git 命令时（未安装 / 不在 PATH），「会不会入库」同样无法判定：
#     真实仓库会被 git rev-parse 的失败误判成「非仓库」，整类静默跳过、退出码 0——
#     这是假绿，比假红危险得多。判定原则：无法判定 ≠ 通过，宁可拦下人工确认。
#   可用环境变量 GIT=/path/to/git 指定 git 位置后重试。
GIT_MISSING=0
IN_GIT=0
if ! command -v "$GITBIN" >/dev/null 2>&1; then
  GIT_MISSING=1
elif "$GITBIN" rev-parse --git-dir >/dev/null 2>&1; then
  IN_GIT=1
fi

# 免检清单：随技能一起发布的模板/示例等，显式声明后不参与本类判定。
# 格式：一行一个相对路径或 glob，# 开头忽略。只写中性文件名，不得写业务名。
# ★ 必须 tr -d '\r'：Windows 记事本/编辑器存成 CRLF 时，行尾 \r 会被当成
#   模式的一部分（'assets/*.html\r'），匹配永远失败 → 静默漏检（假绿）。
#   假绿比假红危险得多，所以所有词表读取都要吃掉 \r。
ALLOW_FILE="./.privacy-allow"
allow_hit() {
  [ -f "$ALLOW_FILE" ] || return 1
  local f="${1#./}" a
  while IFS= read -r a; do
    case "$a" in ''|'#'*) continue ;; esac
    case "$f" in $a) return 0 ;; esac
  done < <(tr -d '\r' < "$ALLOW_FILE")
  return 1
}

artifacts=$(find . \( -name '*.html' -o -name 'biz_rules_private.json' -o -name 'price_real.json' \) -not -path './.git/*' 2>/dev/null)
if [ -z "$artifacts" ]; then
  printf "  [ OK ]  无产物残留\n"
elif [ "$GIT_MISSING" -eq 1 ]; then
  printf "  [WARN]  找不到 git 命令（可设 GIT=/path/to/git 覆盖），无法判定产物是否会入库\n"
  printf "           → 按未通过处理，请人工确认以下候选:\n"
  printf '%s\n' "$artifacts" | sed 's/^/           候选  /'
  flag=1
elif [ "$IN_GIT" -eq 0 ]; then
  printf "  [INFO]  非 git 仓库，「入库」无判定语义 → 本类仅列出候选，不参与结论:\n"
  printf '%s\n' "$artifacts" | sed 's/^/           候选  /'
else
  leaked=0
  while IFS= read -r f; do
    [ -n "$f" ] || continue
    if allow_hit "$f"; then
      printf "           已豁免  %s（见 .privacy-allow）\n" "$f"
    elif "$GITBIN" check-ignore -q "$f" 2>/dev/null; then
      printf "           已忽略  %s\n" "$f"
    else
      printf "           **会入库** %s\n" "$f"
      leaked=1
    fi
  done <<< "$artifacts"
  [ "$leaked" -eq 1 ] && flag=1
fi

echo "---- 6. Git 暂存/跟踪区实际会发布的文件 ----"
if [ "$GIT_MISSING" -eq 1 ]; then
  printf "  [INFO]  找不到 git 命令，跳过（可设 GIT=/path/to/git 覆盖）\n"
elif [ "$IN_GIT" -eq 1 ]; then
  tracked=$("$GITBIN" ls-files | wc -l)
  printf "  [INFO]  将被推送的文件 %s 个:\n" "$tracked"
  "$GITBIN" ls-files | sed 's/^/           /'
else
  printf "  [INFO]  非 git 仓库，跳过\n"
fi

echo "======================================"
if [ "$flag" -eq 0 ]; then
  echo "结论: 干净，可以发布。"
else
  echo "结论: 存在泄露项，处理后再发布。"
fi
exit "$flag"
