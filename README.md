# dgx-spark-multiuser

實驗室 NVIDIA DGX Spark 的**多帳號、可從外網登入**研究伺服器建置包。
容器化（JupyterHub ＋ DockerSpawner）、防火牆零 inbound port（Tailscale ＋ Cloudflare Tunnel/Access）、
掛載政策即程式碼（spawn 前驗證，違規即拒絕啟動）。

> ⚠️ 這個 repo 應設為 **private**。它不含任何病患資料或密碼，但描述了伺服器的網路與權限配置。

## 文件

| 文件 | 內容 |
|---|---|
| [`docs/01_Proposal.md`](docs/01_Proposal.md) | 設計理由、架構、風險、時程（給教授／資訊室） |
| [`docs/02_虛擬環境驗證報告-靜態.md`](docs/02_虛擬環境驗證報告-靜態.md) | 設定檔層級的 108 項驗證 |
| [`docs/03_建置操作手冊-Runbook.md`](docs/03_建置操作手冊-Runbook.md) | 真機逐步建置（14 階段） |
| [`docs/04_虛擬機端到端實作報告.md`](docs/04_虛擬機端到端實作報告.md) | Hub 實際運作下的 54 項驗證與兩個新 bug |
| [`CHANGELOG.md`](CHANGELOG.md) | 修正紀錄與**已知待辦**（部署前請先看） |

## 目錄

```
00_bootstrap/   真機建置腳本（帳號、資料樹、quota、docker 網段、Hub 安裝）
configs/        所有設定檔；mount_policy.yaml 是掛載的唯一真實來源
tests/          run_all.sh（靜態 108 項）、e2e_vm.py（端到端）、audit_arm64_wheels.py
vm/             虛擬機覆寫層：只改 GPU／映像／CPU／culler 時間，不碰任何安全設定
ops/            使用規則、GPU 登記表、GPU 觀測腳本
docs/           上表四份文件
```

## 驗證

```bash
# 靜態驗證（需 root：會建立帳號並切換身分測權限）
sudo env PY=/opt/jupyterhub/bin/python ./tests/run_all.sh

# 端到端（Hub 必須已在跑；會設定隨機測試密碼，結束一定要 cleanup）
sudo /opt/jupyterhub/bin/python tests/e2e_vm.py --phase all
sudo /opt/jupyterhub/bin/python tests/e2e_vm.py --phase cleanup

# arm64 套件盤點（需連 PyPI）
python3 tests/audit_arm64_wheels.py --python 312
```

**改任何設定都先跑驗證再部署。** 這個專案發現的 8 個問題中，有 6 個的共同特徵是「不報錯，只是靜靜地不生效」。

## 不要破壞的規則

1. 不把任何人加入 `docker` 群組（等同 root）；需要 CLI 容器者用 rootless Docker。
2. `/data/raw` 不掛進任何容器，包含 admin 自己的；去識別化只在主機 shell 做。
3. 掛載只改 `configs/mount_policy.yaml`，不在 `jupyterhub_config.py` 裡手寫 volumes。
4. Hub 的 `hub_ip` 不可設為 `0.0.0.0`（會繞過 Cloudflare Access）。
5. GPU 無法硬切分（Spark 不支援 MIG）；長時間訓練先在 `ops/GPU_SCHEDULE.md` 登記。
