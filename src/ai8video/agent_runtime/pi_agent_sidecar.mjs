import readline from "node:readline";

import { Agent } from "@earendil-works/pi-agent-core";
import { Type, createModels, createProvider } from "@earendil-works/pi-ai";
import { openAICompletionsApi } from "@earendil-works/pi-ai/api/openai-completions.lazy";


const PROTOCOL_VERSION = 1;
const pendingToolCalls = new Map();
let activeRequestId = null;


function emit(payload) {
  process.stdout.write(`${JSON.stringify(payload)}\n`);
}


function errorText(error) {
  return String(error?.message || error || "unknown error").slice(0, 2000);
}


function agentTools(requestId) {
  const definitions = [
    {
      name: "prepare_video_plan",
      label: "准备视频方案",
      description: "自主理解用户目标并准备结构化视频方案。工具栏中的共享设置由服务端注入，不要自行覆盖。只负责规划，不生成视频。",
      parameters: Type.Object({
        goal: Type.String({ description: "结合完整对话理解出的本轮任务目标，不要求用户使用固定口令" }),
        videoCount: Type.Optional(Type.Integer({
          minimum: 1,
          maximum: 50,
          description: "仅在智能分集模式且用户语义明确限定数量时填写；手动批量数量由工具栏设置决定",
        })),
        styleHint: Type.Optional(Type.String()),
        coreKeywords: Type.Optional(Type.String()),
        durationSeconds: Type.Optional(Type.Integer({ minimum: 1, maximum: 60 })),
        ratio: Type.Optional(Type.String()),
        resolution: Type.Optional(Type.String()),
        preset: Type.Optional(Type.String()),
        constraints: Type.Optional(Type.Array(Type.String())),
      }),
    },
    {
      name: "review_video_plan",
      label: "审核视频方案",
      description: "审核最新视频方案，返回 accept、revise 或 reject。",
      parameters: Type.Object({
        focus: Type.Optional(Type.String()),
      }),
    },
    {
      name: "generate_video_batch",
      label: "生成视频批次",
      description: "提交已经审核的视频方案。Runtime 会负责轮询、下载、后处理和归档。",
      parameters: Type.Object({
        count: Type.Integer({ minimum: 1, maximum: 50 }),
        retryFailedOnly: Type.Optional(Type.Boolean()),
        reason: Type.Optional(Type.String()),
      }),
    },
    {
      name: "inspect_generation_result",
      label: "检查生成结果",
      description: "读取最新终态任务、成功文件、归档和失败原因。",
      parameters: Type.Object({
        includeFailures: Type.Optional(Type.Boolean()),
      }),
    },
    {
      name: "archive_and_deliver",
      label: "归档并交付",
      description: "整理已经完成且可交付的结果。不得发布到外部平台。",
      parameters: Type.Object({
        includePartialSuccess: Type.Optional(Type.Boolean()),
        publishExternally: Type.Optional(Type.Boolean()),
      }),
    },
    {
      name: "task_user",
      label: "询问用户",
      description: "遇到实质歧义、额外成本或高风险操作时暂停并询问用户。",
      parameters: Type.Object({
        question: Type.String(),
        reason: Type.String(),
        choices: Type.Optional(Type.Array(Type.String())),
      }),
    },
  ];
  return definitions.map((definition) => ({
    ...definition,
    executionMode: "sequential",
    execute: async (toolCallId, parameters, signal, onUpdate) => {
      if (signal?.aborted) {
        throw new Error("Agent action cancelled");
      }
      onUpdate?.({
        content: [{ type: "text", text: `正在校验 ${definition.label}` }],
        details: { phase: "policy" },
      });
      const result = await bridgeToolCall({
        requestId,
        toolCallId,
        name: definition.name,
        arguments: parameters,
      });
      if (!result.ok) {
        throw new Error(result.error || `${definition.name} failed`);
      }
      return {
        content: [{ type: "text", text: JSON.stringify(result.result) }],
        details: result.result,
        terminate: true,
      };
    },
  }));
}


function bridgeToolCall(payload) {
  return new Promise((resolve, reject) => {
    const key = `${payload.requestId}:${payload.toolCallId}`;
    const timeout = setTimeout(() => {
      pendingToolCalls.delete(key);
      reject(new Error("Python tool bridge timed out"));
    }, 15 * 60 * 1000);
    pendingToolCalls.set(key, {
      resolve: (value) => {
        clearTimeout(timeout);
        resolve(value);
      },
    });
    emit({ type: "tool_call", ...payload });
  });
}


function buildModel(config) {
  const providerId = "ai8video-bound";
  const model = {
    id: String(config.model || ""),
    name: String(config.model || "AI8video Agent Model"),
    api: "openai-completions",
    provider: providerId,
    baseUrl: String(config.baseUrl || "").replace(/\/$/, ""),
    reasoning: false,
    input: ["text"],
    cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
    contextWindow: Number(config.contextWindow || 128000),
    maxTokens: Number(config.maxTokens || 8192),
    compat: {
      supportsDeveloperRole: false,
      supportsReasoningEffort: false,
      supportsStore: false,
    },
  };
  const provider = createProvider({
    id: providerId,
    name: "AI8video Bound Model",
    baseUrl: model.baseUrl,
    auth: {
      apiKey: {
        name: "AI8video model key",
        resolve: async () => ({ auth: {} }),
      },
    },
    models: [model],
    api: openAICompletionsApi(),
  });
  const models = createModels();
  models.setProvider(provider);
  return { model, models };
}


function lastAssistantMessage(messages) {
  return [...messages].reverse().find((item) => item?.role === "assistant") || null;
}


function assistantText(message) {
  if (!message) return "";
  if (typeof message.content === "string") return message.content.trim();
  if (!Array.isArray(message.content)) return "";
  return message.content
    .filter((item) => item?.type === "text")
    .map((item) => String(item.text || ""))
    .join("\n")
    .trim();
}


async function handleDecision(command) {
  const requestId = String(command.requestId || "");
  if (!requestId) throw new Error("requestId is required");
  if (activeRequestId) throw new Error("sidecar already has an active decision");
  activeRequestId = requestId;
  let observedAction = null;
  try {
    const config = command.modelConfig || {};
    if (!config.baseUrl || !config.apiKey || !config.model) {
      throw new Error("bound LLM configuration is incomplete");
    }
    const { model, models } = buildModel(config);
    const tools = agentTools(requestId);
    const agent = new Agent({
      initialState: {
        systemPrompt: String(command.systemPrompt || ""),
        model,
        thinkingLevel: "off",
        tools,
        messages: Array.isArray(command.messages) ? command.messages : [],
      },
      sessionId: String(command.sessionId || requestId),
      toolExecution: "sequential",
      afterToolCall: async () => ({ terminate: true }),
      streamFn: (selectedModel, context, options) =>
        models.streamSimple(selectedModel, context, {
          ...options,
          apiKey: String(config.apiKey),
        }),
    });
    agent.subscribe((event) => {
      if (event.type === "tool_execution_start") {
        observedAction = {
          toolCallId: event.toolCallId,
          name: event.toolName,
          arguments: event.args,
        };
      }
    });
    await agent.prompt(String(command.prompt || "请选择下一步。"));
    await agent.waitForIdle();
    const assistant = lastAssistantMessage(agent.state.messages);
    emit({
      type: "decision_result",
      requestId,
      text: assistantText(assistant),
      action: observedAction,
      messageCount: agent.state.messages.length,
      stopReason: assistant?.stopReason || null,
      usage: assistant?.usage || null,
    });
  } finally {
    activeRequestId = null;
  }
}


function handleToolResult(command) {
  const key = `${command.requestId}:${command.toolCallId}`;
  const pending = pendingToolCalls.get(key);
  if (!pending) return;
  pendingToolCalls.delete(key);
  pending.resolve({
    ok: Boolean(command.ok),
    result: command.result,
    error: errorText(command.error),
  });
}


const input = readline.createInterface({ input: process.stdin, crlfDelay: Infinity });
input.on("line", (line) => {
  let command;
  try {
    command = JSON.parse(line);
  } catch (error) {
    emit({ type: "protocol_error", error: errorText(error) });
    return;
  }
  if (command.type === "tool_result") {
    handleToolResult(command);
    return;
  }
  if (command.type === "health") {
    emit({ type: "health", requestId: command.requestId, ok: true, protocol: PROTOCOL_VERSION });
    return;
  }
  if (command.type === "decide") {
    handleDecision(command).catch((error) => {
      emit({
        type: "decision_error",
        requestId: command.requestId,
        error: errorText(error),
      });
      activeRequestId = null;
    });
    return;
  }
  emit({ type: "protocol_error", requestId: command.requestId, error: "unsupported command" });
});

emit({ type: "ready", protocol: PROTOCOL_VERSION, pid: process.pid });
