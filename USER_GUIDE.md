Autobuild Onetree - 使用者手冊
============================

這份文件只說明日常操作與常見使用問題。若你要從零安裝、搬機、重部署或升級，請改看 `DEPLOY_GUIDE.md`。

一、系統用途
------------
Autobuild Onetree 是內網用的 Yocto build 管理介面，提供：
- Linux PAM 帳號登入
- 建立與追蹤 build job
- 查看即時 log
- 下載 artifacts
- 管理個人 GitLab token
- 清理單一 job workspace 或共用 cache

二、登入
--------
- 使用 Linux 系統帳號登入。
- 只有允許群組內的帳號可登入。

說明：
- 目前預設允許群組是 `scm-bmc`。
- 若帳號密碼正確但仍無法登入，通常是群組權限問題，不是 UI 問題。

三、導覽
--------
左側選單可進入：
- Dashboard
- Create Job
- Jobs
- Recipes
- Profile
- Settings

四、建立 Job
------------
- 進入 Create Job。
- 選擇 recipe。
- 視需要填寫 note、codebase 或 base job。
- 送出後可在 Jobs 頁面追蹤。

建立前先確認：
- Profile 頁面已至少儲存一組有效的 GitLab token。
- 目標 recipe 已存在且內容正確。

五、Job 狀態與詳細頁
-------------------
Job 狀態：
- `Pending`：排隊中
- `Running`：執行中
- `Success`：成功完成
- `Failed`：失敗結束

Job 詳細頁：
- 可查看 log、時間戳與 exit code。
- artifacts 會列在詳細頁。
- 新 job 的 log 不應再出現每行重複兩次；若舊 job 有重複屬於歷史資料。

常見輸出：
- `.bin`
- `.img`
- `.mtd`
- `.static.mtd`

六、Pin 功能
------------
- Jobs 列表與 job 詳細頁都可 pin / unpin。
- 被 pin 的 job 會置頂，且不可被 prune 或 delete。

七、空間清理
------------
- `Prune Workspace` 只會刪除該 job 的 `work/` 目錄。
- log 與 artifacts 會保留。
- Settings 頁面的 Storage Maintenance 可清理：
  - `SState cache`：刪除超過指定天數未被存取的檔案
  - `Downloads`：只刪除 `/work/downloads` 根目錄檔案，不進入子目錄

八、Profile / Tokens
-------------------
- GitLab tokens 由 Profile 頁面維護。
- token 實際存放位置：`/work/autobuild_workspace/secrets/gitlab/<username>.token`
- 若 UI 顯示已保存但 job 仍報 token 缺失，先檢查該檔是否存在、權限是否仍屬於 `scm-bmc`。

建議：
- 變更 token 後，直接再建立一個新 job 驗證。
- 若 repo 分 primary / secondary host，請把對應欄位都填完整。

九、常見問題
-----------
無法登入：
- 確認 Linux 帳號存在。
- 確認帳號屬於 `AUTOBUILD_ALLOWED_GROUP`。
- 若管理員剛修改 `.env` 的允許群組，請重啟 `autobuild`。

Build 顯示 token 缺失：
- 確認 Profile 已存至少一組 token。
- 確認 token 檔存在於 `/work/autobuild_workspace/secrets/gitlab/<username>.token`。

看不到新 log：
- 重新整理 job 詳細頁。
- 確認 job 仍在 `Running`，並請管理員查看 service log。

Artifacts 沒出現：
- 先確認 job 是否真的成功。
- 再確認 recipe / runner 產出的檔案型別是否在收集清單內。

十、給管理員的分流指引
----------------------
以下情況請直接看 `DEPLOY_GUIDE.md` 而不是本手冊：
- 首次安裝新主機
- 重建 `/opt/autobuild` 或 `/work/autobuild_workspace`
- 調整 `.env`、systemd、nginx
- 升級或重部署 Autobuild
- 搬移 DB、workspace、token storage
