# Privacy Policy: Gesit browser extension

_Last updated: 16 November 2026_

The **Gesit Integration** browser extension connects your browser to the **Gesit** download manager installed
on your own computer. It has no server, no account, no analytics, and no advertising.

## What the extension does with data

When a download starts in the browser (or when you click "Download this video", "Download with Gesit", or
"Download all links"), the extension reads the information the download manager needs to fetch the same file:

- the file's address (URL), the page it came from (referrer), and the page title for video names,
- the cookies the browser would send to that file's site, so downloads that need a login keep working,
- the browser's user-agent string, and file name and size reported by the server,
- the response headers and addresses of audio/video requests, so videos on the current page can be listed.

This information is passed **only to the Gesit application on your computer**, using the browser's Native
Messaging feature. It is never sent to the developer or to any other server by the extension. Gesit then contacts
the file's own website directly to download the file, exactly as your browser would.

## What is stored

- The extension keeps a few settings in the browser's local extension storage: the on/off switch, the
  capture rules copied from the Gesit application, and the last capture decision (shown in the popup).
- Detected media addresses for each tab are kept in memory only while the tab is open.
- The Gesit application stores its download list on your computer (`~/.local/share/gesit/downloads.db` on Linux, `%LOCALAPPDATA%\Gesit\downloads.db` on Windows; readable only by
  your user). Delete a download from the list to delete its record.

## What is never done

- No data is sold, shared, or transferred to third parties.
- No tracking, profiling, or analytics.
- No remote code is downloaded or executed; all code ships inside the extension package.

## Permissions and why they are needed

| Permission | Why |
|---|---|
| `downloads` | Notice browser downloads and hand them to Gesit; cancel the browser's copy afterwards |
| `nativeMessaging` | Talk to the Gesit application on this computer |
| `cookies` | Give Gesit the cookies the file's site expects (logins, sessions) |
| `webRequest` and site access (`http://*/*`, `https://*/*`) | Detect video/audio streams on pages you visit, and read cookies for the download's site |
| `contextMenus` | "Download with Gesit" right-click entries |
| `tabs` | Read the current tab's address and title for video downloads and the popup |
| `storage` | Remember the on/off switch and settings |
| `alarms` | Periodically re-check that the Gesit application is reachable |
| `notifications` | Tell you when Gesit could not be reached and the browser kept the download |

## Contact

Questions or removal requests: open an issue at https://github.com/Rifal21/GesitDownloadManager/issues
