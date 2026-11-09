# E2iPTV (Lab version)

An IPTV player plugin for Enigma2 set-top boxes. It plays **Xtream Codes** and **Stalker / Ministra (MAC)** providers, with no other plugins needed.

This is the **Lab** version, where the new look and new features are tried out first. It installs as its own plugin, **E2iPTV Lab**, with its own settings and data, so it can sit alongside a standard E2iPTV install without either affecting the other.

**New to the plugin?** Read the **[User Guide (PDF)](E2iPTV%20Lab%20User%20Guide.pdf)**: installing, adding providers, Live TV, the guide, Movies and Series, recordings, Music, settings and troubleshooting.

## What it does

- **Live TV** with a full EPG grid, mini TV and a full-screen player, all on one screen with a slide-in icon rail
- **Movies and Series**, with posters, details, seasons and episodes, sort options (A–Z, newest added, by year) and resume
- **Catch-up** on Xtream providers, from the guide
- **Recording**, both one-off and repeat, with a two-panel recordings browser, bookmarks and a recycle bin
- **Music** from your own folder or NAS:
  - browse by folder, or by Artist, Genre, Year and Album
  - Search with And/Or (artist, genre, year range, album, track name)
  - your own playlists
  - tracker music (MOD, S3M, XM, IT and more), played with no extra install
  - reopens where you left off
- **Favourites**, **Search** across providers, and your own **bouquets** built from channels of any provider
- **Colour themes:** Dark (default) or Sky Blue (the original E2iPTV colours), in Settings → Appearance
- Nightly guide refresh, channel order and hiding, and a per-provider EPG offset

## Tested on

OpenATV 8.0.1 and 8.0.2 (development) on a Vu+ Solo4K. Earlier versions were also tested on OpenATV 7.6, OpenBH 6.0.001 and OpenViX 6.9.003. OpenPLi (Scarthgap) is supported from version 0.1-20261108a.

## Install

1. Download the latest `.ipk` from **Releases** (on the right of this page).
2. Copy it to `/tmp` on the box.
3. Install it and restart the GUI:

   ```
   opkg install --force-reinstall /tmp/enigma2-plugin-extensions-e2iptvlab_<version>_all.ipk && killall -9 enigma2
   ```

4. Open **E2iPTV Lab** from the Plugins menu.

Updating is the same: install the new `.ipk` over the old one. Your settings and data are kept.

A hard disk or USB stick is recommended. The plugin keeps its guide, channel lists, settings and music index there, in `E2iPTVLab_Data`.

## Add your providers

The first time it opens, the plugin creates `/etc/enigma2/E2iPTVLab/playlists.txt` with examples in it. Put one provider per line, with the name to show after a `#`:

```
http://server:port/get.php?username=USER&password=PASS&type=m3u_plus #My Xtream
http://portal-address/c/?mac=00:1A:79:XX:XX:XX #My Stalker
```

Put `#` straight in front of `http` to switch a provider off. Restart the GUI after editing. You can point to a different playlist folder in Settings → Other → Playlists.

## Sharing logs

Box logs can contain your provider's full stream address, **including your username and password**. Blank addresses out before sharing a log:

```
sed -E 's#(https?(:|%3a)//)[^ "]*#\1***#gI' crash.log > crash_clean.log
```

Never share `playlists.txt`.

## More

- **[User Guide (PDF)](E2iPTV%20Lab%20User%20Guide.pdf)** — the full guide.
- [`E2iPTVLab/README.txt`](E2iPTVLab/README.txt) — the short guide that's also on the box.
