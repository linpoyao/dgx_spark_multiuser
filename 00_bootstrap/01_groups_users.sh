#!/usr/bin/env bash
# 01_groups_users.sh —— 建立群組與帳號（冪等，可重複執行）
# 身分以 Linux 帳號為準；JupyterHub 用 PAMAuthenticator 直接吃系統帳號，
# 因此容器內 UID / 檔案權限 / ACL 全部自動一致，不需第二套使用者資料庫。
set -euo pipefail

# ---- 角色群組 ----------------------------------------------------------------
GROUPS_ALL=(labadmin phi-raw lung-research prostate-research hubsvc)
for g in "${GROUPS_ALL[@]}"; do
  getent group "$g" >/dev/null || groupadd "$g"
done

# ---- 帳號定義：使用者:主群組:附加群組:到期日(空=不到期) ------------------------
# admin 在真機上要另外加入 sudo；沙盒沒有 sudo 群組，故用 labadmin 代表。
USERS=(
  "poyao:poyao:labadmin,phi-raw,lung-research,prostate-research:"
  "alice:alice:lung-research:"
  "bob:bob:prostate-research:"
  "guest1:guest1:lung-research:2026-12-31"
)

for spec in "${USERS[@]}"; do
  IFS=':' read -r user pgrp sgrps expiry <<<"$spec"
  getent group "$pgrp" >/dev/null || groupadd "$pgrp"
  if ! id "$user" >/dev/null 2>&1; then
    useradd -m -s /bin/bash -g "$pgrp" "$user"
  fi
  usermod -aG "$sgrps" "$user"
  # 一律禁用密碼登入：對外只走 SSH 公鑰（Tailscale 內）或 JupyterHub
  passwd -l "$user" >/dev/null
  if [[ -n "$expiry" ]]; then
    chage -E "$expiry" "$user"          # 短期協作者帳號自動到期
  else
    chage -E -1 "$user"
  fi
  # 密碼最長使用天數（PAM 側的基本要求，即使走公鑰也保留）
  chage -M 365 "$user"
done

# ---- 關鍵安全前提：任何使用者都不得在 docker 群組 -----------------------------
# docker 群組 == root（可 -v /:/host 掛整台主機進容器），會讓所有 PHI 權限設計失效。
if getent group docker >/dev/null; then
  members=$(getent group docker | cut -d: -f4)
  if [[ -n "$members" ]]; then
    echo "FATAL: docker 群組不得有成員，目前為: $members" >&2
    exit 1
  fi
fi

echo "OK: 群組與帳號建立完成"
