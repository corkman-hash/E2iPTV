# E2iPTV

An IPTV player plugin for Enigma2 set-top boxes. It plays **Xtream Codes** and **Stalker / Ministra (MAC)** providers, with no other plugins needed.

## What it does

- **Live TV** with a full EPG grid, mini TV and a full-screen player
- **Movies and Series**, with posters, plots, seasons and episodes, and resume from where you stopped
- **Catch-up** on Xtream providers, from the guide
- **Recording**, both one-off and repeat, with a recordings browser
- **Favourites**, **Search** across providers, and your own **bouquets** built from channels of any provider
- Nightly guide refresh, channel order and hiding, and a per-provider EPG offset
- Automatic reconnect when a live channel freezes

## Tested on

- OpenATV 7.6 and 8.0.1
- OpenBH 6.0.001
- OpenViX 6.9.003

All tests were on a Vu+ Solo4K. Any current Python 3 image should work.

## Install

1. Download the latest `.ipk` from **Releases** (on the right of this page).
2. Copy it to `/tmp` on the box.
3. Install it and restart the GUI:

   ```
   opkg install --force-reinstall /tmp/enigma2-plugin-extensions-e2iptv_<version>_all.ipk && killall -9 enigma2
   ```

4. Open **E2iPTV** from the Plugins menu.

A hard disk or USB stick is recommended. The plugin keeps its guide, channel lists and settings there.

## Add your providers

The first time it opens, the plugin creates `/etc/enigma2/E2iPTV/playlists.txt` with examples in it. Put one provider per line, with the name to show after a `#`:

```
http://server:port/get.php?username=USER&password=PASS&type=m3u_plus #My Xtream
http://portal-address/c/?mac=00:1A:79:XX:XX:XX #My Stalker
```

Put `#` straight in front of `http` to switch a provider off. Restart the GUI after editing.

## Sharing logs

Box logs can contain your provider's full stream address, **including your username and password**. Blank addresses out before sharing a log:

```
sed -E 's#(https?(:|%3a)//)[^ "]*#\1***#gI' crash.log > crash_clean.log
```

Never share `playlists.txt`.

## More

See [`E2iPTV/README.txt`](E2iPTV/README.txt) for the full guide: keys, where data is kept, channel order files, the recording repair tools and how to report a problem.
