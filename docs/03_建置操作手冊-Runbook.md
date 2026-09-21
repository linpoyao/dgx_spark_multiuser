# DGX Spark 多帳號外網登入伺服器：建置操作手冊（Runbook）

| | |
|---|---|
| 版本 | v1.0（2026-09-13） |
| 搭配文件 | `DGX-Spark-多帳號外網登入-Proposal.md`（設計理由）、`DGX-Spark-虛擬環境驗證報告.md`（驗證依據）、`dgx-spark-multiuser/`（建置包） |
| 適用 | DGX OS 7（Ubuntu 24.04 arm64 為基底）、GB10、128 GB 統一記憶體 |
| 執行者 | admin（具 sudo） |
| 預估 | 淨工時約 2 個工作日，可分 6 個階段跨天執行 |

## 怎麼使用這份手冊

每個步驟都有 **① 指令 → ② 預期輸出 → ③ 失敗時怎麼辦**。請照順序做，不要跳階段：階段 2（Tailscale）必須在階段 3（sshd 硬化）之前完成，否則會把自己鎖在機器外面。

**四條貫穿全程的鐵則**

1. 做任何破壞性動作前，**保留一個已登入且不要關掉的 SSH／實體終端**當救命通道。
2. 改完設定檔一律先驗證再套用（`sshd -t`、`fail2ban-client -d`、`tests/run_all.sh`），不要直接 restart。
3. 不把任何人加進 `docker` 群組（等同發 root）。
4. `/data/raw` 不掛進任何容器，包含 admin 自己的。

**全域變數**（先決定好，後面指令直接沿用）

```bash
# 建議寫進 /root/lab.env，每次開工 source 一次
cat > /root/lab.env <<'EOF'
export LAB_DOMAIN="lab.example.edu.tw"          # 你們實際的（子）網域
export HUB_HOST="jupyter.${LAB_DOMAIN}"         # JupyterHub 對外主機名
export TUNNEL_NAME="dgx-spark-lab"
export IMG_MAIN="lab/pytorch-arm64:2026.09"
export IMG_RAD="lab/radiomics-arm64:2026.09"
export NGC_TAG="nvcr.io/nvidia/pytorch:25.08-py3"   # 以 NGC 上實際可用的 arm64 tag 為準
export BUNDLE="/opt/dgx-spark-multiuser"
EOF
source /root/lab.env
```

---

# 階段 0：前置準備（不在機器上做）

## 0.1 決策確認清單

在動手前這五件事要有答案，否則中途會卡住：

| # | 要決定的事 | 建議 | 誰決定 |
|---|---|---|---|
| 1 | 是否需向醫院資訊室／IRB 報備 | 建議先問；「院內資料放在可從外網登入的機器上」屬需書面同意事項 | 教授 |
| 2 | 對外網域 | 用學校既有子網域最省事；否則自備一個網域掛在 Cloudflare | 教授／資訊室 |
| 3 | 初期帳號名單與題目分組 | 例：`poyao`(admin) / `alice`(lung) / `bob`(prostate) / `guest1`(短期，到期日) | 柏嶢 |
| 4 | 第二位 admin | 強烈建議有，避免單點 | 教授 |
| 5 | `/data/raw` 加密方式 | 見 §5.1 兩個選項（獨立分割區 vs LUKS 容器檔） | 柏嶢 |

## 0.2 需要先申請／安裝的東西

- Cloudflare 帳號（免費方案即可），網域的 DNS 需託管在 Cloudflare。
- Tailscale 帳號（Personal 或 Starter 免費額度足夠），建議用學校 Google 帳號登入以便後續 Access 白名單一致。
- NGC 帳號（拉 `nvcr.io` 映像用；部分映像需 `docker login`）。
- 每位使用者自備 SSH 公鑰（`ssh-keygen -t ed25519`），或改用 Tailscale SSH（免散發公鑰，見 §2.4）。
- 建置包放到機器上：`/opt/dgx-spark-multiuser`。

## 0.3 把建置包放上機器

```bash
# 在自己的筆電上
scp -r dgx-spark-multiuser <admin>@<spark-臨時位址>:/tmp/
# 在 Spark 上
sudo mv /tmp/dgx-spark-multiuser /opt/ && sudo chown -R root:root /opt/dgx-spark-multiuser
sudo chmod +x /opt/dgx-spark-multiuser/{00_bootstrap,tests,ops}/*.sh
```

> 建議把整個建置包放進 git（含後續修改），未來交接與重建只要 clone。

---

# 階段 1：系統確認與基礎設定（約 1 小時）

目的：確認平台事實與設計假設相符，並把「半夜自動更新打斷訓練」這類地雷先拆掉。

## 1.1 確認硬體與軟體事實

```bash
source /root/lab.env
uname -m                      # 預期：aarch64
lsb_release -d                # 預期：Ubuntu 24.04（DGX OS 7 基底）
cat /etc/dgx-release 2>/dev/null || true
nvidia-smi                    # 預期：看到 GB10；Memory-Usage 顯示 Not Supported 是正常的
free -g                       # 預期：total 約 119–128 GB（統一記憶體，CPU/GPU 共用）
docker --version && docker info | grep -i runtime   # 預期：runtimes 含 nvidia
df -h /                       # 確認 NVMe 4 TB 與可用空間
lsblk                         # 記下裝置名稱，階段 5 要用
```

**③ 失敗處置**

- `nvidia-smi` 無輸出 → driver 未載入，先 `sudo systemctl status nvidia-persistenced`，或重跑 DGX OS 首次開機設定。
- `docker info` 沒有 nvidia runtime → 安裝 `nvidia-container-toolkit` 並 `systemctl restart docker`。
- `uname -m` 不是 `aarch64` → 你在錯的機器上（本手冊的映像與套件結論都以 arm64 為前提）。

## 1.2 基礎設定

```bash
sudo timedatectl set-timezone Asia/Taipei
sudo hostnamectl set-hostname dgx-spark
sudo apt-get update
sudo apt-get install -y acl quota fail2ban auditd git tmux htop nvtop jq python3-venv nodejs npm
```

## 1.3 關閉自動更新（避免半夜升級驅動打斷訓練）

```bash
sudo systemctl disable --now unattended-upgrades apt-daily.timer apt-daily-upgrade.timer
sudo sed -i 's/^APT::Periodic::Unattended-Upgrade.*/APT::Periodic::Unattended-Upgrade "0";/' \
  /etc/apt/apt.conf.d/20auto-upgrades 2>/dev/null || true
systemctl is-enabled unattended-upgrades || echo "OK: 已停用"
```

改為每月由 admin 手動更新（見 §13.1）。

## 1.4 為 `/workspace` 所在檔案系統開啟 quota

```bash
# 先確認 /workspace 會落在哪個掛載點（通常是 /）
findmnt -no TARGET,SOURCE,FSTYPE -T /
# 在 fstab 對應那一行的 options 加上 usrquota,grpquota
sudo cp /etc/fstab /etc/fstab.bak.$(date +%F)
sudo vi /etc/fstab          # 例：defaults → defaults,usrquota,grpquota
sudo mount -o remount /
grep -q usrquota /proc/mounts && echo "OK: quota 選項已生效" || echo "FAIL: 需重開機"
```

**③** 若 remount 沒生效（根檔案系統常見），重開機一次再確認；不要跳過這步，階段 4 的 `03_quota.sh` 會依賴它。

---

# 階段 2：Tailscale（約 30 分鐘）

目的：建立一條「不用開防火牆 port」的管理通道。**必須在 sshd 硬化之前完成並驗證可用。**

## 2.1 安裝並上線

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up --hostname=dgx-spark --advertise-tags=tag:spark --ssh
# 會印出一個網址，用管理者帳號在瀏覽器開啟授權
tailscale status
tailscale ip -4            # 記下這個 100.x.x.x，階段 3 要填進 sshd 設定
export TS_IP=$(tailscale ip -4 | head -1); echo "export TS_IP=$TS_IP" >> /root/lab.env
```

**② 預期**：`tailscale status` 顯示 `dgx-spark` 為 active，且有 `tag:spark`。

**③ 失敗處置**

- tag 被拒 → 先在 Tailscale 管理後台的 ACL 設定 `tagOwners`（建置包 `configs/tailscale/acl.json` 已含範例），再重跑 `tailscale up`。
- 出現 `--advertise-tags` 與使用者身分衝突 → 用管理者帳號授權，或先不帶 tag 上線、之後在後台補 tag。

## 2.2 套用 ACL（誰能連哪個 port）

在 Tailscale 管理後台 → Access controls，貼上 `configs/tailscale/acl.json` 的內容，並把 `groups` 裡的 email 換成真實帳號。重點：

- `group:lab-guest` **只有 8000**（JupyterHub），沒有 22。
- 沒有任何規則寫成 `tag:spark:*`。
- Tailscale SSH 只允許 `autogroup:nonroot`，並設 `checkPeriod: 12h`。

儲存後在後台的 ACL 測試工具（或 `tailscale status --json`）確認規則有生效。

## 2.3 從外網實測這條通道

在**自己的筆電**（切到手機熱點，確保不在校內網路）：

```bash
tailscale status | grep dgx-spark
ssh <admin>@$TS_IP            # 這時仍是預設 sshd 設定，應該可以登入
```

**② 預期**：從校外也能登入。**這一步沒過，絕對不要進階段 3。**

## 2.4 （可選）改用 Tailscale SSH，免散發公鑰

`tailscale up --ssh` 已啟用。好處是離職撤銷只要從 Tailscale 群組移除，不必上機刪 `authorized_keys`。若採用此方式，階段 3 的 sshd 仍要硬化（Tailscale SSH 走自己的通道，但一般 sshd 仍在聽）。

---

# 階段 3：sshd 硬化與 fail2ban（約 30 分鐘）

⚠️ **這是最容易把自己鎖在外面的階段。** 開始前：保留一個**已登入且不關閉**的 SSH session 當救命通道，並確認你能用實體鍵盤／HDMI 或 Tailscale SSH 進入。

## 3.1 套用 sshd drop-in

```bash
source /root/lab.env
sudo install -d /run/sshd /etc/ssh/sshd_config.d
sudo install -m 0644 $BUNDLE/configs/sshd/lab.conf /etc/ssh/sshd_config.d/lab.conf
# 把 ListenAddress 換成本機的 Tailscale 位址
sudo sed -i "s/^ListenAddress .*/ListenAddress ${TS_IP}/" /etc/ssh/sshd_config.d/lab.conf
grep ^ListenAddress /etc/ssh/sshd_config.d/lab.conf
```

## 3.2 先驗證，再套用（順序不可顛倒）

```bash
sudo sshd -t                      # ② 預期：沒有任何輸出
sudo sshd -T -C user=alice,host=dgx-spark,addr=${TS_IP} | \
  grep -Ei '^(passwordauthentication|permitrootlogin|maxauthtries|allowgroups|listenaddress|authenticationmethods|x11forwarding|allowtcpforwarding)'
```

**② 預期**：`passwordauthentication no`、`permitrootlogin no`、`maxauthtries 3`、`allowgroups labadmin lung-research prostate-research`、`listenaddress 100.x.x.x:22`、`authenticationmethods publickey`。

**③** `sshd -t` 有輸出就是有錯，**不要 reload**；照訊息修正（常見：`Missing privilege separation directory: /run/sshd` → `sudo install -d /run/sshd`）。

## 3.3 確認自己的帳號在白名單內，再 reload

```bash
id $(logname 2>/dev/null || echo $SUDO_USER) | grep -E 'labadmin|lung-research|prostate-research' \
  || echo "警告：你的帳號不在 AllowGroups 內，reload 後會無法登入！"
# 若警告出現，先把自己加進 labadmin（階段 4 會正式建立群組）
sudo groupadd -f labadmin && sudo usermod -aG labadmin $SUDO_USER

sudo systemctl reload ssh
```

**驗收（不要關掉現有 session）**：另開一個終端 `ssh <admin>@$TS_IP`，成功登入後才算過。同時確認公網介面已聽不到 22：

```bash
sudo ss -tlnp | grep ':22'        # ② 預期：只有 100.x.x.x:22，沒有 0.0.0.0:22
```

**③ 被鎖在外面時**：用保留的 session 或實體終端 `sudo rm /etc/ssh/sshd_config.d/lab.conf && sudo systemctl reload ssh` 回復，再找原因。

## 3.4 fail2ban

```bash
sudo install -m 0644 $BUNDLE/configs/fail2ban/jail.local /etc/fail2ban/jail.local
sudo install -m 0644 $BUNDLE/configs/fail2ban/filter.d/jupyterhub.conf /etc/fail2ban/filter.d/
sudo install -d -m 0755 /var/log/jupyterhub
sudo fail2ban-client -d >/dev/null && echo "OK: 設定可載入"
sudo systemctl restart fail2ban
sudo fail2ban-client status                       # ② 預期：jail list 含 sshd, jupyterhub
sudo fail2ban-client status jupyterhub
```

**注意**（見 proposal §9.2）：`jail.local` 的 `ignoreip` 含 `127.0.0.1/8`，因為經 Cloudflare Tunnel 進來的來源 IP 都是 `127.0.0.1`；不排除的話一個人打錯密碼會封掉整條 tunnel。外網側的暴力登入防護由階段 10 的 Cloudflare Access 承擔。

---

# 階段 4：帳號、群組、資料樹、quota（約 1 小時）

## 4.1 編輯真實名單

```bash
sudo vi $BUNDLE/00_bootstrap/01_groups_users.sh
```

改 `USERS` 陣列（格式 `使用者:主群組:附加群組:到期日`），例如：

```bash
USERS=(
  "poyao:poyao:labadmin,phi-raw,lung-research,prostate-research:"
  "alice:alice:lung-research:"
  "bob:bob:prostate-research:"
  "guest1:guest1:lung-research:2026-12-31"
)
```

真機上 admin 還要加入 `sudo` 群組（腳本用 `labadmin` 代表角色，不自動給 sudo）：

```bash
sudo usermod -aG sudo poyao
```

## 4.2 執行

```bash
cd $BUNDLE
sudo ./00_bootstrap/01_groups_users.sh      # ② 預期：OK: 群組與帳號建立完成
sudo ./00_bootstrap/02_data_tree.sh         # ② 預期：OK: 資料樹與權限建立完成
sudo ./00_bootstrap/03_quota.sh / 400G 500G # ② 預期：repquota 列出各使用者
```

**③ 失敗處置**

- `FATAL: docker 群組不得有成員` → 這是刻意的檢查。`sudo gpasswd -d <user> docker` 移除後再跑（需要 CLI 容器的人走 rootless Docker，見 §12.3）。
- `03_quota.sh` 報 quota 未啟用 → 回到 §1.4，fstab 沒加 `usrquota` 或沒重開機。

## 4.3 確認權限（先跑一次自動驗證）

```bash
sudo ./tests/test_permissions.sh
```

**② 預期**：`總計：PASS=23  FAIL=0`。

**③** 若有 FAIL，看是哪一條。三個最常見原因（都是驗證階段實測踩過的）：

| 症狀 | 原因 | 處置 |
|---|---|---|
| researcher 讀不到 `/data/deid/*` | `/data` 不是 `0755`，無法穿越 | `sudo chmod 0755 /data` |
| admin 讀不到 `/data/raw` 裡的檔案 | 檔案不屬 `phi-raw` 或 `/data/raw` 缺 setgid／default ACL | `sudo chmod 2750 /data/raw; sudo setfacl -m g:phi-raw:rx -d -m g:phi-raw:rx /data/raw` |
| `/shared/models` 新檔群組不對 | setgid 位掉了 | `sudo chmod 2775 /shared/models` |

## 4.4 匯入資料的規則（寫死在流程裡）

原始資料只由 admin 在主機上處理，且一律設成 `0640 root:phi-raw`：

```bash
# 範例：匯入一批原始 DICOM
sudo rsync -a --chown=root:phi-raw --chmod=D2750,F0640 /media/usb/cohort2018/ /data/raw/cohort2018/
# 去識別化（同樣只在主機 shell 做，不在容器裡做）
sudo -u root /opt/deid/run_deid.py --in /data/raw/cohort2018 --out /data/deid/lung/cohort2018
sudo chown -R root:lung-research /data/deid/lung/cohort2018
sudo chmod -R u=rwX,g=rX,o= /data/deid/lung/cohort2018
```

---

# 階段 5：原始資料加密與稽核（約 1 小時）

## 5.1 `/data/raw` 加密：兩個選項

**選項 A（建議，但需在重裝／新增磁碟時做）：獨立分割區 ＋ LUKS**

```bash
lsblk                                    # 找出目標裝置，例如外接 NVMe /dev/nvme1n1
sudo cryptsetup luksFormat /dev/nvme1n1p1
sudo cryptsetup open /dev/nvme1n1p1 phi_raw
sudo mkfs.ext4 -L phi-raw /dev/mapper/phi_raw
sudo mount /dev/mapper/phi_raw /data/raw
# 不要寫進 /etc/crypttab 自動解鎖 —— 刻意保留「開機後由 admin 手動解鎖」
```

**選項 B（系統已裝好、不想重新分割時）：LUKS 容器檔**

```bash
sudo fallocate -l 1T /var/lib/phi_raw.img
sudo cryptsetup luksFormat /var/lib/phi_raw.img
sudo cryptsetup open --type luks /var/lib/phi_raw.img phi_raw
sudo mkfs.ext4 /dev/mapper/phi_raw
sudo mount /dev/mapper/phi_raw /data/raw
sudo chown root:phi-raw /data/raw && sudo chmod 2750 /data/raw
sudo setfacl -m g:phi-raw:rx -d -m g:phi-raw:rx /data/raw
```

每次開機後的解鎖流程（寫進 `ops/RULES.md` 與交接文件）：

```bash
sudo cryptsetup open /var/lib/phi_raw.img phi_raw && sudo mount /dev/mapper/phi_raw /data/raw
# 用完（例如去識別化批次結束）可以關回去
sudo umount /data/raw && sudo cryptsetup close phi_raw
```

> 密碼／keyfile 不要放在這台機器上。建議用實驗室的密碼管理器保管，並由兩位 admin 各持一份。

## 5.2 auditd：誰碰過原始資料

```bash
sudo tee /etc/audit/rules.d/phi.rules >/dev/null <<'EOF'
-w /data/raw -p rwa -k phi_raw_access
-w /etc/ssh/sshd_config.d/ -p wa -k ssh_config_change
-w /srv/jupyterhub/mount_policy.yaml -p wa -k mount_policy_change
EOF
sudo augenrules --load && sudo systemctl restart auditd
sudo auditctl -l                          # ② 預期：列出三條規則
# 測試：讀一次原始檔再查紀錄
sudo cat /data/raw/cohort2018/* >/dev/null 2>&1
sudo ausearch -k phi_raw_access -i | tail -5
```

保留期限設 ≥180 天：

```bash
sudo sed -i 's/^num_logs = .*/num_logs = 20/; s/^max_log_file = .*/max_log_file = 50/' /etc/audit/auditd.conf
sudo systemctl restart auditd
```

## 5.3 備份

```bash
# 內建 NVMe → 外接 NVMe 冷備（外接盒平時拔除、上鎖保管）
sudo rsync -aHAX --info=progress2 /data/deid/ /mnt/backup/deid/
sudo rsync -aHAX --info=progress2 /workspace/ /mnt/backup/workspace/
# 原始資料的備份同樣要加密後才離機
```

---

# 階段 6：建置容器映像（約 1–2 小時，多數時間在等）

## 6.1 （必要時）登入 NGC

```bash
docker login nvcr.io      # Username: $oauthtoken  Password: <你的 NGC API key>
docker pull $NGC_TAG      # ② 預期：pull 成功（arm64 manifest）
```

**③** 若 pull 回報 `no matching manifest for linux/arm64` → 換一個有 arm64 manifest 的 tag；在 NGC catalog 的 PyTorch 頁面確認 `Architecture: arm64` 的 tag 清單，並同步更新 `$NGC_TAG` 與 Dockerfile 的 `FROM`。

## 6.2 建主映像

```bash
cd $BUNDLE
# Spark 本身就是 arm64，--platform 可省略；跨機建置時才需要
sudo docker build -t $IMG_MAIN -f configs/docker/Dockerfile.pytorch-arm64 .
```

**② 預期**：建置成功，且 36 個套件裡除了 pyradiomics 之外都從 wheel 安裝（不會出現長時間的 C 編譯）。

**③ 失敗處置**

| 症狀 | 處置 |
|---|---|
| 某套件開始編譯 C 並失敗 | 跑 `python3 tests/audit_arm64_wheels.py --python <容器內的 py 版本>` 確認該版本有無 aarch64 wheel；必要時降版或移出主映像 |
| `jupyterhub` 版本與 Hub 不符 | 主映像的 `jupyterhub==6.*` 要與階段 9 安裝的 Hub 同一 major |
| 磁碟被 build cache 吃滿 | `docker builder prune` |

## 6.3 建 radiomics 專用映像

```bash
sudo docker build -t $IMG_RAD -f configs/docker/Dockerfile.radiomics-arm64 .
```

Dockerfile 最後一行會自我驗收（列出 radiomics 特徵類別）；**建置成功即代表 pyradiomics 可用**。

為什麼要分開：pyradiomics 3.1.0 在 Python 3.12 完全裝不起來（與架構無關，三個原因見 proposal §7.3），所以固定 `python:3.11` ＋ `numpy<2` ＋ 從 GitHub repo 安裝。

**使用方式**（不透過 Hub，由 admin 或 researcher 在 Tailscale 內以 rootless Docker 跑）：

```bash
docker run --rm -v /data/deid/lung:/data/deid/lung:ro -v /workspace/$USER:/work \
  $IMG_RAD python /work/extract_features.py
```

## 6.4 驗證映像可用

```bash
sudo docker run --rm --gpus all $IMG_MAIN python -c \
  "import torch;print(torch.__version__, torch.cuda.is_available(), torch.cuda.get_device_name(0))"
sudo docker run --rm $IMG_MAIN python -c \
  "import SimpleITK, pydicom, monai, lungmask; print('imaging stack OK')"
sudo docker run --rm $IMG_MAIN jupyterhub-singleuser --version
```

**② 預期**：`True` ＋ GB10 裝置名稱；`imaging stack OK`；singleuser 版本與 Hub 相符。

**③** `cuda.is_available()` 為 `False` → NVIDIA Container Toolkit 沒生效：`sudo nvidia-ctk runtime configure --runtime=docker && sudo systemctl restart docker` 後重試。

---

# 階段 7：Docker 網段與 GPU 直通（約 20 分鐘）

## 7.1 建立 Hub 與使用者容器共用的網段

```bash
cd $BUNDLE
sudo ./00_bootstrap/04_docker_network.sh
```

腳本做三件事：建立 `jupyterhub-net`、以 `--gpus all` 實測 GPU 直通、確認 `docker` 群組為空。

## 7.2 取得 Hub 在該網段上的位址（重要，容器要靠它連回 Hub）

`jupyterhub_config.py` 預設寫 `hub_connect_ip = "172.17.0.1"`（docker0）。但使用者容器是接在 `jupyterhub-net` 這個 user-defined bridge 上，**閘道位址通常不是 172.17.0.1**，必須實際查出來並改掉，否則容器起來後連不到 Hub，會在 `Spawn failed: Timeout` 卡住。

```bash
GW=$(sudo docker network inspect jupyterhub-net -f '{{(index .IPAM.Config 0).Gateway}}')
echo "jupyterhub-net gateway = $GW"
echo "export HUB_GW=$GW" >> /root/lab.env
```

記下這個值，階段 9 會填進設定檔。

---

# 階段 8：（可選）rootless Docker 給需要 CLI 的人（約 30 分鐘）

只有在 researcher 明確需要在 SSH 內自己跑容器時才做。**不要因此把人加進 `docker` 群組。**

```bash
sudo apt-get install -y uidmap dbus-user-session docker-ce-rootless-extras
# 以該使用者身分執行（不是 root）
sudo -u alice -i bash -lc '
  dockerd-rootless-setuptool.sh install
  echo "export DOCKER_HOST=unix:///run/user/$(id -u)/docker.sock" >> ~/.bashrc
'
sudo loginctl enable-linger alice        # 讓其 daemon 在登出後續存（長時間訓練需要）
```

驗收：

```bash
sudo -u alice -i bash -lc 'docker run --rm hello-world >/dev/null && echo rootless OK'
# 確認她碰不到原始資料：預期失敗
sudo -u alice -i bash -lc 'docker run --rm -v /data/raw:/x:ro alpine ls /x' && \
  echo "FAIL: rootless 竟可讀 /data/raw" || echo "PASS: 讀不到 /data/raw"
```

**② 預期**：`rootless OK`，且第二條印出 `PASS`（`/data/raw` 為 `2750 root:phi-raw`，alice 不在 `phi-raw`，user namespace 映射後無權）。

**③** 若第二條竟然成功，立刻停用該使用者的 rootless daemon 並檢查 `/data/raw` 權限與 ACL。

---

# 階段 9：JupyterHub（約 1 小時）

## 9.1 安裝

```bash
cd $BUNDLE
sudo ./00_bootstrap/05_jupyterhub_install.sh
```

腳本會：安裝 `configurable-http-proxy`（npm 全域）、建 `/opt/jupyterhub` venv 並安裝 `jupyterhub` / `dockerspawner` / `jupyterhub-idle-culler` / `pyyaml`、把三個設定檔放到 `/srv/jupyterhub/`、產生 `0600` 的 `env`（含 `CONFIGPROXY_AUTH_TOKEN`）、安裝並啟用 systemd unit。

## 9.2 依實機修正兩個值

```bash
source /root/lab.env
# (1) 容器連回 Hub 的位址（階段 7.2 查到的）
sudo sed -i "s|^c.JupyterHub.hub_connect_ip = .*|c.JupyterHub.hub_connect_ip = \"${HUB_GW}\"|" \
  /srv/jupyterhub/jupyterhub_config.py
# (2) 映像 tag 與白名單
sudo sed -i "s|lab/pytorch-arm64:2026.09|${IMG_MAIN}|" /srv/jupyterhub/jupyterhub_config.py
sudo vi /srv/jupyterhub/jupyterhub_config.py   # 改 allowed_users / admin_users 為真實名單
grep -E 'hub_connect_ip|image|allowed_users|admin_users' /srv/jupyterhub/jupyterhub_config.py
```

## 9.3 靜態驗證（啟動前先驗）

```bash
cd $BUNDLE
sudo env PY=/opt/jupyterhub/bin/python CONFIGPROXY_AUTH_TOKEN=dummy \
  /opt/jupyterhub/bin/python tests/validate_jupyterhub_config.py
```

**② 預期**：`總計：PASS=25  FAIL=0`。

**③ 常見 FAIL**

| 訊息 | 意思 | 處置 |
|---|---|---|
| `沒有不存在的 trait（錯字）` FAIL 並列出名稱 | 你改的設定名稱在 JupyterHub 6 不存在（例如 `auth_token_file`） | 照列出的名稱修正；trait 打錯不會報錯，只會被靜默忽略 |
| `DockerSpawner 能以此設定建構` FAIL | 型別錯（例如 `mem_limit = 48` 少了單位） | 依錯誤訊息修正 |
| `hook 為 alice 掛入 lung 去識別資料（ro）` FAIL | 該帳號不在題目群組，或 `mount_policy.yaml` 的群組名稱拼錯 | `id alice` 對照 yaml 的 `groups:` 鍵名 |

## 9.4 啟動並確認只聽 loopback

```bash
sudo systemctl restart jupyterhub
sudo systemctl status jupyterhub --no-pager | head -15
sudo ss -tlnp | grep -E ':8000|:8001'
```

**② 預期**：`127.0.0.1:8000`（Hub／proxy 對外埠）；**不應出現** `0.0.0.0:8000`。

**③ 起不來時**：

```bash
sudo journalctl -u jupyterhub -n 60 --no-pager
```

| 訊息 | 處置 |
|---|---|
| `configurable-http-proxy: command not found` | `sudo npm install -g configurable-http-proxy`；確認 PATH 含 `/usr/local/bin` |
| `Failed to connect to docker` | Hub 服務缺 docker 群組：確認 unit 有 `SupplementaryGroups=docker` 並 `daemon-reload` |
| `ModuleNotFoundError: mount_policy` | `/srv/jupyterhub/mount_policy.py` 沒放好，或 `sys.path` 被改動 |
| `PolicyViolation` | 政策檔有違規條目（這是刻意的 fail-closed）；看訊息指出的路徑 |

## 9.5 先在本機用文字瀏覽器確認登入頁

```bash
curl -sI http://127.0.0.1:8000/hub/login | head -3   # ② 預期：HTTP/1.1 200 OK
```

## 9.6 第一次實際 spawn（在 Tailscale 內測，還沒開外網）

在筆電上建一條臨時 port forward，避免先把服務暴露出去：

```bash
ssh -L 8000:127.0.0.1:8000 <admin>@$TS_IP
# 然後在筆電瀏覽器開 http://127.0.0.1:8000
```

以 `alice` 登入（密碼走 PAM；若帳號已 `passwd -l` 鎖住密碼，先改用 §12.1 的作法設定登入方式）。

**② 預期**：容器起來、JupyterLab 開在 `/workspace`。在 Lab 的 Terminal 內逐條確認：

```bash
id                       # 預期：uid=1002(alice)，不是 0
ls /workspace            # 預期：可寫
ls /data/deid/lung       # 預期：可讀
touch /data/deid/lung/x  # 預期：Read-only file system
ls /data/raw             # 預期：No such file or directory（根本沒掛進來）
python -c "import torch;print(torch.cuda.is_available())"   # 預期：True
```

**③ Spawn 失敗**：

| 症狀 | 最可能原因 | 處置 |
|---|---|---|
| `Timeout waiting for server` | `hub_connect_ip` 不對（階段 7.2 沒改） | 改成 `jupyterhub-net` 的 gateway，重啟 Hub |
| 容器立刻結束 | 映像沒有 `jupyterhub-singleuser`，或版本不符 | `docker run --rm $IMG_MAIN jupyterhub-singleuser --version` |
| `Permission denied: /workspace/.jupyter` | 容器以非 root UID 執行但 HOME 不可寫 | 確認 Dockerfile 有 `ENV HOME=/workspace` 且 `/workspace/<user>` 屬該使用者 |
| PAM 認證一直失敗 | 帳號密碼被鎖 | 見 §12.1 |

---

# 階段 10：Cloudflare Tunnel ＋ Access（約 1 小時）

## 10.1 安裝 cloudflared（arm64）

```bash
sudo mkdir -p --mode=0755 /usr/share/keyrings
curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg | \
  sudo tee /usr/share/keyrings/cloudflare-main.gpg >/dev/null
echo "deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] https://pkg.cloudflare.com/cloudflared any main" | \
  sudo tee /etc/apt/sources.list.d/cloudflared.list
sudo apt-get update && sudo apt-get install -y cloudflared
cloudflared --version     # ② 預期：版本字串，架構 arm64
```

## 10.2 建立 tunnel 與 DNS

```bash
source /root/lab.env
sudo cloudflared tunnel login            # 瀏覽器授權你的網域
sudo cloudflared tunnel create $TUNNEL_NAME
sudo cloudflared tunnel list             # 記下 UUID
sudo cloudflared tunnel route dns $TUNNEL_NAME $HUB_HOST
```

憑證檔會產生在 `/root/.cloudflared/<UUID>.json`，搬到設定目錄：

```bash
UUID=$(sudo cloudflared tunnel list --output json | jq -r ".[] | select(.name==\"$TUNNEL_NAME\") | .id")
sudo install -d -m 0700 /etc/cloudflared
sudo install -m 0600 /root/.cloudflared/$UUID.json /etc/cloudflared/$TUNNEL_NAME.json
sudo install -m 0644 $BUNDLE/configs/cloudflared/config.yml /etc/cloudflared/config.yml
sudo sed -i "s|jupyter.lab.example.edu.tw|$HUB_HOST|g" /etc/cloudflared/config.yml
sudo sed -i "s|/etc/cloudflared/dgx-spark-lab.json|/etc/cloudflared/$TUNNEL_NAME.json|" /etc/cloudflared/config.yml
```

## 10.3 驗證設定並啟動

```bash
cd $BUNDLE && /opt/jupyterhub/bin/python tests/test_external_access.py
sudo cloudflared tunnel --config /etc/cloudflared/config.yml ingress validate
sudo cloudflared service install
sudo systemctl status cloudflared --no-pager | head -10
```

**② 預期**：`test_external_access.py` 17 項 PASS；`ingress validate` 回 OK；服務 active 且 log 顯示已建立多條連線。

## 10.4 設定 Access 政策（這是外網側的第二因素）

在 Cloudflare Zero Trust 後台：

1. **Access → Applications → Add an application → Self-hosted**
2. Application domain 填 `jupyter.<你的網域>`
3. Policy：
   - Action: **Allow**
   - Include: **Emails ending in** `@<學校網域>` **AND** **Emails** 明列使用者清單
   - Session duration：建議 8 小時（不要太長）
4. Authentication：啟用 Google（或學校 SSO）為 identity provider
5. 另外加一條 **Block** 的 catch-all 政策，避免漏網

## 10.5 從外網完整驗收

在筆電（切手機熱點）：

```bash
# 1) 網域可解析且走 Cloudflare
dig +short $HUB_HOST
# 2) 未登入時應被 Access 擋在登入頁（不是 JupyterHub 的登入頁）
curl -sI https://$HUB_HOST | head -5
```

**② 預期**：`curl` 回 302 導向 Cloudflare Access 登入；用瀏覽器開才會看到 Google 登入 → 通過後才進到 JupyterHub 登入頁。

再用**不在名單內**的 Google 帳號試一次，**必須被拒**。

## 10.6 確認主機仍然零曝露

```bash
# 在機器上
sudo ss -tlnp | grep -v '127.0.0.1\|100\.' ; echo "---(上面應該沒有對外監聽)---"
# 在筆電上（關掉 Tailscale）
nmap -Pn -p 22,80,443,8000 <Spark 的公網 IP 或校內 IP>
```

**② 預期**：nmap 全部 `filtered`／`closed`，沒有任何 open。

---

# 階段 11：完整驗收（約 1 小時）

## 11.1 自動化驗證（108 項）

```bash
cd $BUNDLE
sudo env PY=/opt/jupyterhub/bin/python CONFIGPROXY_AUTH_TOKEN=dummy ./tests/run_all.sh
```

**② 預期**：六組全 PASS、總表無 FAIL。**任一項 FAIL 就不要開放給使用者。**

> `test_permissions.sh` 會在 `/data` 底下建立少量測試檔（`secret.dcm`、`case001.npz` 等），驗收完可刪：
> `sudo rm -f /data/raw/secret.dcm /data/deid/*/case001.npz /data/public/luna16/list.csv /shared/models/resnet.pt`

## 11.2 手動驗收清單（逐條打勾）

| # | 驗收項目 | 指令／作法 | 預期 |
|---|---|---|---|
| 1 | GPU 直通 | `docker run --rm --gpus all $IMG_MAIN python -c "import torch;print(torch.cuda.get_device_name(0))"` | 顯示 GB10 |
| 2 | 兩個映像都在 | `docker images \| grep -E 'pytorch-arm64\|radiomics-arm64'` | 兩行 |
| 3 | radiomics 可用 | `docker run --rm $IMG_RAD python -c "from radiomics import featureextractor;print('ok')"` | `ok` |
| 4 | researcher 容器內看不到原始資料 | 以 alice 登入 Hub → Terminal → `ls /data/raw` | No such file or directory |
| 5 | 去識別資料唯讀 | 同上 → `touch /data/deid/lung/x` | Read-only file system |
| 6 | 容器非 root | 同上 → `id` | uid 非 0 |
| 7 | 題目隔離 | 以 bob 登入 → `ls /data/deid/lung` | 不存在／無權 |
| 8 | guest 範圍限縮 | 以 guest1 登入 → `ls /data/deid/lung` | 只有 `cohort2018` |
| 9 | fail-closed 生效 | 暫時在 `mount_policy.yaml` 的 `common` 加一條掛 `/data/raw`，重啟 Hub 後登入 | 容器起不來，log 出現 `PolicyViolation`；**測完立刻改回** |
| 10 | 外網零曝露 | 關 Tailscale 後 `nmap -Pn -p 22,443,8000 <公網IP>` | 無 open |
| 11 | Access 白名單 | 用名單外帳號開 `https://$HUB_HOST` | 被拒 |
| 12 | quota 生效 | `sudo repquota -s /` | 各使用者有上限 |
| 13 | auditd 有記錄 | `sudo ausearch -k phi_raw_access -i \| tail` | 有讀取紀錄 |
| 14 | 閒置回收 | 登入後放置 4 小時 | 容器被 idle-culler 移除 |
| 15 | `docker` 群組為空 | `getent group docker` | 第四欄為空 |
| 16 | 重開機後自動恢復 | `sudo reboot` 後 `systemctl status jupyterhub cloudflared tailscaled` | 皆 active；`/data/raw` **未**自動掛載（刻意） |

第 16 項特別重要：重開機後 `/data/raw` 需要 admin 手動解鎖（§5.1），這是設計而非故障。

## 11.3 把驗收結果存檔

```bash
sudo env PY=/opt/jupyterhub/bin/python CONFIGPROXY_AUTH_TOKEN=dummy \
  ./tests/run_all.sh > /srv/jupyterhub/acceptance-$(date +%F).log 2>&1
```

這份 log 之後要附在給資訊室／IRB 的說明文件裡。

---

# 階段 12：開帳號給使用者（每位約 10 分鐘）

## 12.1 登入方式二選一

**方式 A（建議）：Hub 用 PAM 密碼，SSH 用公鑰**

`01_groups_users.sh` 預設 `passwd -l`（鎖密碼），所以 PAM 登入 Hub 會失敗。若要讓使用者用密碼登入 Hub：

```bash
sudo passwd -u alice           # 解鎖密碼
sudo passwd alice              # 設一組臨時密碼，當面交付
sudo chage -d 0 alice          # 強制首次登入後改密碼
```

SSH 仍只吃公鑰（`PasswordAuthentication no`），所以密碼只能用在 Hub 網頁。

**方式 B：Hub 也不用密碼，改用 Cloudflare Access ＋ 免密碼 Authenticator**

若不想管理密碼，可把 Hub 的 authenticator 換成 `jupyterhub.auth.NullAuthenticator` 之類、僅信任 Access 傳來的身分標頭。**這需要額外驗證 header 不可被偽造**（Access 的 `Cf-Access-Jwt-Assertion` 要驗簽），本手冊不含此設定；初期建議先用方式 A。

## 12.2 SSH 公鑰安裝

```bash
sudo install -d -m 0700 -o alice -g alice /home/alice/.ssh
sudo install -m 0600 -o alice -g alice /tmp/alice_id_ed25519.pub /home/alice/.ssh/authorized_keys
sudo -u alice ssh-keygen -lf /home/alice/.ssh/authorized_keys   # 核對指紋
```

若採 Tailscale SSH，跳過這步，改成把她加入 ACL 的 `group:lab-researcher`。

## 12.3 交付給使用者的東西

1. `ops/RULES.md`（當面說明並簽名）
2. Tailscale 邀請（加入對應群組）
3. Cloudflare Access 名單加入她的學校 Google 帳號
4. 兩條連線方式：
   - 網頁：`https://jupyter.<網域>`（先過 Google 登入，再用 Hub 帳密）
   - VS Code Remote / SSH：`ssh alice@100.x.x.x`（需先安裝 Tailscale）
5. 三件要提醒的事：
   - 長時間訓練用 detached 容器或 tmux，不要跑在 Jupyter kernel 裡
   - 大訓練前在 `ops/GPU_SCHEDULE.md` 登記；GPU **不能切分**，記憶體吃滿別人會 OOM
   - 資料不得複製出去；需要 radiomics 就用 `$IMG_RAD`，不要自己在主映像裡 `pip install pyradiomics`（裝不起來）

## 12.4 新增／移除人員（日後）

```bash
# 新增一位 lung 題目的 researcher
sudo useradd -m -s /bin/bash carol && sudo usermod -aG lung-research carol
sudo install -d -o carol -g carol -m 0700 /workspace/carol
sudo setquota -u carol 400G 500G 0 0 /
sudo vi /srv/jupyterhub/jupyterhub_config.py     # 加入 allowed_users
sudo systemctl restart jupyterhub
cd $BUNDLE && sudo ./tests/test_permissions.sh   # 確認沒有破壞既有權限

# 離開實驗室（當日完成）
sudo chage -E $(date +%F) carol         # 帳號當日到期
sudo pkill -u carol; sudo docker ps --filter "name=jupyter-carol" -q | xargs -r sudo docker rm -f
# Tailscale 後台移除；Cloudflare Access 名單移除；撤銷其公鑰
sudo mv /home/carol/.ssh/authorized_keys{,.revoked}
```

---

# 階段 13：日常維運

## 13.1 每月例行（admin，約 30 分鐘）

```bash
source /root/lab.env
# 1) 更新（刻意手動，避免半夜打斷訓練）——先確認沒人在跑
$BUNDLE/ops/gpu-watch.sh
sudo apt-get update && sudo apt-get upgrade   # 需要時重開機
# 2) 重跑驗證，確認這個月的改動沒有破壞任何保證
cd $BUNDLE && sudo env PY=/opt/jupyterhub/bin/python ./tests/run_all.sh
# 3) 檢查稽核與封鎖紀錄
sudo ausearch -k phi_raw_access -i --start this-month | tail -20
sudo fail2ban-client status sshd
# 4) 檢查容量與 quota
df -h /; sudo repquota -s /
# 5) 備份
sudo rsync -aHAX /data/deid/ /mnt/backup/deid/
```

## 13.2 每季

- 重新確認帳號名單（離職者是否都已撤銷：Linux 帳號、Tailscale、Access 三處）
- 更新映像 tag（例：`lab/pytorch-arm64:2026.12`），舊 tag 保留，供論文重現
- 檢查 `/srv/jupyterhub/acceptance-*.log` 是否有新的一份

## 13.3 觀測指令速查

```bash
$BUNDLE/ops/gpu-watch.sh            # 誰在用 GPU；記憶體看 free -g，不看 nvidia-smi
sudo docker ps                      # 目前有哪些使用者容器
sudo journalctl -u jupyterhub -f    # Hub 即時 log
sudo journalctl -u cloudflared -f   # tunnel 狀態
tailscale status                    # overlay 內誰在線
sudo docker stats --no-stream       # CPU 側資源
```

---

# 附錄 A：疑難排解速查

| 症狀 | 最可能原因 | 處置 |
|---|---|---|
| SSH 進不去（Tailscale 內） | `AllowGroups` 不含你的群組；或 `ListenAddress` 填錯 | 用實體終端／Tailscale SSH 進入，`sudo rm /etc/ssh/sshd_config.d/lab.conf; systemctl reload ssh` 回復 |
| Hub 登入頁打不開（外網） | cloudflared 未連上，或 Access 政策把自己擋了 | `journalctl -u cloudflared -n 50`；Access 後台看 Logs → Access requests |
| 全體突然無法登入 Hub | fail2ban 封了 `127.0.0.1`（tunnel） | `fail2ban-client set jupyterhub unbanip 127.0.0.1`；確認 `ignoreip` 有設 |
| Spawn 卡在 timeout | `hub_connect_ip` 不是 `jupyterhub-net` 的 gateway | 階段 7.2 重新取值並改設定 |
| 容器起不來、log 有 `PolicyViolation` | `mount_policy.yaml` 有違規條目（fail-closed，設計如此） | 照訊息修正政策檔；`python /srv/jupyterhub/mount_policy.py alice` 可預覽掛載 |
| researcher 說看不到資料 | `/data` 權限不是 0755，或她不在題目群組 | `sudo ./tests/test_permissions.sh` 直接指出哪一條 |
| 訓練跑到一半 OOM | 別人把統一記憶體吃滿（系統無法硬擋） | `free -g` ＋ `gpu-watch.sh` 找出 process；落實登記制 |
| `pip install pyradiomics` 在容器內失敗 | 這是預期的（Python 3.12 裝不起來） | 改用 `$IMG_RAD` 專用映像 |
| 某套件在 arm64 裝不起來 | 該版本沒有 aarch64 wheel | `python3 tests/audit_arm64_wheels.py --python <版本>` 確認，必要時降版或改在專用映像編譯 |
| `nvidia-smi` 記憶體顯示 Not Supported | 統一記憶體架構的正常現象 | 改看 `free -g` |
| 重開機後 `/data/raw` 不見了 | 刻意不自動解鎖 LUKS | `cryptsetup open ... && mount`（§5.1） |

# 附錄 B：緊急回復（rollback）

| 要回復什麼 | 怎麼做 |
|---|---|
| sshd 設定 | `sudo rm /etc/ssh/sshd_config.d/lab.conf && sudo systemctl reload ssh`（原 `/etc/ssh/sshd_config` 未被修改） |
| fail2ban | `sudo mv /etc/fail2ban/jail.local{,.off} && sudo systemctl restart fail2ban` |
| JupyterHub | `sudo systemctl stop jupyterhub`；設定檔改壞時從建置包重新 `install` 一份 |
| 掛載政策 | 建置包的 `configs/mount_policy.yaml` 是乾淨版本，直接覆蓋回去 |
| 外網入口 | `sudo systemctl stop cloudflared`（只影響網頁入口，Tailscale SSH 不受影響） |
| 整台機器的存取 | 最後手段：`sudo tailscale down` ＋ `sudo systemctl stop cloudflared jupyterhub`，機器回到只有實體終端可進的狀態 |
| fstab 改壞無法開機 | 用 `fstab.bak.<日期>` 在救援模式回復 |

# 附錄 C：使用者端快速上手（可直接轉貼給同學）

**網頁（最簡單）**

1. 瀏覽器開 `https://jupyter.<網域>`
2. 用**學校 Google 帳號**登入（這是 Cloudflare Access 的身分驗證）
3. 再輸入實驗室給你的 Hub 帳號密碼 → JupyterLab 會開在你的 `/workspace`
4. 你的資料夾：`/workspace`（可寫）、`/data/deid/<你的題目>`（唯讀）、`/data/public/luna16`（唯讀）、`/shared/models`（組內共用）

**VS Code Remote / SSH**

1. 安裝 Tailscale，用被邀請的帳號登入
2. `ssh <你的帳號>@100.x.x.x`（位址向 admin 索取）
3. VS Code 裝 Remote-SSH 擴充，Host 填同一位址

**長時間訓練（重要）**

```bash
# 不要跑在 Jupyter kernel 裡 —— 連線斷掉就沒了
tmux new -s train
python train.py 2>&1 | tee /workspace/logs/train_$(date +%F).log
# Ctrl-B D 離開；下次 tmux attach -t train
```

**規矩三條**

1. 大訓練前在 `/shared/GPU_SCHEDULE.md` 登記時段（GPU 不能切分，記憶體吃滿別人會 OOM）
2. 資料不得複製到個人筆電、外接硬碟或雲端
3. 要用 pyradiomics 請用 `lab/radiomics-arm64` 映像，不要在主環境 `pip install`（裝不起來）
