#!/usr/bin/env bash
# 02_data_tree.sh —— 建立資料樹與權限（冪等）
# 設計原則：/data/raw 只有 phi-raw 群組（= admin）能進入，且永不掛入任何容器。
set -euo pipefail

# /data 本身必須可被所有人「穿越」(x)，否則 alice 連 /data/deid 都進不去（實測踩到）
install -d -o root -g root               -m 0755 /data
# /data/raw：群組 phi-raw 可讀，others 完全無權；rx 不給 w，避免誤刪原始資料
install -d -o root -g phi-raw            -m 2750 /data/raw   # setgid：匯入的原始檔自動屬 phi-raw
install -d -o root -g root               -m 0755 /data/deid
install -d -o root -g lung-research      -m 0750 /data/deid/lung
install -d -o root -g prostate-research  -m 0750 /data/deid/prostate
install -d -o root -g root               -m 0755 /data/public
install -d -o root -g root               -m 0755 /data/public/luna16

# /shared/models：群組共用、setgid 讓新檔自動繼承群組
install -d -o root -g root               -m 0755 /shared
install -d -o root -g lung-research      -m 2775 /shared/models

install -d -o root -g root               -m 0755 /workspace
for u in poyao alice bob guest1; do
  id "$u" >/dev/null 2>&1 || continue
  install -d -o "$u" -g "$u" -m 0700 "/workspace/$u"   # 0700：同儕之間互不可見
done

# ---- ACL：guest 只讀指定子資料夾（比加群組更細） ------------------------------
if command -v setfacl >/dev/null; then
  install -d -o root -g lung-research -m 0750 /data/deid/lung/cohort2018
  setfacl -m u:guest1:rx  /data/deid/lung
  setfacl -m u:guest1:rx  /data/deid/lung/cohort2018
  setfacl -d -m u:guest1:rx /data/deid/lung/cohort2018   # 新檔預設也讓 guest1 可讀
  # /data/raw 預設 ACL：admin 匯入的新檔即使 umask 太緊，phi-raw 仍可讀（實測踩到）
  setfacl -m g:phi-raw:rx -d -m g:phi-raw:rx /data/raw
fi

echo "OK: 資料樹與權限建立完成"
