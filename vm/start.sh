#!/usr/bin/env bash
# 相當於 systemd unit 的 ExecStart（沙盒沒有 systemd）
set -a; source /srv/jupyterhub/env; set +a
export PATH=/home/claude/.npm-global/bin:$PATH
cd /srv/jupyterhub
exec /home/claude/venv/bin/jupyterhub -f /srv/jupyterhub/jupyterhub_config_vm.py
