from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    app_env: str = "production"
    app_base_url: str = ""
    database_url: str = "sqlite+aiosqlite:///./data/saspro.db"
    telegram_bot_token: str = ""
    telegram_webapp_short_name: str = ""
    telegram_webhook_secret: str = ""
    telegram_webhook_auto_configure: bool = True
    telegram_channel_id: str = ""
    telegram_channel_link: str = ""
    owner_telegram_id: int = 0
    twelve_data_api_key: str = ""
    twelve_data_api_keys: str = ""
    finnhub_api_key: str = ""
    finnhub_api_keys: str = ""
    fmp_api_key: str = ""
    fmp_api_keys: str = ""
    # Optional licensed market-data provider. Disabled unless explicitly configured.
    licensed_market_data_url: str = ""
    licensed_market_data_api_key: str = ""
    licensed_market_data_timeout_seconds: int = 10
    panwatch_base_url: str = "http://panwatch:8000"
    panwatch_timeout_seconds: int = 180
    pro_monthly_stars: int = 2250
    pro_3month_stars: int = 6075
    pro_6month_stars: int = 10800
    pro_yearly_stars: int = 18900
    monthly_sar: int = 150
    three_month_sar: int = 405
    six_month_sar: int = 720
    yearly_sar: int = 1260
    trial_days: int = 3
    invite_hours: int = 48
    trial_channel_id: str = ""
    # AI radar: provider-agnostic OpenAI-compatible endpoints.
    ai_radar_enabled: bool = True
    ai_radar_concurrency: int = 2
    ai_radar_timeout_seconds: int = 25
    ai_max_news: int = 5
    ai_max_calls_per_cycle: int = 2
    ai_news_cache_minutes: int = 10
    # Optional Free Claude Code reviewer. Never part of radar gating.
    fcc_reviewer_enabled: bool = False
    fcc_reviewer_base_url: str = ""
    fcc_reviewer_model: str = ""
    fcc_reviewer_timeout_seconds: int = 20
    fcc_reviewer_failure_cooldown_seconds: int = 900
    groq_api_key: str = ""
    groq_api_keys: str = ""
    groq_base_url: str = "https://api.groq.com/openai/v1"
    groq_model: str = "openai/gpt-oss-120b"
    gemini_api_key: str = ""
    gemini_base_url: str = "https://generativelanguage.googleapis.com/v1beta/openai"
    gemini_model: str = "gemini-2.5-flash"
    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_model: str = "openai/gpt-oss-20b:free"
    expiry_warning_hours: int = 72
    holiday_radar_interval_minutes: int = 180
    weekend_radar_interval_minutes: int = 180
    market_brief_enabled: bool = True
    market_brief_window_minutes: int = 65
    radar_interval_minutes: int = 30
    radar_staging_limit: int = 150
    radar_shortlist_limit: int = 60
    twelve_data_scan_fallback_symbols: int = 3
    twelve_data_intraday_symbols: int = 2
    # Safety limits: Twelve Data is optional and must never be allowed to drain the account.
    # 0 disables the local daily request cap; the credit reserve remains active when configured.
    twelve_data_daily_request_cap: int = 30
    twelve_data_reserve_credits: int = 100
    api_key_cooldown_seconds: int = 60
    news_cache_minutes: int = 10
    fmp_news_enabled: bool = True
    market_update_interval_minutes: int = 30
    cron_secret: str = ""
    openterminal_base_url: str = ""
    openterminal_api_url: str = "http://openterminal-api:4000"
    sas_terminal_secret: str = ""
    google_sheets_id: str = ""
    google_sheets_range: str = "Subscriptions"
    google_service_account_json: str = ""
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

settings = Settings()
