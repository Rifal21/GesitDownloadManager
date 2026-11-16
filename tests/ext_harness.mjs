// Runs extension/shared/background.js against a mocked WebExtension API.
import fs from "node:fs";
import vm from "node:vm";
import assert from "node:assert/strict";

const src = fs.readFileSync(new URL("../extension/shared/background.js", import.meta.url), "utf8");

function makeEnv({ nativeReply = { ok: true }, storage = {}, chromium = false } = {}) {
  const calls = { native: [], downloads: [], badge: [], notifications: [] };
  const listeners = {};
  const ev = (name) => ({ addListener: (fn) => (listeners[name] = fn) });
  const store = { enabled: true, appConfig: { enabled: true, extensions: ["zip", "mp4"], exclude_hosts: ["blocked.test"], min_size_kb: 0 }, ...storage };
  const chrome = {
    runtime: {
      sendNativeMessage: async (_h, msg) => { calls.native.push(msg); if (nativeReply instanceof Error) throw nativeReply; return typeof nativeReply === "function" ? nativeReply(msg) : nativeReply; },
      onMessage: ev("onMessage"), onInstalled: ev("onInstalled"), onStartup: ev("onStartup"),
    },
    storage: { local: { get: async (d) => ({ ...d, ...store }), set: async (o) => Object.assign(store, o) } },
    downloads: {
      onCreated: ev("onCreated"),
      ...(chromium ? { onDeterminingFilename: ev("onDeterminingFilename") } : {}),
      pause: async (id) => calls.downloads.push(["pause", id]),
      resume: async (id) => calls.downloads.push(["resume", id]),
      cancel: async (id) => calls.downloads.push(["cancel", id]),
      removeFile: async (id) => calls.downloads.push(["removeFile", id]),
      erase: async (q) => calls.downloads.push(["erase", q.id]),
    },
    cookies: { getAll: async () => [{ name: "sid", value: "abc" }, { name: "x", value: "1" }] },
    webRequest: { onHeadersReceived: ev("onHeadersReceived") },
    tabs: { onRemoved: ev("tabRemoved"), onUpdated: ev("tabUpdated"), query: async () => [{ id: 7, url: "https://site.test/watch", title: "My Video" }], sendMessage: async () => ({ urls: [] }), get: async () => ({ id: 7, url: "https://site.test/" }) },
    contextMenus: { removeAll: async () => {}, create: () => {}, onClicked: ev("menuClicked") },
    action: { setBadgeText: (o) => calls.badge.push(o), setBadgeBackgroundColor: () => {} },
    alarms: { create: () => {}, onAlarm: ev("onAlarm") },
    notifications: { create: async (o) => calls.notifications.push(o) },
  };
  const ctx = vm.createContext({ chrome, navigator: { userAgent: "UA/1.0" }, URL, setTimeout, clearTimeout, console });
  vm.runInContext(src, ctx);
  return { calls, listeners, store };
}

const wait = (ms = 20) => new Promise((r) => setTimeout(r, ms));
let passed = 0;
async function test(name, fn) { await fn(); passed++; console.log("ok -", name); }

await test("captures a zip: pauses, sends cookies/referer, then cancels and erases", async () => {
  const { calls, listeners } = makeEnv();
  await listeners.onCreated({ id: 1, url: "https://files.test/a/b.zip", referrer: "https://files.test/", filename: "", totalBytes: 1234, mime: "application/zip" });
  const add = calls.native.find((m) => m.cmd === "add");
  assert.equal(add.url, "https://files.test/a/b.zip");
  assert.equal(add.cookies, "sid=abc; x=1");
  assert.equal(add.referer, "https://files.test/");
  assert.equal(add.user_agent, "UA/1.0");
  assert.equal(add.filesize, 1234);
  assert.deepEqual(calls.downloads.map((d) => d[0]), ["pause", "cancel", "removeFile", "erase"]);
});

await test("ignores file types that are not in the capture list", async () => {
  const { calls, listeners } = makeEnv();
  await listeners.onCreated({ id: 2, url: "https://x.test/page.html", mime: "text/html", filename: "" });
  assert.equal(calls.native.length, 0);
  assert.equal(calls.downloads.length, 0);
});

await test("ignores blob:/data: URLs and excluded hosts", async () => {
  const { calls, listeners } = makeEnv();
  await listeners.onCreated({ id: 3, url: "blob:https://x.test/uuid", filename: "a.zip" });
  await listeners.onCreated({ id: 4, url: "https://blocked.test/a.zip", filename: "" });
  assert.equal(calls.native.length, 0);
});

await test("captures extensionless downloads such as Google Drive, even with no file name yet", async () => {
  const { calls, listeners, store } = makeEnv();
  await listeners.onCreated({ id: 20, url: "https://drive.usercontent.google.com/download?id=ABC&export=download&confirm=t", referrer: "https://drive.google.com/", filename: "", mime: "application/octet-stream" });
  await listeners.onCreated({ id: 21, url: "https://docs.google.com/document/d/ABC/export?format=pdf", filename: "", mime: "" });
  assert.equal(calls.native.filter((m) => m.cmd === "add").length, 2);
  assert.equal(store.lastDecision.capture, true);
});

await test("still ignores html pages and unlisted file types", async () => {
  const { calls, listeners, store } = makeEnv();
  await listeners.onCreated({ id: 22, url: "https://x.test/view", filename: "", mime: "text/html" });
  assert.equal(store.lastDecision.reason, "html page");
  await listeners.onCreated({ id: 23, url: "https://x.test/data.csv", filename: "", mime: "text/csv" });
  assert.equal(calls.native.length, 0);
});

await test("Chromium: captures in onDeterminingFilename (before any Save-As dialog), no pause needed", async () => {
  const { calls, listeners } = makeEnv({ chromium: true });
  let suggested = 0;
  const ret = listeners.onDeterminingFilename({ id: 30, url: "https://www.mediafire.com/file/x/Mora.apk/file", filename: "Mora.apk", mime: "application/vnd.android.package-archive" }, () => suggested++);
  assert.equal(ret, true, "must keep the event open (async suggest)");
  await wait(40);
  assert.deepEqual(calls.downloads.map((d) => d[0]), ["cancel", "removeFile", "erase"]); // cancelled before suggest()
  assert.equal(calls.native.find((m) => m.cmd === "add").filename, "Mora.apk");
  assert.equal(suggested, 1);
  assert.equal(listeners.onCreated, undefined, "no onCreated handler on Chromium (would double-handle)");
});

await test("Chromium: if Gesit is unreachable the event is released so the browser proceeds normally", async () => {
  const { calls, listeners } = makeEnv({ chromium: true, nativeReply: new Error("host not found") });
  let suggested = 0;
  listeners.onDeterminingFilename({ id: 31, url: "https://x.test/a.zip", filename: "a.zip" }, () => suggested++);
  await wait(40);
  assert.equal(suggested, 1);
  assert.equal(calls.downloads.length, 0, "download untouched");
});

await test("Chromium: ignored downloads are released immediately", async () => {
  const { calls, listeners } = makeEnv({ chromium: true });
  let suggested = 0;
  listeners.onDeterminingFilename({ id: 32, url: "https://x.test/page", filename: "", mime: "text/html" }, () => suggested++);
  await wait(30);
  assert.equal(suggested, 1);
  assert.equal(calls.native.length, 0);
});

await test("respects the global on/off switch", async () => {
  const { calls, listeners } = makeEnv({ storage: { enabled: false } });
  await listeners.onCreated({ id: 5, url: "https://x.test/a.zip", filename: "" });
  assert.equal(calls.native.length, 0);
});

await test("if Gesit is unreachable the browser download resumes untouched", async () => {
  const { calls, listeners } = makeEnv({ nativeReply: new Error("Specified native messaging host not found.") });
  await listeners.onCreated({ id: 6, url: "https://x.test/a.zip", filename: "" });
  const kinds = calls.downloads.map((d) => d[0]);
  assert.deepEqual(kinds, ["pause", "resume"]);
  assert.ok(calls.notifications.length === 1);
});

await test("sniffs media, ignores segments/tiny files/googlevideo, badges the tab", async () => {
  const { calls, listeners } = makeEnv();
  const hdr = (type, len) => [{ name: "Content-Type", value: type }, { name: "Content-Length", value: String(len) }];
  const fire = (url, type, len, tabId = 7) => listeners.onHeadersReceived({ tabId, url, statusCode: 200, responseHeaders: hdr(type, len) });
  fire("https://cdn.test/movie.mp4", "video/mp4", 5_000_000);
  fire("https://cdn.test/live/master.m3u8?token=1", "application/vnd.apple.mpegurl", 300);
  fire("https://cdn.test/seg1.ts", "video/mp2t", 900_000);
  fire("https://cdn.test/tiny.mp4", "video/mp4", 50_000);
  fire("https://rr1.googlevideo.com/videoplayback?id=1", "video/mp4", 9_000_000);
  fire("https://cdn.test/x.mp4", "video/mp4", 5_000_000, -1);
  await wait(50);
  const res = await new Promise((r) => listeners.onMessage({ type: "get-media", tabId: 7 }, {}, r));
  const got = JSON.stringify(res.items.map((i) => [i.kind, i.url.split("/").pop().split("?")[0]]).sort()); // vm realm arrays: compare as JSON
  assert.equal(got, JSON.stringify([["hls", "master.m3u8"], ["http", "movie.mp4"]]));
  assert.equal(calls.badge.at(-1).text, "2");
});

await test("download-media sends the right kind and uses the tab title for HLS names", async () => {
  const { calls, listeners } = makeEnv();
  const send = (msg) => new Promise((r) => listeners.onMessage(msg, { tab: { id: 7, url: "https://site.test/watch", title: "My Video" } }, r));
  await send({ type: "download-media", item: { url: "https://cdn.test/master.m3u8", kind: "hls" } });
  await send({ type: "download-media", item: { url: "https://site.test/watch", kind: "ytdlp" } });
  const adds = calls.native.filter((m) => m.cmd === "add");
  assert.equal(adds[0].kind, "hls");
  assert.equal(adds[0].filename, "My Video.mp4");
  assert.equal(adds[1].kind, "ytdlp");
  assert.equal(adds[1].referer, "https://site.test/watch");
});

console.log(`\n${passed} extension tests passed`);
