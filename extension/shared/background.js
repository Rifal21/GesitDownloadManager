"use strict";
/* Gesit browser integration: capture downloads, sniff media, context menus.
 * Shared by the Chromium (MV3 service worker) and Firefox (MV2 background) builds. */

const api = globalThis.browser ?? globalThis.chrome;
const action = api.action ?? api.browserAction;
const HOST = "app.gesit.host";

const FALLBACK_EXTS = [
  "zip", "rar", "7z", "gz", "bz2", "xz", "zst", "tar", "tgz", "iso", "pdf", "doc", "docx", "xls", "xlsx",
  "ppt", "pptx", "epub", "mp3", "flac", "wav", "ogg", "m4a", "mp4", "mkv", "avi", "mov", "wmv", "flv", "webm",
  "exe", "msi", "deb", "rpm", "appimage", "apk", "dmg", "jar",
];
const BINARY_MIMES = /^(application\/(octet-stream|zip|x-.*|gzip|vnd\.|pdf|java-archive)|binary\/)/i;

/* ---------------------------------------------------------------- native */
async function native(msg) {
  try {
    return await api.runtime.sendNativeMessage(HOST, msg);
  } catch (e) {
    return { ok: false, error: String((e && e.message) || e) };
  }
}

async function settings() {
  return api.storage.local.get({ enabled: true, appConfig: null, connected: false });
}

async function refreshConfig() {
  const r = await native({ cmd: "get_config" });
  if (r && r.ok) await api.storage.local.set({ appConfig: r, connected: true });
  else await api.storage.local.set({ connected: false });
  return r;
}

/* ------------------------------------------------------------ media store */
const sessionStore = api.storage.session ?? null;
let media = {}; // tabId -> { url -> info }
let mediaLoaded = false;
let saveTimer = null;

async function loadMedia() {
  if (mediaLoaded) return;
  mediaLoaded = true;
  if (sessionStore) {
    const s = await sessionStore.get({ media: {} });
    media = Object.assign(s.media, media);
  }
}

function saveMedia() {
  if (!sessionStore || saveTimer) return;
  saveTimer = setTimeout(() => {
    saveTimer = null;
    sessionStore.set({ media }).catch(() => {});
  }, 300);
}

function updateBadge(tabId) {
  const n = Object.keys(media[tabId] || {}).length;
  try {
    action.setBadgeText({ tabId, text: n ? String(n) : "" });
    action.setBadgeBackgroundColor({ tabId, color: "#2476d2" });
  } catch (_) { /* tab may be gone */ }
}

function headerMap(headers) {
  const out = {};
  for (const h of headers || []) out[h.name.toLowerCase()] = h.value || "";
  return out;
}

const SEGMENT_MIMES = /^(video\/mp2t|audio\/aac|audio\/mp2t)$/i;
const SEGMENT_PATHS = /\.(ts|m4s|aac|cmfv|cmfa)$/i;
const MEDIA_PATHS = /\.(mp4|webm|mkv|mp3|m4a|flv|ogg|opus|mov|wav|flac)$/i;

function classify(details) {
  if (details.statusCode >= 400) return null;
  const h = headerMap(details.responseHeaders);
  const ct = (h["content-type"] || "").toLowerCase().split(";")[0].trim();
  const path = details.url.split(/[?#]/)[0].toLowerCase();
  if (/googlevideo\.com\/videoplayback/.test(details.url)) return null; // chunked; handled via yt-dlp
  let size = parseInt(h["content-length"] || "0", 10) || 0;
  const cr = /\/(\d+)\s*$/.exec(h["content-range"] || "");
  if (cr) size = parseInt(cr[1], 10);
  if (/mpegurl/.test(ct) || path.endsWith(".m3u8")) return { kind: "hls", type: "HLS stream", size: 0 };
  if (ct === "application/dash+xml" || path.endsWith(".mpd")) return { kind: "hls", type: "DASH stream", size: 0 };
  if (SEGMENT_MIMES.test(ct) || SEGMENT_PATHS.test(path)) return null;
  const isMedia = ct.startsWith("video/") || ct.startsWith("audio/") ||
    (MEDIA_PATHS.test(path) && !ct.startsWith("text/"));
  if (isMedia && size >= 200 * 1024) {
    return { kind: "http", type: ct || "media", size };
  }
  return null;
}

api.webRequest.onHeadersReceived.addListener(
  (details) => {
    if (details.tabId < 0) return;
    const c = classify(details);
    if (!c) return;
    loadMedia().then(() => {
      const t = (media[details.tabId] = media[details.tabId] || {});
      t[details.url] = { url: details.url, ...c, ts: Date.now() };
      saveMedia();
      updateBadge(details.tabId);
    });
  },
  { urls: ["http://*/*", "https://*/*"], types: ["media", "xmlhttprequest", "other"] },
  ["responseHeaders"],
);

function clearTab(tabId) {
  loadMedia().then(() => {
    delete media[tabId];
    saveMedia();
    updateBadge(tabId);
  });
}
api.tabs.onRemoved.addListener(clearTab);
api.tabs.onUpdated.addListener((tabId, change) => {
  if (change.status === "loading" && change.url) clearTab(tabId);
});

async function mediaFor(tabId) {
  await loadMedia();
  return Object.values(media[tabId] || {}).sort((a, b) => b.ts - a.ts);
}

/* ---------------------------------------------------------------- helpers */
async function cookieHeader(url) {
  try {
    const cs = await api.cookies.getAll({ url });
    return cs.map((c) => `${c.name}=${c.value}`).join("; ");
  } catch (_) {
    return "";
  }
}

function extOf(nameOrUrl) {
  const m = /\.([a-z0-9]{1,8})$/i.exec((nameOrUrl || "").split(/[?#]/)[0]);
  return m ? m[1].toLowerCase() : "";
}

function baseName(p) {
  return (p || "").split(/[\\/]/).pop();
}

function hostOf(url) {
  try { return new URL(url).hostname.toLowerCase(); } catch (_) { return ""; }
}

async function sendToApp(payload, dialog = true) {
  const r = await native({ cmd: "add", dialog, user_agent: navigator.userAgent, ...payload });
  if (r && r.ok) await api.storage.local.set({ connected: true });
  return r;
}

async function notify(title, message) {
  try {
    if (api.notifications) {
      await api.notifications.create({ type: "basic", iconUrl: "icons/icon128.png", title, message });
    }
  } catch (_) { /* optional */ }
}

/* ------------------------------------------------------- capture downloads */
/* Returns [capture?, reason]. The browser only fires onCreated for real file downloads (a page it can
 * render never gets here), so an unknown extension is captured too: Google Drive, Dropbox, export
 * endpoints etc. have URLs like /download?id=... and often no file name yet when the download starts. */
function decide(item, cfg) {
  if (!/^https?:/i.test(item.url)) return [false, "not an http(s) URL"];
  const host = hostOf(item.url);
  if ((cfg.exclude_hosts || []).some((h) => host === h || host.endsWith("." + h))) return [false, "excluded site"];
  const min = (cfg.min_size_kb || 0) * 1024;
  if (min && item.totalBytes > 0 && item.totalBytes < min) return [false, "smaller than the minimum size"];
  const exts = cfg.extensions && cfg.extensions.length ? cfg.extensions : FALLBACK_EXTS;
  const ext = extOf(baseName(item.filename)) || extOf(item.finalUrl || item.url);
  if (ext && exts.includes(ext)) return [true, "file type ." + ext];
  if (/^text\/html/i.test(item.mime || "")) return [false, "html page"];
  if (!ext || BINARY_MIMES.test(item.mime || "")) return [true, ext ? "binary content type" : "download without file extension"];
  return [false, "file type ." + ext + " is not in the capture list"];
}

async function remember(item, capture, reason) {
  try {
    await api.storage.local.set({
      lastDecision: { ts: Date.now(), url: item.url, mime: item.mime || "", capture, reason },
    });
  } catch (_) { /* diagnostics only */ }
}

/* Hand a browser download over to Gesit. Returns true when Gesit took it.
 * early=true: called from onDeterminingFilename (Chromium), i.e. before the browser's "Save as" dialog
 * exists, so nothing has to be paused and no dialog can appear. */
async function handleDownload(item, early) {
  const s = await settings();
  if (!s.enabled) return false;
  const cfg = s.appConfig || {};
  if (cfg.enabled === false) { await remember(item, false, "capture disabled in Gesit options"); return false; }
  const [capture, reason] = decide(item, cfg);
  await remember(item, capture, reason);
  if (!capture) return false;

  if (!early) { try { await api.downloads.pause(item.id); } catch (_) { /* may already be done */ } }
  const url = item.url;
  const payload = {
    url,
    referer: item.referrer || "",
    page_url: item.referrer || "",
    cookies: await cookieHeader(url),
    filename: baseName(item.filename) || undefined,
    filesize: item.totalBytes > 0 ? item.totalBytes : undefined,
  };
  const r = await sendToApp(payload);
  if (r && r.ok) {
    try { await api.downloads.cancel(item.id); } catch (_) { /* ignore */ }
    try { await api.downloads.removeFile(item.id); } catch (_) { /* nothing on disk */ }
    try { await api.downloads.erase({ id: item.id }); } catch (_) { /* ignore */ }
    return true;
  }
  // Gesit unreachable: let the browser carry on so the user never loses a download
  if (!early) { try { await api.downloads.resume(item.id); } catch (_) { /* ignore */ } }
  notify("Gesit not reachable", "The download continues in the browser. " + ((r && r.error) || ""));
  return false;
}

if (api.downloads.onDeterminingFilename) {
  // Chromium: decide before the browser asks where to save. Returning true keeps the event open until
  // suggest() is called, which we do only after Gesit answered (or failed), so no "Save as" dialog flashes.
  api.downloads.onDeterminingFilename.addListener((item, suggest) => {
    handleDownload(item, true).then(() => suggest(), () => suggest());
    return true;
  });
} else {
  // Firefox has no such event: act on creation (its own location prompt, if enabled, comes first)
  api.downloads.onCreated.addListener((item) => handleDownload(item, false));
}

/* -------------------------------------------------------------- context menu */
async function setupMenus() {
  await api.contextMenus.removeAll(); // promise form works in both Chromium MV3 and Firefox
  api.contextMenus.create({ id: "gesit-link", title: "Download with Gesit", contexts: ["link"] });
  api.contextMenus.create({ id: "gesit-media", title: "Download with Gesit", contexts: ["image", "video", "audio"] });
  api.contextMenus.create({ id: "gesit-all", title: "Download all links with Gesit", contexts: ["page", "selection"] });
  api.contextMenus.create({ id: "gesit-video", title: "Download this page's video with Gesit", contexts: ["page"] });
}

api.contextMenus.onClicked.addListener(async (info, tab) => {
  const pageUrl = info.pageUrl || (tab && tab.url) || "";
  if (info.menuItemId === "gesit-link" || info.menuItemId === "gesit-media") {
    const url = info.menuItemId === "gesit-link" ? info.linkUrl : info.srcUrl;
    if (!/^https?:/i.test(url || "")) return;
    const r = await sendToApp({ url, referer: pageUrl, page_url: pageUrl, cookies: await cookieHeader(url) });
    if (!r || !r.ok) notify("Gesit not reachable", (r && r.error) || "");
  } else if (info.menuItemId === "gesit-all" && tab) {
    await downloadAllLinks(tab, !!info.selectionText);
  } else if (info.menuItemId === "gesit-video") {
    const r = await sendToApp({ url: pageUrl, kind: "ytdlp", referer: pageUrl, page_url: pageUrl,
      cookies: await cookieHeader(pageUrl) });
    if (!r || !r.ok) notify("Gesit not reachable", (r && r.error) || "");
  }
});

async function downloadAllLinks(tab, selectionOnly) {
  let res;
  try {
    res = await api.tabs.sendMessage(tab.id, { type: "collect-links", selectionOnly }, { frameId: 0 });
  } catch (_) {
    notify("Gesit", "Reload the page once so the extension can read its links.");
    return { ok: false };
  }
  const urls = (res && res.urls) || [];
  if (!urls.length) { notify("Gesit", "No links found on this page."); return { ok: false }; }
  const r = await native({
    cmd: "add_many", urls, referer: tab.url, page_url: tab.url,
    cookies: await cookieHeader(tab.url), user_agent: navigator.userAgent,
  });
  if (!r || !r.ok) notify("Gesit not reachable", (r && r.error) || "");
  return r;
}

/* ------------------------------------------------------- messages (UI/content) */
async function activeTab() {
  const [t] = await api.tabs.query({ active: true, currentWindow: true });
  return t;
}

async function handleMessage(msg, sender) {
  switch (msg && msg.type) {
    case "get-status": {
      const r = await refreshConfig();
      const s = await settings();
      const last = (await api.storage.local.get({ lastDecision: null })).lastDecision;
      return { enabled: s.enabled, connected: !!(r && r.ok), version: r && r.version, error: r && r.error, last };
    }
    case "set-enabled":
      await api.storage.local.set({ enabled: !!msg.enabled });
      return { ok: true };
    case "show-app":
      return native({ cmd: "show" });
    case "get-media": {
      const tab = msg.tabId != null ? { id: msg.tabId } : sender && sender.tab ? sender.tab : await activeTab();
      return { items: tab ? await mediaFor(tab.id) : [], tabId: tab && tab.id };
    }
    case "download-media": {
      const tab = sender && sender.tab ? sender.tab : await activeTab();
      const pageUrl = (tab && tab.url) || msg.pageUrl || "";
      const item = msg.item || {};
      let filename;
      if (item.kind === "hls" && tab && tab.title) filename = tab.title.slice(0, 120).trim() + ".mp4";
      return sendToApp({
        url: item.url, kind: item.kind === "ytdlp" ? "ytdlp" : item.kind === "hls" ? "hls" : "http",
        referer: pageUrl, page_url: pageUrl, cookies: await cookieHeader(item.url || pageUrl),
        filename, filesize: item.size > 0 ? item.size : undefined,
      });
    }
    case "download-page-video": {
      const tab = msg.tabId != null ? { id: msg.tabId, url: msg.url } : sender && sender.tab ? sender.tab : await activeTab();
      const url = (tab && tab.url) || msg.url;
      return sendToApp({ url, kind: "ytdlp", referer: url, page_url: url, cookies: await cookieHeader(url) });
    }
    case "download-all-links": {
      const tab = msg.tabId != null ? await api.tabs.get(msg.tabId) : await activeTab();
      return tab ? downloadAllLinks(tab, false) : { ok: false };
    }
    default:
      return { ok: false, error: "unknown message" };
  }
}

api.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  handleMessage(msg, sender).then(sendResponse, (e) => sendResponse({ ok: false, error: String(e) }));
  return true; // async response
});

/* ------------------------------------------------------------------ lifecycle */
api.runtime.onInstalled.addListener(() => {
  setupMenus();
  refreshConfig();
});
api.runtime.onStartup.addListener(() => {
  refreshConfig();
});
if (api.alarms) {
  api.alarms.create("gesit-refresh", { periodInMinutes: 5 });
  api.alarms.onAlarm.addListener((a) => { if (a.name === "gesit-refresh") refreshConfig(); });
}
