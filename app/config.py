from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    app_env: str = "production"
    app_base_url: str = ""
    database_url: str = "sqlite+aiosqlite:///./data/saspro.db"
    telegram_bot_token: str = ""
    telegram_webapp_short_name: str = ""
    telegram_webhook_secret: str = ""
    telegram_channel_id: str = ""
    owner_telegram_id: int = 0
    twelve_data_api_key: str = ""
    finnhub_api_key: str = ""
    panwatch_base_url: str = "http://panwatch:8000"
    panwatch_timeout_seconds: int = 180
    pro_monthly_stars: int = 0
    pro_3month_stars: int = 0
    pro_6month_stars: int = 0
    pro_yearly_stars: int = 0
    monthly_sar: int = 100
    three_month_sar: int = 250
    six_month_sar: int = 500
    yearly_sar: int = 1000
    trial_days: int = 3
    invite_hours: int = 48
    trial_channel_id: str = ""
    expiry_warning_hours: int = 72
    holiday_radar_interval_minutes: int = 30
    cron_secret: str = ""
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

settings = Settings()
