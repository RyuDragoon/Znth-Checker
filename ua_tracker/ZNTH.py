# znth.cx tracker module for Upload-Assistant, built from src/trackers/UNIT3D_TEMPLATE.py.
# Installed into Upload-Assistant/src/trackers/ by `zenith_check.py setup`.
from typing import Any

from src.console import console
from src.trackers.COMMON import COMMON
from src.trackers.UNIT3D import UNIT3D

Meta = dict[str, Any]
Config = dict[str, Any]

# Ids from znth.cx (2026-10-25). The site has no HDTV type and only these resolutions.
CATEGORY_IDS = {'MOVIE': '1', 'TV': '2'}
TYPE_IDS = {'DISC': '1', 'REMUX': '2', 'ENCODE': '3', 'DVDRIP': '3', 'WEBDL': '4', 'WEBRIP': '5'}
RESOLUTION_IDS = {'2160p': '2', '1080p': '3', '720p': '5', '576p': '6', '480p': '8'}


def _lookup(mapping: dict[str, str], key: str, value: str, meta_value: str,
            reverse: bool, mapping_only: bool) -> dict[str, str]:
    if mapping_only:
        return mapping
    if reverse:
        return {v: k for k, v in mapping.items()}
    return {key: mapping.get(value or meta_value, '0')}


class ZNTH(UNIT3D):
    def __init__(self, config: Config) -> None:
        super().__init__(config, tracker_name='ZNTH')
        self.config = config
        self.common = COMMON(config)
        self.tracker = 'ZNTH'
        self.base_url = 'https://znth.cx'
        self.id_url = f'{self.base_url}/api/torrents/'
        self.upload_url = f'{self.base_url}/api/torrents/upload'
        self.requests_url = f'{self.base_url}/api/requests/filter'
        self.search_url = f'{self.base_url}/api/torrents/filter'
        self.torrent_url = f'{self.base_url}/torrents/'
        # znth.cx's banned groups (2026-10-25). [name, note]: Upload-Assistant shows the note when it skips one.
        self.banned_groups = [
            ['4K4U', 'Quality'], ['Alcaide_Kira', 'Retagging'], ['AROMA', 'Retagging'], ['aXXo', 'Quality'],
            ['BiTOR', 'Faking DV/Atmos, falsifying MediaInfo'], ['BlackTV', 'Retags'], ['BRrip', 'Quality'],
            ['CM8', 'Quality'], ['CrEwSaDe', 'Quality'], ['d3g', 'Quality'], ['DNL', 'Quality'],
            ['EVO', 'Quality'], ['FaNGDiNG0', 'Quality'], ['FGT', 'Retagging'], ['FRDS', 'Quality'],
            ['GalaxyRG', 'Quality'], ['GalaxyTV', 'Quality'], ['HD2DVD', 'Quality'], ['HDTime', 'Quality'],
            ['Hi10', 'Re-encoding'], ['ION10', 'Quality'], ['iPlanet', 'Quality'], ['KiNGDOM', 'Quality'],
            ['LAMA', 'Retagging'], ['MeGusta', 'Quality'], ['mHD', 'Quality'], ['mSD', 'Quality'],
            ['NhaNc3', 'Quality'], ['nHD', 'Quality'], ['nikt0', 'Quality'], ['nSD', 'Quality'],
            ['OFT', 'Quality, nikt0 alt'], ['PRODJi', 'Quality'], ['RARBG', 'Quality'], ['SANTi', 'Quality'],
            ['SPDVD', 'Modified full discs'], ['STUTTERSHIT', 'Quality'], ['Telly', 'Retagging'],
            ['TGx', 'Quality'], ['TSP', 'Quality'], ['TSPxL', 'Quality'], ['WAF', 'Quality'],
            ['x0r', 'Quality'], ['YIFY', 'Quality'], ['YTS', 'Quality']
        ]

    async def get_category_id(self, meta: Meta, category: str = "", reverse: bool = False,
                              mapping_only: bool = False) -> dict[str, str]:
        return _lookup(CATEGORY_IDS, 'category_id', category, meta.get('category', ''), reverse, mapping_only)

    async def get_type_id(self, meta: Meta, type: str = "", reverse: bool = False,
                          mapping_only: bool = False) -> dict[str, str]:
        return _lookup(TYPE_IDS, 'type_id', type, meta.get('type', ''), reverse, mapping_only)

    async def get_resolution_id(self, meta: Meta, resolution: str = "", reverse: bool = False,
                                mapping_only: bool = False) -> dict[str, str]:
        return _lookup(RESOLUTION_IDS, 'resolution_id', resolution, meta.get('resolution', ''), reverse, mapping_only)

    async def get_additional_checks(self, meta: Meta) -> bool:
        if meta.get('category') not in CATEGORY_IDS:
            console.print(f"[bold red]{self.tracker} only takes movies and TV, skipping.")
            return False
        if meta.get('type') not in TYPE_IDS:
            console.print(f"[bold red]{self.tracker} has no {meta.get('type')} type, skipping.")
            return False
        if meta.get('resolution') not in RESOLUTION_IDS:
            console.print(f"[bold red]{self.tracker} has no {meta.get('resolution')} resolution, skipping.")
            return False
        return True
