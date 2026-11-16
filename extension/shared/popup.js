"use strict";
const api = globalThis.browser ?? globalThis.chrome;
const $ = (id) => document.getElementById(id);

async function ask(msg) {
  try { return await api.runtime.sendMessage(msg); } catch (e) { return { ok: false, error: String(e) }; }
}

function say(text) { $("msg").textContent = text; }

async function init() {
  const st = await ask({ type: "get-status" });
  $("enabled").checked = !!(st && st.enabled);
  const s = $("status");
  if (st && st.connected) { s.textContent = `Connected to Gesit ${st.version || ""}`; s.className = "status ok"; }
  else { s.textContent = "Gesit is not running (starts automatically on the next download)"; s.className = "status bad"; }

  if (st && st.last) {
    let host = st.last.url;
    try { host = new URL(st.last.url).hostname; } catch (_) { /* keep raw */ }
    $("last").textContent = `Last browser download (${host}): ${st.last.capture ? "sent to Gesit" : "left to the browser"} - ${st.last.reason}`;
  }

  const res = await ask({ type: "get-media" });
  const list = $("media");
  list.textContent = "";
  const items = (res && res.items) || [];
  $("empty").hidden = items.length > 0;
  for (const it of items) {
    const li = document.createElement("li");
    const box = document.createElement("div");
    box.className = "name";
    const name = decodeURIComponent(it.url.split(/[?#]/)[0].split("/").pop() || it.url);
    box.textContent = name;
    const meta = document.createElement("span");
    meta.className = "meta";
    meta.textContent = it.kind === "hls" ? it.type : `${it.type}${it.size ? " · " + (it.size / 1048576).toFixed(1) + " MB" : ""}`;
    box.appendChild(meta);
    const b = document.createElement("button");
    b.textContent = "Download";
    b.onclick = async () => {
      const r = await ask({ type: "download-media", item: it });
      say(r && r.ok ? "Sent to Gesit." : "Could not reach Gesit: " + ((r && r.error) || "unknown error"));
    };
    li.append(box, b);
    list.appendChild(li);
  }
}

$("enabled").addEventListener("change", (e) => ask({ type: "set-enabled", enabled: e.target.checked }));
$("open").onclick = async () => { await ask({ type: "show-app" }); window.close(); };
$("all").onclick = async () => {
  const r = await ask({ type: "download-all-links" });
  say(r && r.ok ? "Links sent to Gesit." : "No links sent. " + ((r && r.error) || ""));
};
$("video").onclick = async () => {
  const r = await ask({ type: "download-page-video" });
  say(r && r.ok ? "Sent to Gesit (yt-dlp)." : "Could not reach Gesit: " + ((r && r.error) || "unknown error"));
};
init();
