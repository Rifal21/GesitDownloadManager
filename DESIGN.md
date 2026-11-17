# Gesit · Rancangan

Download manager multi-koneksi untuk Linux dengan antarmuka desktop klasik (toolbar, tree kategori, daftar download,
dialog progress bersegmen) dan ekstensi browser untuk menangkap download dan video secara otomatis.

> Catatan scope: alur kerja dan tata letaknya terinspirasi download manager desktop klasik, tetapi semua kode,
> ikon, dan aset ditulis dari nol. Fitur yang khusus Windows diganti padanan Linux atau dilewati; dukungan Windows
> direncanakan terpisah di [docs/WINDOWS.md](docs/WINDOWS.md).

## 1. Fitur utama

| Fitur | Implementasi |
|---|---|
| Dynamic file segmentation (hingga 32 koneksi, segmen dipecah ulang saat ada yang selesai) | Engine sendiri, work-stealing split segmen terbesar |
| Resume download yang putus / dijeda | State segmen di file `.gesit`, validasi ETag / Last-Modified |
| Add URL (+ authorization) | Dialog Add URL |
| Dialog "Download File Info" saat capture | Save As, Category, Start / Download Later |
| Dialog progress: status, per-connection, speed limiter, on-completion | Bar progress bersegmen + tabel koneksi |
| Kategori otomatis (Compressed, Documents, Music, Programs, Video) | Kategori + daftar ekstensi, folder tujuan per kategori |
| Panel kiri: All Downloads, Unfinished, Finished, Queues | Tree kategori |
| Daftar dengan kolom File name, Q, Size, Status, Time left, Transfer rate, Last try date | Tabel Qt |
| Scheduler (waktu mulai/berhenti, hari, aksi saat selesai) | Scheduler + exit / suspend / poweroff via systemd |
| Options: General, Save To, Connection, Proxy, Site Logins | Dialog Options bertab |
| Speed limiter (global & per-file) | Token bucket |
| Tombol "Download this video" di atas video | Content script + floating button |
| Integrasi browser (capture link, context menu, sniff media) | Ekstensi Chromium & Firefox |
| Tray icon, notifikasi selesai, single instance | QSystemTrayIcon, notifikasi desktop, Unix socket |

## 2. Arsitektur

```
 Browser (Chrome/Firefox)                     Proses Gesit (GUI, PySide6)
 ┌────────────────────────┐                  ┌──────────────────────────────────┐
 │ Ekstensi (MV3 / MV2)   │                  │ GUI (Qt main thread)             │
 │  - downloads.onCreated │  Native          │   ▲ snapshot 4 Hz / ▼ perintah   │
 │  - webRequest (media)  │  Messaging       │ DownloadManager (asyncio thread) │
 │  - context menu        │◄──stdio JSON────►│   ├ Engine (httpx, segmen)       │
 │  - content script      │                  │   ├ Queue + Scheduler            │
 └────────────────────────┘        │         │   ├ SQLite (riwayat) + Config    │
                                   ▼         │   └ IPC server (Unix socket)     │
                        gesit-native-host ───►│       $XDG_RUNTIME_DIR/gesit.sock │
                        (jembatan stdio↔sock)└──────────────────────────────────┘
```

Alasan pilihan utama:

- **Python 3.14 + PySide6 + httpx**: cepat dibangun, Qt memberi tampilan desktop
  klasik (toolbar, tree, tabel, tray). httpx sudah terpasang. Di Arch cukup
  `pacman -S pyside6 python-httpx`.
- **Engine sendiri, bukan aria2**: dynamic segmentation dan kontrol penuh
  atas visualisasi per-koneksi, limiter, dan perilaku retry.
- **Native Messaging** (bukan HTTP localhost): cara resmi browser bicara dengan
  aplikasi lokal, tidak membuka port yang bisa dipanggil halaman web mana pun.
  Host kecil meneruskan pesan ke GUI lewat Unix socket (izin 0600).
- **GUI = daemon**: satu proses, minimize ke tray. Jika dipanggil saat belum
  jalan, native host menjalankan `gesit --background`.

## 3. Engine download

1. **Probe**: `HEAD`, fallback `GET Range: bytes=0-0`. Ambil ukuran, `Accept-Ranges`,
   ETag, Last-Modified, `Content-Disposition` (nama file, RFC 5987), URL akhir setelah redirect.
2. **Segmentasi**: `N` koneksi awal (default 8, maks 32). File di-preallocate, tiap
   worker menulis dengan `os.pwrite` pada offset sendiri.
3. **Dynamic split**: worker yang selesai mengambil segmen dengan sisa terbesar dan
   membelahnya di tengah (minimum 256 KiB). Worker pemilik membaca batas `end`
   di setiap chunk, jadi tidak ada byte yang diunduh dua kali.
4. **Resume**: tabel segmen (`start, pos, end`) ditulis ke `<file>.gesit` tiap 1 detik.
   Saat lanjut, cek ETag / Last-Modified; kalau berubah, mulai ulang.
   Server tanpa Range: satu koneksi, ditandai "resume tidak didukung".
5. **Ketahanan**: retry dengan backoff, timeout, redirect, cookie/referer/UA/header
   kustom dari browser, basic auth, proxy HTTP/SOCKS dari Options, 403/410 ditandai
   "link kedaluwarsa".
6. **Speed limiter**: token bucket global + per-download.
7. **Selesai**: rename `.part` → nama final (tangani konflik nama), notifikasi,
   aksi "on completion" (buka file/folder, exit, suspend, poweroff).
8. **Media**: HLS `.m3u8` lewat ffmpeg; situs seperti YouTube lewat `yt-dlp`
   (dipanggil sebagai subprocess, progress di-parse).

## 4. Model data & penyimpanan

- Config: `~/.config/gesit/config.json` (koneksi, folder, proxy, kategori, login situs, limiter).
- Riwayat: `~/.local/share/gesit/downloads.db` (SQLite): id, url, referer, cookies-ref,
  save_path, size, downloaded, status, queue, category, added/last_try, description.
- Status: `queued → downloading → paused | error | complete`.
- Folder default: `~/Downloads/Gesit/{Compressed,Documents,Music,Programs,Video,General}`.

## 5. Protokol IPC

JSON satu objek per baris lewat Unix socket. Native Messaging membungkus JSON yang sama
dengan header panjang 4 byte.

```
→ {"cmd":"ping"}                         ← {"ok":true,"version":"1.0.0"}
→ {"cmd":"add","url":"...","referer":"...","cookies":"a=b; c=d","user_agent":"...",
   "filename":"x.zip","filesize":123,"page_url":"...","headers":{},"dialog":true}
                                         ← {"ok":true,"id":42}
→ {"cmd":"add_many","items":[{...}]}     ← {"ok":true,"ids":[...]}
→ {"cmd":"get_config"}                   ← {"ok":true,"enabled":true,"extensions":[...],
                                              "min_size":0,"exclude_hosts":[...]}
→ {"cmd":"show"}                         ← {"ok":true}   # tampilkan jendela utama
```

## 6. Ekstensi browser

Satu basis kode JS, dua manifest (Chromium MV3 service worker, Firefox background script).

- **Capture download**: `downloads.onCreated` → cocokkan aturan (ekstensi, ukuran minimum,
  host yang dikecualikan) → batalkan download browser, hapus dari riwayat, kirim URL +
  cookies (`cookies.getAll`) + referrer + UA ke Gesit.
- **Sniff media**: `webRequest.onHeadersReceived` mendeteksi `video/*`, `audio/*`, `.m3u8`,
  `.mpd`. Disimpan per-tab, tampil di badge dan popup.
- **Floating "Download this video"** di atas elemen `<video>` (content script).
- **Context menu**: Download with Gesit (link / gambar / video / audio), Download all links.
- **Popup**: toggle on/off, status koneksi ke aplikasi, daftar media tab ini.
- **Native host**: manifest di `~/.config/{google-chrome,chromium,BraveSoftware/...}/NativeMessagingHosts/`
  dan `~/.mozilla/native-messaging-hosts/`. ID ekstensi Chrome dipaku dengan field `key`;
  Firefox memakai `gesit@gesit.app`.

Batasan Firefox: ekstensi yang tidak ditandatangani hanya jalan sebagai temporary add-on
atau di Developer Edition/ESR. Untuk pakai permanen, tanda tangani lewat AMO (`web-ext sign`).

## 7. GUI (PySide6)

- **Jendela utama**: menu (Tasks, File, Downloads, View, Help), toolbar, tree kategori
  di kiri, tabel download di kanan, status bar total kecepatan.
- **Dialog**: Add URL, Download File Info, Progress (bar bersegmen + tabel koneksi + tab
  speed limiter / on-completion), Download Complete, Options, Scheduler, About.
- **Tray**: tampil/sembunyi, Add URL, Stop All, keluar. Close window → ke tray.
- **Tema**: mengikuti palet sistem (Fusion), ikon SVG buatan sendiri.

## 8. Struktur proyek

```
gesit/            paket Python
  engine/        probe, segmen, downloader, limiter, hls, ytdlp
  core/          manager, db, config, kategori, scheduler, ipc, util
  gui/           main_window, dialogs, progress, options, scheduler, tray, icons
  native_host.py jembatan Native Messaging
extension/       shared/ (JS+HTML), chrome/, firefox/, build.sh
packaging/       PKGBUILD, gesit.desktop, install.sh, ikon
tests/           pytest (server HTTP lokal dengan Range)
```

## 9. Fase pembangunan

1. Engine + tes dengan server HTTP lokal (segmen, resume, limiter).
2. Manager, SQLite, config, IPC, native host.
3. GUI lengkap.
4. Ekstensi Chromium + Firefox, installer native host.
5. Packaging Arch (PKGBUILD, desktop entry), README.
6. Roadmap: Site Grabber, FTP, sinkronisasi queue, antrean berjadwal lanjutan, refresh link kedaluwarsa.

## 10. Definisi selesai

- Download 1 file besar dengan 8+ koneksi, putus di tengah, lalu resume dengan hasil identik (sha256).
- Klik link file di Chrome atau Firefox → muncul dialog Gesit → file masuk kategori yang benar.
- Video di halaman web muncul tombol "Download this video" dan bisa diunduh.
- `makepkg -si` memasang aplikasi, launcher, dan native host manifest.

## 11. Status implementasi

Selesai dan teruji: engine (segmen dinamis, resume, retry, limiter, fallback tanpa `os.pwrite`), manager + queue +
scheduler, SQLite (pulih otomatis dari database rusak), IPC, native host (stdlib saja, juga untuk sandbox Flatpak),
GUI lengkap, ekstensi Chromium + Firefox, HLS (ffmpeg), yt-dlp, migrasi dari nama lama, paket Arch (terbukti
membangun), `.deb` (divalidasi `python-debian`), paket toko ekstensi, `install.sh`.

Skrip AppImage (`packaging/build_appimage.sh`) sudah ditulis tetapi belum pernah dijalankan.

Windows (1.1.0): IPC TCP + token, native host berbasis registry, installer Inno Setup, CI; lihat docs/WINDOWS.md.

Belum: Site Grabber penuh, FTP, refresh link kedaluwarsa, resume untuk job HLS/yt-dlp, tanda tangan kode Windows.
Ekstensi diuji dengan harness Node yang meniru API browser; pemuatan di Chrome/Firefox asli perlu dilakukan manual.
