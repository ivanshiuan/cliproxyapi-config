# SPORTS INTELLIGENCE DESK — 前端建置 / 測試 / 部署
.PHONY: help build serve qa backtest deploy deploy-cf
PROJECT ?= 498-win

help:               ## 顯示這份說明
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

build:              ## 打包單檔 dist/SID-standalone.html + 乾淨上傳目錄 dist/site/
	node build.js

serve:              ## 本機預覽（http://127.0.0.1:8080）
	python3 -m http.server 8080

qa:                 ## 上線前 10 輪復盤（必過）
	node qa.js

backtest:           ## 5000 場合成回測報告
	node backtest_sim.js

deploy:             ## 一鍵部署到 Cloudflare Pages（自動預檢 token/egress）
	PROJECT=$(PROJECT) ./deploy.sh

deploy-cf: build    ## 裸 wrangler 部署（需 CLOUDFLARE_API_TOKEN 或先 wrangler login）
	@echo "→ 將 dist/site/ 部署為獨立 Pages 專案（與 buffhotpot 無關）"
	npx wrangler pages deploy dist/site --project-name $(PROJECT)

# 用法：make deploy PROJECT=498-win   （需先在環境設定好 CLOUDFLARE_API_TOKEN + egress 允許 api.cloudflare.com）
