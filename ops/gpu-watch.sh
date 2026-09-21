#!/usr/bin/env bash
# gpu-watch.sh —— Spark 上的 GPU 觀測。注意 nvidia-smi 的 Memory-Usage 在統一記憶體架構下
# 會顯示 Not Supported，真正的記憶體佔用要看 free -g（CPU/GPU 共用同一塊 128 GB）。
set -uo pipefail
echo "== 誰在用 GPU（process 清單）=="
nvidia-smi --query-compute-apps=pid,process_name,used_memory --format=csv 2>/dev/null || \
  nvidia-smi | sed -n '/Processes/,$p'
echo
echo "== 統一記憶體實際佔用（這才是會 OOM 的那個數字）=="
free -g
echo
echo "== 對應到使用者 =="
for pid in $(nvidia-smi --query-compute-apps=pid --format=csv,noheader 2>/dev/null); do
  ps -o pid=,user=,etime=,cmd= -p "$pid" 2>/dev/null | cut -c1-120
done
