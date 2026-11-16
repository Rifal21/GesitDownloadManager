"use strict";
/* Floating "Download this video" button + link collector. */
(() => {
  if (window.__gesitInjected) return;
  window.__gesitInjected = true;
  const api = globalThis.browser ?? globalThis.chrome;

  /* ------------------------------------------------------ link collection */
  api.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
    if (!msg || msg.type !== "collect-links") return;
    const out = new Set();
    let roots = [document];
    if (msg.selectionOnly) {
      const sel = window.getSelection();
      roots = [];
      for (let i = 0; i < (sel ? sel.rangeCount : 0); i++) roots.push(sel.getRangeAt(i).cloneContents());
    }
    for (const root of roots) {
      for (const a of root.querySelectorAll("a[href]")) {
        try {
          const u = new URL(a.getAttribute("href"), location.href);
          if (u.protocol === "http:" || u.protocol === "https:") out.add(u.href);
        } catch (_) { /* skip malformed */ }
      }
      if (!msg.selectionOnly) {
        for (const el of root.querySelectorAll("video[src], audio[src], source[src]")) {
          try { out.add(new URL(el.getAttribute("src"), location.href).href); } catch (_) { /* skip */ }
        }
      }
    }
    sendResponse({ urls: [...out] });
  });

  /* ---------------------------------------------------------- video button */
  let host, shadow, btn, menu, toast, current, hideTimer;

  function build() {
    host = document.createElement("div");
    host.style.cssText = "all:initial;position:fixed;z-index:2147483647;top:0;left:0;width:0;height:0;";
    shadow = host.attachShadow({ mode: "closed" });
    shadow.innerHTML = `
      <style>
        .btn{position:fixed;display:none;align-items:center;gap:6px;padding:6px 11px;border:0;border-radius:6px;
          background:linear-gradient(#ff9a3d,#d9480f);color:#fff;font:600 12px/1 system-ui,sans-serif;cursor:pointer;
          box-shadow:0 2px 8px rgba(0,0,0,.45);}
        .btn:hover{filter:brightness(1.1)}
        .menu{position:fixed;display:none;min-width:240px;max-width:380px;background:#fff;color:#14202e;border-radius:8px;
          box-shadow:0 6px 24px rgba(0,0,0,.4);font:13px system-ui,sans-serif;overflow:hidden}
        .menu button{display:block;width:100%;text-align:left;padding:9px 12px;border:0;background:none;color:inherit;
          font:inherit;cursor:pointer;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
        .menu button:hover{background:#fff0e6}
        .menu small{color:#5b6b7c;margin-left:6px}
        .toast{position:fixed;display:none;padding:8px 12px;border-radius:6px;background:#14202e;color:#fff;
          font:12px system-ui,sans-serif;box-shadow:0 2px 8px rgba(0,0,0,.4)}
      </style>
      <button class="btn" part="b">⬇ Download this video</button>
      <div class="menu"></div><div class="toast"></div>`;
    btn = shadow.querySelector(".btn");
    menu = shadow.querySelector(".menu");
    toast = shadow.querySelector(".toast");
    btn.addEventListener("click", onButton);
    btn.addEventListener("mouseenter", () => clearTimeout(hideTimer));
    document.documentElement.appendChild(host);
  }

  function place(el, x, y) {
    el.style.left = Math.max(4, x) + "px";
    el.style.top = Math.max(4, y) + "px";
  }

  function showFor(video) {
    if (!host) build();
    current = video;
    const r = video.getBoundingClientRect();
    if (r.width < 200 || r.height < 110 || r.bottom < 0 || r.top > innerHeight) return hide();
    btn.style.display = "flex";
    place(btn, Math.min(r.right, innerWidth) - btn.offsetWidth - 10, Math.max(r.top, 0) + 10);
    clearTimeout(hideTimer);
    hideTimer = setTimeout(hide, 3500);
  }

  function hide() {
    if (!host) return;
    btn.style.display = "none";
    menu.style.display = "none";
  }

  function say(text) {
    toast.textContent = text;
    toast.style.display = "block";
    const b = btn.getBoundingClientRect();
    place(toast, b.left - 20, b.bottom + 8);
    setTimeout(() => (toast.style.display = "none"), 2500);
  }

  async function send(item) {
    menu.style.display = "none";
    const r = await api.runtime.sendMessage({ type: "download-media", item });
    say(r && r.ok ? "Sent to Gesit" : "Gesit not reachable: " + ((r && r.error) || "unknown error"));
  }

  async function onButton() {
    const res = await api.runtime.sendMessage({ type: "get-media" });
    const items = ((res && res.items) || []).slice(0, 8);
    const src = current && current.currentSrc;
    if (!items.length && src && /^https?:/i.test(src)) items.push({ url: src, kind: "http", type: "video", size: 0 });
    if (items.length === 1) return send(items[0]);

    menu.textContent = "";
    for (const it of items) {
      const b = document.createElement("button");
      const name = decodeURIComponent(it.url.split(/[?#]/)[0].split("/").pop() || it.url);
      b.textContent = name;
      const small = document.createElement("small");
      small.textContent = it.kind === "hls" ? it.type : `${it.type}${it.size ? " · " + (it.size / 1048576).toFixed(1) + " MB" : ""}`;
      b.appendChild(small);
      b.onclick = () => send(it);
      menu.appendChild(b);
    }
    const page = document.createElement("button");
    page.textContent = "Download page video (yt-dlp)";
    page.onclick = () => send({ url: location.href, kind: "ytdlp" });
    menu.appendChild(page);
    const r = btn.getBoundingClientRect();
    menu.style.display = "block";
    place(menu, r.right - menu.offsetWidth, r.bottom + 6);
  }

  document.addEventListener("mouseover", (e) => {
    const t = e.target;
    if (t && t.tagName === "VIDEO") showFor(t);
  }, true);
  document.addEventListener("scroll", () => host && hide(), true);
})();
