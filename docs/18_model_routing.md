# 模型分流設定教學（全雲端版：Claude/MiniMax/DeepSeek + 代管 Ornith）

> 目標：開發 / 重要任務走 Claude（MiniMax、DeepSeek 依序備援），
> 日常寫程式小任務、日常聊天走「按量計費的便宜雲端模型」，省 token 又不犧牲關鍵任務品質。
> 配置檔在 `litellm/config.yaml`，中間層用 [LiteLLM Proxy](https://docs.litellm.ai/docs/proxy/quick_start)。
> **全程零磁碟、零 GPU 需求** —— 不裝任何本地模型。

---

## 全貌（90 秒看懂）

```
你的工具 (Claude Code / Hermes / 編輯器 / LINE bot)
        │  base URL 一律指向 http://localhost:4000/v1
        ▼
   LiteLLM Proxy（litellm/config.yaml）
        │
        ├─ model=smart      → Claude Sonnet 5 ─掛了才→ MiniMax ─再掛→ DeepSeek
        ├─ model=code-lite  → DeepInfra 代管 Ornith-1.0 35B ─掛了→ DeepSeek
        └─ model=chat-lite  → DeepSeek-chat
```

**分工原則**：

| 別名 | 場景 | 後端 | 成本感 |
|---|---|---|---|
| `smart` | 開發、重要任務、多步 agent | Claude Sonnet 5（優先） | 品質優先，該花就花 |
| `code-lite` | 日常寫程式小任務（改函式、寫測試、補註解） | 代管 Ornith-1.0 35B | 約 $0.14/M 輸入、$1.0/M 輸出 |
| `chat-lite` | 組織語言、聊天、文案、潤飾 | DeepSeek-chat | 極便宜、中文強 |

兩個刻意的設計決定：

1. **不裝本地模型**。35B 權重要 18-20GB 磁碟 + 24GB VRAM 等級硬體，目前機器不符。
   按量計費 API 一樣達到「日常小任務用便宜模型」，還不用維運。未來有機器再啟用
   `litellm/config.yaml` 檔尾的附錄區塊即可，對外別名不變、工具端零改動。
2. **不做「系統自動判斷任務重不重要」**。分類器會誤判，重要任務被錯丟去小模型的
   代價比多花點錢高。改用場景綁定：每個工具/情境固定用哪個別名，行為可預測。

另外：**聊天不要用 Ornith** —— 它是純 coding 模型，社群回報沒工具時幻覺明顯，
所以 `chat-lite` 走 DeepSeek-chat 而不是 Ornith。

---

## 1. 申請 API Key（都免綁月費，儲值制/按量計費）

| 變數 | 去哪拿 | 用途 |
|---|---|---|
| `ANTHROPIC_API_KEY` | https://console.anthropic.com | `smart` 主力（已有就沿用） |
| `MINIMAX_API_KEY` | https://www.minimax.io/platform | `smart` 第一備援（選填） |
| `DEEPSEEK_API_KEY` | https://platform.deepseek.com | `smart` 第二備援 + `chat-lite` |
| `DEEPINFRA_API_KEY` | https://deepinfra.com | `code-lite`（代管 Ornith） |
| `LITELLM_MASTER_KEY` | 自己生成：`openssl rand -hex 32` | 保護你的本地 proxy |

```bash
cp .env.example .env   # 然後把上面的 key 填進去
```

沒申請 MiniMax 也能跑——`smart` 只是少一層備援，Claude 掛掉時直接退 DeepSeek。

⚠️ `litellm/config.yaml` 裡的 MiniMax 模型代號（`MiniMax-M2`）常變動，
上線前到 MiniMax 平台核對目前的正確代號。

---

## 2. 啟動 Proxy

```bash
pip install 'litellm[proxy]'
litellm --config litellm/config.yaml --port 4000
```

## 3. 驗證三條線都通

```bash
export LLKEY=你的LITELLM_MASTER_KEY

curl http://localhost:4000/v1/chat/completions \
  -H "Authorization: Bearer $LLKEY" -H "Content-Type: application/json" \
  -d '{"model":"smart","messages":[{"role":"user","content":"ping"}]}'

curl http://localhost:4000/v1/chat/completions \
  -H "Authorization: Bearer $LLKEY" -H "Content-Type: application/json" \
  -d '{"model":"code-lite","messages":[{"role":"user","content":"寫一個 Python 的 fibonacci 函式"}]}'

curl http://localhost:4000/v1/chat/completions \
  -H "Authorization: Bearer $LLKEY" -H "Content-Type: application/json" \
  -d '{"model":"chat-lite","messages":[{"role":"user","content":"幫我把這句話潤飾得更專業"}]}'
```

三個都回應正常就是接通了。

## 4. 把工具指過去

任何支援自訂 OpenAI base URL 的工具都能接：

```
Base URL:  http://localhost:4000/v1
API Key:   你的 LITELLM_MASTER_KEY
Model:     smart / code-lite / chat-lite
```

**建議綁定**：
- Claude Code / DevSwarm 這類開發工具 → 固定 `smart`
  （本專案 DevSwarm 目前直接呼叫 Anthropic API；要改走 proxy 需另外改
  `devswarm/config.py` 的 base URL，屬獨立任務，這份文件不動它）
- 日常筆記 / 聊天工具（LINE bot、備忘錄助理）→ 固定 `chat-lite`
- 編輯器裡的程式碼補全 / 小改動外掛 → 固定 `code-lite`

---

## 常見問題

**Q: 為什麼不裝本地模型？不是免費嗎？**
A: 「免費」的前提是你已經有 20GB+ 閒置磁碟和 24GB VRAM 等級的硬體。沒有的話，
硬裝的結果是磁碟爆掉或推論慢到不能用。代管 API 每月日常用量通常只要幾十塊台幣，
遠低於買硬體。未來有機器，啟用 config 檔尾的附錄區塊即可無痛切換。

**Q: fallback 什麼時候觸發？**
A: 呼叫失敗（額度用完、超時、5xx）才會轉。正常情況 `smart` 打的都是 Claude。

**Q: Ornith 可以拿來做重要開發任務嗎？**
A: 不建議。它同尺寸開源模型的 benchmark 亮眼，但只贏上一代 Claude Opus 4.7，
社群實測在長 agent 任務有 doom loop（重複打轉）抱怨。定位是「便宜的日常小工具」。

**Q: 怎麼控制每月花費？**
A: 三個後端（DeepSeek、DeepInfra、MiniMax）都是儲值制——儲多少花多少，天然封頂。
Anthropic 可在 console 設每月上限。要更細的話 LiteLLM 支援
[per-key budget](https://docs.litellm.ai/docs/proxy/users)，之後有需要再加。

**Q: `code-lite` 會不會突然失效？**
A: 有風險。DeepInfra 官方頁面（2026-07-16 查證）寫「因用量低，
[Ornith-1.0-35B](https://deepinfra.com/deepreinforce-ai/Ornith-1.0-35B) 將於
2026-07-19 下架」——這類小眾開源模型的代管服務常會這樣悄悄下架。真的下架時
`code-lite` 會自動 fallback 到 `chat-lite`（DeepSeek），**系統不會壞，只是會
失去 Ornith 的 coding 特化優勢**。設定的時候先點進上面那個連結確認模型還在架，
不在的話去 [DeepInfra 模型列表](https://deepinfra.com/models/text-generation)
挑一個當時還活著的 Ornith 尺寸換上去。
