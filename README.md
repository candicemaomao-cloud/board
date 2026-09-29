# P&L Board

股票交易看板：记录每日盈亏、画资金曲线，并在服务端做胜率、盈亏比、最大回撤、标签和星期效应分析。

## 架构

- **前端**：Vue 3 + Vite + ECharts（`frontend/`）
- **后端**：Python FastAPI + SQLAlchemy（`backend/`）
- **数据库**：默认 SQLite（`backend/data/board.db`），可通过 `DATABASE_URL` 换成 PostgreSQL

## 启动

先开后端（会自动建表）：

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python seed.py          # 写入 2026-03 至 2026-08 的演示数据，可跳过
uvicorn app.main:app --reload --port 8000
```

再开前端：

```bash
cd frontend
npm install
npm run dev
```

浏览器打开 [http://localhost:5173](http://localhost:5173)。前端开发服务器会把 `/api` 代理到 `8000` 端口。

## 每天怎么记

1. **记一笔交易**：代码、多空、盈亏、策略/心态标签（如 `突破`、`FOMO追高`）。月底看标签图，就能知道哪种模式在赚钱。
2. **记当日资金**：收盘总资产。用来画资金曲线和回撤；不填当日盈亏时，会用与上一交易日总资产的差额推算。

两套数据分开：交易日志负责复盘，资金快照负责账户曲线。

## 主要接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/analytics/overview` | 卡片指标、资金曲线、日历、星期、标签、连胜连亏 |
| GET/POST | `/api/snapshots` | 每日资金快照 |
| GET/POST | `/api/trades` | 交易明细 |
| GET | `/api/trades/meta/tags` | 已有标签 |

查询参数：`start`、`end` 为 `YYYY-MM-DD`。

## 核心指标

- **胜率**：盈利交易日 / 有记录的交易日
- **盈亏比 (Profit Factor)**：盈利日金额合计 / 亏损日金额绝对值
- **最大回撤**：资金曲线相对前高的最大下跌
- **星期效应**：周一到周五的累计盈亏，用来看是不是周五特别容易亏
- **标签分析**：按策略/心理标签汇总胜率和盈亏
