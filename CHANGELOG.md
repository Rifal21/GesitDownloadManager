# Changelog

## 1.1.0 (2026-11-17)

Windows support.

### Windows
- Installer (`Gesit-1.1.0-windows-x64-setup.exe`, per-user, no administrator needed) and a portable zip
- Browser integration for Chrome, Edge, Brave, Vivaldi, Chromium and Firefox through the registry-based native messaging host
- ffmpeg and yt-dlp are bundled in the installer (checksum-verified at build time)
- Optional start at sign-in; "when finished" actions use `shutdown` and suspend
- IPC uses 127.0.0.1 with a random port and a per-session token stored in your profile; web pages cannot talk to Gesit
- Built and smoke-tested on a Windows runner in GitHub Actions (silent install, native host, download, uninstall)

### Both platforms
- Downloads are written in binary mode (required on Windows, no change on Linux)
- Windows file-name rules (reserved names such as CON/NUL, trailing dots and spaces)
- Removing a download deletes its partial files only after the connection released them
- `gesit --enable-autostart` / `--disable-autostart`


## 1.0.0 (2026-11-16)

First public release of Gesit (previously developed under the working name LIDM).

### Downloading
- Multi-connection downloads (up to 32) with dynamic segmentation: the largest remaining segment is split whenever a connection finishes
- Byte-exact resume (ETag / Last-Modified validation), retries with backoff, redirects, cookies and referer from the browser
- Queue, scheduler (start/stop times, days, action when finished), global and per-download speed limiter, HTTP/SOCKS5 proxy, per-site logins
- Automatic categories (Compressed, Documents, Music, Programs, Video, General) with their own folders
- HLS/DASH through ffmpeg, YouTube and other video sites through yt-dlp

### Application
- Toolbar, category tree and download list; Download File Info, segmented progress, Options and Scheduler dialogs
- Single instance, runs hidden in the background when the window is closed (works without a tray icon, e.g. GNOME)
- Recovers from a corrupt download list; migrates settings and downloads from the earlier LIDM builds

### Browser integration
- Extension for Chrome, Chromium, Brave, Edge, Vivaldi (Manifest V3) and Firefox
- Downloads are taken over before the browser's "Save as" dialog; videos get a "Download this video" button
- If Gesit is not running, the browser keeps the download
- Flatpak browsers are supported with a minimal sandbox grant (only Gesit's socket folder)

### Packaging
- Arch package and AUR recipe, `.deb`, source tarball, per-user `install.sh`, extension packages for the Chrome Web Store and AMO
