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
  local_pat=$(grep -v -e '^[[:space:]]*#' -e '^[[:space:]]*$' "$LOCAL_PATHS_FILE" | tr '\n' '|' | sed 's/|$//')
  if [ -n "$local_pat" ]; then
    check "本机工作路径（词表: $LOCAL_PATHS_FILE）" "$local_pat"
  else
    printf "  [ OK ]  路径词表为空\n"
  fi
else
  printf "  [SKIP]  未找到路径词表 %s（格式见 references/biz_names.example.txt）\n" "$LOCAL_PATHS_FILE"
fi

echo "---- 5. 运行产物（应被 .gitignore 拦住） ----"
echo "  （仅提示，不影响结论；下方「Git 是否真的忽略」才是判据）"
found=$(find . -name '*.html' -not -path './.git/*' 2>/dev/null | wc -l)
foundj=$(find . \( -name 'biz_rules_private.json' -o -name 'price_real.json' \) -not -path './.git/*' 2>/dev/null | wc -l)
if [ "$found" -gt 0 ] || [ "$foundj" -gt 0 ]; then
  printf "  [INFO]  发现 %s 个 html / %s 个私有 json，用 git 判定它们是否会被入库:\n" "$found" "$foundj"
  leaked=0
  while IFS= read -r f; do
    if git check-ignore -q "$f" 2>/dev/null; then
      printf "           已忽略  %s\n" "$f"
    else
      printf "           **会入库** %s\n" "$f"
      leaked=1
    fi
  done < <(find . \( -name '*.html' -o -name 'biz_rules_private.json' -o -name 'price_real.json' \) -not -path './.git/*' 2>/dev/null)
  [ "$leaked" -eq 1 ] && flag=1
else
  printf "  [ OK ]  无产物残留\n"
fi

echo "---- 6. Git 暂存/跟踪区实际会发布的文件 ----"
if git rev-parse --git-dir >/dev/null 2>&1; then
  tracked=$(git ls-files | wc -l)
  printf "  [INFO]  将被推送的文件 %s 个:\n" "$tracked"
  git ls-files | sed 's/^/           /'
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
