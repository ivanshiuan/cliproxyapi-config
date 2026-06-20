#!/usr/bin/env node
/* =====================================================================
 * SID build — 把多檔前端內嵌成「單一 HTML」(CSS/JS 全進去，離線可開)
 * 用法：node build.js   →   dist/SID-standalone.html
 * 零依賴，改完一鍵重建。
 * ===================================================================== */
"use strict";
const fs = require("fs");
const path = require("path");

const ROOT = __dirname;
const JS_ORDER = ["js/data.js", "js/history.js", "js/engine.js", "js/backtest.js", "js/live.js", "js/app.js"];

function read(p) { return fs.readFileSync(path.join(ROOT, p), "utf8"); }

const css = read("css/terminal.css");
const js = JS_ORDER.map(f => `/* === ${f} === */\n` + read(f)).join("\n");

let html = read("index.html")
  // 外部 css → 內嵌 <style>
  .replace(/<link rel="stylesheet"[^>]*>/, "<style>\n" + css + "\n</style>")
  // 移除 manifest（單檔離線用不到）
  .replace(/<link rel="manifest"[^>]*>\s*/g, "")
  // 移除 6 支外部 <script src>
  .replace(/<script src="js\/[^"]*"><\/script>\s*/g, "")
  // service worker 註冊區塊 → 換成內嵌全部 JS
  .replace(/<script>\s*if \("serviceWorker"[\s\S]*?<\/script>/, "<script>\n" + js + "\n</script>");

const outDir = path.join(ROOT, "dist");
fs.mkdirSync(outDir, { recursive: true });
const out = path.join(outDir, "SID-standalone.html");
fs.writeFileSync(out, html);

// 健檢：不該有殘留外部引用
const leftSrc = (html.match(/<script src=/g) || []).length;
const leftLink = (html.match(/<link /g) || []).length;
if (leftSrc || leftLink) { console.error(`✗ 殘留外部引用 src=${leftSrc} link=${leftLink}`); process.exit(1); }

console.log(`✓ ${path.relative(ROOT, out)} (${(fs.statSync(out).size / 1024).toFixed(1)} KB) — 單檔離線可開`);

/* ---------- dist/site/ ：Cloudflare Pages 上傳根目錄（只含執行檔） ----------
 * 乾淨、不含 qa.js / backtest_sim.js / build.js / docs / deploy —— 避免測試碼外洩。 */
const SITE_FILES = ["index.html", "manifest.webmanifest", "sw.js"];
const SITE_DIRS = ["css", "js"];
const siteDir = path.join(outDir, "site");
fs.rmSync(siteDir, { recursive: true, force: true });
fs.mkdirSync(siteDir, { recursive: true });
for (const f of SITE_FILES) fs.copyFileSync(path.join(ROOT, f), path.join(siteDir, f));
for (const d of SITE_DIRS) fs.cpSync(path.join(ROOT, d), path.join(siteDir, d), { recursive: true });
const count = SITE_FILES.length + SITE_DIRS.reduce((n, d) => n + fs.readdirSync(path.join(ROOT, d)).length, 0);
console.log(`✓ dist/site/ (${count} 檔) — Cloudflare Pages 上傳根目錄`);

