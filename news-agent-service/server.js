import "dotenv/config";
import cors from "cors";
import express from "express";
import { askAgent } from "./news-agent.js";

const app = express();
app.use(cors());
app.use(express.json());

app.get("/health", (_req, res) => res.json({ status: "ok" }));

app.post("/ask", async (req, res) => {
  const { question, history } = req.body || {};
  if (!question || typeof question !== "string" || !question.trim()) {
    return res.status(400).json({ error: "请填写 question" });
  }
  try {
    const result = await askAgent(question.trim(), Array.isArray(history) ? history : []);
    res.json(result);
  } catch (err) {
    console.error("[server] /ask 出错：", err);
    res.status(500).json({ error: err.message || "agent 执行失败" });
  }
});

const port = process.env.PORT || 4001;
app.listen(port, () => {
  console.log(`[news-agent-service] listening on http://127.0.0.1:${port}`);
});
