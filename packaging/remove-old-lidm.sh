#!/bin/sh
# Remove a pre-rename "LIDM" install. Your settings and download list are NOT deleted:
# Gesit copies them into its own folders on first start (~/.config/lidm and ~/.local/share/lidm stay as a backup).
PREFIX="${PREFIX:-$HOME/.local}"
OLD="$PREFIX/share/lidm"

# stop a running old instance (it listens on the old socket path)
[ -x "$PREFIX/bin/lidm" ] && "$PREFIX/bin/lidm" --quit >/dev/null 2>&1 || true
sleep 1

# old browser integration: manifests in every browser profile, Flatpak copies and the Flatpak override
for d in "$HOME/.config/google-chrome" "$HOME/.config/chromium" "$HOME/.config/BraveSoftware/Brave-Browser" \
         "$HOME/.config/vivaldi" "$HOME/.config/microsoft-edge" "$HOME/.config/opera"; do
    rm -f "$d/NativeMessagingHosts/com.lidm.host.json"
done
rm -f "$HOME/.mozilla/native-messaging-hosts/com.lidm.host.json" "$HOME/.librewolf/native-messaging-hosts/com.lidm.host.json"
for app in com.brave.Browser com.google.Chrome org.chromium.Chromium com.microsoft.Edge com.vivaldi.Vivaldi org.mozilla.firefox; do
    base="$HOME/.var/app/$app"
    [ -d "$base" ] || continue
    find "$base" -name com.lidm.host.json -delete 2>/dev/null
    rm -rf "$base/data/lidm"
    command -v flatpak >/dev/null 2>&1 && flatpak override --user --nofilesystem=xdg-run/lidm "$app" >/dev/null 2>&1 || true
done

# old launcher, desktop entry, icon, autostart and app files (data folders are kept)
rm -f "$PREFIX/bin/lidm" "$PREFIX/share/applications/lidm.desktop" "$PREFIX/share/icons/hicolor/scalable/apps/lidm.svg" \
      "$HOME/.config/autostart/lidm.desktop"
rm -rf "$OLD/lidm" "$OLD/extension" "$OLD/lidm-native-host" "$OLD/lidm-native-host"
rmdir "$OLD" 2>/dev/null || true
[ -d "$HOME/.config/lidm" ] || [ -d "$HOME/.local/share/lidm" ] && echo "Old LIDM data kept as a backup in ~/.config/lidm and ~/.local/share/lidm"
exit 0
