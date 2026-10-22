# =============================================================================
# E2iPTV Backend — XMLTV Bulk EPG Reader
# 1. Downloads provider XMLTV feed (matched by epg_channel_id)
# 2. Downloads Rytec UK as supplement for regional BBC/ITV not in provider feed
# =============================================================================

import time
import calendar

try:
    import urllib2
    from xml.etree import cElementTree as ET
except ImportError:
    import urllib.request as urllib2
    from xml.etree import ElementTree as ET

from .epg import Epg

# Map our epg_channel_id values to Rytec channel IDs
# (Rytec uses different capitalisation/naming)
RYTEC_ID_MAP = {
    # BBC Regional
    'bbceastmidlands.uk':      'BBC1EastMidlands.uk',
    'bbconeeast.uk':           'BBC1East.uk',
    'bbcwest.uk':              'BBC1West.uk',
    'bbcnorthwest.uk':         'BBC1NorthWest.uk',
    'bbcsoutheast.uk':         'BBC1SouthEast.uk',
    'bbcsouth.uk':             'BBC1South.uk',
    'bbcwestmidlans.uk':       'BBC1Midlands.uk',
    'bbconechannelislands.uk': 'BBC1ChannelIslands.uk',
    'bbcyourkshirelincolnshire.uk': 'BBC1YorksLincs.uk',
    'bbcyorkshire.uk':         'BBC1Yorkshire.uk',
    'bbconescotlandhd.uk':     'BBC1Scotland.uk',
    'bbconescotland.uk':       'BBC1Scotland.uk',
    'bbcscotland.uk':          'BBCScotland.uk',
    'bbc1wales.uk':            'BBC1Wales.uk',
    'bbc2wales.uk':            'BBC2Wales.uk',
    'bbc1northernireland.uk':  'BBC1NorthernIreland.uk',
    'bbcnortheast.uk':         'BBC1NorthEast.uk',
    # ITV Regional
    'itvcentral.uk':           'ITV1Central.uk',
    'itvcentralplus.uk':       'ITV1Central.uk',
    'itvmeridian.uk':          'ITV1Meridian.uk',
    'itvyorkshire.uk':         'ITV1Yorkshire.uk',
    'itvwales.uk':             'ITV1Wales.uk',
    'itvwestcountry.uk':       'ITV1WestCountry.uk',
    'itvborderscotland.uk':    'ITV1BorderScotland.uk',
    'itvborderengland.uk':     'ITV1BorderEngland.uk',
    'itvgranada.uk':           'ITV1Granada.uk',
    'itvanglia.uk':            'ITV1Anglia.uk',
    'itvtynetees.uk':          'ITV1TyneTees.uk',
    'itvlondon.uk':            'ITV1London.uk',
    'itvwest.uk':              'ITV1West.uk',
    'itvchannel.uk':           'ITV1Channel.uk',
}

# Rytec UK Basic feed URLs (tried in order)
RYTEC_URLS = [
    'http://epg.vuplus-community.net/rytecUK_Basic.xz',
    'http://www.xmltvepg.nl/rytecUK_Basic.xz',
    'http://rytecepg.wanwizard.eu/rytecUK_Basic.xz',
]


class XmltvReader(object):

    def _parse_xmltv_time(self, s):
        """Parse XMLTV time: '20260804063000 +0100'"""
        try:
            s = s.strip()
            parts = s.split()
            t_str  = parts[0]
            offset = parts[1] if len(parts) > 1 else '+0000'
            struct = time.strptime(t_str, "%Y%m%d%H%M%S")
            epoch  = int(calendar.timegm(struct))
            sign   = 1 if offset[0] == '+' else -1
            oh     = int(offset[1:3])
            om     = int(offset[3:5])
            return epoch - sign * (oh * 3600 + om * 60)
        except Exception:
            return 0

    def _parse_xml(self, data, offset_secs=0):
        """Parse XMLTV XML bytes into dict: channel_id.lower() -> [Epg]"""
        results = {}
        try:
            root = ET.fromstring(data)
            for prog in root.iter('programme'):
                ch_id = prog.get('channel', '').lower()
                if not ch_id:
                    continue
                start = self._parse_xmltv_time(prog.get('start', ''))
                end   = self._parse_xmltv_time(prog.get('stop', '') or prog.get('end', ''))
                if not start or not end or start >= end:
                    continue
                title_el = prog.find('title')
                desc_el  = prog.find('desc')
                e             = Epg()
                e.stream_id   = ''
                e.title       = title_el.text.strip() if title_el is not None and title_el.text else ''
                e.description = desc_el.text.strip()  if desc_el  is not None and desc_el.text  else ''
                e.start       = start + offset_secs
                e.end         = end   + offset_secs
                results.setdefault(ch_id, []).append(e)
        except Exception:
            pass
        return results

    def fetch(self, provider):
        """Download provider XMLTV feed. Returns dict: epg_id.lower() -> [Epg]"""
        url = "%s/xmltv.php?username=%s&password=%s" % (
            provider.base_url, provider.username, provider.password)
        offset = int(getattr(provider, 'epg_offset', 0)) * 3600
        try:
            r    = urllib2.urlopen(url, timeout=60)
            data = r.read()
            return self._parse_xml(data, offset)
        except Exception:
            return {}

    def fetch_rytec(self):
        """
        Download Rytec UK Basic feed.
        Returns dict: rytec_id.lower() -> [Epg]
        Only called when provider XMLTV has no data for a channel.
        """
        import lzma
        for url in RYTEC_URLS:
            try:
                r          = urllib2.urlopen(url, timeout=30)
                compressed = r.read()
                data       = lzma.decompress(compressed)
                return self._parse_xml(data)
            except Exception:
                continue
        return {}

    def fetch_with_rytec_supplement(self, provider, channels):
        """
        Fetch provider XMLTV, then supplement missing channels from Rytec.
        Returns dict: epg_id.lower() -> [Epg]
        """
        # Build set of epg_channel_ids that need Rytec supplement
        needs_rytec = set()
        for ch in channels:
            eid = (getattr(ch, 'epg_channel_id', '') or '').lower()
            if eid and eid in [k.lower() for k in RYTEC_ID_MAP]:
                needs_rytec.add(eid)

        # Get provider XMLTV
        provider_data = self.fetch(provider)

        # Check which Rytec-mapped channels are missing from provider data
        missing = {eid for eid in needs_rytec
                   if eid not in provider_data or not provider_data[eid]}

        if not missing:
            return provider_data

        # Fetch Rytec for the missing ones
        rytec_data = self.fetch_rytec()

        # Merge: map our IDs to Rytec IDs and add to results
        for our_id in missing:
            rytec_id = RYTEC_ID_MAP.get(our_id, '').lower()
            if rytec_id and rytec_id in rytec_data:
                provider_data[our_id] = rytec_data[rytec_id]

        return provider_data
