#!/usr/bin/env bash
# 05_jupyterhub_install.sh —— 安裝 JupyterHub（主機側，與使用者容器無關）
set -euo pipefail
apt-get update
apt-get install -y python3-venv nodejs npm acl quota fail2ban auditd
npm install -g configurable-http-proxy

install -d -m 0700 /srv/jupyterhub /var/log/jupyterhub
python3 -m venv /opt/jupyterhub
/opt/jupyterhub/bin/pip install --upgrade pip
/opt/jupyterhub/bin/pip install jupyterhub dockerspawner jupyterhub-idle-culler pyyaml

install -m 0644 configs/jupyterhub_config.py /srv/jupyterhub/jupyterhub_config.py
install -m 0644 configs/mount_policy.py      /srv/jupyterhub/mount_policy.py
install -m 0644 configs/mount_policy.yaml    /srv/jupyterhub/mount_policy.yaml
chown -R root:root /srv/jupyterhub && chmod 0644 /srv/jupyterhub/mount_policy.yaml

# proxy token 放在只有 root 可讀的 EnvironmentFile
[[ -f /srv/jupyterhub/env ]] || {
  printf 'CONFIGPROXY_AUTH_TOKEN=%s\n' "$(openssl rand -hex 32)" > /srv/jupyterhub/env
  chmod 0600 /srv/jupyterhub/env
}
install -m 0644 configs/systemd/jupyterhub.service /etc/systemd/system/jupyterhub.service
systemctl daemon-reload && systemctl enable --now jupyterhub
echo "OK: JupyterHub 已啟動（只聽 127.0.0.1:8000）"
