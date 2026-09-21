#!/usr/bin/env bash
# run_all.sh —— 一次跑完所有可自動驗證的項目，輸出 PASS/FAIL 總表。
# 真機上 bootstrap 完成後應再跑一次；任何一項 FAIL 就不要開放外網。
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
PY="${PY:-python3}"
export HUB_GW="${HUB_GW:-172.18.0.1}"   # 靜態驗證不連 docker；閘道位址給預設值
declare -A RESULT
run() { local name="$1"; shift; echo; echo "############ $name ############"; if "$@"; then RESULT[$name]=PASS; else RESULT[$name]=FAIL; fi; }

run "帳號與權限模型"        bash    "$HERE/test_permissions.sh"
run "掛載政策（含負向測試）" "$PY"  "$HERE/test_mount_policy.py"
run "JupyterHub 設定"       "$PY"   "$HERE/validate_jupyterhub_config.py"
run "sshd 硬化設定"         bash    "$HERE/test_sshd_config.sh"
run "fail2ban 與 filter"    bash    "$HERE/test_fail2ban.sh"
run "外網路徑設定"          "$PY"   "$HERE/test_external_access.py"

echo; echo "############ 總表 ############"
fails=0
for k in "帳號與權限模型" "掛載政策（含負向測試）" "JupyterHub 設定" "sshd 硬化設定" "fail2ban 與 filter" "外網路徑設定"; do
  printf "%-28s %s\n" "$k" "${RESULT[$k]}"
  [[ "${RESULT[$k]}" == FAIL ]] && fails=$((fails+1))
done
echo
echo "另有一項需要網路（PyPI）：$PY $HERE/audit_arm64_wheels.py --python 312"
exit $fails
