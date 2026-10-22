E2iPTV - IPTV player for Enigma2 (test release)
================================================

Plays Xtream Codes and Stalker/Ministra (MAC) providers: Live TV with a
full EPG grid, Movies, Series, catch-up (Xtream), recording, favourites,
search, and your own bouquets built from channels of any provider.

Tested on OpenATV 7.6, OpenATV 8.0.1, OpenBH 6.0.001 and OpenViX 6.9.003,
on a Vu+ Solo4K.

On OpenBH the image keeps the EPG key for itself - in the EPG grid use 0
instead (jump to now / back to the categories).

Sharing a log or crash file? Box logs contain your provider's full stream
address - INCLUDING YOUR USERNAME AND PASSWORD. Blank them out first, e.g.:
    sed -E 's#(https?(:|%3a)//)[^ "]*#\1***#gI' crash.log > crash_clean.log
and never send playlists.txt.


REQUIREMENTS
------------
- A hard disk or USB stick. The plugin keeps its guide data, channel
  lists, bouquets and settings on it. Without one it works, but keeps
  everything in memory and loses it at every restart.
- Python 3 image (any current OpenATV / OpenBH).


INSTALL
-------
1. Copy the .ipk to /tmp on the box.
2. opkg install /tmp/enigma2-plugin-extensions-e2iptv_<version>_all.ipk
3. Restart the GUI (or: killall -9 enigma2).
4. Open E2iPTV from the Plugins menu.


ADD YOUR PROVIDERS
------------------
The first time it opens, the plugin creates

    /etc/enigma2/E2iPTV/playlists.txt

with examples in it. Edit it (FTP / network share), one provider per
line, with the name you want shown after a # at the end:

  Xtream:
    http://server:port/get.php?username=USER&password=PASS&type=m3u_plus #My Xtream

  Stalker:
    http://portal-address/c/?mac=00:1A:79:XX:XX:XX #My Stalker

Put # straight in front of http to switch a provider off. Restart the GUI
after editing. (You can point to a different playlist folder in
Settings -> Other -> Playlists.)


WHERE THINGS ARE KEPT
---------------------
Data disk: the first hard disk / USB stick found (/media/hdd,
/media/usb, ...), or the folder chosen in Settings -> Other -> Data
Folder. Everything the plugin keeps is in one folder on it,
E2iPTV_Data:

  E2iPTV_Data/cache/       settings, channel lists, last channel, favourites
  E2iPTV_Data/epg/         guide data and the nightly EPG log
  E2iPTV_Data/order/       channel order files (editable - see below)
  E2iPTV_Data/bouquets/    your bouquets
  E2iPTV_Data/recordings/  scheduled and repeat recordings

Also on the disk, shared with the box:
  picon/<provider>/  channel logos (folder set in Settings -> Other)
  movie/             default recording folder

Per box (not on the disk): /etc/enigma2/E2iPTV/ (playlist, data folder
setting), /etc/enigma2/e2iptv_* (Live TV position, EPG offsets).


USEFUL TO KNOW
--------------
Opening E2iPTV goes back to the last section you used. If that was
Live TV, it opens straight on the last channel, full screen; if it was
EPG Grid, straight into the grid on that channel's category.

Icon rail (Left on most screens, or Menu - see below):
  Live TV              the last channel, full screen
  EPG Grid             the grid, on the last channel's category
  Movies, Series, Recordings, Search, Favourites, Settings, Exit

Live TV (category list):
  OK on a provider     open / close it
  OK on a category     open the EPG grid
  0                    full screen on what the mini TV is playing
  Left                 icon rail

Full-screen picture (live channel, film or episode) and EPG grid:
  Menu                 the icon rail, over the picture/grid
                       (a live channel keeps playing; a film or episode
                       remembers where you got to)

EPG grid:
  OK                   menu: watch, record, Move / Hide Channel,
                       Copy to Bouquet, Show Hidden Channels, ...
  0 (or EPG)           jump to now; again to go back to the categories
  Up/Down              wrap round from the bottom of the list to the top

Channel order: each category has a text file in E2iPTV_Data/order/ you can
edit on a computer - move lines to reorder, put - in front of a line to
hide a channel. Move Channel / Hide Channel in the grid edit the same
file. Your order survives provider refreshes.

EPG offset: if a provider's guide is a few hours out, set its offset in
Settings -> Playlists -> (provider) -> EPG Offset.

Not yet supported: catch-up on Stalker providers.


RECORDINGS: SOUND STOPPING, WRONG LENGTH, SKIPS JUMPING ABOUT
-------------------------------------------------------------
Caused by the provider restarting its stream mid-programme (or a
reconnect after a drop). Recordings made with this version are corrected
as they're written. For older recordings:

  python3 /usr/lib/enigma2/python/Plugins/Extensions/E2iPTV/tools/ts_audio_check.py "<file>.ts"
  python3 /usr/lib/enigma2/python/Plugins/Extensions/E2iPTV/tools/ts_fix_audio.py "<file>.ts"

The first reports where the sound changes; the second writes a repaired
copy, "<file> (fixed).ts", next to the original (which is left alone).
The repair also removes the short, regular pauses that versions
1.0-20261011b to l added to recordings of channels with subtitles or
teletext.


REPORTING A PROBLEM
-------------------
Please say which image and box, the version shown in Settings -> About,
and what you did. The logs can be read on the TV in
Settings -> Status & Logs (RED clears one, GREEN re-reads it), which
also has a per-provider EPG refresh summary. Useful logs:

  /tmp/e2iptv_slow.log      anything on screen that took too long
  /tmp/e2iptv_catchup.log   catch-up requests
  /tmp/e2iptv_xtream.log    failed Xtream requests
  <disk>/E2iPTV_Data/epg/nightly_refresh.log   the nightly EPG job

For full debug logging, create an empty file /etc/enigma2/E2iPTV/debug
and restart the GUI (delete it again afterwards).
