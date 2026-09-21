# CHANGELOG

格式：每一項都註明「在哪一層驗證中被發現」。靜態驗證抓不到的問題，標示為 VM 端到端。

## [0.1.0] — 2026-09-21

第一個可部署版本。沙盒驗證：靜態 108 項 ＋ 虛擬機端到端 54 項 ＋ 清理 6 項，全部通過。
尚未在 DGX Spark 真機上驗收（見 `docs/03_建置操作手冊-Runbook.md` 階段 11）。

### 安全性修正
- **Hub API 不再綁 0.0.0.0**（VM 端到端）。原設定使 Hub 登入頁在所有網卡的 `:8081` 可達，
  能繞過 Cloudflare Access 直接對 PAM 嘗試密碼。改為只綁 `jupyterhub-net` 閘道，閘道位址由 docker SDK 自動查詢。
- **e2e 測試不再寫死密碼**。改為每次隨機產生，存於 `/root/.e2e_vm_passwords.json`（0600），
  並新增 `--phase cleanup`：鎖回密碼、刪除測試帳號、刪除密碼檔。
- `/data/raw` 由 `0700 root:root` 改為 `2750 root:phi-raw` ＋ default ACL（靜態）。

### 功能性修正
- **idle-culler 補上 `read:servers` scope**（VM 端到端）。缺少時 culler 看不到任何伺服器，閒置容器永遠不回收，且不報錯。
- `hub_connect_ip` 不再寫死 172.17.0.1（VM 端到端）。user-defined bridge 的閘道實測為 172.18.0.1。
- `/data` 改為 `0755`，否則 researcher 無法穿越到 `/data/deid`（靜態）。
- `/data/raw` 加 default ACL，否則 admin 匯入的檔案自己讀不到（靜態）。
- `ConfigurableHTTPProxy.auth_token_file` 不存在，改用 `auth_token` ＋ `EnvironmentFile`（靜態）。
- fail2ban JupyterHub filter 的 failregex 不可含行首日期，原寫法永遠 0 matched（靜態）。
- 掛載驗證器的拒絕清單中，`/` 只比對根目錄本身，原寫法擋掉所有合法掛載（靜態）。

### 已知限制與待辦
- [ ] `docs/01_Proposal.md` §3.5／§10.1 與 `docs/03_建置操作手冊-Runbook.md` §7.2、§9.2 尚未反映上述兩個 VM 端到端修正。
      **Runbook §7.2 的手動查閘道步驟與 §9.2 的 `sed hub_connect_ip` 已不需要，請跳過。**
- [ ] Runbook §12.4／附錄 A 需補上：停止使用者伺服器必須走 Hub API，直接 `docker rm` 會使使用者卡在 HTTP 424。
- [ ] SSH 真實登入的端到端測試（公鑰可進、密碼／guest／root 被拒）尚未實作。
- [ ] GPU 直通、arm64 映像建置、Tailscale／Cloudflare 實連、quota、auditd 只能在真機驗證。
