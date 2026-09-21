# DGX Spark 多帳號外網登入伺服器：建置方案（Proposal）

| | |
|---|---|
| 版本 | v1.0（2026-09-13） |
| 撰寫 | 柏嶢 |
| 對象 | 指導教授；必要時提供給醫院資訊室／IRB |
| 標的機器 | 實驗室配發之 NVIDIA DGX Spark（GB10 Grace Blackwell，128 GB 統一記憶體，4 TB NVMe） |
| 附件 | `dgx-spark-multiuser/` 建置包（設定檔 + bootstrap 腳本 + 6 組自動化驗證）、`驗證報告.md` |

---

## 1. 摘要

本方案把實驗室的 DGX Spark 建成**可從外網登入的多帳號研究伺服器**，並以容器化提供環境隔離與資料存取控制。三項設計決定：

1. **單一收斂入口**：使用者一律經 JupyterHub 起自己的容器，主機上沒有 `docker` 指令權，也沒有共用的 conda 環境。
2. **主機不曝露**：防火牆**不開任何 inbound port**。SSH／VS Code Remote 走 Tailscale overlay，JupyterHub 網頁走 Cloudflare Tunnel ＋ Access（校內 Google 帳號白名單，等同第二因素）。
3. **掛載清單即存取清單**：容器可見的資料由 `mount_policy.yaml` 單一來源決定，spawn 前程式化驗證，違規則**拒絕啟動容器**（fail-closed）。原始院內資料 `/data/raw` 永不掛入任何容器，包含 admin 自己的容器。

方案中所有「不依賴 GPU 與 arm64」的部分，已在虛擬環境（Ubuntu 24.04 沙盒）完整實作並通過 **108 項自動化驗證**，過程中修正了 6 個會實際造成故障或安全破口的錯誤（§10）。需要真機才能確認的項目另列驗收清單（§11.2）。

預估建置時間：**admin 2 個工作日**（不含向資訊室報備的行政時程）。

---

## 2. 需求與前提

| 項目 | 內容 |
|---|---|
| 使用者 | 初期 3–4 人（1 admin、2 researcher、1 短期協作者），預期擴充到 6–8 人 |
| 題目分組 | `lung-research`（肺結節良惡性）、`prostate-research`；資料不得跨題目互看 |
| 資料 | 院內 CT DICOM（含 PHI）＋ 去識別化後的訓練集 ＋ 公開資料集（LUNA16） |
| 硬需求 1 | 必須能從**外網**登入，不限校內網路 |
| 硬需求 2 | 採**容器化（Docker）**，不走「大家 SSH 進來共用一套 conda」的最小作法 |
| 合規前提 | 院內資料放在可從外網登入的機器上，需經教授確認並向資訊室／IRB 報備（§9.4） |

### 2.1 為什麼不先做「SSH ＋ 共用 conda」的最小版本

原本規劃是先做最小版本再升級。既然確定要開外網，這個順序應該倒過來：

| | 純 SSH ＋ 共用 conda | 容器化（本方案） |
|---|---|---|
| 對外暴露面 | 每個使用者都是一個可登入 shell，任一組金鑰外流即主機淪陷 | 單一入口，可統一掛第二因素、逐次稽核、閒置回收 |
| 環境隔離 | 無。一人 `pip install` 升級 numpy，全體實驗跟著變 | 各自的映像與 site-packages，互不影響 |
| arm64 套件問題 | 每人各踩一次編譯的坑 | 編譯一次封成映像，`docker pull` 即用 |
| 可重現性 | 「我機器上可以跑」 | 映像 tag 即環境版本，論文附錄可直接引用 |
| PHI 存取控制 | 靠檔案權限，使用者仍在同一個 namespace | 掛載清單就是存取清單，且可程式化驗證 |
| 建置成本 | 半天 | 2 天 |

多出來的 1.5 天換到：外網可開、環境互不汙染、PHI 掛載可稽核。

---

## 3. 平台事實與三個硬限制

### 3.1 硬體／軟體

| 項目 | 規格 |
|---|---|
| SoC | GB10 Grace Blackwell：20 核 Arm（10× Cortex-X925 ＋ 10× Cortex-A725）＋ Blackwell GPU，FP4 約 1 PFLOP |
| 記憶體 | 128 GB LPDDR5x **統一記憶體**（CPU/GPU 共用），273 GB/s |
| 儲存 | 4 TB NVMe M.2 2242（PCIe Gen5，自加密 SED） |
| 網路 | 1× RJ-45 10 GbE；2× QSFP ConnectX-7（200 Gbps，Spark 對接用）；Wi-Fi 7 |
| 電源 | 240 W 外接電源；GB10 TDP 140 W |
| OS | DGX OS 7（Ubuntu 24.04 arm64 為基底，內建 driver、CUDA、Docker ＋ NVIDIA Container Toolkit、DGX Dashboard） |

### 3.2 三個直接影響設計的硬限制（必須誠實對教授與同學說明）

1. **不支援 MIG / vGPU。** 統一記憶體架構的緣故，GPU 無法硬切成數份分給不同帳號，只能**時間分享**（多個 process 同時提交 kernel，由驅動排程）。容器化解決的是「環境與檔案的隔離」，**不是算力的切分**。
2. **GPU 記憶體上限管不住。** `nvidia-smi` 的 Memory-Usage 顯示 `Not Supported` 屬正常；Docker 的 `--memory` cgroup 限制對 CUDA unified memory 配置也攔不完全。一個人把記憶體吃滿，其他人的訓練會 OOM——只能靠登記制與監控（§8），無法靠系統硬擋。
3. **這是 arm64 平台。** 所有映像都要 aarch64 版本。實際盤點結果比預期樂觀（§7.2）：36 個常用醫學影像套件中 35 個可直接安裝，唯一例外是 `pyradiomics`，且它的問題與架構無關、需要獨立處理。

---

## 4. 架構總覽

```
            外網使用者
   ┌─────────────┴──────────────┐
   │                            │
瀏覽器 https://jupyter.<域名>    SSH / VS Code Remote
   │                            │
Cloudflare Access               Tailscale (WireGuard)
（校內 Google 帳號白名單）        │
   │                            │
Cloudflare Tunnel               │  100.x.x.x overlay
   │  (主機主動向外建立連線)      │
   ▼                            ▼
┌────────────────────────────────────────────────────────┐
│ DGX Spark（inbound port 開放數：0）                     │
│                                                        │
│  cloudflared ──► JupyterHub :8000 (只綁 127.0.0.1)     │
│                      │  PAMAuthenticator = 系統帳號     │
│                      │  pre_spawn_hook：套用掛載政策     │
│                      ▼                                 │
│              DockerSpawner（唯一持有 docker socket）     │
│                      │                                 │
│         ┌────────────┼────────────┐                    │
│      容器 alice    容器 bob     容器 guest1             │
│      UID 1002      UID 1003     UID 1004               │
│      /workspace rw /workspace rw /workspace rw          │
│      /data/deid/lung ro         /data/deid/lung/        │
│                    /data/deid/prostate ro   cohort2018 ro│
│                                                        │
│  sshd (只綁 tailscale0) ──► 主機 shell（researcher/admin）│
│                                                        │
│  /data/raw（原始 PHI）── 不掛入任何容器、不出現在 Jupyter │
│                          檔案樹；LUKS 加密、admin 手動解鎖│
└────────────────────────────────────────────────────────┘
```

**路徑分工**：Tailscale 負責 SSH／VS Code Remote；Cloudflare Tunnel ＋ Access 負責 JupyterHub 網頁。兩者都是主機**主動向外**建立連線，因此不必請資訊室開 port，也不受動態 IP 影響——同時解掉「校方防火牆不給開 port」與「IP 會變」兩個實務問題。

### 4.1 三種外網作法的比較（為什麼不用公網 IP）

| 方案 | 使用者怎麼連 | 需開放的 inbound port | 評價 |
|---|---|---|---|
| 公網 IP ＋ SSH/443 直接對外 | 直連 IP | 22 / 443 | 主機持續暴露在全網掃描下；PHI 環境不建議 |
| Tailscale overlay | 裝 client，以 100.x 私網位址直連 | 0 | SSH、VS Code Remote 走這條 |
| Cloudflare Tunnel ＋ Access | 瀏覽器打 https://jupyter.\<域名\> | 0 | JupyterHub 網頁走這條；可掛 Google 登入 ＋ 網域白名單當第二因素 |

---

## 5. 帳號與權限模型

身分**以 Linux 帳號為準**（JupyterHub 用 `PAMAuthenticator` 直接吃系統帳號）。這樣容器內的 UID、檔案權限與 ACL 全部自動一致，不需維護第二套使用者資料庫。

| 角色 | 群組 | 能做什麼 |
|---|---|---|
| admin（1–2 人） | `sudo`, `phi-raw`, 各題目群組 | 建帳號、建映像、管理 Hub 與 tunnel、讀原始 PHI、去識別化、備份 |
| researcher | 所屬題目群組 | 經 Hub 起容器、讀去識別資料、寫自己的 `/workspace/<user>`、用 GPU、SSH（Tailscale 內） |
| guest／短期協作 | 指定題目群組 ＋ ACL | 唯讀指定子資料夾、**無 SSH**、帳號設到期日（`chage -E`） |

### 5.1 不給任何人 `docker` 群組

傳統 Docker 的 `docker` 群組**等同 root**——可以 `-v /:/host` 把整台主機掛進容器。給 researcher 這個群組，等於前面所有 PHI 權限設計全部白做。兩條合規的路：

1. **JupyterHub ＋ DockerSpawner（主要路徑）**：只有 Hub 這個 systemd 服務持有 docker socket（`SupplementaryGroups=docker`），使用者透過網頁起容器，本身沒有 docker 指令權。
2. **rootless Docker（進階補充）**：需要 CLI 的人各自跑 `dockerd-rootless-setuptool.sh install`，UID 由 user namespace 映射，碰不到別人的檔案，也碰不到 `/data/raw`。

`00_bootstrap/01_groups_users.sh` 會在 `docker` 群組有任何成員時**直接中止**，避免這條線被無聲地破壞。

### 5.2 資料樹與權限（已實測修正）

| 路徑 | 模式 | 擁有者:群組 | 說明 |
|---|---|---|---|
| `/data` | `0755` | root:root | **必須可被所有人穿越**，否則 researcher 連 `/data/deid` 都進不去（實測踩到） |
| `/data/raw` | `2750` ＋ default ACL `g:phi-raw:rx` | root:phi-raw | setgid 讓匯入的原始檔自動屬 `phi-raw`；沒有 default ACL 時，admin 匯入的檔案連自己都讀不到（實測踩到） |
| `/data/deid/lung` | `0750` | root:lung-research | 題目群組唯讀 |
| `/data/deid/prostate` | `0750` | root:prostate-research | 同上，與 lung 互不可見 |
| `/data/public/luna16` | `0755` | root:root | 公開資料集 |
| `/shared/models` | `2775` | root:\<題目群組\> | setgid：新權重檔自動繼承群組，供組內共用 |
| `/workspace/<user>` | `0700` | user:user | 同儕之間互不可見 |

去識別化與資料搬運**只在 admin 的主機 shell 上做，不在容器裡做**——這樣「誰碰過原始資料」永遠只有 admin 的 `auditd` 紀錄那一條路徑。

---

## 6. 掛載政策即程式碼（本方案的核心安全機制）

容器化最大的安全紅利是「掛載清單就是存取清單」。但把清單手寫在 `jupyterhub_config.py` 裡，改錯一個字就破功且沒人會發現。因此本方案把它拆成**政策資料 ＋ 驗證程式 ＋ 單元測試**：

- `configs/mount_policy.yaml`：唯一真實來源。宣告 `deny`（永不掛載）、`readonly_prefixes`（只能 ro）、共同掛載、各題目群組掛載、特定帳號覆寫。
- `configs/mount_policy.py`：依帳號與其系統群組產生 DockerSpawner 的 `volumes`，並在回傳前自我驗證。
- `jupyterhub_config.py` 的 `pre_spawn_hook`：每次 spawn 都跑一次；驗證失敗就丟 `PolicyViolation`，**讓容器起不來**，而不是「掛錯了但容器照起」。

驗證會攔下的情形（皆有對應負向測試，§10）：直接掛 `/data/raw`、掛其子目錄、用 `..` 迴避、用 symlink 指向、掛 `/`（`-v /:/host`）、掛 docker socket、把去識別資料改成 `rw`、掛 `/etc`、掛載模式打錯字、容器內掛載點覆蓋系統目錄。

`pre_spawn_hook` 同時做兩件權限收斂：

- **容器以使用者自己的 UID:GID 執行**（`--user 1002:1008`），寫出來的檔案屬於本人；預設的 root 容器會在 `/workspace`、`/shared/models` 留下 root 檔案，且能繞過群組限制。
- **帶入附加群組**（`group_add`），讓 `/shared/models` 的 setgid 共用在容器內同樣成立。

容器另外丟棄所有 capability（`cap_drop: ALL`）並禁止提權（`no-new-privileges`）。

---

## 7. 映像與 arm64 相依性

### 7.1 主映像

以 `nvcr.io/nvidia/pytorch:<tag>-py3`（arm64）為底，加上 JupyterHub single-user 套件與醫學影像相依，tag 以年月標記（`lab/pytorch-arm64:2026.09`），Dockerfile 進版控。論文附錄引用這個 tag 即完整環境描述。

> 注意：容器內的 `jupyterhub` 套件必須與 Hub 同一 major 版本，否則 `/hub/api` 版本不合會起不來。

### 7.2 aarch64 套件盤點結果（實測，PyPI 現況）

以 `tests/audit_arm64_wheels.py` 盤點 36 個常用套件在 `linux aarch64 / cp312` 的安裝方式：

| 結論 | 數量 | 代表套件 |
|---|---|---|
| 純 Python wheel | 22 | pydicom、nibabel、MONAI、TorchIO、nnU-Netv2、lungmask、TotalSegmentator、timm、transformers |
| 有可用的 aarch64 wheel | 13 | numpy、scipy、pandas、scikit-image、h5py、connected-components-3d；SimpleITK／ITK／OpenCV 是 `cpXY-abi3`（穩定 ABI，cp312 可用）；xgboost 是 `py3-none-aarch64` |
| 只有原始碼、需處理 | **1** | `pyradiomics` |

**結論：「arm64 要自己編譯」的實際範圍只有 pyradiomics 一個。** 這推翻了原先「多數冷門套件要自行編譯」的假設，映像建置時間因此從數小時降到十幾分鐘。

### 7.3 pyradiomics：必須獨立成專用映像

實測發現 `pyradiomics 3.1.0` 在 **Python 3.12 完全裝不起來，且與 CPU 架構無關**，三層都卡：

1. `setup.py` 在建置期 `import numpy`，卻沒宣告在 `build-system.requires` → 隔離建置必然失敗（`ModuleNotFoundError: numpy`）。
2. 關掉 build isolation 後，vendored 的 `versioneer.py` 使用 `configparser.SafeConfigParser`——**Python 3.12 已移除該類別** → `AttributeError`。
3. 把 versioneer patch 掉之後仍失敗：PyPI 的 sdist **漏包** `radiomics/src/cmatrices.h` → `fatal error: cmatrices.h: No such file or directory`。

因此 radiomics 特徵抽取**不放進主映像**，改用 `Dockerfile.radiomics-arm64`：固定 `python:3.11`、`numpy<2`、`--no-build-isolation`，並**從 GitHub repo 安裝**（repo 含完整 C 原始碼，sdist 沒有）。這直接影響肺 CT radiomic feature stability 那條線的環境規劃：兩個容器、兩份環境描述，不要混在一起。

---

## 8. 共用資源約定與監控

GPU 只能時間分享（§3.2 限制 1），所以**制度比技術重要**：

- `ops/GPU_SCHEDULE.md` 登記制：長時間訓練前先登記時段；臨時小實驗不用。
- 每人 `/workspace` 設 quota（`00_bootstrap/03_quota.sh`，預設 soft 400 G / hard 500 G），避免一個實驗把 4 TB 塞滿。
- 監控：DGX Dashboard 看整機負載；`ops/gpu-watch.sh` 列出 GPU process 與對應使用者；**真正會 OOM 的數字看 `free -g`**（統一記憶體），`nvidia-smi` 的記憶體欄位不可信。
- Hub 側 `jupyterhub-idle-culler` 4 小時回收閒置容器；`mem_limit 48G`、`cpu_limit 12` 為 CPU 側防呆（擋不住 CUDA unified memory）。
- **關閉自動更新**，避免半夜升級驅動打斷訓練；改由 admin 每月手動更新。
- 長時間訓練不要開在 Jupyter kernel 裡（連線斷掉就沒了）：用 detached 容器或 tmux，Jupyter 只用來看結果與互動除錯。

---

## 9. 資料安全與合規

### 9.1 主機硬化（外網環境下是必要而非加分）

- `sshd` 只綁 Tailscale 介面：公網介面完全聽不到 22；`PasswordAuthentication no`、`AuthenticationMethods publickey`、`PermitRootLogin no`、`MaxAuthTries 3`、`AllowGroups` 白名單、關閉 X11／TCP／Agent 轉發、`LogLevel VERBOSE`（記下公鑰指紋，可追「哪把金鑰何時進來」）。
- Tailscale ACL 限制誰能連到哪個 port（guest 只有 8000，沒有 22）。
- Cloudflare Access 政策：只允許校內網域的 Google 帳號且在名單內——等同替 JupyterHub 加上第二因素，不必自己實作 TOTP。
- JupyterHub：`allowed_users` 白名單、`admin_users` 明列、閒置回收、只綁 `127.0.0.1`。

### 9.2 fail2ban 的實際有效範圍（重要修正）

經 Cloudflare Tunnel 進來的請求，JupyterHub 看到的來源 IP **一律是 `127.0.0.1`**。若不排除，任何一個使用者打錯幾次密碼，就會把**整條 tunnel** 封掉（全體斷線）。因此：

- `jail.local` 設 `ignoreip = 127.0.0.1/8 ::1`；
- **外網側的暴力登入防護靠 Cloudflare Access（身分驗證）＋ WAF rate limiting，不是 fail2ban**；
- fail2ban 的真正戰場是 **Tailscale overlay 內的 sshd**，以及直接連到 8000 埠的校內來源。

### 9.3 資料保護

- NVMe 本身自加密（SED）；`/data/raw` 另以 LUKS 或 ZFS native encryption 加密，**開機需 admin 手動解鎖**——即使外網入侵取得一般帳號，也拿不到原始資料。
- 備份：內建 NVMe ＋ 外接 NVMe 冷備兩份；外接盒平時拔除、上鎖保管。
- 稽核：`auditd` 監看 `/data/raw` 的所有讀取；JupyterHub 與 Cloudflare Access 的登入紀錄保留 ≥180 天。
- 帳號到期自動停用；離開實驗室者當日撤銷 Tailscale 與 Cloudflare Access 授權。
- 資料不得複製到個人筆電或雲端（`ops/RULES.md`，開帳號時簽署）。
- PHI 不經過外網存取路徑：`/data/raw` 既不掛容器，也不出現在任何 Jupyter 的檔案樹裡。外網使用者最多看得到去識別後的資料。

### 9.4 行政程序（建議在開放外網前完成）

「院內資料放在一台可從外網登入的機器上」是需要被書面同意的事，不宜事後補。建議請教授確認是否需要向醫院資訊室與 IRB 報備，並在報備文件中附上本方案的 §5、§6、§9 與驗證報告——這三節正好對應資訊室最常問的三件事：誰能存取、怎麼保證、出事怎麼查。

---

## 10. 可行性驗證（虛擬環境實作結果）

在 Ubuntu 24.04 沙盒（x86_64、無 GPU、無 Docker daemon）中，把方案裡不依賴 GPU 與 arm64 的部分**完整實作並自動化驗證**。`tests/run_all.sh` 一次跑完：

| 驗證項目 | 斷言數 | 結果 |
|---|---|---|
| 帳號、群組、資料樹權限（`runuser` 逐角色實測 allow/deny） | 23 | PASS |
| 掛載政策（含 10 個負向攻擊測試） | 21 | PASS |
| `jupyterhub_config.py`（trait 存在性、型別、`pre_spawn_hook` 行為） | 25 | PASS |
| `sshd` 硬化（`sshd -t` ＋ `sshd -T` 展開值逐條核對） | 14 | PASS |
| fail2ban 與自訂 JupyterHub filter（比對樣本記錄） | 8 | PASS |
| 外網路徑設定（cloudflared YAML、Tailscale ACL、systemd unit 交叉檢查） | 17 | PASS |
| **合計** | **108** | **PASS** |

另有一項需要網路：`audit_arm64_wheels.py`（36 個套件的 aarch64 可裝性，結果見 §7.2）。

### 10.1 驗證過程中抓到、已修正的 6 個錯誤

若直接照原始規劃部署，這 6 個問題會在真機上發生：

| # | 問題 | 後果 | 修正 |
|---|---|---|---|
| 1 | `/data` 設 `0750 root:phi-raw` | researcher 連 `/data/deid` 都穿越不進去，資料完全讀不到 | `/data` 改 `0755` |
| 2 | `/data/raw` 設 `0700 root:root` | `phi-raw` 群組形同虛設，admin 也要 sudo 才能讀原始資料 | 改 `2750 root:phi-raw` |
| 3 | `/data/raw` 無 default ACL | admin 匯入的檔案因 umask 過緊，連自己都讀不到 | 加 `setfacl -d -m g:phi-raw:rx` ＋ setgid |
| 4 | `c.ConfigurableHTTPProxy.auth_token_file` | 該 trait **不存在**（JupyterHub 6.0），設定被靜默忽略 | 改用 `auth_token` ＋ systemd `EnvironmentFile` |
| 5 | fail2ban filter 的 failregex 含行首日期 | fail2ban 會先剝掉日期才比對，結果**永遠 0 matched**，jail 形同關閉 | failregex 從 `JupyterHub log:` 開始 |
| 6 | 掛載驗證器把 `/` 當前綴比對 | deny 清單放 `/` 會擋掉**所有**合法掛載，沒人能起容器 | `/` 只比對根目錄本身 |

第 4、5、6 項的共同特徵是**失敗時不會報錯，只會靜靜地不生效**——這是把設定做成「有測試的程式碼」而非「一份文件」的主要理由。

---

## 11. 未驗證項目與真機驗收清單

### 11.1 沙盒無法驗證的項目（誠實列出）

| 項目 | 原因 |
|---|---|
| Docker daemon ＋ NVIDIA Container Toolkit 實際起容器 | 沙盒無 Docker daemon |
| `device_requests` 是否真的把 GPU 給到容器 | 無 GPU |
| MIG 不支援、統一記憶體上限攔不住 | 無 GB10 硬體；此結論引自 NVIDIA 官方論壇回覆，未經本人實測 |
| arm64 映像實際建置與 pyradiomics 專用映像 build 通過 | 沙盒為 x86_64（但 §7.3 的三個失敗原因與架構無關，已實測） |
| Tailscale / Cloudflare Tunnel 實連與 Access 政策 | 沙盒無法安裝 client、無網域 |
| disk quota 實際生效 | 沙盒無 loop device，`usrquota` 無法掛載 |
| `auditd` 規則實際記錄 | 沙盒無 systemd／auditd |

### 11.2 真機驗收清單（bootstrap 完成後逐項打勾，任一項未過就不開放外網）

- [ ] `tests/run_all.sh` 在真機上 108 項全 PASS（資料樹與帳號用真實名單）
- [ ] `docker run --rm --gpus all ... torch.cuda.is_available()` 回傳 `True` 且抓到 GB10
- [ ] `docker build --platform linux/arm64` 兩個映像皆成功；radiomics 映像的 `featureClassNames` 驗收指令通過
- [ ] 以 researcher 帳號登入 Hub，容器內 `ls /data/raw` **失敗**、`ls /data/deid/<自己題目>` 成功、`touch /data/deid/<自己題目>/x` **失敗**
- [ ] 以 researcher 帳號登入 Hub，容器內 `id` 顯示自己的 UID（非 0）
- [ ] 以 bob 登入，容器內看不到 lung 資料；以 guest1 登入，只看到指定 cohort
- [ ] 故意把 `mount_policy.yaml` 改成掛 `/data/raw`，容器**起不來**且 Hub log 出現 `PolicyViolation`（測完改回）
- [ ] 關掉 Tailscale 後，從外網 `nmap` 掃主機**看不到任何開放 port**
- [ ] 未在 Cloudflare Access 名單內的 Google 帳號**無法**進入 Hub 登入頁
- [ ] `repquota` 顯示各使用者 quota 生效；`auditd` 有 `/data/raw` 讀取紀錄
- [ ] 閒置 4 小時後容器被 idle-culler 回收
- [ ] `getent group docker` 成員為空

---

## 12. 建置時程與分工

| 階段 | 工作 | 時間 | 負責 |
|---|---|---|---|
| 0 | 與教授確認架構；確認是否需資訊室／IRB 報備 | — | 柏嶢 ＋ 教授 |
| 1 | 帳號、群組、資料樹、quota、`auditd`（`00_bootstrap/01–03`） | 0.5 天 | admin |
| 2 | Tailscale 上線、sshd 硬化、fail2ban | 0.5 天 | admin |
| 3 | 建兩個 arm64 映像、docker 網段、GPU 直通確認（`04`） | 0.5 天 | admin |
| 4 | JupyterHub ＋ DockerSpawner ＋ 掛載政策（`05`） | 0.5 天 | admin |
| 5 | Cloudflare Tunnel ＋ Access | 0.5 天 | admin |
| 6 | 跑完 §11.2 驗收清單、開帳號、簽 `RULES.md` | 0.5 天 | admin ＋ 使用者 |

外部費用：Tailscale 個人／團隊方案與 Cloudflare Tunnel ＋ Access 在本規模下皆為免費額度內；需要一個網域（若學校已有子網域可用則為零成本）。

---

## 13. 風險與緩解

| 風險 | 影響 | 緩解 |
|---|---|---|
| 一人吃滿統一記憶體，他人 OOM | 訓練中斷 | 登記制 ＋ `gpu-watch.sh` ＋ 群組公告；技術上無法硬擋（§3.2） |
| 設定改動造成無聲失效 | 安全機制形同關閉 | 所有設定皆有自動化測試；改動後必跑 `run_all.sh` |
| 有人要求加入 `docker` 群組 | 等同發 root，PHI 設計全破 | bootstrap 腳本硬性檢查 ＋ 提供 rootless Docker 替代路徑 |
| Cloudflare／Tailscale 服務中斷 | 外網暫時無法登入 | 校內網段保留 Tailscale 直連；admin 可臨時經校內存取 |
| 金鑰／帳號外流 | 可讀去識別資料 | 第二因素（Access）＋ 帳號到期 ＋ 原始資料 LUKS 手動解鎖 ＋ 稽核紀錄 |
| pyradiomics 環境與主環境版本漂移 | 特徵不可重現 | 兩個映像各自固定 tag，論文附錄分別引用 |
| admin 只有一人（單點） | 人不在就沒人能維運 | 建議第二位 admin；`RULES.md` 與建置包進版控，交接可複製 |

---

## 附錄 A：建置包檔案清單

```
dgx-spark-multiuser/
├── README.md
├── 00_bootstrap/
│   ├── 01_groups_users.sh          群組、帳號、到期日；docker 群組硬性檢查
│   ├── 02_data_tree.sh             資料樹、setgid、ACL（含 §10.1 的 3 項修正）
│   ├── 03_quota.sh                 /workspace 使用者 quota
│   ├── 04_docker_network.sh        docker 網段 ＋ GPU 直通確認
│   └── 05_jupyterhub_install.sh    Hub 安裝與 systemd 啟用
├── configs/
│   ├── mount_policy.yaml           掛載政策（唯一真實來源）
│   ├── mount_policy.py             政策 → volumes ＋ fail-closed 驗證
│   ├── jupyterhub_config.py        Hub 設定（PAM、DockerSpawner、pre_spawn_hook）
│   ├── systemd/jupyterhub.service  只有此服務持有 docker socket
│   ├── sshd/lab.conf               sshd 硬化 drop-in
│   ├── fail2ban/jail.local         jail（含 ignoreip 修正）
│   ├── fail2ban/filter.d/jupyterhub.conf  自訂 filter（含 datepattern 修正）
│   ├── docker/Dockerfile.pytorch-arm64    主映像
│   ├── docker/Dockerfile.radiomics-arm64  radiomics 專用映像
│   ├── tailscale/acl.json          誰能連哪個 port
│   └── cloudflared/config.yml      tunnel ingress
├── tests/
│   ├── run_all.sh                  一次跑完 6 組驗證（108 項）
│   ├── test_permissions.sh         主機權限 allow/deny
│   ├── test_mount_policy.py        掛載政策正向 ＋ 負向
│   ├── validate_jupyterhub_config.py  Hub 設定靜態驗證
│   ├── test_sshd_config.sh         sshd 語法與生效值
│   ├── test_fail2ban.sh            jail ＋ filter 實測
│   ├── test_external_access.py     外網路徑設定交叉檢查
│   └── audit_arm64_wheels.py       aarch64 套件盤點
└── ops/
    ├── RULES.md                    使用規則（開帳號時簽署）
    ├── GPU_SCHEDULE.md             GPU 登記表
    └── gpu-watch.sh                GPU／統一記憶體觀測
```

## 附錄 B：常用指令速查

```bash
# 新增一位 researcher
usermod -aG lung-research <user> && install -d -o <user> -g <user> -m 0700 /workspace/<user>
# 加入 Hub 白名單後重載
vi /srv/jupyterhub/jupyterhub_config.py && systemctl reload jupyterhub

# 改完任何設定，必跑
PY=/opt/jupyterhub/bin/python tests/run_all.sh

# 看某人的容器會掛到什麼（不啟動容器）
/opt/jupyterhub/bin/python /srv/jupyterhub/mount_policy.py alice

# 短期協作者到期
chage -E 2026-12-31 guest1 && tailscale ... && cloudflare access 名單移除

# GPU 觀測（記憶體看 free -g，不看 nvidia-smi）
ops/gpu-watch.sh
```
