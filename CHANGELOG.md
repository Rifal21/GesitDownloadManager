# Changelog

## 1.1.4 (2026-11-20)

### Video downloads
- Fixed yt-dlp downloads showing no progress: the bar stayed at 0% with "Receiving data..." and the file suddenly turned complete. yt-dlp is in quiet mode whenever Gesit asks it for file names, and quiet mode switches its progress lines off; Gesit now asks for them explicitly
- Video and audio are fetched as two streams; the progress now covers both as one download (one total, no restart from zero when the audio starts)

## 1.1.3 (2026-11-20)

### Network speed test
- **Tasks > Network speed test...** (also in the tray menu) measures latency and jitter, download speed (4 parallel connections) and upload speed, with live numbers, and remembers the last result
- `gesit --speedtest` prints the same measurement in the terminal
- It only runs when you press Start and uses Cloudflare's public test servers (speed.cloudflare.com) with throw-away random data; nothing else is sent. Results show Mbit/s as your provider quotes it and MB/s as the download list shows it

### Video downloads
- When a yt-dlp download fails and the yt-dlp in use is more than 45 days old, the error now says so and how to update it. An old yt-dlp is what makes YouTube answer `HTTP Error 403` for every format of some videos
- Gesit also looks for ffmpeg and yt-dlp in its own tools folder (`~/.local/share/gesit/tools`, `%LOCALAPPDATA%\Gesit\tools` on Windows) before PATH, so a newer yt-dlp can be dropped there without touching other software; the folder survives reinstalling Gesit

## 1.1.2 (2026-11-20)

### Video downloads
- Fixed `The page needs to be reloaded` on YouTube when the browser is logged in: public videos are now tried without the browser's cookies first, and the cookies are only used when the video needs them (age limit, private, members)
- Other sites: cookies are tried first (many downloads need the login), then once more without them if that fails, so a stale login can no longer block a public video
- Cookies reach yt-dlp through a temporary cookie file (deleted afterwards, owner-only) instead of a `Cookie:` header, which newer yt-dlp flags as deprecated
- When both attempts fail, the message shown is the site's own answer ("Sign in to confirm...", "Private video") instead of an error from the stale login

## 1.1.1 (2026-11-20)

### Updates
- Gesit now tells you when a newer release is published: a dialog (or a tray notification when the window is hidden) on start and then at most once a day, once per version
- **Help > Check for updates...** checks on demand; **Options > General** has a switch to turn the automatic check off
- The only thing contacted is the public GitHub "latest release" page for this project; only the app name and version go in the request. The release page opens in the browser, nothing is downloaded or installed by itself

### Video downloads
- yt-dlp videos (YouTube and others) are now saved as `.mp4` (H.264 + AAC) instead of `.webm`; whatever ffmpeg can merge or remux is put into an mp4 container
- Fixed `HTTP Error 403` on YouTube: separate video and audio streams are requested first, the pre-merged low-resolution format (itag 18) that YouTube refuses is only a fallback
- Fixed HLS downloads failing with "Invalid argument" when the stream's audio is MP3 or AC3 (a forced `aac_adtstoasc` filter was removed; the mp4 muxer adds it for AAC by itself)

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
