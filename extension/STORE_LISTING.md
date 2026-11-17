# Store listing: Gesit Integration

Everything to paste into the Chrome Web Store dashboard and addons.mozilla.org (AMO).
Packages: `dist/gesit-chrome-webstore.zip` (no pinned key, the store assigns the id) and `dist/gesit-firefox-amo.zip`.
Images: `extension/store-assets/` (promo tile 440x280, app screenshot 1280x800). The stores also want
screenshots of the extension *in a browser*: take two real ones (popup on a video page, the Gesit dialog after
clicking a download link) at 1280x800.

## Name
Gesit Integration

## Homepage / support URL
https://github.com/Rifal21/GesitDownloadManager
https://github.com/Rifal21/GesitDownloadManager/issues

## Privacy policy URL
https://github.com/Rifal21/GesitDownloadManager/blob/main/PRIVACY.md

## Short description (max 132 characters)
Send downloads and videos from your browser to Gesit, the fast multi-connection download manager for Linux and Windows.

## Category
Chrome Web Store: Tools (or Productivity). AMO: Download Management.

## Detailed description (English)
Gesit Integration connects your browser to **Gesit**, a fast multi-connection download manager for Linux.

**Requires the free Gesit desktop application** (Windows installer, Linux AppImage, .deb, or Arch package). The extension does nothing by
itself: it only forwards downloads to the app running on your own computer.

What you get
- Click a download link and Gesit takes over: up to 32 connections per file, resume after interruptions, queue and scheduler
- The browser's own "Save as" prompt is skipped when Gesit takes the download
- A "Download this video" button appears on videos; the popup lists the media found on the page
- Right-click: Download with Gesit, Download all links, Download this page's video (YouTube and many other sites)
- If Gesit is not running, the browser simply keeps the download, so nothing is ever lost
- Choose which file types are captured, minimum size, and sites to ignore, in the Gesit options

Privacy: no accounts, no analytics, no servers. Everything stays on your computer. See the privacy policy.

## Deskripsi (Bahasa Indonesia)
Gesit Integration menghubungkan browser ke **Gesit**, download manager multi-koneksi yang cepat untuk Linux.

**Butuh aplikasi desktop Gesit (gratis)**. Ekstensi ini hanya meneruskan download ke aplikasi di komputer kamu sendiri.

- Klik link file, Gesit mengambil alih: hingga 32 koneksi per file, resume, queue, dan scheduler
- Dialog "Save as" bawaan browser dilewati saat Gesit mengambil download
- Tombol "Download this video" muncul di video, popup menampilkan media di halaman
- Klik kanan: Download with Gesit, Download all links, Download this page's video
- Jika Gesit tidak berjalan, browser melanjutkan download sendiri
- Privasi: tanpa akun, tanpa analitik, semua tetap di komputer kamu

## Single purpose (Chrome Web Store field)
Hand browser downloads and detected videos over to the Gesit download manager installed on the user's computer.

## Permission justifications (Chrome Web Store, "Privacy practices" tab)
- **downloads**: detect a download starting, cancel the browser's copy and hand the address to Gesit.
- **nativeMessaging**: the only channel to the Gesit desktop application (host `app.gesit.host`).
- **cookies**: pass the cookies the file's site expects to Gesit so authenticated downloads (for example cloud storage) work.
- **webRequest + host permissions (http/https)**: observe response headers of audio/video requests to list videos on the current page; read cookies for the download's site. Nothing is blocked or modified.
- **contextMenus**: right-click entries "Download with Gesit".
- **tabs**: read the active tab's URL and title (video file name, popup).
- **storage**: keep the on/off switch and capture rules.
- **alarms**: periodic reachability check of the desktop app.
- **notifications**: tell the user when the desktop app could not be reached.
- **Remote code**: none. **Data usage**: "Web history" and "Website content" (URLs, cookies) are handled only locally and sent only to the user's own desktop application; not sold, not used for unrelated purposes, not transferred to the developer.

## Notes for reviewers (paste in "Test instructions")
The extension forwards downloads to a native desktop application, so it cannot show any result without it installed.
Install Gesit (Windows installer or Linux package) from the project page, run it once, choose Tasks > "Set up browser integration", then click any
downloadable link (for example a .zip) in the browser. The popup shows the last capture decision and whether the app
is connected. If Gesit is not running, the extension leaves the browser download untouched.

## AMO specifics
- Add-on id: `gesit@gesit.app` (change it in `extension/firefox/manifest.json` if you prefer another; it must also match
  `FIREFOX_EXTENSION_ID` in `gesit/hostinstall.py`).
- `data_collection_permissions` is declared as `none` (nothing is collected by the developer).
- The code is plain JavaScript, not minified or generated, so no source-code upload is required.
- Select "On your own" distribution first if you only want a signed .xpi for the website and GitHub release.
