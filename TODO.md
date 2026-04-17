# Autobuild Onetree — 待修清單

> 內部 Build Server，依實際影響排序
> 建立日期：2026-04-17

---

## P0 — 影響系統穩定性

- [x] **記憶體洩漏止血**：Uvicorn 跑 2 個月佔 30GB RAM（峰值 89GB）。加 systemd `RuntimeMaxSec=86400` 每日自動重啟
- [x] **Housekeeping prune 改進**：prune 邏輯修正 — artifacts/logs 保留（原設計正確），新增清理 HOME 殘留目錄 (.cache/.config/.local/.npm)，prune 失敗改為 warning 而非靜默
- [x] **清理 `/opt/autobuild/workspace/job-{7,8}`**：已手動刪除，釋放系統碟 ~13 GB

## P1 — 影響維護性與正確性

- [ ] **時區雙重轉換**：`format_datetime_taipei()` 在 route 和 template filter 被雙重呼叫，顯示時間可能 +16 小時。需統一為單一轉換點
- [ ] **`requirements.txt` 版本鎖定**：目前無 pin 版本，下次部署 `pip install` 可能拉到 breaking change。跑一次 `pip freeze`
- [ ] **Dual DB 架構統一**：SQLAlchemy ORM（SystemSettings）+ raw sqlite3（jobs）並存，增加維護複雜度。長期應統一
- [x] **`_spec_locks` dict 記憶體洩漏**：已加上限 200 + 自動清理未持有的 lock
- [ ] **啟用 SSE 替代 JSON polling**：SSE endpoint 已實作但前端未使用，目前每 4-5 秒 poll 一次，效率差且 log 更新不即時

## P2 — 良好實踐，有空再做

- [ ] **`.env` 權限收緊**：目前 `-rw-r--r--` 所有人可讀（含 SMTP 密碼），改為 `0600`
- [ ] **GitLab token 儲存**：明文 JSON 存在 filesystem，目前 secrets 目錄已限 `scm-bmc` group，風險可控
- [ ] **SQL injection in db migration**：`app/db.py` 用 f-string 拼 ALTER TABLE，值來自程式碼常量非用戶輸入，風險極低
- [ ] **Path traversal 檢查**：`routes/jobs.py` `_safe_job_dir()` 用字串比對，應改用 `Path.is_relative_to()`
- [ ] **CSRF 保護**：內網信任用戶，風險低，但 POST endpoint 目前皆無 CSRF token
- [ ] **HTTPS**：純內網可接受，若需要可加自簽憑證
- [ ] **Systemd security directives**：目前 `ProtectSystem=off`，內部機器風險可控
- [ ] **Port 8080 file server**：`python3 -m http.server 8080` 由 nathan 跑在 0.0.0.0，確認是否仍需要
