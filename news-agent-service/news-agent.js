// 核心：手写的 tool-calling agent 循环，不用 LangChain 的 AgentExecutor 黑盒——
// 目的是把"模型怎么决定调不调工具、调完之后怎么把结果喂回去"这一整个过程写清楚、
// 可以讲明白原理，而不是调一个封装好的函数就完事。
//
// 循环逻辑：
//   1. 把系统提示 + 历史消息 + 这次的问题一起发给模型，模型可以在回复里带
//      tool_calls（"我要调用这个工具，参数是这些"），也可以直接给文字回答。
//   2. 如果带了 tool_calls：依次执行对应的工具函数，把每个工具的返回结果包成
//      ToolMessage 塞回消息历史，然后把完整历史再发给模型一次——这样模型才能
//      "看到"工具执行的结果，接着决定是再调一次工具、还是可以给最终回答了。
//   3. 如果没有 tool_calls：说明模型觉得信息够了，直接把这次回复的文字内容
//      当作最终答案返回。
//   4. 设一个最大轮数上限，防止模型陷入"一直调工具、一直不给答案"的死循环。
import "dotenv/config";
import { ChatAnthropic } from "@langchain/anthropic";
import { AIMessage, HumanMessage, SystemMessage, ToolMessage } from "@langchain/core/messages";
import { allTools } from "./tools.js";

const MAX_ITERATIONS = 5;

const SYSTEM_PROMPT = `你是一个美股新闻分析助手，服务于一个个人美股观察系统。
你可以调用工具查新闻、查股价，但不要瞎编——查不到的信息要如实说查不到。
回答要简洁、说人话，别整一堆免责声明，但如果是在做涨跌判断，提醒一句"仅供参考，不是投资建议"就够了。
能用工具查证的事实性问题，优先调用工具，不要单纯凭自己的知识回答（你的知识可能是过时的，工具里的新闻是实时的）。`;

function buildModel() {
  const apiKey = process.env.ANTHROPIC_API_KEY;
  if (!apiKey) {
    throw new Error("没有配置 ANTHROPIC_API_KEY（在 news-agent-service/.env 里加一行 ANTHROPIC_API_KEY=sk-ant-...）");
  }
  return new ChatAnthropic({
    apiKey,
    model: process.env.ANTHROPIC_MODEL || "claude-sonnet-5",
    temperature: 0.2,
  }).bindTools(allTools);
}

const toolsByName = Object.fromEntries(allTools.map((t) => [t.name, t]));

/**
 * @param {string} question 用户的自然语言问题
 * @param {Array<{role: 'user'|'assistant', content: string}>} history 之前几轮对话（可选，用于多轮上下文）
 * @returns {Promise<{answer: string, steps: Array}>}
 */
export async function askAgent(question, history = []) {
  const model = buildModel();
  const steps = [];

  const messages = [
    new SystemMessage(SYSTEM_PROMPT),
    ...history.map((h) => (h.role === "user" ? new HumanMessage(h.content) : new AIMessage(h.content))),
    new HumanMessage(question),
  ];

  for (let iteration = 0; iteration < MAX_ITERATIONS; iteration++) {
    console.log(`\n[agent] === 第 ${iteration + 1} 轮 ===`);
    const response = await model.invoke(messages);
    messages.push(response);

    const toolCalls = response.tool_calls || [];
    if (!toolCalls.length) {
      console.log(`[agent] 模型没有再要求调用工具，给出最终回答。`);
      steps.push({ type: "final_answer", content: response.content });
      return { answer: response.content, steps };
    }

    console.log(`[agent] 模型要求调用 ${toolCalls.length} 个工具：${toolCalls.map((c) => c.name).join("、")}`);
    for (const call of toolCalls) {
      const tool = toolsByName[call.name];
      let resultText;
      if (!tool) {
        resultText = JSON.stringify({ error: `未知工具：${call.name}` });
        console.warn(`[agent] 模型要求调用不存在的工具：${call.name}`);
      } else {
        console.log(`[agent] 执行工具 ${call.name}，参数：`, call.args);
        try {
          resultText = await tool.invoke(call.args);
        } catch (err) {
          resultText = JSON.stringify({ error: err.message });
          console.error(`[agent] 工具 ${call.name} 执行出错：`, err.message);
        }
      }
      console.log(`[agent] 工具 ${call.name} 返回：`, resultText.slice(0, 200));
      steps.push({ type: "tool_call", name: call.name, args: call.args, result: resultText });
      messages.push(new ToolMessage({ content: resultText, tool_call_id: call.id, name: call.name }));
    }
  }

  console.warn(`[agent] 到达最大轮数（${MAX_ITERATIONS}），强制结束`);
  steps.push({ type: "max_iterations_reached" });
  return {
    answer: "这个问题需要的步骤有点多，我没能在限定轮数内给出确定答案，换个更具体的问法试试？",
    steps,
  };
}
