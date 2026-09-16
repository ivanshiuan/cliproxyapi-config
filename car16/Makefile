# car16 — 建置 / 測試 / 部署
.PHONY: help build serve test dev deploy deploy-worker db-create db-migrate db-local db-seed-local
PROJECT ?= car16

help:                ## 顯示這份說明
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

build:               ## 打包前端 → dist/site/
	node build.js

serve: build         ## 本機預覽前端（http://127.0.0.1:8788）
	cd dist/site && python3 -m http.server 8788

test:                ## Worker 純函式測試（ecpay 簽章 / normalize / gating）
	cd worker && node --test

dev:                 ## 本機跑 Worker（本地 D1，http://localhost:8787）
	cd worker && npx wrangler dev --local

deploy: build        ## 一鍵部署前端到 Cloudflare Pages（含 token/egress 預檢）
	PROJECT=$(PROJECT) ./deploy.sh

deploy-worker:       ## 部署 API Worker（需 CLOUDFLARE_API_TOKEN）
	cd worker && npx --yes wrangler@latest deploy

db-create:           ## 建立 D1 資料庫（一次性；把回傳的 database_id 填進 wrangler.toml）
	cd worker && npx wrangler d1 create car16

db-migrate:          ## 跑 migrations（遠端正式 D1）
	cd worker && npx wrangler d1 migrations apply car16 --remote

db-local:            ## 跑 migrations（本地 D1，配合 make dev）
	cd worker && npx wrangler d1 migrations apply car16 --local

db-seed-local:       ## 灌本地開發假資料
	cd worker && npx wrangler d1 execute car16 --local --file=../scripts/seed-dev.sql
