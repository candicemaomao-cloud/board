import enum
from datetime import date, datetime, timezone

from sqlalchemy import Column, Date, DateTime, Enum, Float, ForeignKey, Integer, String, Table, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    """登录用户：admin 管权限与总金额；持仓/交易/快照按 user_id 隔离。"""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(16), default="user", index=True)  # admin | user
    total_amount: Mapped[float] = mapped_column(Float, default=0.0)  # 总金额（本金），管理员可改
    permissions: Mapped[str] = mapped_column(Text, default="[]")  # JSON list of perm codes
    live_symbols: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON: [{symbol,name,source}]
    is_active: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class Side(str, enum.Enum):
    LONG = "LONG"
    SHORT = "SHORT"


class DailySnapshot(Base):
    __tablename__ = "daily_snapshots"
    __table_args__ = (UniqueConstraint("user_id", "date", name="uq_daily_snapshots_user_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    date: Mapped[date] = mapped_column(Date, index=True)
    total_equity: Mapped[float] = mapped_column(Float, nullable=False)
    daily_pnl: Mapped[float] = mapped_column(Float, nullable=False)
    daily_return_pct: Mapped[float] = mapped_column(Float, default=0.0)
    cash_balance: Mapped[float] = mapped_column(Float, default=0.0)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


trade_tags = Table(
    "trade_tags",
    Base.metadata,
    Column("trade_id", ForeignKey("trade_logs.id", ondelete="CASCADE"), primary_key=True),
    Column("tag_id", ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True),
)


class Tag(Base):
    __tablename__ = "tags"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, index=True)


class TradeLog(Base):
    __tablename__ = "trade_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    date: Mapped[date] = mapped_column(Date, index=True)
    symbol: Mapped[str] = mapped_column(String(16), index=True)
    side: Mapped[Side] = mapped_column(Enum(Side), nullable=False)
    pnl_amount: Mapped[float] = mapped_column(Float, nullable=False)
    pnl_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
    tags: Mapped[list[Tag]] = relationship(secondary=trade_tags)
    source: Mapped[str] = mapped_column(String(32), default="manual")
    external_id: Mapped[str | None] = mapped_column(String(64), nullable=True)


class AppSettings(Base):
    __tablename__ = "app_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_a: Mapped[float] = mapped_column(Float, default=11000)
    account_b: Mapped[float] = mapped_column(Float, default=9641)
    current_pnl: Mapped[float] = mapped_column(Float, default=-1000)
    target_profit: Mapped[float] = mapped_column(Float, default=30_000_000)
    monthly_return: Mapped[float] = mapped_column(Float, default=0.08)
    weekly_plan: Mapped[str | None] = mapped_column(Text, nullable=True)
    watchlist: Mapped[str | None] = mapped_column(String(255), nullable=True)
    binance_api_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    binance_api_secret: Mapped[str | None] = mapped_column(String(128), nullable=True)
    binance_symbols: Mapped[str | None] = mapped_column(String(255), nullable=True)
    notify_telegram: Mapped[int] = mapped_column(Integer, default=0)
    notify_whatsapp: Mapped[int] = mapped_column(Integer, default=0)
    notify_wx: Mapped[int] = mapped_column(Integer, default=0)
    telegram_bot_token: Mapped[str | None] = mapped_column(String(128), nullable=True)
    telegram_chat_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    whatsapp_phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    whatsapp_apikey: Mapped[str | None] = mapped_column(String(64), nullable=True)
    wx_webhook: Mapped[str | None] = mapped_column(String(512), nullable=True)
    wx_sendkey: Mapped[str | None] = mapped_column(String(128), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class NotifyRecipient(Base):
    __tablename__ = "notify_recipients"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    notify_telegram: Mapped[int] = mapped_column(Integer, default=0)
    notify_whatsapp: Mapped[int] = mapped_column(Integer, default=0)
    notify_wx: Mapped[int] = mapped_column(Integer, default=0)
    telegram_bot_token: Mapped[str | None] = mapped_column(String(128), nullable=True)
    telegram_chat_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    whatsapp_phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    whatsapp_apikey: Mapped[str | None] = mapped_column(String(64), nullable=True)
    wx_webhook: Mapped[str | None] = mapped_column(String(512), nullable=True)
    wx_sendkey: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class Position(Base):
    __tablename__ = "positions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    platform: Mapped[str] = mapped_column(String(16), index=True)
    status: Mapped[str] = mapped_column(String(16), default="未开始", index=True)
    name: Mapped[str] = mapped_column(String(64), index=True)
    shares: Mapped[float] = mapped_column(Float, default=0.0)
    open_price: Mapped[float] = mapped_column(Float, default=0.0)
    market_value: Mapped[float] = mapped_column(Float, default=0.0)
    open_amount: Mapped[float] = mapped_column(Float, nullable=False)
    close_amount: Mapped[float | None] = mapped_column(Float, nullable=True)
    fee: Mapped[float] = mapped_column(Float, default=0.0)
    opened_on: Mapped[date] = mapped_column(Date, index=True)
    closed_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    expected_days: Mapped[int] = mapped_column(Integer, default=21)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    open_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    close_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    strategy_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    trade_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class Strategy(Base):
    __tablename__ = "strategies"
    __table_args__ = (UniqueConstraint("user_id", "name", name="uq_strategies_user_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), index=True, nullable=True)
    name: Mapped[str] = mapped_column(String(64), index=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    formula: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    side: Mapped[str] = mapped_column(String(8), default="long")
    timeframe: Mapped[str] = mapped_column(String(8), default="1d")
    allow_push: Mapped[int] = mapped_column(Integer, default=0)
    last_notified_asof: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class Alert(Base):
    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    join: Mapped[str] = mapped_column(String(8), default="and")
    indicators: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    recipient_ids: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    allow_push: Mapped[int] = mapped_column(Integer, default=0)
    interval_sec: Mapped[int] = mapped_column(Integer, default=30)
    last_asof: Mapped[str | None] = mapped_column(String(32), nullable=True)
    last_hit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    last_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_error: Mapped[str | None] = mapped_column(String(255), nullable=True)
    last_notified_asof: Mapped[str | None] = mapped_column(String(32), nullable=True)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class RiskPortfolio(Base):
    """已保存的风险投资组合：开启后每日扫描危机系数。"""

    __tablename__ = "risk_portfolios"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    legs: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    loss_limit: Mapped[float] = mapped_column(Float, default=0.05)
    window: Mapped[int] = mapped_column(Integer, default=252)
    horizon_days: Mapped[int] = mapped_column(Integer, default=21)
    enabled: Mapped[int] = mapped_column(Integer, default=0)
    crisis_coefficient: Mapped[float | None] = mapped_column(Float, nullable=True)
    crisis_level: Mapped[str | None] = mapped_column(String(16), nullable=True)
    last_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_error: Mapped[str | None] = mapped_column(String(255), nullable=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_run_date: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    # 一个月预算：目标盈利 vs 实际，用于复盘校准
    entry_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    budget_capital: Mapped[float | None] = mapped_column(Float, nullable=True)
    budget_target_pnl: Mapped[float | None] = mapped_column(Float, nullable=True)
    budget_days: Mapped[int] = mapped_column(Integer, default=30)
    budget_start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    budget_baseline: Mapped[str | None] = mapped_column(Text, nullable=True)
    budget_model_pnl: Mapped[float | None] = mapped_column(Float, nullable=True)
    budget_review: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class RiskPortfolioRun(Base):
    """每日危机系数快照。"""

    __tablename__ = "risk_portfolio_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    portfolio_id: Mapped[int] = mapped_column(Integer, index=True)
    run_date: Mapped[date] = mapped_column(Date, index=True)
    crisis_coefficient: Mapped[float] = mapped_column(Float, nullable=False)
    crisis_level: Mapped[str] = mapped_column(String(16), nullable=False)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class StockWatch(Base):
    """日内策略股票列表：按代码增删改查，可选监听价格并推手机。"""

    __tablename__ = "stock_watches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    strategy: Mapped[str] = mapped_column(String(32), default="mu_dip")  # mu_dip=局部底/顶μ，deriv=导数策略
    # 导数策略专属参数（strategy=deriv 时才用得到）
    fml_stop_loss_atr_mult: Mapped[float] = mapped_column(Float, default=0.5)
    fml_daily_atr_n: Mapped[int] = mapped_column(Integer, default=14)
    fml_volume_high_pct: Mapped[float] = mapped_column(Float, default=90.0)
    fml_vwap_trend_lookback: Mapped[int] = mapped_column(Integer, default=6)
    fml_allow_short: Mapped[int] = mapped_column(Integer, default=0)
    fml_use_shape_filter: Mapped[int] = mapped_column(Integer, default=0)
    fml_shape_confidence_threshold: Mapped[float] = mapped_column(Float, default=40.0)
    fml_close_no_trade_minutes: Mapped[int] = mapped_column(Integer, default=0)
    fml_market_symbol: Mapped[str] = mapped_column(String(16), default="SPY")
    monitor: Mapped[int] = mapped_column(Integer, default=0)
    allow_push: Mapped[int] = mapped_column(Integer, default=0)
    recipient_ids: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    interval_sec: Mapped[int] = mapped_column(Integer, default=60)
    budget: Mapped[float] = mapped_column(Float, default=10000.0)
    hedge_symbol: Mapped[str] = mapped_column(String(16), default="SPY")
    allow_trend: Mapped[int] = mapped_column(Integer, default=0)
    allow_short: Mapped[int] = mapped_column(Integer, default=0)
    day_filter: Mapped[int] = mapped_column(Integer, default=1)
    vol_adapt: Mapped[int] = mapped_column(Integer, default=1)
    integral: Mapped[int] = mapped_column(Integer, default=1)
    hedge: Mapped[int] = mapped_column(Integer, default=0)
    gradient: Mapped[int] = mapped_column(Integer, default=1)
    vol_confirm: Mapped[int] = mapped_column(Integer, default=0)
    vol_z_th: Mapped[float] = mapped_column(Float, default=1.0)
    kelly: Mapped[int] = mapped_column(Integer, default=0)
    last_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    last_action: Mapped[str | None] = mapped_column(String(128), nullable=True)
    last_asof: Mapped[str | None] = mapped_column(String(32), nullable=True)
    last_error: Mapped[str | None] = mapped_column(String(255), nullable=True)
    last_notified_asof: Mapped[str | None] = mapped_column(String(32), nullable=True)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class ArbStrategy(Base):
    """价差 / 回归套利：两条腿 + 对冲系数 + z 开平。"""

    __tablename__ = "arb_strategies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    kind: Mapped[str] = mapped_column(String(16), default="ols")
    timeframe: Mapped[str] = mapped_column(String(8), default="1d")
    leg_a: Mapped[str] = mapped_column(String(32), nullable=False)
    leg_b: Mapped[str] = mapped_column(String(32), nullable=False)
    lookback: Mapped[int] = mapped_column(Integer, default=60)
    entry_z: Mapped[float] = mapped_column(Float, default=2.0)
    exit_z: Mapped[float] = mapped_column(Float, default=0.5)
    stop_z: Mapped[float] = mapped_column(Float, default=3.5)
    beta: Mapped[float | None] = mapped_column(Float, nullable=True)
    notional: Mapped[float] = mapped_column(Float, default=10000.0)
    bt_days: Mapped[int] = mapped_column(Integer, default=0)
    macro_filter: Mapped[int] = mapped_column(Integer, default=0)
    factors: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class DailyWatch(Base):
    """每日观察：纯手动记录，一只股票一行，每天自己填一遍，不接行情/不算指标。"""

    __tablename__ = "daily_watches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    prev_close: Mapped[float | None] = mapped_column(Float, nullable=True)
    prev_open: Mapped[float | None] = mapped_column(Float, nullable=True)
    prev_low: Mapped[float | None] = mapped_column(Float, nullable=True)
    prev_high: Mapped[float | None] = mapped_column(Float, nullable=True)
    current_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    gamma_low: Mapped[float | None] = mapped_column(Float, nullable=True)
    gamma_high: Mapped[float | None] = mapped_column(Float, nullable=True)
    regression_line: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_pain: Mapped[float | None] = mapped_column(Float, nullable=True)
    short_entry_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    long_entry_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    is_potential: Mapped[int] = mapped_column(Integer, default=0)
    market_direction: Mapped[str] = mapped_column(String(8), default="横盘")
    sector: Mapped[str] = mapped_column(String(16), default="其他")
    events: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class StockJournal(Base):
    """股票日志：按日手记行情与建议（空/多/观望），本地入库，供后续 LLM 使用。"""

    __tablename__ = "stock_journals"
    __table_args__ = (UniqueConstraint("user_id", "log_date", name="uq_stock_journals_user_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), index=True)
    log_date: Mapped[date] = mapped_column(Date, index=True)
    stance: Mapped[str] = mapped_column(String(8), default="观望")  # 空 | 多 | 观望
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class TradePlan(Base):
    """日 / 周 / 月计划，按用户隔离。"""

    __tablename__ = "trade_plans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), index=True)
    kind: Mapped[str] = mapped_column(String(8), index=True)  # day | week | month
    title: Mapped[str] = mapped_column(String(128), default="")
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    plan_date: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class CryptoCoin(Base):
    """用户自选虚拟币，按用户隔离。"""

    __tablename__ = "crypto_coins"
    __table_args__ = (UniqueConstraint("user_id", "symbol", name="uq_crypto_coins_user_symbol"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), index=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)  # BTC
    name: Mapped[str] = mapped_column(String(64), default="")
    coingecko_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    binance_symbol: Mapped[str | None] = mapped_column(String(32), nullable=True)  # BTCUSDT
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class CryptoPatternStudy(Base):
    """Saved K-line slice research for later outcome review."""

    __tablename__ = "crypto_pattern_studies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), index=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    interval: Mapped[str] = mapped_column(String(8), index=True)
    slice_start_ts: Mapped[int] = mapped_column(Integer)
    slice_end_ts: Mapped[int] = mapped_column(Integer, index=True)
    horizon: Mapped[int] = mapped_column(Integer, default=3)
    direction: Mapped[str] = mapped_column(String(16), default="震荡")
    probability: Mapped[float] = mapped_column(Float, default=0.0)
    composite_score: Mapped[float] = mapped_column(Float, default=0.0)
    sample_symbols: Mapped[str] = mapped_column(Text, default="[]")
    sample_count: Mapped[int] = mapped_column(Integer, default=0)
    entry_price: Mapped[float] = mapped_column(Float, default=0.0)
    payload: Mapped[str] = mapped_column(Text, default="{}")
    status: Mapped[str] = mapped_column(String(16), default="待验证", index=True)
    actual_return_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    success: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class CryptoPaperTrade(Base):
    """Paper trade optionally associated with a saved pattern study."""

    __tablename__ = "crypto_paper_trades"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), index=True)
    study_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("crypto_pattern_studies.id"), nullable=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    interval: Mapped[str] = mapped_column(String(8))
    trade_type: Mapped[str] = mapped_column(String(12), default="spot")
    leverage: Mapped[float] = mapped_column(Float, default=1.0)
    side: Mapped[str] = mapped_column(String(8), default="long")
    quantity: Mapped[float] = mapped_column(Float, default=0.0)
    remaining_quantity: Mapped[float | None] = mapped_column(Float, nullable=True)
    notional: Mapped[float] = mapped_column(Float, default=0.0)
    entry_price: Mapped[float] = mapped_column(Float)
    target_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    exit_after_bars: Mapped[int] = mapped_column(Integer, default=3)
    entry_ts: Mapped[int] = mapped_column(Integer)
    due_ts: Mapped[int] = mapped_column(Integer, index=True)
    status: Mapped[str] = mapped_column(String(16), default="open", index=True)
    exit_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    pnl_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    pnl_amount: Mapped[float | None] = mapped_column(Float, nullable=True)
    realized_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    fee_paid: Mapped[float] = mapped_column(Float, default=0.0)
    exit_reason: Mapped[str | None] = mapped_column(String(24), nullable=True)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class CryptoStrategy(Base):
    """虚拟币策略：指标组合 + 监听推送。"""

    __tablename__ = "crypto_strategies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), index=True)
    coin_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("crypto_coins.id"), nullable=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    binance_symbol: Mapped[str | None] = mapped_column(String(32), nullable=True)
    name: Mapped[str] = mapped_column(String(64), default="")
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    join: Mapped[str] = mapped_column(String(8), default="and")
    indicators: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    timeframe: Mapped[str] = mapped_column(String(8), default="1d")
    recipient_ids: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    enabled: Mapped[int] = mapped_column(Integer, default=0)
    allow_push: Mapped[int] = mapped_column(Integer, default=0)
    interval_sec: Mapped[int] = mapped_column(Integer, default=300)
    last_asof: Mapped[str | None] = mapped_column(String(32), nullable=True)
    last_hit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    last_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_error: Mapped[str | None] = mapped_column(String(255), nullable=True)
    last_notified_asof: Mapped[str | None] = mapped_column(String(32), nullable=True)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class CryptoStrategySignal(Base):
    """Frozen strategy conclusion created from one monitored candle hit."""

    __tablename__ = "crypto_strategy_signals"
    __table_args__ = (UniqueConstraint("strategy_id", "signal_asof", name="uq_crypto_signal_event"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), index=True)
    strategy_id: Mapped[int] = mapped_column(Integer, ForeignKey("crypto_strategies.id"), index=True)
    strategy_name: Mapped[str] = mapped_column(String(64), default="")
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    binance_symbol: Mapped[str] = mapped_column(String(32), index=True)
    monitor_timeframe: Mapped[str] = mapped_column(String(8), default="1h")
    signal_asof: Mapped[str] = mapped_column(String(64), index=True)
    entry_price: Mapped[float] = mapped_column(Float)
    direction: Mapped[str] = mapped_column(String(16), default="观察", index=True)
    verdict: Mapped[str] = mapped_column(String(255), default="")
    target_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    stop_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    evidence_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    hit_indicators: Mapped[str] = mapped_column(Text, default="[]")
    evidence: Mapped[str] = mapped_column(Text, default="{}")
    report_text: Mapped[str] = mapped_column(Text, default="")
    review_due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    review_status: Mapped[str] = mapped_column(String(24), default="pending", index=True)
    review_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    actual_return_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_favorable_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_adverse_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    outcome: Mapped[str | None] = mapped_column(String(32), nullable=True)
    success: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    review_error: Mapped[str | None] = mapped_column(String(255), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class CryptoCustomStrategy(Base):
    """虚拟币自定义策略实例：算法（如导数策略）+ 币种 + 参数 + 监听推送。"""

    __tablename__ = "crypto_custom_strategies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), index=True)
    coin_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("crypto_coins.id"), nullable=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    binance_symbol: Mapped[str | None] = mapped_column(String(32), nullable=True)
    strategy_key: Mapped[str] = mapped_column(String(32), default="deriv", index=True)
    name: Mapped[str] = mapped_column(String(64), default="")
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    params: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    recipient_ids: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    enabled: Mapped[int] = mapped_column(Integer, default=0)
    allow_push: Mapped[int] = mapped_column(Integer, default=0)
    interval_sec: Mapped[int] = mapped_column(Integer, default=60)
    last_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    last_action: Mapped[str | None] = mapped_column(String(128), nullable=True)
    last_asof: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_direction: Mapped[str | None] = mapped_column(String(16), nullable=True)
    last_error: Mapped[str | None] = mapped_column(String(255), nullable=True)
    last_notified_asof: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class CryptoNewsPush(Base):
    """虚拟币重大新闻推送：每用户一行，定时扫 X / Google 等源，命中大新闻推给所选推送人。"""

    __tablename__ = "crypto_news_push"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), unique=True, index=True)
    enabled: Mapped[int] = mapped_column(Integer, default=0)
    recipient_ids: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    interval_sec: Mapped[int] = mapped_column(Integer, default=300)
    min_score: Mapped[int] = mapped_column(Integer, default=6)
    x_accounts: Mapped[str] = mapped_column(Text, nullable=False, default="")
    seen_keys: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    history: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    last_sources: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    last_error: Mapped[str | None] = mapped_column(String(255), nullable=True)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class StockNewsPush(Base):
    """美股重大新闻推送：结构同 CryptoNewsPush，源与打分规则不同。"""

    __tablename__ = "stock_news_push"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), unique=True, index=True)
    enabled: Mapped[int] = mapped_column(Integer, default=0)
    recipient_ids: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    interval_sec: Mapped[int] = mapped_column(Integer, default=300)
    min_score: Mapped[int] = mapped_column(Integer, default=6)
    x_accounts: Mapped[str] = mapped_column(Text, nullable=False, default="")
    seen_keys: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    history: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    last_sources: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    last_error: Mapped[str | None] = mapped_column(String(255), nullable=True)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class StockAnalysisNote(Base):
    __tablename__ = "stock_analysis_notes"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), index=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class StockScreenPreset(Base):
    """A user's reusable stock-screen configuration."""

    __tablename__ = "stock_screen_presets"
    __table_args__ = (UniqueConstraint("user_id", "name", name="uq_stock_screen_presets_user_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(80))
    config: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class HomeMessageDismissal(Base):
    """A home-page message dismissed by one user for its generated day/key."""

    __tablename__ = "home_message_dismissals"
    __table_args__ = (UniqueConstraint("user_id", "message_key", name="uq_home_message_dismissal"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), index=True)
    message_key: Mapped[str] = mapped_column(String(255), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
