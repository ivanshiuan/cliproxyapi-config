#!/usr/bin/env node
/* car16 build — site/ → dist/site/（Cloudflare Pages 上傳根目錄）
 * 零依賴。順便做基本健檢：必要檔案齊全、HTML 都有引 config/api/ui。 */
"use strict";
const fs = require("fs");
const path = require("path");

const ROOT = __dirname;
const SRC = path.join(ROOT, "site");
const OUT = path.join(ROOT, "dist", "site");

const REQUIRED = [
  "index.html", "search.html", "auction.html", "dashboard.html", "login.html", "agency.html",
  "pay/result.html", "legal/terms.html", "legal/privacy.html",
  "css/main.css",
  "js/config.js", "js/api.js", "js/ui.js", "js/chatbot.js", "js/search.js", "js/dashboard.js",
];

let failed = false;
for (const f of REQUIRED) {
  if (!fs.existsSync(path.join(SRC, f))) { console.error(`✗ 缺檔案 site/${f}`); failed = true; }
}

// 每個 HTML 都要有共用腳本（漏引 ui.js 會沒有版頭與機器人）
for (const f of REQUIRED.filter((f) => f.endsWith(".html"))) {
  const html = fs.readFileSync(path.join(SRC, f), "utf8");
  for (const dep of ["config.js", "api.js", "ui.js", "chatbot.js"]) {
    if (!html.includes(dep)) { console.error(`✗ site/${f} 缺 <script src=…${dep}>`); failed = true; }
  }
}
if (failed) process.exit(1);

fs.rmSync(OUT, { recursive: true, force: true });
fs.cpSync(SRC, OUT, { recursive: true });

const count = (dir) =>
  fs.readdirSync(dir, { withFileTypes: true }).reduce((n, e) => n + (e.isDirectory() ? count(path.join(dir, e.name)) : 1), 0);
console.log(`✓ dist/site/（${count(OUT)} 檔）— Cloudflare Pages 上傳根目錄`);
