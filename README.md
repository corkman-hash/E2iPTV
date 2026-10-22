# E2iPTV (Lab version)

An IPTV player plugin for Enigma2 set-top boxes. It plays **Xtream Codes** and **Stalker / Ministra (MAC)** providers, with no other plugins needed.

This is the **Lab** version, where the new look and new features are tried out first. It installs as its own plugin, **E2iPTV Lab**, with its own settings and data, so it can sit alongside a standard E2iPTV install without either affecting the other.

## What it does

- **Live TV** with a full EPG grid, mini TV and a full-screen player, all on one screen with a slide-in icon rail
- **Movies and Series**, with posters, details, seasons and episodes, sort options (A–Z, newest added, by year) and resume
- **Catch-up** on Xtream providers, from the guide
- **Recording**, both one-off and repeat, with a two-panel recordings browser, bookmarks and a recycle bin
- **Music** player for your own music folder
- **Favourites**, **Search** across providers, and your own **bouquets** built from channels of any provider
- **Colour themes:** Dark (default) or Sky Blue (the original E2iPTV colours), in Settings → Appearance
- Nightly guide refresh, channel order and hiding, and a per-provider EPG offset
- Automatic reconnect when a live channel freezes

## Tested on

OpenATV 8.0.1 on a Vu+ Solo4K. Earlier E2iPTV versions were also tested on OpenATV 7.6, OpenBH 6.0.001 and OpenViX 6.9.003.

## Install

1. Download the latest `.ipk` from **Releases** (on the right of this page).
2. Copy it to `/tmp` on the box.
3. Install it and restart the GUI:

   ```
   opkg install --force-reinstall /tmp/enigma2-plugin-extensions-e2iptvlab_<version>_all.ipk && killall -9 enigma2
   ```

4. Open **E2iPTV Lab** from the Plugins menu.

A hard disk or USB stick is recommended. The plugin keeps its guide, channel lists and settings there, in `E2iPTVLab_Data`.

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

See [`E2iPTVLab/README.txt`](E2iPTVLab/README.txt) for the full guide: keys, where data is kept, channel order files, the recording repair tools and how to report a problem.
