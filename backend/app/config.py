from pydantic_settings import BaseSettings, SettingsConfigDict
import os
from pathlib import Path

from dotenv import dotenv_values


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./data/board.db"
    cors_origins: str = ("http://localhost:5173,http://127.0.0.1:5173,"
                         "http://localhost:5174,http://127.0.0.1:5174,"
                         "http://localhost:4174,http://127.0.0.1:4174")
    jwt_secret: str = "pnl-board-dev-secret-change-me"
    # 仅本地 .env 填写 Clash 代理；服务器务必留空，否则外网请求会失败
    http_proxy: str = ""
    https_proxy: str = ""
    all_proxy: str = ""
    no_proxy: str = "127.0.0.1,localhost,::1"


settings = Settings()


def apply_proxy_env() -> None:
    """把 .env / Settings 里的代理写进进程环境，供 httpx(trust_env=True)、yfinance 等读取。"""
    # shell（尤其是 Clash 启动的终端）可能自带 ALL_PROXY=socks5://...。
    # 项目 .env 必须优先，否则未安装 socksio 的 httpx 会在创建客户端时直接报错。
    dotenv = dotenv_values(Path(__file__).resolve().parents[1] / ".env")

    def configured(name: str, fallback: str) -> str:
        value = dotenv.get(name)
        return str(value).strip() if value is not None else fallback

    mapping = {
        "HTTP_PROXY": configured("HTTP_PROXY", settings.http_proxy),
        "HTTPS_PROXY": configured("HTTPS_PROXY", settings.https_proxy),
        "ALL_PROXY": configured("ALL_PROXY", settings.all_proxy),
        "NO_PROXY": configured("NO_PROXY", settings.no_proxy),
    }
    mapping.update({key.lower(): value for key, value in list(mapping.items())})
    for key, value in mapping.items():
        text = (value or "").strip()
        if text:
            os.environ[key] = text
        else:
            os.environ.pop(key, None)


apply_proxy_env()
