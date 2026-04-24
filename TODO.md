# Autobuild Onetree — Current TODO

> 只保留目前仍成立的待辦與營運項目
> 更新日期：2026-04-24

## Active Operations Items

- [ ] **確認正式機 `.env` 權限**
  - repo 文件與 deploy script 已要求 `0600`
  - 但仍需要在實際部署主機上確認 `/opt/autobuild/.env` 已套用正確權限與 owner/group

- [ ] **驗證 systemd hardening 邊界**
  - 目前 repo 採保守設定：保留 `RequiresMountsFor=/work`、`RuntimeMaxSec=86400`、`RestartSec=3`、`TimeoutStopSec=30`
  - 若要再加 `ProtectSystem=strict`、`ProtectHome=yes`、`NoNewPrivileges=yes` 等限制，需先在真實 Yocto / git / reverse proxy 流程上驗證

- [ ] **確認是否仍需要 port 8080 file server**
  - 若現場仍有額外的 `python3 -m http.server 8080`，應明確決定保留用途或關閉

- [ ] **決定是否要啟用 HTTPS**
  - 目前文件維持內網 HTTP 部署可接受
  - 若使用場景改變，需補 nginx / certificate / proxy header 對應設定

## Documentation Hygiene

- [ ] **後續功能變更時同步更新文件**
  - 需要一起檢查：`README.md`、`DEPLOY_GUIDE.md`、`USER_GUIDE.md`、`docs/prompt.txt`
  - 特別注意不要再引入以下舊描述：
    - `/srv/autobuild/...`
    - `sudoers` / `NOPASSWD` 為目前必要部署步驟
    - `sudo -u <owner>` 啟動 runner
    - `~/.autobuild/gitlab_token` 為目前 token storage

## Already Resolved Recently

- [x] 文件已對齊目前部署路徑、token root、runner 啟動方式
- [x] `codebase_id` 殘留 job-creation 參數已移除；現行重用 workspace 流程統一使用 `base_job_id`
- [x] `work/` 路徑已成為實際 prune / housekeeping 使用的 canonical job workspace
- [x] job detail 的時區雙重轉換問題已不再成立
- [x] README 已移除過時 `MVP` 與舊部署假設語氣
