"""
應用全域設定管理
統一時區、超時、併發等系統級配置
"""
import os
from dataclasses import dataclass
from zoneinfo import ZoneInfo


@dataclass
class AppSettings:
    """應用核心設定"""
    
    # 時區設定
    system_timezone: str = "Asia/Taipei"
    
    # 構建超時（秒）
    build_timeout_seconds: int = 3600 * 6  # 6小時
    
    # 日誌輪詢間隔（毫秒）
    log_polling_interval_ms: int = 1000
    
    # 最大併發任務數
    max_concurrent_jobs: int = 3
    
    # 最小磁碟空間（GB）
    disk_min_free_gb: int = 5
    
    # 後臺任務週期檢查間隔（秒）
    housekeeping_interval_seconds: int = 3600  # 1小時
    
    @property
    def tz(self) -> ZoneInfo:
        """取得時區物件"""
        try:
            return ZoneInfo(self.system_timezone)
        except Exception:
            return ZoneInfo("Asia/Taipei")
    
    @classmethod
    def from_env(cls) -> "AppSettings":
        """從環境變數加載設定"""
        return cls(
            system_timezone=os.getenv("AUTOBUILD_TIMEZONE", "Asia/Taipei"),
            build_timeout_seconds=int(os.getenv("AUTOBUILD_BUILD_TIMEOUT", 21600)),
            log_polling_interval_ms=int(os.getenv("AUTOBUILD_LOG_POLLING_MS", 1000)),
            max_concurrent_jobs=int(os.getenv("AUTOBUILD_MAX_CONCURRENT_JOBS", 3)),
            disk_min_free_gb=int(os.getenv("AUTOBUILD_DISK_MIN_FREE_GB", 5)),
            housekeeping_interval_seconds=int(os.getenv("AUTOBUILD_HOUSEKEEPING_INTERVAL", 3600)),
        )


# 全域設定實例
app_settings = AppSettings.from_env()
