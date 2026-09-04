// 右下角客服機器人：FAQ 快捷鈕 + 自由輸入（後端規則式，設 key 後自動 AI）
window.initChatbot = function initChatbot() {
  if (document.getElementById("c16-chat-fab")) return;
  const sessionKey =
    localStorage.getItem("car16_chat_key") ||
    (localStorage.setItem("car16_chat_key", crypto.randomUUID()), localStorage.getItem("car16_chat_key"));

  const fab = document.createElement("button");
  fab.id = "c16-chat-fab";
  fab.setAttribute("aria-label", "客服");
  fab.textContent = "💬";
  const panel = document.createElement("div");
  panel.id = "c16-chat-panel";
  panel.innerHTML = `
    <div class="c16-chat-head"><span>car16 小幫手</span><button aria-label="關閉">✕</button></div>
    <div class="c16-chat-msgs"></div>
    <div class="c16-quick"></div>
    <div class="c16-chat-input">
      <input placeholder="輸入問題…" maxlength="500">
      <button class="btn sm">送出</button>
    </div>`;
  document.body.append(fab, panel);

  const msgs = panel.querySelector(".c16-chat-msgs");
  const quick = panel.querySelector(".c16-quick");
  const input = panel.querySelector("input");
  const sendBtn = panel.querySelector(".c16-chat-input button");

  const QUICK = ["選號流程", "收費方案", "競標規則", "委託代辦", "付款問題"];
  quick.innerHTML = QUICK.map((q) => `<button>${q}</button>`).join("");
  quick.querySelectorAll("button").forEach((b) => (b.onclick = () => send(b.textContent)));

  function add(role, text) {
    const div = document.createElement("div");
    div.className = `c16-msg ${role}`;
    div.textContent = text;
    msgs.append(div);
    msgs.scrollTop = msgs.scrollHeight;
  }

  let greeted = false;
  fab.onclick = () => {
    panel.classList.toggle("open");
    if (panel.classList.contains("open") && !greeted) {
      greeted = true;
      add("bot", "你好！我是 car16 小幫手 👋 選號、競標、代辦、方案問題都可以問我。也可以點下面的快速按鈕。");
    }
  };
  panel.querySelector(".c16-chat-head button").onclick = () => panel.classList.remove("open");

  async function send(text) {
    const message = (text || input.value).trim();
    if (!message) return;
    input.value = "";
    add("user", message);
    sendBtn.disabled = true;
    try {
      const d = await api.post("/api/chat/message", { message, session_key: sessionKey });
      add("bot", d.reply);
      if (d.intent === "fallback") {
        const div = document.createElement("div");
        div.className = "c16-msg bot";
        div.innerHTML = `需要真人協助？<a href="${CAR16.LINE_URL}" target="_blank" rel="noopener">加 LINE 客服</a> 或到 <a href="agency.html">委託代辦</a> 留下聯絡方式。`;
        msgs.append(div);
        msgs.scrollTop = msgs.scrollHeight;
      }
    } catch {
      add("bot", "連線出了點問題，請稍後再試，或直接加 LINE 客服。");
    } finally {
      sendBtn.disabled = false;
    }
  }
  sendBtn.onclick = () => send();
  input.addEventListener("keydown", (e) => { if (e.key === "Enter") send(); });
};
