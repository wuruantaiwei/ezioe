"""Phương án B: sinh userscript chạy ngay trong trình duyệt của người dùng.

Vì sao có lớp này: nếu IOE chặn IP/dấu vân tay TLS/bắt captcha thì mọi client
Python đều chết, nhưng trình duyệt thật của người dùng thì luôn qua. Script
dùng chính phiên đăng nhập sẵn có trong tab, gọi API bằng `fetch` cùng origin
và mang theo kho đáp án đã học được, nên vẫn làm được bài.

    python run.py userscript            # ghi data/ioe-fallback.user.js
    # cài vào Tampermonkey -> mở trang tự luyện -> bấm nút "Chạy ezioe"
"""

from __future__ import annotations

import json
import os

from . import games, paths

HEADER = """// ==UserScript==
// @name         ezioe fallback
// @namespace    ezioe
// @version      1.0
// @description  Làm bài tự luyện IOE bằng phiên trình duyệt (phương án B khi API chặn client lạ)
// @match        https://ioe.vn/*
// @run-at       document-idle
// @grant        none
// ==/UserScript==
"""

BODY = r"""
(function () {
  "use strict";
  const API = "https://api-edu.go.vn";
  const SVC = "/ioe-service/v3";
  const GAME = "/ioe-service/v2/game/";
  const BANK = __BANK__;
  const T_PAIR = 7, T_FILL = 3, T_ORDER = 5, T_SHAPE = 2;
  const log = (...a) => panel.push(a.join(" "));

  function token() {
    for (const store of [localStorage, sessionStorage]) {
      for (let i = 0; i < store.length; i++) {
        const v = store.getItem(store.key(i));
        if (!v) continue;
        const m = v.match(/eyJ[\w-]+\.[\w-]+\.[\w-]+/);
        if (m) return m[0];
        try {
          const j = JSON.parse(v);
          for (const k in j) if (typeof j[k] === "string" && /^eyJ[\w-]+\.[\w-]+\.[\w-]+$/.test(j[k])) return j[k];
        } catch (e) {}
      }
    }
    return "";
  }

  let TOK = "";
  async function api(path, body) {
    TOK = TOK || token();
    if (!TOK) throw new Error("không thấy token đăng nhập - hãy đăng nhập ioe.vn lại");
    const r = await fetch(API + path, {
      method: "POST", credentials: "include",
      headers: { "Content-Type": "application/json", "Origin": location.origin },
      body: JSON.stringify({ ...body, token: TOK, serviceCode: "IOE", sign: "" }),
    });
    const out = await r.json();
    if (out.code && ![24, 85, 86, 87].includes(out.code)) throw new Error(`${path}: ${out.code} ${out.message}`);
    if ([85, 86, 87].includes(out.code)) { TOK = ""; throw new Error("token hết hạn, tải lại trang"); }
    return out.data || {};
  }

  const qkey = (q) => q.type + "|" + q.content.content;
  const ordered = (q) => (q.ans || []).slice().sort((a, b) => (a.orderTrue || 0) - (b.orderTrue || 0)).map((a) => a.content);
  function guess(q) {
    const known = BANK[qkey(q)];
    const opts = (q.ans || []).map((a) => a.content).filter((s) => s && s.trim());
    if (known) return [known].concat(opts.filter((o) => o !== known));
    if (!opts.length) return [q.content.content];
    if (q.type === T_PAIR) return opts;
    if (q.type === T_SHAPE) return ordered(q);
    if ([T_FILL, T_ORDER].includes(q.type)) return [ordered(q).join("|"), ordered(q).join(" ")];
    return opts;
  }

  async function base(tok, examKey) {
    return { token: tok, examKey: examKey, api_key: "string", IPClient: "string", deviceId: "string" };
  }

  async function play(ex) {
    log(`bài ${ex.idexercise} (game ${ex.gameId})`);
    const info = await api(`${SVC}/TuLuyen/setinfo`, {
      idExercise: ex.idexercise, round: 0, gameId: ex.gameId,
      redirectUrl: "https://ioe.vn/hoc-sinh/tu-luyen",
    });
    const short = new URL(info.urlgame).searchParams.get("token");
    const g = await api(`${GAME}getinfo`, { api_key: "string", token: short, gameId: ex.gameId, Level: "8", Round: "1", deviceId: "string", sign: "string" });
    const tok = g.token, examKey = g.game.examKey, base0 = await base(tok, examKey);
    await api(`${GAME}startgame`, { ...base0, gameId: ex.gameId });

    const started = Date.now();
    const answers = [];
    for (let i = 0; i < g.game.question.length; i++) {
      const q = g.game.question[i];
      const values = guess(q);
      let best = values[0] || "", point = 0;
      for (const v of values.slice(0, 4)) {
        try {
          const d = await api(`${GAME}answercheck`, { ...base0, ans: { ans: v, point: q.Point, questId: q.id, position: i + 1 } });
          if ((d.point || 0) > point) { best = v; point = d.point; }
          if (point >= q.Point) break;
        } catch (e) { break; }
      }
      answers.push({ ans: best, point: point, questId: q.id, position: i + 1 });
      log(`  câu ${i + 1}: ${best} (${point}/${q.Point})`);
      await new Promise((r) => setTimeout(r, 300 + Math.random() * 700));
    }
    const seconds = Math.max(30, Math.round((Date.now() - started) / 1000));
    const res = await fetch(API + GAME + "finishgame", {
      method: "POST", credentials: "include",
      headers: { "Content-Type": "application/json", "Origin": location.origin },
      body: JSON.stringify({ ...base0, ans: answers, totalTime: seconds }),
    }).then((r) => r.json());
    log(`  nộp: ${res.code === 0 ? `${(res.data || {}).point}/${(res.data || {}).examScore} điểm` : "lỗi " + res.message}`);
    return res.code === 0;
  }

  async function run() {
    const data = await api(`${SVC}/TuLuyen/getlistGameAndResultMax`, {});
    const games = ((data.roundInfoRes || {}).lstgames || []).filter((e) => e.Status !== 2 && e.Status !== 3);
    if (!games.length) { log("không còn bài nào trong vòng"); return; }
    for (const ex of games) {
      try { await play(ex); } catch (e) { log("  lỗi: " + e.message); if (String(e.message).includes("token")) return; }
      await new Promise((r) => setTimeout(r, 2000 + Math.random() * 3000));
    }
    log("xong, bấm F5 để thấy kết quả");
  }

  const panel = {
    box: null,
    init() {
      const box = document.createElement("div");
      box.style.cssText = "position:fixed;right:12px;bottom:12px;z-index:99999;width:340px;max-height:50vh;overflow:auto;background:#111;color:#eee;font:12px/1.45 monospace;border:1px solid #444;border-radius:8px;padding:8px";
      box.innerHTML = '<b>ezioe fallback</b> <span style="float:right;cursor:pointer" id="ez-x">x</span>';
      const btn = document.createElement("button");
      btn.textContent = "Chạy ezioe";
      btn.style.cssText = "width:100%;margin:6px 0;padding:6px;background:#2b6;color:#fff;border:0;border-radius:5px;cursor:pointer";
      btn.onclick = async () => { btn.disabled = true; try { await run(); } catch (e) { log("lỗi: " + e.message); } btn.disabled = false; };
      const out = document.createElement("div");
      out.id = "ez-out";
      box.append(btn, out);
      document.body.append(box);
      box.querySelector("#ez-x").onclick = () => box.remove();
      this.box = out;
    },
    push(text) { if (this.box) { this.box.append(document.createTextNode(text + "\n")); this.box.scrollTop = 1e9; } },
  };

  if (location.pathname.includes("tu-luyen")) panel.init();
})();
"""


def build(bank: dict) -> str:
    """Sinh nội dung userscript, nhúng sẵn kho đáp án tốt."""
    only = {k: v["answer"] for k, v in bank.items()
            if v.get("answer") and int(v.get("point") or 0) > 0}
    payload = json.dumps(only, ensure_ascii=False, separators=(",", ":"))
    return HEADER + BODY.replace("__BANK__", payload)


def write(bank: dict, path: str = "") -> str:
    path = path or os.path.join(paths.data_dir(), "ioe-fallback.user.js")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    text = build(bank)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


def known(bank: dict) -> int:
    return sum(1 for v in bank.values() if v.get("answer") and int(v.get("point") or 0) > 0)


_ = games  # giữ import cho tính năng kho đáp án dùng chung
