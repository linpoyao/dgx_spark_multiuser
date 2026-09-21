#!/usr/bin/env bash
# test_permissions.sh —— 主機側權限模型驗證（以 root 執行，runuser 代入各角色）
# 每一條對應 proposal §3.3 的一項承諾；任一條失敗即表示權限設計沒有落實。
set -uo pipefail

PASS=0; FAIL=0
allow() { # allow <說明> <指令...>
  local desc="$1"; shift
  if "$@" >/dev/null 2>&1; then printf "PASS  [應可]   %s\n" "$desc"; PASS=$((PASS+1));
  else printf "FAIL  [應可]   %s\n" "$desc"; FAIL=$((FAIL+1)); fi
}
deny() {  # deny <說明> <指令...>
  local desc="$1"; shift
  if "$@" >/dev/null 2>&1; then printf "FAIL  [應拒]   %s\n" "$desc"; FAIL=$((FAIL+1));
  else printf "PASS  [應拒]   %s\n" "$desc"; PASS=$((PASS+1)); fi
}
as() { local u="$1"; shift; runuser -u "$u" -- bash -c "$*"; }

# ---- 測試用檔案 --------------------------------------------------------------
# 原始資料匯入規則：0640 root:phi-raw（由 admin 的 ingest 腳本保證）
echo "PHI-ORIGINAL" > /data/raw/secret.dcm; chgrp phi-raw /data/raw/secret.dcm; chmod 640 /data/raw/secret.dcm
echo "deid-lung"    > /data/deid/lung/case001.npz;      chgrp lung-research      /data/deid/lung/case001.npz;     chmod 640 /data/deid/lung/case001.npz
echo "deid-prost"   > /data/deid/prostate/case001.npz;  chgrp prostate-research  /data/deid/prostate/case001.npz; chmod 640 /data/deid/prostate/case001.npz
echo "cohort"       > /data/deid/lung/cohort2018/a.npz; chgrp lung-research      /data/deid/lung/cohort2018/a.npz; chmod 640 /data/deid/lung/cohort2018/a.npz
echo "luna"         > /data/public/luna16/list.csv;     chmod 644 /data/public/luna16/list.csv

echo "── 1. 原始 PHI 隔離 ────────────────────────────────────────────────"
deny  "alice 讀 /data/raw/secret.dcm"                 as alice  'cat /data/raw/secret.dcm'
deny  "alice 列出 /data/raw"                          as alice  'ls /data/raw'
deny  "guest1 列出 /data/raw"                         as guest1 'ls /data/raw'
allow "poyao(phi-raw) 讀 /data/raw/secret.dcm"        as poyao  'cat /data/raw/secret.dcm'
allow "/data/raw 為 0750 root:phi-raw（others 無權）"  bash -c '[ "$(stat -c "%a %U:%G" /data/raw)" = "2750 root:phi-raw" ]'
allow "/data 本身可穿越（0755）"                      bash -c '[ "$(stat -c %a /data)" = 755 ]'

echo "── 2. 去識別資料：本題目可讀、不可寫 ──────────────────────────────"
allow "alice 讀 /data/deid/lung/case001.npz"          as alice  'cat /data/deid/lung/case001.npz'
deny  "alice 寫入 /data/deid/lung/"                   as alice  'touch /data/deid/lung/x'
deny  "alice 改寫既有去識別檔"                        as alice  'echo tampered > /data/deid/lung/case001.npz'
allow "alice 讀公開資料 luna16"                       as alice  'cat /data/public/luna16/list.csv'

echo "── 3. 題目之間互相隔離 ────────────────────────────────────────────"
deny  "bob 讀 lung 題目資料"                          as bob    'cat /data/deid/lung/case001.npz'
deny  "bob 列出 /data/deid/lung"                      as bob    'ls /data/deid/lung'
deny  "alice 讀 prostate 題目資料"                    as alice  'cat /data/deid/prostate/case001.npz'

echo "── 4. 個人工作區 ──────────────────────────────────────────────────"
allow "alice 寫入自己的 /workspace/alice"             as alice  'touch /workspace/alice/exp1.py'
deny  "alice 列出 /workspace/bob"                     as alice  'ls /workspace/bob'
deny  "alice 寫入 /workspace/bob"                     as alice  'touch /workspace/bob/x'

echo "── 5. /shared/models 群組共用（setgid 繼承） ──────────────────────"
allow "alice 寫入 /shared/models"                     as alice  'touch /shared/models/resnet.pt'
allow "新檔群組自動繼承 lung-research"                bash -c '[ "$(stat -c %G /shared/models/resnet.pt)" = lung-research ]'
allow "poyao 也能讀 alice 放的權重"                   as poyao  'cat /shared/models/resnet.pt'

echo "── 6. guest 以 ACL 限定到單一子資料夾 ─────────────────────────────"
allow "guest1 讀 cohort2018（ACL 授權）"              as guest1 'cat /data/deid/lung/cohort2018/a.npz'
deny  "guest1 寫入 cohort2018"                        as guest1 'touch /data/deid/lung/cohort2018/x'
allow "guest1 帳號已設到期日"                         bash -c 'chage -l guest1 | grep -qv "Account expires.*never"'

echo "── 7. docker 群組必須為空（否則等同發 root） ──────────────────────"
allow "無任何使用者在 docker 群組"                    bash -c '[ -z "$(getent group docker | cut -d: -f4)" ]'

printf "\n總計：PASS=%d  FAIL=%d\n" "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
