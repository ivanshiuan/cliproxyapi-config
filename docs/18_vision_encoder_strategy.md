# 18 — Vision Encoder Strategy

> 視覺基礎模型（vision foundation model）選型決策文件。
> 緣起：研究 NVlabs/RADIO 後發現 2026 年的 SOTA 已經分化，
> 不能再用「一個模型打天下」的舊思維。本文 lock 我們的選型決策，
> 之後 Phase 2/3 加新視覺功能時直接照表抓武器。
>
> **TL;DR：DAM/檢索用 SigLIP 2、OCR 用 Qwen2.5-VL 或 Nemotron Nano VL、
> 密集特徵備用 C-RADIOv4。不要把 RADIO 當萬靈丹。**

---

## 0. 為什麼要有這份文件

我們的 Phase 2 視覺需求清單在會議裡逐漸長出來：
- 行銷素材庫（DAM）+ 語意檢索
- 中文菜單 / 收據 / 手寫單 OCR
- 品牌一致性自動審核
- 響應式 hero 智能裁切
- 競品 landing page 視覺差異監控

每項都需要視覺模型，但**「用同一個模型」是個誘惑性陷阱**：聽起來省事，
實際上你會在每個情境都拿到次優結果 + 一堆繞過工作。
這份文件把每個情境鎖定到對的工具，並說明理由。

---

## 1. 一張表看完 2026 視覺基礎模型版圖

| 模型 | 強項 | 弱項 | 授權 | 對我們可用？ |
|---|---|---|---|---|
| **SigLIP 2 SO400M**（Google, 2025/02） | 零樣本檢索 SOTA、**多語含中文**、400M 輕 | 密集特徵弱（ADE20K 44.0）| **Apache 2.0** | ⭐ 主力 |
| **DINOv3-7B**（Meta, 2025/08） | 密集特徵 SOTA（ADE20K 55.9、Cityscapes 81.1）| 7B 跑得慢、Meta license 較緊 | DINOv3 License | 邊角備用 |
| **C-RADIOv4-H**（NVIDIA, 2026/01） | 631M 參數打平 DINOv3-7B 密集；單模型同時涵蓋 CLIP+DINO+SAM | 不支援多語、NVOML Section 8 賠償條款 | NVIDIA Open Model License | 商用 OK 但有風險 |
| **C-RADIOv4-SO400M**（NVIDIA, 2026/01）| 對齊 SigLIP2 形狀方便接 VLM；412M 更小 | 同上 | NVOML | 商用 OK 但有風險 |
| **AIMv2-3B**（Apple, 2024/11）| ImageNet frozen trunk 89.5（最強）| 3B 重、Apple license | Apple license | 不選 |
| **Llama-3.1-Nemotron-Nano-VL-8B**（NVIDIA, 2025）| 現成 VLM（內含 C-RADIOv2）、**OCRBench v2 EN/CN 60.1** | 8B 重；要 GPU | NVOML | OCR 候選 |
| **Qwen2.5-VL-7B**（Alibaba, 2025）| **中文 OCR / 文件理解 SOTA**、Apache 2.0、社群活躍 | 不能離線推論（除非自架）| Apache 2.0 | ⭐ OCR 主力候選 |
| **舊 RADIO / E-RADIO / RADIOv2.5** | — | NSCLv1 非商用 | NSCLv1 | ❌ 不能用 |

**關鍵觀察**：
1. **NVILA（NVIDIA 自家 2024 旗艦 VLM）用 SigLIP 而非 RADIO**。NVIDIA 自己的選擇是
   最強反證 — 「one model 打三場」的論點被自己的產品線打臉。
2. **Eagle 論文（NVIDIA, 2024）結論：多編碼器混合 > 單一蒸餾編碼器**。
3. **C-RADIOv4 真正能贏的軸只有「同樣質量但 10× 少參數」**，這是有用但有限的優勢。

---

## 2. NVlabs/RADIO 細節（為什麼研究完後選擇不主用）

### 2.1 RADIO 是什麼
NVIDIA 用 agglomerative distillation 把多個視覺老師（CLIP、DINOv2、SAM 等）
蒸進一個 ViT 學生。一次前向得到：
- summary token（檢索用）
- spatial features NLC/NCHW（密集預測用）
- 可選 adaptor heads（對齊到老師空間）

### 2.2 版本演進
| 版本 | 老師 | 商用 |
|---|---|---|
| RADIO v1 / v2 / v2.5 | CLIP + DINOv2 + SAM（+ SigLIP from v2.5）| ❌ NSCLv1 |
| E-RADIO | 同上（hybrid CNN-ViT，6-10× 快） | ❌ NSCLv1 |
| **C-RADIOv3** B/L/H/g | + PHI-S 老師白化平衡 | ✅ NVOML |
| **C-RADIOv4** H / SO400M | **SigLIP 2 + DINOv3 + SAM 3** | ✅ NVOML |

### 2.3 對抗式驗證的發現
- **「+6.8% ImageNet 零樣本」**：比的是自己的老師，不是 2026 SOTA。
  跟 OpenCLIP DFN-2B 同解析度比，gap 只剩 1-3pp。
- **DINOv3 在密集任務全面贏**：ADE20K +0.7, Cityscapes +2.7, NYUv2 depth 贏。
  C-RADIOv4 在 1pp 內逼近，但**真要最強密集就直接 DINOv3**。
- **SigLIP 2 在零樣本檢索 + 多語贏**：RADIO 沒多語訓練，台灣業務這項致命。
- **已知 bug**：c-RADIOv2/v3/v4 在 LLaVA-style pretraining 不收斂（issue #167）；
  v4 spatial features 有 PCA outlier 偽影。

### 2.4 NVOML 授權的真實風險（法務必看）
- ✅ 商用 SaaS 推論可、衍生模型可、output embedding 商用存可
- ⚠️ Section 8 賠償條款：「You will indemnify and hold harmless NVIDIA」—
  這在 OSI-approved license 中不常見，第三方分析認定為「極嚴重的責任轉移」
- ⚠️ Trustworthy AI 條款引用入合約：未來可成為 NVIDIA 撤銷的鉤子
- ⚠️ **NVOML 不是 OSI-approved open source**

**對我們的意義**：未來真要用 C-RADIOv4，要先過法務 + 確認 E&O 保險覆蓋。
不要用在受高度監管的垂直。

---

## 3. 對自家業務的應用矩陣

| 情境 | 主選 | 備案 | 不該選 |
|---|---|---|---|
| 行銷素材庫 DAM + 語意檢索 | **SigLIP 2 SO400M** | C-RADIOv4-SO400M | OpenAI Embeddings API |
| 中文菜單/收據/手寫單 OCR | **Qwen2.5-VL-7B** | Llama-3.1-Nemotron-Nano-VL-8B | 自己從 RADIO encoder 堆 VLM |
| 品牌一致性審核 | **SigLIP 2 + reference set** | + C-RADIOv4 dense 補構圖 | RADIO 單獨 |
| 響應式 hero 智能裁切 | **C-RADIOv4-H dense features** | DINOv3 ViT-L | SigLIP 2（密集弱）|
| 競品 landing page 視覺差異 | **DINOv3** 或 **C-RADIOv4** | — | SigLIP 2 |
| 食材 / 菜色辨識（未來）| **SigLIP 2** zero-shot + 自家 fine-tune | Qwen2.5-VL prompt | — |

### 3.1 決策樹

```
拿到一個視覺需求 → 問三個問題：

Q1: 需要文字-圖片對齊嗎？（搜尋、tagging、檢索）
    YES → 需要中文？
          YES → SigLIP 2 SO400M
          NO  → SigLIP 2 / C-RADIOv4-SO400M 都行（看授權偏好）
    NO  → 往 Q2

Q2: 主要是密集像素級任務？（分割、深度、saliency、構圖）
    YES → 要最強？
          YES → DINOv3（注意 license）
          NO  → C-RADIOv4-H（10× 少參數逼近）
    NO  → 往 Q3

Q3: 是 VLM 任務？（VQA、OCR、文件理解、生成 caption）
    YES → 要中文 OCR？
          YES → Qwen2.5-VL-7B（Apache 2.0）
          NO  → 看延遲與成本，可考慮 Claude vision / GPT-4V API
    NO  → 重新定義需求
```

---

## 4. 不該用 RADIO 的場景（清單）

| 場景 | 該用什麼 | 為什麼不該用 RADIO |
|---|---|---|
| 中文文字-圖片檢索 | SigLIP 2 | RADIO 沒多語訓練 |
| OCR / 文件理解 | Qwen2.5-VL / Nemotron Nano VL | RADIO 是 encoder，要自己堆 VLM |
| 純密集 SOTA | DINOv3 | RADIO 還是輸 ~1pp |
| 醫療 / 衛星 | DINOv3 domain checkpoints | RADIO 沒對應 domain pretrain |
| CPU 推論 | SigLIP-base 224 / MobileCLIP | RADIO H 在 CPU 慢到不可用 |
| 受高度監管垂直 | Apache 2.0 模型（SigLIP / Qwen-VL）| NVOML Section 8 賠償風險 |

---

## 5. 部署現實（我們會踩到的事）

| 模型 | 權重（FP16）| 推論記憶體 @432 | 適合的硬體 |
|---|---|---|---|
| SigLIP 2 SO400M | ~1.6 GB | ~2 GB | L4 24GB 綽綽有餘、單張可放多 model |
| C-RADIOv4-H | 4.29 GB | ~3 GB | L4 OK；H100 / 4090 跑全部解析度 |
| C-RADIOv4-SO400M | ~1.6-2.7 GB | ~2 GB | L4 OK |
| DINOv3-L (3B) | ~6 GB | ~4 GB | L4 緊；H100 推薦 |
| Qwen2.5-VL-7B | ~14 GB | ~16 GB | H100 / A100 80GB |
| Nemotron Nano VL 8B | ~16 GB | ~18 GB | H100 |

### 5.1 共用 GPU 策略
Phase 2 預算允許 1-2 張 L4：
- **L4 #1**：SigLIP 2 SO400M + C-RADIOv4-SO400M 共駐（總計 ~5 GB），跑檢索 + 密集
- **L4 #2 或租用 H100**：OCR VLM（按需 spawn / scale-to-zero）

成本估算（雲端 L4 USD 0.6/hr，H100 USD 2-3/hr）：
- L4 24/7 ~ USD 430/月
- H100 按需 100 hr/月 ~ USD 200-300/月
- **總視覺基礎設施月成本 < USD 1000**，可以承受

---

## 6. 第一步 PoC（已 lock，3 天）

**做 DAM + SigLIP 2**。Spec：`specs/visual_asset_embedding.md`。

理由：
1. 行銷團隊找素材的痛點每天都在發生，ROI 立即可見
2. SigLIP 2 中文支援是我們業務剛需
3. Apache 2.0 授權沒有 NVOML 法務風險
4. PG + pgvector 我們已經有，零基礎設施新增
5. 跑通了情境 3（品牌一致性）跟情境 6（食材辨識）都用同一個 embedding

**驗收標準**（看 spec AC）：
- 1000 張公開測試集 cosine recall@10 > 0.85
- 中文 query 「熱湯」找到熱湯類圖片
- 100 ms 內回 top-10 結果（pgvector hnsw）
- 多租戶完全隔離

**後續路徑**：
- Sprint 2：情境 3 品牌一致性（reuse SigLIP 2，+ reference set）
- Sprint 3：情境 4 智能裁切（+ C-RADIOv4-SO400M 上 L4）
- Sprint 4：情境 2 OCR（接 Qwen2.5-VL，先 API 後自架）

---

## 7. 永遠不做（視覺領域版）

- 不要把 NSCLv1 模型（RADIO / E-RADIO / RADIOv2.5）放進生產
- 不要在沒法務看過 NVOML Section 8 前把 C-RADIO 嵌進收費功能
- 不要從 vision encoder 自己堆 VLM（NVIDIA Eagle 已證明多編碼器混合更好）
- 不要在多租戶情境讓不同 tenant 的 embedding 落到同一個未隔離索引
- 不要在沒 GPU 預算決議前用 OpenAI / Anthropic vision API 跑大量批次
  （API 月帳會壓垮 unit economics）
- 不要把模型權重 commit 進 git（pull from HF，cache 在 volume）

---

## 8. 文件審視週期

- 每 6 個月重新檢視一次（視覺模型迭代快，2025-2026 半年就翻盤）
- 主要觸發條件：
  - 出現新 SOTA（>=2pp 跨多 bench）
  - 我們現用模型授權條款改動
  - NVIDIA 推出取代 RADIO 線的新蒸餾模型

下次審視預定：**2026-12-27**

---

## 9. 來源

完整研究報告（30 個一手連結）見 chat session log 2026-06-27。
重點來源：
- AM-RADIO 論文 https://arxiv.org/abs/2312.06709
- C-RADIOv4 tech report https://arxiv.org/abs/2601.17237
- DINOv3 https://arxiv.org/abs/2508.10104
- SigLIP 2 https://arxiv.org/abs/2502.14786
- NVILA（用 SigLIP 而非 RADIO）https://arxiv.org/abs/2412.04468
- Eagle（多編碼器 > 單一蒸餾）https://arxiv.org/abs/2408.15998
- NVIDIA Open Model License PDF
  https://developer.download.nvidia.com/licenses/nvidia-open-model-license-agreement-june-2024.pdf
- Shuji Sado NVOML 賠償條款分析
  https://shujisado.org/2025/12/19/nvidia-open-model-license-a-corporate-risk-analysis/

— end of 18_vision_encoder_strategy.md —
