# Rencana rilis Windows

Status: **belum dikerjakan**. Dokumen ini mencatat apa yang sudah portabel, apa yang harus diganti, dan urutan kerjanya.
Pilihan teknologinya (Python + PySide6 + httpx) memang dipilih karena jalan di Windows tanpa menulis ulang.

## Sudah portabel
- Engine download (httpx, asyncio), manager, queue, SQLite, scheduler, semua GUI Qt, ekstensi browser (kode sama,
  Chrome/Edge/Firefox memakai native messaging yang sama di Windows)
- Lokasi folder: sudah lewat `gesit/core/paths.py`, yang punya cabang Windows (`%APPDATA%\Gesit`, `%LOCALAPPDATA%\Gesit`)
- Penulisan file di engine: fallback `seek + write` untuk `os.pwrite` sudah ada dan dites (`test_download_works_without_os_pwrite`)

## Yang harus diganti (urut dari yang paling berisiko)

| # | Area | Masalah di Windows | Rencana |
|---|---|---|---|
| 1 | **IPC** (`core/ipc.py`, `native_host.py`) | `asyncio.start_unix_server` dan `AF_UNIX` untuk asyncio tidak ada di Windows | Named pipe (`\\.\pipe\gesit-<user>`) lewat `ProactorEventLoop.start_serving_pipe`, atau TCP `127.0.0.1` dengan port acak, token acak di berkas yang hanya bisa dibaca pengguna, dan wajib dikirim di setiap pesan. Bungkus di satu antarmuka `Transport` agar Linux tetap memakai socket Unix |
| 2 | **Native host** | stdin/stdout Windows berbasis teks, pesan biner rusak | `msvcrt.setmode(fd, os.O_BINARY)` untuk stdin dan stdout. Dibungkus jadi `gesit-native-host.exe` (PyInstaller) karena manifest harus menunjuk ke executable |
| 3 | **Manifest native host** | Chrome/Edge/Firefox di Windows membaca lokasi manifest dari **registry**, bukan dari folder | `HKCU\Software\Google\Chrome\NativeMessagingHosts\app.gesit.host` (default = path manifest JSON), `...\Microsoft\Edge\...`, `...\BraveSoftware\Brave-Browser\...`, `HKCU\Software\Mozilla\NativeMessagingHosts\app.gesit.host`. Tulis lewat `winreg` di `hostinstall.py` |
| 4 | **Meluncurkan app dari host** | `start_new_session` tidak ada | `subprocess.Popen(..., creationflags=DETACHED_PROCESS \| CREATE_NEW_PROCESS_GROUP)` |
| 5 | **Aksi selesai** (`manager._finish_action`) | `systemctl` tidak ada | `shutdown /s /t 30`, `rundll32 powrprof.dll,SetSuspendState 0,1,0` |
| 6 | **Autostart** | `~/.config/autostart` | `HKCU\Software\Microsoft\Windows\CurrentVersion\Run` |
| 7 | **Buka folder + pilih file** | D-Bus FileManager1 | `explorer /select,"path"` |
| 8 | **ffmpeg / yt-dlp** | Tidak ada di PATH | Sertakan `ffmpeg.exe` dan `yt-dlp.exe` di installer (lisensi: ffmpeg LGPL build, yt-dlp Unlicense), cari di folder aplikasi dulu |
| 9 | **Nama file** | Karakter dan nama terlarang (`CON`, `NUL`, titik di akhir), batas 260 karakter | Perluas `util.sanitize_filename`, aktifkan long path (`\\?\`) |
| 10 | **Chmod 0600** | Tidak berlaku | Lewati di Windows, simpan di `%APPDATA%` yang sudah per-pengguna |

Kebijakan sandbox Flatpak tidak relevan di Windows, jadi seluruh cabang itu dilewati.

## Pengemasan
1. **PyInstaller** mode `onedir` (bukan `onefile`: start lebih cepat, antivirus lebih jarang salah deteksi) untuk `gesit.exe`
   dan `gesit-native-host.exe`.
2. **Installer**: Inno Setup (`.exe`, per-pengguna tanpa admin, mendaftarkan registry native host, shortcut, uninstall).
   Opsi tambahan: `winget` manifest dan Scoop bucket. MSIX hanya bila perlu Microsoft Store (native messaging dari
   aplikasi MSIX butuh deklarasi tambahan, jadi tunda).
3. **Tanda tangan kode**: tanpa sertifikat, Windows SmartScreen memperingatkan pengguna. Pilihan: sertifikat OV/EV
   berbayar, atau Azure Trusted Signing. Rencanakan biayanya sebelum rilis publik.
4. **Ekstensi**: paket Chrome Web Store dan AMO yang sama. Tambahkan Microsoft Edge Add-ons (zip yang sama).
   ID ekstensi di `allowed_origins` sama di semua OS.

## CI
- GitHub Actions matriks `ubuntu-latest` dan `windows-latest` menjalankan `pytest`. Mulai dari tes yang tidak butuh Qt
  (engine, manager, IPC) agar cepat hijau di Windows, baru GUI offscreen (`QT_QPA_PLATFORM=offscreen`).
- Tes `tests/rangeserver.py` memakai `socket.shutdown`, aman di Windows. `test_native_host_bridge` perlu versi
  named-pipe setelah langkah 1.

## Urutan kerja yang disarankan
1. Langkah 1-2 (IPC + native host) dengan antarmuka `Transport`, tes di Linux dan Windows.
2. Langkah 3-4, 9 (registry, peluncuran, nama file), jalankan alur ekstensi → aplikasi di Windows sungguhan.
3. Langkah 5-8, 10 (kelengkapan sistem).
4. PyInstaller + Inno Setup, lalu CI yang menghasilkan installer.
5. Tanda tangan kode dan publikasi (GitHub Releases, winget).

Perkiraan: 1-2 minggu kerja terfokus untuk versi yang bisa dipakai, ditambah waktu pengujian di berbagai versi Windows
dan browser. Bagian terbesar ada di IPC, registry, dan pengemasan, bukan di engine atau GUI.
