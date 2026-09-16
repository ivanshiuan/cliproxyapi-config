// 客服機器人後端：v1 規則式 FAQ；設了 ANTHROPIC_API_KEY 即自動升級 Claude AI 客服。
import { json, err } from "../util.js";
import { getUser } from "../auth.js";

// ── FAQ 決策樹（前端 chatbot.js 有同一份按鈕樹；這裡是自由輸入的關鍵字比對）──
const FAQ = [
  {
    intent: "pickno_flow",
    keywords: ["選號", "怎麼選", "流程", "領牌"],
    reply:
      "監理站選號流程：1) 在本站查詢想要的號碼 2) 到監理服務網完成網路選號與轉帳 3) 轉帳成功後「次一個工作日」內要完成領牌，逾期視同放棄。太麻煩的話可以用我們的代辦服務，點「委託代辦」留下需求即可。",
  },
  {
    intent: "pricing",
    keywords: ["收費", "價格", "多少錢", "方案", "費用"],
    reply:
      "方案：一般版 NT$149/30天（不限次查詢+追蹤10組）、業務專業版 NT$499/30天（追蹤50組+匯出）、車商版 NT$1,999/30天（追蹤200組+批次查詢+代辦優先）。免費會員每天可查 10 次。",
  },
  {
    intent: "auction",
    keywords: ["競標", "標牌", "保證金", "出價"],
    reply:
      "標牌競標在監理服務網進行：出價超過 30 萬需先繳 20% 保證金，得標後未繳款會被沒收保證金並禁標。我們提供競標公告彙整與提醒；若需要代標服務，可留下需求由合作代辦業者聯繫你（平台不代收保證金）。",
  },
  {
    intent: "agency",
    keywords: ["代辦", "代標", "委託", "幫我"],
    reply:
      "委託代辦：在「委託代辦」頁留下需求（想要的號碼、地區、預算、聯絡方式），合作代辦業者會在 1 個工作天內聯繫你報價。證件資料直接交給代辦業者核對，平台不留存。",
  },
  {
    intent: "payment",
    keywords: ["付款", "刷卡", "ATM", "轉帳", "發票", "退費"],
    reply:
      "付款走綠界金流，支援信用卡與 ATM 虛擬帳號。ATM 轉帳後 1-3 個工作天入帳，入帳後方案自動開通。退費規則請見服務條款頁。",
  },
];

const FALLBACK =
  "這個問題我先記下來了！你可以留下 LINE 或電話，真人客服會盡快回覆你；或試試問我「選號流程」「收費方案」「競標規則」「委託代辦」。";

export function ruleReply(message) {
  const m = String(message || "");
  for (const f of FAQ) {
    if (f.keywords.some((k) => m.includes(k))) return { intent: f.intent, reply: f.reply };
  }
  return { intent: "fallback", reply: FALLBACK };
}

async function claudeReply(message, env) {
  const res = await fetch("https://api.anthropic.com/v1/messages", {
    method: "POST",
    headers: {
      "content-type": "application/json",
      "x-api-key": env.ANTHROPIC_API_KEY,
      "anthropic-version": "2023-06-01",
    },
    body: JSON.stringify({
      model: "claude-haiku-4-5-20251001",
      max_tokens: 500,
      system:
        "你是 car16 車牌選號平台的客服。只回答與台灣車牌選號、標牌競標、監理站流程、本站方案收費（一般版149/專業版499/車商版1999，皆30天）、委託代辦相關的問題。平台不代收競標保證金、不留存證件。回答用繁體中文、精簡、友善。無法回答時請用戶留下聯絡方式。",
      messages: [{ role: "user", content: String(message).slice(0, 1000) }],
    }),
  });
  if (!res.ok) throw new Error(`anthropic HTTP ${res.status}`);
  const data = await res.json();
  return { intent: "ai", reply: data.content?.[0]?.text || FALLBACK };
}

export async function chatMessage(request, env) {
  const db = env.DB;
  const body = await request.json().catch(() => null);
  const message = String(body?.message || "").trim();
  const sessionKey = String(body?.session_key || "").slice(0, 64);
  if (!message || !sessionKey) return err(400, "bad_request", "需要 message 與 session_key");

  const user = await getUser(request, db);
  let result;
  if (env.ANTHROPIC_API_KEY) {
    try {
      result = await claudeReply(message, env);
    } catch {
      result = ruleReply(message); // AI 失敗時退回規則式，不讓客服掛掉
    }
  } else {
    result = ruleReply(message);
  }

  await db.batch([
    db.prepare("INSERT INTO chat_logs (session_key, user_id, role, message) VALUES (?, ?, 'user', ?)")
      .bind(sessionKey, user?.id || null, message.slice(0, 2000)),
    db.prepare("INSERT INTO chat_logs (session_key, user_id, role, message, intent) VALUES (?, ?, 'bot', ?, ?)")
      .bind(sessionKey, user?.id || null, result.reply, result.intent),
  ]);
  return json({ reply: result.reply, intent: result.intent, ai: Boolean(env.ANTHROPIC_API_KEY) });
}
