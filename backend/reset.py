from __future__ import annotations

from sqlalchemy import delete

from app.database import Base, SessionLocal, engine
from app.models import DailySnapshot, Tag, TradeLog, trade_tags
from app.services.goal import get_settings


def reset() -> None:
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    get_settings(db)
    db.execute(delete(trade_tags))
    db.query(TradeLog).delete()
    db.query(DailySnapshot).delete()
    db.query(Tag).delete()
    db.commit()
    trades = db.query(TradeLog).count()
    snaps = db.query(DailySnapshot).count()
    db.close()
    print(f"Cleared journal. trades={trades} snapshots={snaps}. Settings kept.")


if __name__ == "__main__":
    reset()
