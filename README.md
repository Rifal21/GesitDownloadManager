# Gesit

Download manager multi-koneksi untuk Linux, dengan ekstensi browser yang mengambil alih download dan video
dari Chrome, Chromium, Brave, Edge, Vivaldi, dan Firefox. Rancangan teknis ada di [DESIGN.md](DESIGN.md),
rencana Windows di [docs/WINDOWS.md](docs/WINDOWS.md).

## Fitur

- Hingga 32 koneksi per file dengan **dynamic segmentation**: segmen terbesar dibelah otomatis saat ada koneksi yang selesai
- Resume byte-exact (validasi ETag / Last-Modified), retry dengan backoff, redirect, cookie dan referer dari browser
- Kategori otomatis (Compressed, Documents, Music, Programs, Video, General), masing-masing dengan foldernya
- Queue, Scheduler (jam mulai/berhenti, hari, aksi setelah selesai), speed limiter global dan per-file
- Dialog Download File Info, dialog progress dengan bar bersegmen dan tabel koneksi
- Video: HLS/DASH lewat ffmpeg, YouTube dan situs lain lewat yt-dlp
- Proxy HTTP/SOCKS5, login per situs, tray icon (atau berjalan tersembunyi di GNOME), notifikasi

## Pasang di Linux

| Distro | Cara |
|---|---|
| **Arch / Manjaro** | `yay -S gesit` (setelah ada di AUR), atau dari checkout ini: `cd packaging && makepkg -si` |
| **Debian / Ubuntu 24.04+ / Mint** | `sudo apt install ./gesit_1.0.0_all.deb` |
| **Distro lain** | AppImage: `chmod +x Gesit-1.0.0-x86_64.AppImage && ./Gesit-1.0.0-x86_64.AppImage` |
| **Dari source** | `./packaging/install.sh` (ke `~/.local`, tanpa root; butuh `pyside6` dan `python-httpx`) |

Opsional untuk semua cara: `ffmpeg` (HLS), `yt-dlp` (YouTube dan situs video), `python-socksio` (proxy SOCKS5).

AppImage: butuh FUSE 2 (`libfuse2`), atau jalankan dengan `--appimage-extract-and-run`. Di sesi X11 Qt juga butuh
`libxcb-cursor0`; di Wayland tidak perlu. Pada AppImage, integrasi browser disiapkan dari menu
**Tasks → Set up browser integration**.

### Pindah dari LIDM (nama lama)

`./packaging/install.sh` mendeteksi instalasi LIDM, menghapus launcher, manifest browser, dan autostart lamanya,
lalu Gesit menyalin pengaturan dan daftar download pada start pertama. Folder `~/.config/lidm` dan
`~/.local/share/lidm` dibiarkan sebagai cadangan. Download yang belum selesai tetap bisa dilanjutkan.
Ekstensi lama harus dihapus dan diganti ekstensi Gesit (ID-nya berbeda).

## Ekstensi browser

Di dalam aplikasi: **Tasks → Set up browser integration** (muncul juga sebagai tawaran saat pertama dijalankan).

| Browser | Cara |
|---|---|
| Chrome, Brave, Edge, Vivaldi, Chromium | Chrome Web Store (kalau sudah terbit), atau `chrome://extensions` → *Developer mode* → *Load unpacked* → folder `chrome` dari `~/.local/share/gesit/extension` (atau `/usr/share/gesit/extension`) |
| Firefox | Add-on yang sudah ditandatangani dari addons.mozilla.org (kalau sudah terbit). Untuk uji coba: `about:debugging` → *This Firefox* → *Load Temporary Add-on* → `firefox/manifest.json` |
| Browser **Flatpak** | Integrasi dipasang ke dalam sandbox, dan sandbox hanya diberi akses ke folder socket Gesit (`xdg-run/gesit`). Muat ekstensi dari `~/.var/app/<id>/data/gesit/extension/chrome`. Gesit harus sudah berjalan, karena browser di sandbox tidak bisa menyalakannya |

| Aksi di browser | Hasil |
|---|---|
| Klik link file | Download browser dibatalkan sebelum dialog "Save as" muncul, Gesit membuka Download File Info |
| Video diputar | Tombol **Download this video** di atas video, dan daftar di popup ekstensi |
| Klik kanan | *Download with Gesit*, *Download all links with Gesit*, *Download this page's video* |
| Gesit tidak berjalan | Browser melanjutkan download sendiri, tidak ada yang hilang |

Jenis file yang ditangkap, ukuran minimum, dan situs yang dikecualikan diatur di **Options → General**.
Popup ekstensi menampilkan keputusan terakhir ("sent to Gesit" / "left to the browser") beserta alasannya.

## Berkas penting

| Path | Isi |
|---|---|
| `~/.config/gesit/config.json` | Pengaturan (izin 0600) |
| `~/.local/share/gesit/downloads.db` | Daftar download (izin 0600). Jika rusak, dipindah ke `downloads.db.corrupt-*` |
| `$XDG_RUNTIME_DIR/gesit/gesit.sock` | Socket IPC, hanya untuk pengguna ini |
| `<file>.part` + `<file>.gesit` | Data parsial dan status segmen selama download |

CLI: `gesit [URL...]`, `gesit --background`, `gesit --quit`, `gesit --install-host [--autostart] [--extension-id ID]`,
`gesit --uninstall-host`, `gesit --version`.

## Pengembangan

```sh
python -m venv --system-site-packages .venv && .venv/bin/pip install PySide6-Essentials pytest pytest-asyncio
.venv/bin/python -m pytest tests -q          # engine, manager, IPC, native host, HLS, yt-dlp, rilis, ekstensi (node)
.venv/bin/python -m gesit                    # jalankan dari source
python extension/build.py                    # extension/dist/{chrome,firefox,gesit-firefox.xpi}
python packaging/make_icons.py               # buat ulang semua ikon dari gesit/gui/icons.py
```

## Membuat rilis

```sh
./packaging/release.sh               # tarball, paket Arch, .deb, zip toko ekstensi, SHA256SUMS di dist/
./packaging/release.sh --appimage    # plus AppImage (mengunduh CPython, wheel PyPI, appimagetool: baca skripnya dulu)
```

Urutan publikasi:
1. Naikkan versi di `gesit/__init__.py`, `pyproject.toml`, dan kedua PKGBUILD, lalu tulis catatan di `CHANGELOG.md`.
2. `git tag v1.0.0 && git push origin v1.0.0`, buat rilis GitHub dari tag itu, dan unggah isi `dist/`.
3. AUR: salin `packaging/aur/PKGBUILD`, jalankan `updpkgsums`, uji dengan `makepkg -si`, lalu `git push` ke AUR.
4. Chrome Web Store dan AMO: unggah `dist/gesit-chrome-webstore.zip` dan `dist/gesit-firefox-amo.zip`,
   teks listing ada di [extension/STORE_LISTING.md](extension/STORE_LISTING.md), kebijakan privasi di [PRIVACY.md](PRIVACY.md).
5. Setelah toko memberi ID ekstensi Chrome, isi `STORE_EXTENSION_IDS` di `gesit/hostinstall.py`, naikkan versi, dan rilis ulang
   (manifest native host harus menyebut ID itu agar ekstensi dari toko bisa bicara dengan aplikasi).

## Batasan

- Belum ada Site Grabber penuh (ada *Download all links*), FTP, dan refresh link kedaluwarsa
- Download via yt-dlp dan HLS tidak bisa di-resume dan tidak memakai speed limiter
- Tray icon di GNOME butuh ekstensi AppIndicator; tanpa itu Gesit tetap berjalan tersembunyi saat jendela ditutup
- Di Firefox, jika "Always ask where to save files" aktif, dialog browser muncul lebih dulu (API Firefox tidak mengizinkan mencegahnya)
- Windows: belum didukung, lihat [docs/WINDOWS.md](docs/WINDOWS.md)

Proyek independen, tidak berafiliasi dengan produsen download manager komersial mana pun.
