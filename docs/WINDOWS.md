# Gesit di Windows

Status: **kode, installer, dan CI sudah ada (1.1.0)**. Build dan uji asap berjalan di runner Windows GitHub Actions,
bukan di komputer pengembang (Linux), jadi hasil nyata pertama datang dari CI.

## Yang sudah dikerjakan

| Area | Solusi |
|---|---|
| **IPC** (`core/ipc.py`) | TCP `127.0.0.1` dengan port acak. Port dan token acak ditulis ke `%LOCALAPPDATA%\Gesit\ipc.json`; setiap permintaan wajib membawa token, jadi halaman web (yang bisa menjangkau 127.0.0.1) tidak bisa memakainya. Linux tetap memakai socket Unix. Jalur ini juga bisa dipaksa di Linux dengan `GESIT_IPC=tcp` dan dites (`tests/test_ipc_tcp.py`) |
| **Native host** (`native_host.py`) | stdin/stdout diubah ke mode biner dengan `msvcrt.setmode`; meluncurkan `gesit.exe` di sebelahnya dengan `DETACHED_PROCESS`; dibangun sebagai `gesit-native-host.exe` (console, tanpa Qt, start ±0,1 detik) |
| **Manifest native host** (`hostinstall.py`) | Dua manifest di `%LOCALAPPDATA%\Gesit\native-messaging` dan kunci registry `HKCU\Software\{Google\Chrome, Chromium, Microsoft\Edge, BraveSoftware\Brave-Browser, Vivaldi, Mozilla}\NativeMessagingHosts\app.gesit.host`. Akses registry lewat kelas yang bisa diganti, dites di Linux dengan registry palsu |
| **Mode file** (`engine/downloader.py`) | `O_BINARY` saat membuka file; tanpa itu Windows mengubah newline dan merusak setiap download. Fallback `seek + write` untuk `os.pwrite` |
| **Nama file** (`core/util.py`) | Nama perangkat (`CON`, `NUL`, `COM1`...), titik dan spasi di akhir |
| **Hapus download** (`core/manager.py`) | Berkas parsial dihapus setelah koneksi menutupnya (Windows menolak menghapus berkas yang terbuka) |
| **Aksi selesai, autostart, buka folder** (`core/osutil.py`) | `shutdown /s /t`, `SetSuspendState`, kunci `Run` di registry, `explorer /select` |
| **ffmpeg / yt-dlp** | Dicari di `tools\` di samping `gesit.exe` dulu, baru PATH; tanpa jendela konsol yang berkedip |
| **Pengemasan** | `packaging/windows/gesit.spec` (PyInstaller onedir, dua exe berbagi satu `_internal`), `gesit.iss` (Inno Setup, per-pengguna, tanpa admin), `build_windows.ps1` (unduh ffmpeg LGPL dan yt-dlp dengan verifikasi checksum, bangun installer dan zip portable) |
| **CI** | `.github/workflows/tests.yml` (pytest di Ubuntu dan Windows), `.github/workflows/windows.yml` (build, uji asap installer, lampirkan ke rilis pada tag `v*`) |

## Yang sudah diverifikasi di Linux
- Spec PyInstaller dijalankan di Linux: dua executable terbentuk, `gesit --version` jalan, native host beku menyala dalam 0,12 detik,
  host beku meluncurkan `gesit` beku di sebelahnya lewat IPC TCP, file terunduh identik, dan `ipc.json` terhapus saat keluar
- 52 tes lulus, termasuk transport TCP dengan token (permintaan tanpa token dan permintaan HTTP ditolak),
  registry palsu, aturan nama file Windows, pencarian alat, dan perintah daya

## Yang belum bisa diverifikasi tanpa Windows
Skrip `smoke_test.ps1` menguji semuanya di runner Windows: pasang senyap, kunci registry, panggilan native host
seperti dari browser, host yang menyalakan aplikasi, download dengan byte CR/LF (menangkap masalah mode teks),
copot senyap. **Jalankan workflow "Windows build" dulu**; kalau ada langkah gagal, log-nya menunjukkan baris yang rusak.
Hal yang paling mungkin perlu penyesuaian: nama kunci registry untuk Vivaldi, format berkas checksum ffmpeg, dan
perilaku Inno Setup pada mesin tanpa hak admin.

## Belum dikerjakan
- **Tanda tangan kode**: tanpa sertifikat, SmartScreen memperingatkan pengguna. Pilihan: sertifikat OV/EV berbayar, atau
  Azure Trusted Signing. Setelah punya, tambahkan langkah `signtool` di `build_windows.ps1` untuk `gesit.exe`,
  `gesit-native-host.exe`, dan installer.
- **winget / Scoop**: manifest bisa dibuat setelah rilis publik pertama.
- **Microsoft Edge Add-ons**: unggah zip Chrome yang sama (`gesit-chrome-webstore.zip`).
- Perilaku Qt di Windows 10 vs 11 (tema gelap, DPI tinggi) belum diperiksa secara visual.
