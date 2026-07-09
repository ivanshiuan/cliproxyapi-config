# 模型分流設定教學（Claude/MiniMax/DeepSeek + 本地 MoE）

> 目標：開發 / 重要任務走雲端大模型（Claude 優先，MiniMax、DeepSeek 依序備援），
> 日常寫程式小任務、日常聊天走本地免費模型，省 token 又不犧牲關鍵任務品質。
> 配置檔在 `litellm/config.yaml`，中間層用 [LiteLLM Proxy](https://docs.litellm.ai/docs/proxy/quick_start)。

---

## 全貌（90 秒看懂）

```
你的工具 (Claude Code / Hermes / 編輯器)
        │  base URL 一律指向 http://localhost:4000/v1
        ▼
   LiteLLM Proxy（litellm/config.yaml）
        │
        ├─ model=smart       → Claude Sonnet 5（優先）─fallback→ MiniMax ─fallback→ DeepSeek
        ├─ model=code-local  → 本地 Ornith-1.0 35B（Ollama）
        └─ model=chat-local  → 本地 Qwen3.5 32B（Ollama）
```

**分工原則**：
- **開發、重要任務** → `smart`。雲端付費，品質優先，Claude 掛了才轉 MiniMax/DeepSeek。
- **日常寫程式小任務**（改個函式、寫測試、補註解）→ `code-local`。免費、快。
- **日常組織語言 / 聊天 / 文案** → `chat-local`。**不要用 Ornith**——它是純 coding 模型，沒工具時幻覺明顯，聊天要用通用模型（Qwen3.5 / Gemma 4）。

不做「系統自動判斷任務重不重要」——分類容易判斷錯，重要任務被誤丟去本地小模型後果你要自己扛。改用**場景綁定**：每個工具/情境固定用哪個別名，行為可預測。

---

## 1. 裝本地模型（Ollama）

```bash
# 裝 Ollama（沒裝過的話）：https://ollama.com/download

# code-local：Ornith-1.0 35B MoE，Q4 量化約 18-20GB，需要 24GB VRAM 或 32GB+ 統一記憶體
ollama pull ornith:35b

# 硬體不到 24GB？改拉 9B（約 5-6GB，12GB VRAM / 16GB Mac 可跑，但實戰口碑普通）
# ollama pull ornith:9b

# chat-local：通用模型負責聊天/組織語言
ollama pull qwen3.5:32b
# 記憶體不夠就拉小的：ollama pull qwen3.5:8b
```

⚠️ **Ornith 需要新版推論棧**才能正確解析它的 reasoning + tool-call 格式（Transformers ≥ 5.8.1 / vLLM ≥ 0.19.1）。Ollama 保持最新版即可，用 GGUF 走 llama.cpp 後端沒有這個問題。

⚠️ **已知限制**：Ornith 在長 agent 任務上有社群回報的 doom loop（陷入重複打轉不收斂）。這是它被歸類為「日常小任務」而不是「開發/重要任務」的原因——重要的多步驟工作還是交給 `smart`。

---

## 2. 填 API Key

```bash
cp .env.example .env
```

編輯 `.env`，補上：

```bash
ANTHROPIC_API_KEY=sk-ant-xxxxx        # 已有的話沿用
MINIMAX_API_KEY=你的-minimax-key       # https://www.minimax.io/platform
DEEPSEEK_API_KEY=你的-deepseek-key     # https://platform.deepseek.com
LITELLM_MASTER_KEY=自己隨便設一組長字串  # 保護你的本地 proxy，openssl rand -hex 32
```

`litellm/config.yaml` 裡的 MiniMax 模型代號（`MiniMax-M2`）常變動，上線前先到 MiniMax 平台確認目前的正確代號，跟 config 裡的不一樣就改掉。

---

## 3. 啟動 Proxy

```bash
pip install 'litellm[proxy]'
litellm --config litellm/config.yaml --port 4000
```

驗證三條線都通：

```bash
export LLKEY=你剛剛設的LITELLM_MASTER_KEY

curl http://localhost:4000/v1/chat/completions \
  -H "Authorization: Bearer $LLKEY" -H "Content-Type: application/json" \
  -d '{"model":"smart","messages":[{"role":"user","content":"ping"}]}'

curl http://localhost:4000/v1/chat/completions \
  -H "Authorization: Bearer $LLKEY" -H "Content-Type: application/json" \
  -d '{"model":"code-local","messages":[{"role":"user","content":"寫一個 Python 的 fibonacci 函式"}]}'

curl http://localhost:4000/v1/chat/completions \
  -H "Authorization: Bearer $LLKEY" -H "Content-Type: application/json" \
  -d '{"model":"chat-local","messages":[{"role":"user","content":"幫我把這句話潤飾得更專業"}]}'
```

三個都回應正常就是接通了。

---

## 4. 把工具指過去

任何支援自訂 OpenAI base URL 的工具都能接：

```
Base URL:  http://localhost:4000/v1
API Key:   你的 LITELLM_MASTER_KEY
Model:     smart / code-local / chat-local
```

**建議綁定方式**：
- Claude Code / DevSwarm 這類開發工具 → 固定用 `smart`（本專案 DevSwarm 目前直接呼叫 Anthropic API，若要改走 proxy 需另外改 `devswarm/config.py` 的 base URL，屬於獨立任務，這份文件先不動它）
- 日常筆記 / 聊天工具（LINE bot、備忘錄助理等）→ 固定用 `chat-local`
- 編輯器裡的程式碼補全 / 小改動外掛 → 固定用 `code-local`

---

## 常見問題

**Q: 為什麼不讓系統自動判斷任務重不重要？**
A: 分類器本身會誤判，重要任務一旦被錯丟去本地小模型，後果比多花點錢嚴重。場景綁定（工具固定用哪個別名）雖然要你自己選一次，但行為穩定可預期。

**Q: MiniMax/DeepSeek fallback 什麼時候會觸發？**
A: `smart` 呼叫 Claude 失敗（額度用完、超時、5xx 錯誤）才會依序轉 MiniMax 再轉 DeepSeek。正常情況下你打的都是 Claude。

**Q: Ornith 可以拿來做重要開發任務嗎？**
A: 不建議。它同尺寸開源模型中的 benchmark 數字漂亮，但只贏上一代 Claude Opus（4.7），社群實測在長 agent 任務有 doom loop 抱怨。定位它為「免費的日常小工具」，不是主力。
