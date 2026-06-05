from pydantic_settings import BaseSettings
from pydantic import field_validator
from typing import List
import os  # noqa


class Settings(BaseSettings):
    # Discord
    discord_bot_token: str = ""
    discord_channel_id: str = ""
    discord_guild_id: str = ""

    # Binance
    binance_base_url: str = "https://fapi.binance.com"
    binance_api_key: str = ""
    binance_api_secret: str = ""

    # Binance Testnet
    binance_testnet_api_key: str = ""
    binance_testnet_api_secret: str = ""
    binance_testnet_base_url: str = "https://testnet.binancefuture.com"

    # Signal config
    default_watchlist: str = "BTC,ETH,BNB,SOL,XRP,ADA,AVAX,DOT,LINK,TON,TRX,DOGE,LTC,UNI,ATOM,NEAR,ARB,OP,SUI,INJ,APT,MATIC,WLD,FIL,FTM"
    default_risk_profile: str = "balanced"
    default_timeframes: str = "15m,1h"
    min_confidence_conservative: int = 80
    min_confidence_balanced: int = 73
    min_confidence_aggressive: int = 50
    scan_interval_seconds: int = 300

    # News
    cryptopanic_api_key: str = ""

    # Backend
    api_port: int = 8000
    secret_key: str = "change_me_in_production"
    cors_origins: str = "http://localhost:3000"

    # Database
    database_url: str = "sqlite:///./signalbot.db"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"

    @property
    def watchlist(self) -> List[str]:
        return [s.strip().upper() for s in self.default_watchlist.split(",") if s.strip()]

    @property
    def timeframes(self) -> List[str]:
        return [t.strip() for t in self.default_timeframes.split(",") if t.strip()]

    @property
    def cors_origins_list(self) -> List[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    def min_confidence(self, profile: str | None = None) -> int:
        p = (profile or self.default_risk_profile).lower()
        return {
            "conservative": self.min_confidence_conservative,
            "balanced": self.min_confidence_balanced,
            "aggressive": self.min_confidence_aggressive,
        }.get(p, self.min_confidence_balanced)


settings = Settings()
