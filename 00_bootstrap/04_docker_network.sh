#!/usr/bin/env bash
# 04_docker_network.sh —— 建立 Hub 與使用者容器共用的 docker 網段；順手做兩個安全確認
set -euo pipefail
docker network inspect jupyterhub-net >/dev/null 2>&1 || \
  docker network create --driver bridge jupyterhub-net

# 確認 NVIDIA Container Toolkit 有掛上（arm64 / DGX OS 7 內建）
docker run --rm --gpus all nvcr.io/nvidia/pytorch:25.08-py3 \
  python -c "import torch;print('cuda:',torch.cuda.is_available(),torch.cuda.get_device_name(0))"

# 確認沒有人在 docker 群組（等同 root）
members=$(getent group docker | cut -d: -f4 || true)
[[ -z "$members" ]] || { echo "FATAL: docker 群組有成員：$members" >&2; exit 1; }
echo "OK: docker 網段與 GPU 直通確認完成"
