from collections.abc import Generator
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings

if settings.database_url.startswith("sqlite"):
    Path("data").mkdir(exist_ok=True)
    engine = create_engine(
        settings.database_url,
        connect_args={"check_same_thread": False},
    )
else:
    engine = create_engine(settings.database_url)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def ensure_schema() -> None:
    statements = [
        "ALTER TABLE app_settings ADD COLUMN weekly_plan TEXT",
        "ALTER TABLE app_settings ADD COLUMN watchlist VARCHAR(255)",
        "ALTER TABLE app_settings ADD COLUMN binance_api_key VARCHAR(128)",
        "ALTER TABLE app_settings ADD COLUMN binance_api_secret VARCHAR(128)",
        "ALTER TABLE app_settings ADD COLUMN binance_symbols VARCHAR(255)",
        "ALTER TABLE trade_logs ADD COLUMN source VARCHAR(32) DEFAULT 'manual'",
        "ALTER TABLE trade_logs ADD COLUMN external_id VARCHAR(64)",
        "ALTER TABLE positions ADD COLUMN open_price FLOAT DEFAULT 0",
        "ALTER TABLE positions ADD COLUMN fee FLOAT DEFAULT 0",
        "ALTER TABLE positions ADD COLUMN open_reason VARCHAR(64)",
        "ALTER TABLE positions ADD COLUMN close_reason VARCHAR(64)",
        "ALTER TABLE positions ADD COLUMN strategy_id INTEGER",
        "ALTER TABLE positions ADD COLUMN expected_days INTEGER DEFAULT 21",
        "ALTER TABLE strategies ADD COLUMN side VARCHAR(8) DEFAULT 'long'",
        "ALTER TABLE app_settings ADD COLUMN notify_telegram INTEGER DEFAULT 0",
        "ALTER TABLE app_settings ADD COLUMN notify_whatsapp INTEGER DEFAULT 0",
        "ALTER TABLE app_settings ADD COLUMN notify_wx INTEGER DEFAULT 0",
        "ALTER TABLE app_settings ADD COLUMN telegram_bot_token VARCHAR(128)",
        "ALTER TABLE app_settings ADD COLUMN telegram_chat_id VARCHAR(64)",
        "ALTER TABLE app_settings ADD COLUMN whatsapp_phone VARCHAR(32)",
        "ALTER TABLE app_settings ADD COLUMN whatsapp_apikey VARCHAR(64)",
        "ALTER TABLE app_settings ADD COLUMN wx_webhook VARCHAR(512)",
        "ALTER TABLE app_settings ADD COLUMN wx_sendkey VARCHAR(128)",
        "ALTER TABLE alerts ADD COLUMN last_notified_asof VARCHAR(16)",
        "ALTER TABLE alerts ADD COLUMN allow_push INTEGER DEFAULT 0",
        "ALTER TABLE alerts ADD COLUMN recipient_ids TEXT DEFAULT '[]'",
        "ALTER TABLE strategies ADD COLUMN allow_push INTEGER DEFAULT 0",
        "ALTER TABLE strategies ADD COLUMN last_notified_asof VARCHAR(64)",
        "ALTER TABLE strategies ADD COLUMN timeframe VARCHAR(8) DEFAULT '1d'",
        "ALTER TABLE alerts ADD COLUMN interval_sec INTEGER DEFAULT 30",
        "ALTER TABLE risk_portfolios ADD COLUMN entry_date DATE",
        "ALTER TABLE risk_portfolios ADD COLUMN budget_capital FLOAT",
        "ALTER TABLE risk_portfolios ADD COLUMN budget_target_pnl FLOAT",
        "ALTER TABLE risk_portfolios ADD COLUMN budget_days INTEGER DEFAULT 30",
        "ALTER TABLE risk_portfolios ADD COLUMN budget_start_date DATE",
        "ALTER TABLE risk_portfolios ADD COLUMN budget_baseline TEXT",
        "ALTER TABLE risk_portfolios ADD COLUMN budget_model_pnl FLOAT",
        "ALTER TABLE risk_portfolios ADD COLUMN budget_review TEXT",
        "ALTER TABLE arb_strategies ADD COLUMN notional FLOAT DEFAULT 10000",
        "ALTER TABLE arb_strategies ADD COLUMN bt_days INTEGER DEFAULT 0",
        "ALTER TABLE arb_strategies ADD COLUMN factors TEXT",
        "ALTER TABLE stock_watches ADD COLUMN strategy VARCHAR(32) DEFAULT 'mu_dip'",
        "ALTER TABLE stock_watches ADD COLUMN fml_stop_loss_atr_mult FLOAT DEFAULT 0.5",
        "ALTER TABLE stock_watches ADD COLUMN fml_daily_atr_n INTEGER DEFAULT 14",
        "ALTER TABLE stock_watches ADD COLUMN fml_volume_high_pct FLOAT DEFAULT 90.0",
        "ALTER TABLE stock_watches ADD COLUMN fml_vwap_trend_lookback INTEGER DEFAULT 6",
        "ALTER TABLE stock_watches ADD COLUMN fml_allow_short INTEGER DEFAULT 0",
        "ALTER TABLE stock_watches ADD COLUMN fml_use_shape_filter INTEGER DEFAULT 0",
        "ALTER TABLE stock_watches ADD COLUMN fml_shape_confidence_threshold FLOAT DEFAULT 40.0",
        "ALTER TABLE stock_watches ADD COLUMN fml_close_no_trade_minutes INTEGER DEFAULT 0",
        "ALTER TABLE stock_watches ADD COLUMN fml_market_symbol VARCHAR(16) DEFAULT 'SPY'",
        "ALTER TABLE daily_watches ADD COLUMN short_entry_price FLOAT",
        "ALTER TABLE daily_watches ADD COLUMN long_entry_price FLOAT",
        "ALTER TABLE daily_watches ADD COLUMN is_potential INTEGER DEFAULT 0",
        "ALTER TABLE daily_watches ADD COLUMN max_pain FLOAT",
        "ALTER TABLE daily_watches ADD COLUMN sector VARCHAR(16) DEFAULT '其他'",
        "ALTER TABLE positions ADD COLUMN user_id INTEGER",
        "ALTER TABLE trade_logs ADD COLUMN user_id INTEGER",
        "ALTER TABLE daily_snapshots ADD COLUMN user_id INTEGER",
        "ALTER TABLE users ADD COLUMN live_symbols TEXT",
        "ALTER TABLE strategies ADD COLUMN user_id INTEGER",
    ]
    with engine.begin() as conn:
        try:
            conn.execute(text("CREATE TABLE IF NOT EXISTS schema_flags (key VARCHAR(64) PRIMARY KEY)"))
        except Exception:
            pass

        # 指标改按用户隔离：清空历史「我的指标」（只执行一次）
        try:
            done = conn.execute(
                text("SELECT 1 FROM schema_flags WHERE key = 'strategies_user_scoped_v1'")
            ).fetchone()
        except Exception:
            done = True
        if not done:
            try:
                conn.execute(text("UPDATE positions SET strategy_id = NULL WHERE strategy_id IS NOT NULL"))
            except Exception:
                pass
            try:
                conn.execute(text("DELETE FROM strategies"))
            except Exception:
                pass
            try:
                conn.execute(text("ALTER TABLE strategies ADD COLUMN user_id INTEGER"))
            except Exception:
                pass
            try:
                conn.execute(text("DROP INDEX IF EXISTS ix_strategies_name"))
            except Exception:
                pass
            try:
                conn.execute(
                    text(
                        "CREATE UNIQUE INDEX IF NOT EXISTS uq_strategies_user_name "
                        "ON strategies(user_id, name)"
                    )
                )
            except Exception:
                pass
            try:
                conn.execute(text("CREATE INDEX IF NOT EXISTS ix_strategies_user_id ON strategies(user_id)"))
            except Exception:
                pass
            try:
                conn.execute(text("INSERT INTO schema_flags(key) VALUES ('strategies_user_scoped_v1')"))
            except Exception:
                pass

        for stmt in statements:
            try:
                conn.execute(text(stmt))
            except Exception:
                pass
        try:
            # 无主指标一律清掉（不属于任何用户）
            conn.execute(text("UPDATE positions SET strategy_id = NULL WHERE strategy_id IN (SELECT id FROM strategies WHERE user_id IS NULL)"))
            conn.execute(text("DELETE FROM strategies WHERE user_id IS NULL"))
        except Exception:
            pass
        try:
            conn.execute(text("DROP INDEX IF EXISTS ix_strategies_name"))
        except Exception:
            pass
        try:
            conn.execute(
                text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS uq_strategies_user_name "
                    "ON strategies(user_id, name)"
                )
            )
        except Exception:
            pass
        try:
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_strategies_user_id ON strategies(user_id)"))
        except Exception:
            pass
        try:
            conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_trade_logs_external_id ON trade_logs(external_id)"))
        except Exception:
            pass
        try:
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_positions_user_id ON positions(user_id)"))
        except Exception:
            pass
        try:
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_trade_logs_user_id ON trade_logs(user_id)"))
        except Exception:
            pass
        try:
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_daily_snapshots_user_id ON daily_snapshots(user_id)"))
        except Exception:
            pass
        try:
            conn.execute(
                text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS uq_daily_snapshots_user_date "
                    "ON daily_snapshots(user_id, date)"
                )
            )
        except Exception:
            pass
        # SQLite 旧的 date UNIQUE 会挡住多用户同日快照；尽量删掉（失败则忽略）
        for idx in (
            "ix_daily_snapshots_date",
            "sqlite_autoindex_daily_snapshots_1",
        ):
            try:
                conn.execute(text(f"DROP INDEX IF EXISTS {idx}"))
            except Exception:
                pass
        try:
            conn.execute(
                text(
                    "UPDATE positions SET open_price = open_amount / shares "
                    "WHERE (open_price IS NULL OR open_price = 0) AND shares > 0 AND open_amount IS NOT NULL"
                )
            )
        except Exception:
            pass
        try:
            conn.execute(
                text(
                    "UPDATE positions SET market_value = open_price * shares, open_amount = open_price * shares "
                    "WHERE status IN ('已持仓', '开始', '未开始') AND open_price > 0 AND shares > 0"
                )
            )
        except Exception:
            pass
        try:
            conn.execute(text("UPDATE positions SET status = '开始' WHERE status = '已持仓'"))
            conn.execute(text("UPDATE positions SET status = '已结束' WHERE status = '未持仓'"))
        except Exception:
            pass
