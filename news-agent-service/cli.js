// 命令行快速测试，不用起 Express、不用前端：
//   npm run cli -- "最近有什么关于英伟达的新闻？"
import { askAgent } from "./news-agent.js";

const question = process.argv.slice(2).join(" ");
if (!question) {
  console.error('用法: npm run cli -- "你的问题"');
  process.exit(1);
}

const { answer } = await askAgent(question);
console.log("\n=== 最终回答 ===");
console.log(answer);
