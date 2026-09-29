from contextlib import asynccontextmanager
import asyncio
import logging

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings as app_settings
from app.database import Base, SessionLocal, engine, ensure_schema
from app.routers import (
    alerts,
    analytics,
    auth,
    binance,
    budget_models,
    crypto,
    earnings_stocks,
    factors,
    fundamentals,
    macro,
    market,
    plans,
    portfolio,
    positions,
    recipients,
    risk,
    risk_portfolios,
    settings as settings_router,
    snapshots,
    strategy,
    trades,
    arb,
    daily_watch,
    pairs,
    intraday,
    newstrategy,
    news_push,
    options,
    screener,
    stock_journal,
    stock_watches,
    stock_screen,
)
from app.services.auth import ensure_admin_seed, require_user

Base.metadata.create_all(bind=engine)
ensure_schema()

with SessionLocal() as _db:
    ensure_admin_seed(_db)

log = logging.getLogger("alerts")
log_risk = logging.getLogger("risk_portfolios")
log_sw = logging.getLogger("stock_watches")


async def _alert_worker() -> None:
    from app.services.alerts import tick_due_alerts

    await asyncio.sleep(3)
    while True:
        try:
            await asyncio.to_thread(tick_due_alerts)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("alert worker failed")
        await asyncio.sleep(5)


async def _risk_portfolio_worker() -> None:
    """已开启的风险组合：仅在美股收盘后（约 16:05 ET）每天跑一次。"""
    from app.services.risk_portfolios import (
        after_us_close_ready,
        seconds_until_next_post_close,
        tick_due_risk_portfolios,
    )

    await asyncio.sleep(5)
    while True:
        try:
            wait = await asyncio.to_thread(seconds_until_next_post_close)
            remaining = float(wait)
            while remaining > 0:
                chunk = min(remaining, 300.0)
                await asyncio.sleep(chunk)
                remaining -= chunk
                if after_us_close_ready().get("ready"):
                    break
            result = await asyncio.to_thread(tick_due_risk_portfolios)
            if result.get("ran"):
                log_risk.info("post-close crisis scan ran=%s", result.get("ran"))
            # 今日窗口已处理：睡到下一交易日收盘，避免收盘后每 30 秒空转
            wait_next = await asyncio.to_thread(
                lambda: seconds_until_next_post_close(after_attempt=True)
            )
            remaining = float(wait_next)
            while remaining > 0:
                chunk = min(remaining, 300.0)
                await asyncio.sleep(chunk)
                remaining -= chunk
        except asyncio.CancelledError:
            raise
        except Exception:
            log_risk.exception("risk portfolio worker failed")
            await asyncio.sleep(600)


async def _stock_watch_worker() -> None:
    from app.services.stock_watches import tick_due_watches

    await asyncio.sleep(8)
    while True:
        try:
            result = await asyncio.to_thread(tick_due_watches)
            if result.get("ran"):
                log_sw.info(
                    "stock watch ran=%s notified=%s errors=%s",
                    result.get("ran"),
                    result.get("notified"),
                    result.get("errors"),
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            log_sw.exception("stock watch worker failed")
        await asyncio.sleep(15)


async def _shape_prewarm_worker() -> None:
    """独立于 _stock_watch_worker 的后台任务：提前把形态预测模型训练好放进缓存。
    跟 tick_due_watches 那个 to_thread 调用是两个不同的 asyncio task，训练慢
    （20-30秒）不会占用轮询那条线，避免真正该推送信号的那一刻被现场重训卡住。"""
    from app.services.stock_watches import prewarm_shape_models

    await asyncio.sleep(5)
    while True:
        try:
            result = await asyncio.to_thread(prewarm_shape_models)
            if result.get("warmed"):
                log_sw.info("shape model prewarmed for %s", result["warmed"])
            if result.get("errors"):
                log_sw.warning("shape model prewarm errors: %s", result["errors"])
        except asyncio.CancelledError:
            raise
        except Exception:
            log_sw.exception("shape prewarm worker failed")
        await asyncio.sleep(120)


async def _crypto_strategy_worker() -> None:
    from app.services.crypto_strategies import tick_due_crypto_strategies
    from app.services.crypto_custom_strategies import tick_due_custom_strategies

    await asyncio.sleep(10)
    while True:
        try:
            await asyncio.to_thread(tick_due_crypto_strategies)
            await asyncio.to_thread(tick_due_custom_strategies)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("crypto strategy worker failed")
        await asyncio.sleep(20)


async def _news_push_worker() -> None:
    from app.services.news_push import tick_due

    await asyncio.sleep(15)
    while True:
        try:
            await asyncio.to_thread(tick_due)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("news push worker failed")
        await asyncio.sleep(30)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    task = asyncio.create_task(_alert_worker())
    risk_task = asyncio.create_task(_risk_portfolio_worker())
    sw_task = asyncio.create_task(_stock_watch_worker())
    prewarm_task = asyncio.create_task(_shape_prewarm_worker())
    crypto_task = asyncio.create_task(_crypto_strategy_worker())
    news_push_task = asyncio.create_task(_news_push_worker())
    try:
        yield
    finally:
        task.cancel()
        risk_task.cancel()
        sw_task.cancel()
        prewarm_task.cancel()
        crypto_task.cancel()
        news_push_task.cancel()
        for t in (task, risk_task, sw_task, prewarm_task, crypto_task, news_push_task):
            try:
                await t
            except asyncio.CancelledError:
                pass


app = FastAPI(title="P&L Board API", version="1.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in app_settings.cors_origins.split(",") if origin.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

from app.services.auth import require_user  # noqa: E402

_auth = [Depends(require_user)]

app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
app.include_router(plans.router, prefix="/api/plans", tags=["plans"], dependencies=_auth)
app.include_router(snapshots.router, prefix="/api/snapshots", tags=["snapshots"], dependencies=_auth)
app.include_router(trades.router, prefix="/api/trades", tags=["trades"], dependencies=_auth)
app.include_router(analytics.router, prefix="/api/analytics", tags=["analytics"], dependencies=_auth)
app.include_router(settings_router.router, prefix="/api/settings", tags=["settings"], dependencies=_auth)
app.include_router(market.router, prefix="/api/market", tags=["market"], dependencies=_auth)
app.include_router(macro.router, prefix="/api/macro", tags=["macro"], dependencies=_auth)
app.include_router(binance.router, prefix="/api/binance", tags=["binance"], dependencies=_auth)
app.include_router(crypto.router, prefix="/api/crypto", tags=["crypto"], dependencies=_auth)
app.include_router(positions.router, prefix="/api/positions", tags=["positions"], dependencies=_auth)
app.include_router(strategy.router, prefix="/api/strategy", tags=["strategy"], dependencies=_auth)
app.include_router(arb.router, prefix="/api/arb", tags=["arb"], dependencies=_auth)
app.include_router(pairs.router, prefix="/api/pairs", tags=["pairs"], dependencies=_auth)
app.include_router(intraday.router, prefix="/api/intraday", tags=["intraday"], dependencies=_auth)
app.include_router(newstrategy.router, prefix="/api/newstrategy", tags=["newstrategy"], dependencies=_auth)
app.include_router(stock_watches.router, prefix="/api/stock-watches", tags=["stock-watches"], dependencies=_auth)
app.include_router(alerts.router, prefix="/api/alerts", tags=["alerts"], dependencies=_auth)
app.include_router(recipients.router, prefix="/api/recipients", tags=["recipients"], dependencies=_auth)
app.include_router(fundamentals.router, prefix="/api/fundamentals", tags=["fundamentals"], dependencies=_auth)
app.include_router(risk.router, prefix="/api/risk", tags=["risk"], dependencies=_auth)
app.include_router(portfolio.router, prefix="/api/portfolio", tags=["portfolio"], dependencies=_auth)
app.include_router(budget_models.router, prefix="/api/budget-models", tags=["budget-models"], dependencies=_auth)
app.include_router(risk_portfolios.router, prefix="/api/risk-portfolios", tags=["risk-portfolios"], dependencies=_auth)
app.include_router(factors.router, prefix="/api/factors", tags=["factors"], dependencies=_auth)
app.include_router(options.router, prefix="/api/options", tags=["options"], dependencies=_auth)
app.include_router(screener.router, prefix="/api/screener", tags=["screener"], dependencies=_auth)
app.include_router(daily_watch.router, prefix="/api/daily-watch", tags=["daily-watch"], dependencies=_auth)
app.include_router(stock_journal.router, prefix="/api/stock-journal", tags=["stock-journal"], dependencies=_auth)
app.include_router(earnings_stocks.router, prefix="/api/earnings-stocks", tags=["earnings-stocks"], dependencies=_auth)
app.include_router(stock_screen.router, prefix="/api/stock-screen", tags=["stock-screen"], dependencies=_auth)
app.include_router(news_push.router, prefix="/api/news-push", tags=["news-push"], dependencies=_auth)


@app.get("/api/health")
def health():
    return {"status": "ok"}
