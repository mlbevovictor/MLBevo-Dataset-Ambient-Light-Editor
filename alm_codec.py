"""
MLBevo BCM1 (Address 09) Ambient Light Dataset Codec (72C0, 72C1, 72C2, 72C3).

Supports lossless decoding, editing, validation, and re-encoding of VAG SW-CNT XML
and raw binary datasets with automatic Big-Endian IEEE 802.3 CRC-32 calculation.
"""

from __future__ import annotations

import os
import re
import zlib
import zipfile
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Helpers: CRC-32 and 80-Node Bitmask (5 x uint16 Big-Endian)
# ---------------------------------------------------------------------------

def calc_crc32(data_without_crc: bytes) -> int:
    return zlib.crc32(data_without_crc) & 0xFFFFFFFF


def verify_crc32(raw: bytes) -> Tuple[bool, int, int]:
    if len(raw) < 4:
        return False, 0, 0
    calc = calc_crc32(raw[:-4])
    stored = int.from_bytes(raw[-4:], "big")
    return calc == stored, calc, stored


def unpack_nodemask80(b10: bytes) -> List[bool]:
    """Decode 10 bytes (5 x uint16 BE) into 80 booleans (nodes 0..79)."""
    if len(b10) != 10:
        raise ValueError(f"Expected 10 bytes for NodeMask80, got {len(b10)}")
    bits: List[bool] = []
    for w in range(5):
        word = (b10[2 * w] << 8) | b10[2 * w + 1]
        for bit in range(16):
            bits.append(bool((word >> bit) & 1))
    return bits


def pack_nodemask80(bits: List[bool]) -> bytes:
    """Encode 80 booleans (nodes 0..79) into 10 bytes (5 x uint16 BE)."""
    if len(bits) != 80:
        raise ValueError(f"Expected 80 booleans for NodeMask80, got {len(bits)}")
    out = bytearray(10)
    for w in range(5):
        word = 0
        for bit in range(16):
            if bits[w * 16 + bit]:
                word |= (1 << bit)
        out[2 * w] = (word >> 8) & 0xFF
        out[2 * w + 1] = word & 0xFF
    return bytes(out)


def nodemask_to_indices(bits: List[bool]) -> List[int]:
    return [i for i, v in enumerate(bits) if v]


def nodemask_summary(bits: List[bool], max_show: int = 8) -> str:
    idx = nodemask_to_indices(bits)
    if not idx:
        return "None (0)"
    if len(idx) <= max_show:
        return f"[{', '.join(map(str, idx))}] ({len(idx)})"
    shown = ", ".join(map(str, idx[:max_show]))
    return f"[{shown}, ...] ({len(idx)} nodes)"


# Known Audi LIN address hints for UI tooltips / labels
LIN_ADDR_HINTS: Dict[int, str] = {
    0xB0: "Door FL Contour",
    0xB2: "Door FR Contour",
    0xB4: "Door RL Contour",
    0xB6: "Door RR Contour",
    0xA0: "Door FL Surface",
    0xA2: "Door FR Surface",
    0xA4: "Door RL Surface",
    0xA6: "Door RR Surface",
    0x90: "Door FL Pocket",
    0x92: "Door FR Pocket",
    0x94: "Door RL Pocket",
    0x96: "Door RR Pocket",
    0xC0: "Seat Contour FL",
    0xC1: "Seat Contour FR",
    0xC2: "Seat Contour RL",
    0xC3: "Seat Contour RR",
    0x50: "Dashboard Left",
    0x51: "Dashboard Center",
    0x52: "Dashboard Right",
    0x40: "Center Console L",
    0x41: "Center Console R",
    0x42: "Center Console Rear",
    0x30: "Console/Dash Surf L",
    0x31: "Console/Dash Surf R",
    0x20: "Footwell/Front L",
    0x21: "Footwell/Front R",
    0xF8: "Special/Treadplate 1",
    0xF9: "Special/Treadplate 2",
    0xFA: "Special/Treadplate 3",
    0xFB: "Special/Treadplate 4",
}

NODE_GROUPS: Dict[int, str] = {
    0: "0 - White / Fixed",
    1: "1 - Surface (Fläche)",
    2: "2 - Contour (Kontur)",
    3: "3 - Secondary / Footwell",
    4: "4 - Zone 4 / Roof",
    5: "5 - Zone 5 / Accent",
    6: "6 - Zone 6 / Special",
}


# ---------------------------------------------------------------------------
# Common Header & XML Wrapper
# ---------------------------------------------------------------------------

@dataclass
class DatasetHeader:
    version: str = "X001"          # 4 ASCII bytes (0x000..0x003)
    variant_id: str = "AU6510MQQ200"  # 12 ASCII bytes (0x004..0x00F)
    struct_ver: bytes = b"\x03\x06\x00\x00"  # 4 bytes (0x010..0x013)

    @classmethod
    def from_bytes(cls, b: bytes) -> "DatasetHeader":
        return cls(
            version=b[0:4].decode("ascii", errors="replace"),
            variant_id=b[4:16].decode("ascii", errors="replace"),
            struct_ver=bytes(b[16:20]),
        )

    def to_bytes(self) -> bytes:
        v = self.version.encode("ascii", errors="replace")[:4].ljust(4, b"0")
        i = self.variant_id.encode("ascii", errors="replace")[:12].ljust(12, b"0")
        s = bytes(self.struct_ver[:4]).ljust(4, b"\x00")
        return v + i + s


@dataclass
class XmlContainerMeta:
    cnt_datei: str = ""
    cnt_version_inhalt: str = ""
    daten_name: str = ""
    start_adr: str = ""
    source_path: str = ""


# ---------------------------------------------------------------------------
# 72C0 — ALM Color (522 bytes)
# ---------------------------------------------------------------------------

@dataclass
class Dataset72C0:
    header: DatasetHeader
    fixed_color_idx: List[int]              # 4 bytes (0x014..0x017)
    user_color_count: int                   # 1 byte  (0x018)
    color_table_mode: int                   # 1 byte  (0x019)
    hmi_colors: List[Tuple[int, int, int]]  # 30 x RGB (0x01A..0x073)
    lin_colors: List[Tuple[int, int, int]]  # 50 x RGB (0x074..0x109)
    bap_zone_ids: List[int]                 # 8 bytes  (0x10A..0x111)
    vw_ext_zone_cfg: bytes                  # 36 bytes (0x112..0x135)
    color_group_map: List[int]              # 10 bytes (0x136..0x13F)
    profile_colors: List[Tuple[int, int]]   # 8 x (surf, cont) (0x140..0x14F)
    default_brightness: List[int]           # 2 bytes  (0x150..0x151)
    profile_brightness: List[List[int]]     # 8 x 9 bytes (0x152..0x199)
    default_profile: int                    # 1 byte   (0x19A)
    enabled_profiles: List[int]             # 8 bytes  (0x19B..0x1A2)
    feature_mask: int                       # 1 byte   (0x1A3)
    dimming_params: List[int]               # 10 bytes (0x1A4..0x1AD)
    padding: bytes                          # 88 bytes (0x1AE..0x205)
    meta: XmlContainerMeta = field(default_factory=XmlContainerMeta)
    original_raw: bytes = b""

    EXPECTED_LEN = 522

    @classmethod
    def from_bytes(cls, data: bytes, meta: Optional[XmlContainerMeta] = None) -> "Dataset72C0":
        if len(data) != cls.EXPECTED_LEN:
            raise ValueError(f"72C0 must be {cls.EXPECTED_LEN} bytes, got {len(data)}")
        hmi = [(data[0x01A + i*3], data[0x01A + i*3 + 1], data[0x01A + i*3 + 2]) for i in range(30)]
        lin = [(data[0x074 + i*3], data[0x074 + i*3 + 1], data[0x074 + i*3 + 2]) for i in range(50)]
        p_col = [(data[0x140 + i*2], data[0x140 + i*2 + 1]) for i in range(8)]
        p_bri = [list(data[0x152 + r*9 : 0x152 + (r+1)*9]) for r in range(8)]
        return cls(
            header=DatasetHeader.from_bytes(data),
            fixed_color_idx=list(data[0x014:0x018]),
            user_color_count=data[0x018],
            color_table_mode=data[0x019],
            hmi_colors=hmi,
            lin_colors=lin,
            bap_zone_ids=list(data[0x10A:0x112]),
            vw_ext_zone_cfg=bytes(data[0x112:0x136]),
            color_group_map=list(data[0x136:0x140]),
            profile_colors=p_col,
            default_brightness=list(data[0x150:0x152]),
            profile_brightness=p_bri,
            default_profile=data[0x19A],
            enabled_profiles=list(data[0x19B:0x1A3]),
            feature_mask=data[0x1A3],
            dimming_params=list(data[0x1A4:0x1AE]),
            padding=bytes(data[0x1AE:0x206]),
            meta=meta or XmlContainerMeta(start_adr="72C0"),
            original_raw=bytes(data),
        )

    def to_bytes(self) -> bytes:
        buf = bytearray()
        buf.extend(self.header.to_bytes())
        buf.extend(bytes(self.fixed_color_idx[:4]))
        buf.append(self.user_color_count & 0xFF)
        buf.append(self.color_table_mode & 0xFF)
        for r, g, b in self.hmi_colors[:30]:
            buf.extend((r & 0xFF, g & 0xFF, b & 0xFF))
        for r, g, b in self.lin_colors[:50]:
            buf.extend((r & 0xFF, g & 0xFF, b & 0xFF))
        buf.extend(bytes(x & 0xFF for x in self.bap_zone_ids[:8]))
        buf.extend(self.vw_ext_zone_cfg[:36].ljust(36, b"\x00"))
        buf.extend(bytes(x & 0xFF for x in self.color_group_map[:10]))
        for c1, c2 in self.profile_colors[:8]:
            buf.extend((c1 & 0xFF, c2 & 0xFF))
        buf.extend(bytes(x & 0xFF for x in self.default_brightness[:2]))
        for row in self.profile_brightness[:8]:
            buf.extend(bytes(x & 0xFF for x in row[:9]))
        buf.append(self.default_profile & 0xFF)
        buf.extend(bytes(x & 0xFF for x in self.enabled_profiles[:8]))
        buf.append(self.feature_mask & 0xFF)
        buf.extend(bytes(x & 0xFF for x in self.dimming_params[:10]))
        buf.extend(self.padding[:88].ljust(88, b"\x00"))
        assert len(buf) == self.EXPECTED_LEN - 4, f"72C0 body length mismatch: {len(buf)}"
        buf.extend(calc_crc32(bytes(buf)).to_bytes(4, "big"))
        return bytes(buf)

    def get_lin_rgb_hex(self, idx: int) -> str:
        if 0 <= idx < 50:
            r, g, b = self.lin_colors[idx]
            return f"#{r:02X}{g:02X}{b:02X}"
        return "#444444"


# ---------------------------------------------------------------------------
# 72C1 — ALM Szenario (524 bytes)
# ---------------------------------------------------------------------------

@dataclass
class ScenarioStep24:
    off_mask: List[bool]   # 80 bits (10 bytes)
    on_mask: List[bool]    # 80 bits (10 bytes)
    brightness: int        # 1 byte (0..100 or 0xFF)
    fade_time: int         # 1 byte
    hold_time: int         # 1 byte
    flags: int             # 1 byte

    @classmethod
    def from_bytes(cls, b: bytes) -> "ScenarioStep24":
        return cls(
            off_mask=unpack_nodemask80(b[0:10]),
            on_mask=unpack_nodemask80(b[10:20]),
            brightness=b[20],
            fade_time=b[21],
            hold_time=b[22],
            flags=b[23],
        )

    def to_bytes(self) -> bytes:
        return (
            pack_nodemask80(self.off_mask)
            + pack_nodemask80(self.on_mask)
            + bytes((self.brightness & 0xFF, self.fade_time & 0xFF, self.hold_time & 0xFF, self.flags & 0xFF))
        )


@dataclass
class ScenarioBlock:
    name: str
    color_grp1: int
    color_grp2: int
    step_count: int
    flags: int
    steps: List[ScenarioStep24]


@dataclass
class DoorOpenMaskPair:
    off_mask: List[bool]
    on_mask: List[bool]


@dataclass
class Dataset72C1:
    header: DatasetHeader
    global_timing: bytes                    # 10 bytes (0x014..0x01D)
    exit_warn_masks: List[List[bool]]       # 4 x NodeMask80 (0x01E..0x045)
    welcome: ScenarioBlock                  # 3 steps (0x046..0x091)
    leaving: ScenarioBlock                  # 3 steps (0x092..0x0DD)
    scen2: ScenarioBlock                    # 1 step  (0x0DE..0x0F9)
    scen3: ScenarioBlock                    # 1 step  (0x0FA..0x115)
    door_color_grp1: int                    # 0x116
    door_color_grp2: int                    # 0x117
    door_step_count: int                    # 0x118
    door_brightness: int                    # 0x119
    door_fade_time: int                     # 0x11A
    door_hold_time: int                     # 0x11B
    door_open_masks: List[DoorOpenMaskPair] # 4 x (off, on) (0x11C..0x16B)
    padding: bytes                          # 156 bytes (0x16C..0x207)
    meta: XmlContainerMeta = field(default_factory=XmlContainerMeta)
    original_raw: bytes = b""

    EXPECTED_LEN = 524

    @classmethod
    def from_bytes(cls, data: bytes, meta: Optional[XmlContainerMeta] = None) -> "Dataset72C1":
        if len(data) != cls.EXPECTED_LEN:
            raise ValueError(f"72C1 must be {cls.EXPECTED_LEN} bytes, got {len(data)}")

        def parse_block(name: str, off: int, n_steps: int) -> ScenarioBlock:
            steps = [ScenarioStep24.from_bytes(data[off + 4 + s*24 : off + 4 + (s+1)*24]) for s in range(n_steps)]
            return ScenarioBlock(name, data[off], data[off+1], data[off+2], data[off+3], steps)

        exit_masks = [unpack_nodemask80(data[0x01E + i*10 : 0x01E + (i+1)*10]) for i in range(4)]
        doors = [
            DoorOpenMaskPair(
                off_mask=unpack_nodemask80(data[0x11C + i*20 : 0x11C + i*20 + 10]),
                on_mask=unpack_nodemask80(data[0x11C + i*20 + 10 : 0x11C + (i+1)*20]),
            )
            for i in range(4)
        ]
        return cls(
            header=DatasetHeader.from_bytes(data),
            global_timing=bytes(data[0x014:0x01E]),
            exit_warn_masks=exit_masks,
            welcome=parse_block("Welcome / Unlock", 0x046, 3),
            leaving=parse_block("Leaving / Lock", 0x092, 3),
            scen2=parse_block("Scenario 2 (Driving / Base)", 0x0DE, 1),
            scen3=parse_block("Scenario 3 (Parking / Reduced)", 0x0FA, 1),
            door_color_grp1=data[0x116],
            door_color_grp2=data[0x117],
            door_step_count=data[0x118],
            door_brightness=data[0x119],
            door_fade_time=data[0x11A],
            door_hold_time=data[0x11B],
            door_open_masks=doors,
            padding=bytes(data[0x16C:0x208]),
            meta=meta or XmlContainerMeta(start_adr="72C1"),
            original_raw=bytes(data),
        )

    def to_bytes(self) -> bytes:
        buf = bytearray()
        buf.extend(self.header.to_bytes())
        buf.extend(self.global_timing[:10].ljust(10, b"\x00"))
        for m in self.exit_warn_masks[:4]:
            buf.extend(pack_nodemask80(m))
        for blk in (self.welcome, self.leaving, self.scen2, self.scen3):
            buf.extend((blk.color_grp1 & 0xFF, blk.color_grp2 & 0xFF, blk.step_count & 0xFF, blk.flags & 0xFF))
            for st in blk.steps:
                buf.extend(st.to_bytes())
        buf.extend((
            self.door_color_grp1 & 0xFF,
            self.door_color_grp2 & 0xFF,
            self.door_step_count & 0xFF,
            self.door_brightness & 0xFF,
            self.door_fade_time & 0xFF,
            self.door_hold_time & 0xFF,
        ))
        for d in self.door_open_masks[:4]:
            buf.extend(pack_nodemask80(d.off_mask))
            buf.extend(pack_nodemask80(d.on_mask))
        buf.extend(self.padding[:156].ljust(156, b"\x00"))
        assert len(buf) == self.EXPECTED_LEN - 4, f"72C1 body length mismatch: {len(buf)}"
        buf.extend(calc_crc32(bytes(buf)).to_bytes(4, "big"))
        return bytes(buf)


# ---------------------------------------------------------------------------
# 72C2 — ALM Slave (522 bytes)
# ---------------------------------------------------------------------------

@dataclass
class Dataset72C2:
    header: DatasetHeader
    active_nodes: List[bool]          # 80 bits (0x014..0x01D)
    node_groups: List[int]            # 80 bytes (0x01E..0x06D)
    zone_masks: List[List[bool]]      # 5 x NodeMask80 (0x06E..0x09F): [0]=Rsv, [1]=FL, [2]=FR, [3]=RL, [4]=RR
    slave_flags: bytes                # 2 bytes (0x0A0..0x0A1)
    lin_addresses: List[int]          # 48 bytes (0x0A2..0x0D1)
    reserved1: bytes                  # 2 bytes (0x0D2..0x0D3)
    padding: bytes                    # 306 bytes (0x0D4..0x205)
    meta: XmlContainerMeta = field(default_factory=XmlContainerMeta)
    original_raw: bytes = b""

    EXPECTED_LEN = 522

    @classmethod
    def from_bytes(cls, data: bytes, meta: Optional[XmlContainerMeta] = None) -> "Dataset72C2":
        if len(data) != cls.EXPECTED_LEN:
            raise ValueError(f"72C2 must be {cls.EXPECTED_LEN} bytes, got {len(data)}")
        zones = [unpack_nodemask80(data[0x06E + z*10 : 0x06E + (z+1)*10]) for z in range(5)]
        return cls(
            header=DatasetHeader.from_bytes(data),
            active_nodes=unpack_nodemask80(data[0x014:0x01E]),
            node_groups=list(data[0x01E:0x06E]),
            zone_masks=zones,
            slave_flags=bytes(data[0x0A0:0x0A2]),
            lin_addresses=list(data[0x0A2:0x0D2]),
            reserved1=bytes(data[0x0D2:0x0D4]),
            padding=bytes(data[0x0D4:0x206]),
            meta=meta or XmlContainerMeta(start_adr="72C2"),
            original_raw=bytes(data),
        )

    def to_bytes(self) -> bytes:
        buf = bytearray()
        buf.extend(self.header.to_bytes())
        buf.extend(pack_nodemask80(self.active_nodes))
        buf.extend(bytes(x & 0xFF for x in self.node_groups[:80]))
        for z in self.zone_masks[:5]:
            buf.extend(pack_nodemask80(z))
        buf.extend(self.slave_flags[:2].ljust(2, b"\x00"))
        buf.extend(bytes(x & 0xFF for x in self.lin_addresses[:48]))
        buf.extend(self.reserved1[:2].ljust(2, b"\x00"))
        buf.extend(self.padding[:306].ljust(306, b"\x00"))
        assert len(buf) == self.EXPECTED_LEN - 4, f"72C2 body length mismatch: {len(buf)}"
        buf.extend(calc_crc32(bytes(buf)).to_bytes(4, "big"))
        return bytes(buf)

    def node_label(self, node: int) -> str:
        if node < 48:
            bus = (node // 16) + 1
            la = self.lin_addresses[node]
            if la != 0xFF:
                hint = LIN_ADDR_HINTS.get(la, "")
                return f"N{node:02d} L{bus}:0x{la:02X}" + (f" ({hint})" if hint else "")
            return f"N{node:02d} L{bus}:--"
        elif node < 64:
            return f"N{node:02d} PWM/Dir #{node-48}"
        else:
            return f"N{node:02d} Int/Wht #{node-64}"


# ---------------------------------------------------------------------------
# 72C3 — ALM Dynamik (266 bytes)
# ---------------------------------------------------------------------------

@dataclass
class DynamicKeyframe14:
    node_mask: List[bool]  # 80 bits (10 bytes)
    color_idx: int         # 1 byte
    duration: int          # 1 byte (x10ms)
    fade_time: int         # 1 byte
    brightness: int        # 1 byte (0..100)

    @classmethod
    def from_bytes(cls, b: bytes) -> "DynamicKeyframe14":
        return cls(
            node_mask=unpack_nodemask80(b[0:10]),
            color_idx=b[10],
            duration=b[11],
            fade_time=b[12],
            brightness=b[13],
        )

    def to_bytes(self) -> bytes:
        return (
            pack_nodemask80(self.node_mask)
            + bytes((self.color_idx & 0xFF, self.duration & 0xFF, self.fade_time & 0xFF, self.brightness & 0xFF))
        )


@dataclass
class Dataset72C3:
    header: DatasetHeader
    curve_lut: List[int]                 # 16 x uint16 BE (0x014..0x033)
    dynamic_timing_cfg: List[int]        # 11 bytes (0x034..0x03E)
    climate_enable_mask: int             # 1 byte  (0x03F)
    dynamic_brightness: List[int]        # 2 bytes (0x040..0x041)
    event_color_idx: List[int]           # 13 bytes (0x042..0x04E)
    keyframe_count: int                  # 1 byte  (0x04F)
    anim_base_cfg: List[int]             # 2 bytes (0x050..0x051)
    keyframes: List[DynamicKeyframe14]   # 10 x 14 bytes (0x052..0x0DD)
    padding: bytes                       # 40 bytes (0x0DE..0x105)
    meta: XmlContainerMeta = field(default_factory=XmlContainerMeta)
    original_raw: bytes = b""

    EXPECTED_LEN = 266

    @classmethod
    def from_bytes(cls, data: bytes, meta: Optional[XmlContainerMeta] = None) -> "Dataset72C3":
        if len(data) != cls.EXPECTED_LEN:
            raise ValueError(f"72C3 must be {cls.EXPECTED_LEN} bytes, got {len(data)}")
        lut = [int.from_bytes(data[0x014 + i*2 : 0x014 + (i+1)*2], "big") for i in range(16)]
        kfs = [DynamicKeyframe14.from_bytes(data[0x052 + s*14 : 0x052 + (s+1)*14]) for s in range(10)]
        return cls(
            header=DatasetHeader.from_bytes(data),
            curve_lut=lut,
            dynamic_timing_cfg=list(data[0x034:0x03F]),
            climate_enable_mask=data[0x03F],
            dynamic_brightness=list(data[0x040:0x042]),
            event_color_idx=list(data[0x042:0x04F]),
            keyframe_count=data[0x04F],
            anim_base_cfg=list(data[0x050:0x052]),
            keyframes=kfs,
            padding=bytes(data[0x0DE:0x106]),
            meta=meta or XmlContainerMeta(start_adr="72C3"),
            original_raw=bytes(data),
        )

    def to_bytes(self) -> bytes:
        buf = bytearray()
        buf.extend(self.header.to_bytes())
        for val in self.curve_lut[:16]:
            buf.extend((val & 0xFFFF).to_bytes(2, "big"))
        buf.extend(bytes(x & 0xFF for x in self.dynamic_timing_cfg[:11]))
        buf.append(self.climate_enable_mask & 0xFF)
        buf.extend(bytes(x & 0xFF for x in self.dynamic_brightness[:2]))
        buf.extend(bytes(x & 0xFF for x in self.event_color_idx[:13]))
        buf.append(self.keyframe_count & 0xFF)
        buf.extend(bytes(x & 0xFF for x in self.anim_base_cfg[:2]))
        for kf in self.keyframes[:10]:
            buf.extend(kf.to_bytes())
        buf.extend(self.padding[:40].ljust(40, b"\x00"))
        assert len(buf) == self.EXPECTED_LEN - 4, f"72C3 body length mismatch: {len(buf)}"
        buf.extend(calc_crc32(bytes(buf)).to_bytes(4, "big"))
        return bytes(buf)


# ---------------------------------------------------------------------------
# 8W0907063 (B9 / Q5 FY / Q7 4M, SW 0215..0313) Datasets (72C0 - 72C3)
# ---------------------------------------------------------------------------

ZONE_16_NAMES_8W0: Dict[int, str] = {
    0: "Z00 Driver / Interior Base",
    1: "Z01 Door FL Primary",
    2: "Z02 Door FR Primary",
    3: "Z03 Door RL Primary",
    4: "Z04 Door RR Primary",
    5: "Z05 Dash / Roof Ext",
    6: "Z06 Door FL Surface/Contour",
    7: "Z07 Door FR Surface/Contour",
    8: "Z08 Door RL Surface/Contour",
    9: "Z09 Door RR Surface/Contour",
    10: "Z10 Center Console Ext",
    11: "Z11 Center Console / Dash",
    12: "Z12 Rear / Extended",
    13: "Z13 Footwells / Accent",
    14: "Z14 Dome / White Reading",
    15: "Z15 Reserved",
}

PROFILE_NAMES_8W0: List[str] = [
    "P0: Default / Maritime",
    "P1: Auto / Standard",
    "P2: Comfort (Blue)",
    "P3: Impulse / Allroad (Orange)",
    "P4: Dynamic (Red)",
    "P5: Offroad / Lift (Yellow)",
    "P6: Individual / Sport",
]


def zonemask16_summary(mask: int) -> str:
    bits = [i for i in range(16) if (mask >> i) & 1]
    if not bits:
        return f"0x{mask:04X} (none)"
    if len(bits) <= 5:
        return f"0x{mask:04X} (Z" + ",".join(str(b) for b in bits) + ")"
    return f"0x{mask:04X} ({len(bits)} zones)"


def _read_rgb_list(data: bytes, start: int, count: int) -> List[Tuple[int, int, int]]:
    return [(data[start + i * 3], data[start + i * 3 + 1], data[start + i * 3 + 2]) for i in range(count)]


def _pack_rgb(rgb: Tuple[int, int, int]) -> bytes:
    return bytes((rgb[0] & 0xFF, rgb[1] & 0xFF, rgb[2] & 0xFF))


@dataclass
class Dataset72C0_8W0:
    version: str                                                          # 4 B (0x000..0x003)
    fixed_white_rgb: List[Tuple[int, int, int]]                           # 4 x RGB (0x004..0x00F)
    user_color_count: int                                                 # 1 B (0x010)
    hmi_colors: List[Tuple[int, int, int]]                                # 30 x RGB (0x011..0x06A)
    climate_warm_rgb: Tuple[int, int, int]                                # 3 B (0x06B..0x06D)
    climate_cold_rgb: Tuple[int, int, int]                                # 3 B (0x06E..0x070)
    profile_enable_mask: int                                              # 1 B (0x071)
    profile_default_cfg: bytes                                            # 9 B (0x072..0x07A)
    profile_rgb: List[Tuple[Tuple[int, int, int], Tuple[int, int, int]]]  # 7 x (surf, cont) RGB (0x07B..0x0A4)
    profile_brightness: List[List[int]]                                   # 8 x 6 B (0x0A5..0x0D4)
    dimming_params: List[int]                                             # 8 B (0x0D5..0x0DC)
    lin_colors: List[Tuple[int, int, int]]                                # 30 x RGB (0x0DD..0x136)
    new_bap_menu: int                                                     # 1 B (0x137)
    reserved: int                                                         # 1 B (0x138)
    padding: bytes                                                        # 193 B (0x139..0x1F9)
    meta: XmlContainerMeta = field(default_factory=XmlContainerMeta)
    original_raw: bytes = b""

    EXPECTED_LEN = 510
    PLATFORM = "8W0"

    @classmethod
    def from_bytes(cls, data: bytes, meta: Optional[XmlContainerMeta] = None) -> "Dataset72C0_8W0":
        if len(data) != cls.EXPECTED_LEN:
            raise ValueError(f"8W0 72C0 must be {cls.EXPECTED_LEN} bytes, got {len(data)}")
        prgb = []
        for p in range(7):
            o = 0x07B + p * 6
            prgb.append(((data[o], data[o + 1], data[o + 2]), (data[o + 3], data[o + 4], data[o + 5])))
        pbri = [list(data[0x0A5 + r * 6 : 0x0A5 + (r + 1) * 6]) for r in range(8)]
        return cls(
            version=data[0:4].decode("ascii", errors="replace"),
            fixed_white_rgb=_read_rgb_list(data, 0x004, 4),
            user_color_count=data[0x010],
            hmi_colors=_read_rgb_list(data, 0x011, 30),
            climate_warm_rgb=(data[0x06B], data[0x06C], data[0x06D]),
            climate_cold_rgb=(data[0x06E], data[0x06F], data[0x070]),
            profile_enable_mask=data[0x071],
            profile_default_cfg=bytes(data[0x072:0x07B]),
            profile_rgb=prgb,
            profile_brightness=pbri,
            dimming_params=list(data[0x0D5:0x0DD]),
            lin_colors=_read_rgb_list(data, 0x0DD, 30),
            new_bap_menu=data[0x137],
            reserved=data[0x138],
            padding=bytes(data[0x139:0x1FA]),
            meta=meta or XmlContainerMeta(start_adr="72C0"),
            original_raw=bytes(data),
        )

    def to_bytes(self) -> bytes:
        buf = bytearray()
        buf.extend(self.version.encode("ascii", errors="replace")[:4].ljust(4, b" "))
        for rgb in self.fixed_white_rgb[:4]:
            buf.extend(_pack_rgb(rgb))
        buf.append(self.user_color_count & 0xFF)
        for rgb in self.hmi_colors[:30]:
            buf.extend(_pack_rgb(rgb))
        buf.extend(_pack_rgb(self.climate_warm_rgb))
        buf.extend(_pack_rgb(self.climate_cold_rgb))
        buf.append(self.profile_enable_mask & 0xFF)
        buf.extend(self.profile_default_cfg[:9].ljust(9, b"\x00"))
        for c1, c2 in self.profile_rgb[:7]:
            buf.extend(_pack_rgb(c1))
            buf.extend(_pack_rgb(c2))
        for row in self.profile_brightness[:8]:
            buf.extend(bytes(x & 0xFF for x in row[:6]))
        buf.extend(bytes(x & 0xFF for x in self.dimming_params[:8]))
        for rgb in self.lin_colors[:30]:
            buf.extend(_pack_rgb(rgb))
        buf.append(self.new_bap_menu & 0xFF)
        buf.append(self.reserved & 0xFF)
        buf.extend(self.padding[:193].ljust(193, b"\x00"))
        assert len(buf) == self.EXPECTED_LEN - 4, f"8W0 72C0 body mismatch: {len(buf)}"
        buf.extend(calc_crc32(bytes(buf)).to_bytes(4, "big"))
        return bytes(buf)


@dataclass
class ScenarioStep8_8W0:
    mask_grp1: int   # uint16 BE
    mask_grp2: int   # uint16 BE
    mask_aux: int    # uint16 BE
    brightness: int  # uint8
    fade_time: int   # uint8

    @classmethod
    def from_bytes(cls, b: bytes) -> "ScenarioStep8_8W0":
        return cls(
            mask_grp1=int.from_bytes(b[0:2], "big"),
            mask_grp2=int.from_bytes(b[2:4], "big"),
            mask_aux=int.from_bytes(b[4:6], "big"),
            brightness=b[6],
            fade_time=b[7],
        )

    def to_bytes(self) -> bytes:
        return (
            (self.mask_grp1 & 0xFFFF).to_bytes(2, "big")
            + (self.mask_grp2 & 0xFFFF).to_bytes(2, "big")
            + (self.mask_aux & 0xFFFF).to_bytes(2, "big")
            + bytes((self.brightness & 0xFF, self.fade_time & 0xFF))
        )


@dataclass
class ScenarioBlock14_8W0:
    name: str
    rgb1: Tuple[int, int, int]
    rgb2: Tuple[int, int, int]
    step: ScenarioStep8_8W0

    @classmethod
    def from_bytes(cls, name: str, b: bytes) -> "ScenarioBlock14_8W0":
        return cls(
            name=name,
            rgb1=(b[0], b[1], b[2]),
            rgb2=(b[3], b[4], b[5]),
            step=ScenarioStep8_8W0.from_bytes(b[6:14]),
        )

    def to_bytes(self) -> bytes:
        return _pack_rgb(self.rgb1) + _pack_rgb(self.rgb2) + self.step.to_bytes()


@dataclass
class Dataset72C1_8W0:
    version: str                                  # 4 B (0x000..0x003)
    global_timing: bytes                          # 14 B (0x004..0x011)
    exit_warn_masks: List[int]                    # 4 x uint16 BE (0x012..0x019)
    welcome_rgb: List[Tuple[int, int, int]]       # 2 x RGB (0x01A..0x01F)
    welcome_steps: List[ScenarioStep8_8W0]        # 3 x 8 B (0x020..0x037)
    leaving_rgb: List[Tuple[int, int, int]]       # 2 x RGB (0x038..0x03D)
    leaving_steps: List[ScenarioStep8_8W0]        # 3 x 8 B (0x03E..0x055)
    door_open: List[ScenarioBlock14_8W0]          # 4 x 14 B (0x056..0x08D)
    scen_a: ScenarioBlock14_8W0                   # 14 B (0x08E..0x09B)
    scen_b_rgb: List[Tuple[int, int, int]]        # 2 x RGB (0x09C..0x0A1)
    scen_b_steps: List[ScenarioStep8_8W0]         # 3 x 8 B (0x0A2..0x0B9)
    tail_timing: bytes                            # 4 B (0x0BA..0x0BD)
    padding: bytes                                # 60 B (0x0BE..0x0F9)
    meta: XmlContainerMeta = field(default_factory=XmlContainerMeta)
    original_raw: bytes = b""

    EXPECTED_LEN = 254
    PLATFORM = "8W0"

    @classmethod
    def from_bytes(cls, data: bytes, meta: Optional[XmlContainerMeta] = None) -> "Dataset72C1_8W0":
        if len(data) != cls.EXPECTED_LEN:
            raise ValueError(f"8W0 72C1 must be {cls.EXPECTED_LEN} bytes, got {len(data)}")
        ew = [int.from_bytes(data[0x012 + i * 2 : 0x014 + i * 2], "big") for i in range(4)]
        d_names = ["Front Left Door", "Front Right Door", "Rear Left Door", "Rear Right Door"]
        doors = [ScenarioBlock14_8W0.from_bytes(d_names[i], data[0x056 + i * 14 : 0x056 + (i + 1) * 14]) for i in range(4)]
        return cls(
            version=data[0:4].decode("ascii", errors="replace"),
            global_timing=bytes(data[0x004:0x012]),
            exit_warn_masks=ew,
            welcome_rgb=_read_rgb_list(data, 0x01A, 2),
            welcome_steps=[ScenarioStep8_8W0.from_bytes(data[0x020 + i * 8 : 0x028 + i * 8]) for i in range(3)],
            leaving_rgb=_read_rgb_list(data, 0x038, 2),
            leaving_steps=[ScenarioStep8_8W0.from_bytes(data[0x03E + i * 8 : 0x046 + i * 8]) for i in range(3)],
            door_open=doors,
            scen_a=ScenarioBlock14_8W0.from_bytes("Scenario A (All-On / Driving)", data[0x08E:0x09C]),
            scen_b_rgb=_read_rgb_list(data, 0x09C, 2),
            scen_b_steps=[ScenarioStep8_8W0.from_bytes(data[0x0A2 + i * 8 : 0x0AA + i * 8]) for i in range(3)],
            tail_timing=bytes(data[0x0BA:0x0BE]),
            padding=bytes(data[0x0BE:0x0FA]),
            meta=meta or XmlContainerMeta(start_adr="72C1"),
            original_raw=bytes(data),
        )

    def to_bytes(self) -> bytes:
        buf = bytearray()
        buf.extend(self.version.encode("ascii", errors="replace")[:4].ljust(4, b" "))
        buf.extend(self.global_timing[:14].ljust(14, b"\x00"))
        for m in self.exit_warn_masks[:4]:
            buf.extend((m & 0xFFFF).to_bytes(2, "big"))
        for rgb in self.welcome_rgb[:2]:
            buf.extend(_pack_rgb(rgb))
        for st in self.welcome_steps[:3]:
            buf.extend(st.to_bytes())
        for rgb in self.leaving_rgb[:2]:
            buf.extend(_pack_rgb(rgb))
        for st in self.leaving_steps[:3]:
            buf.extend(st.to_bytes())
        for blk in self.door_open[:4]:
            buf.extend(blk.to_bytes())
        buf.extend(self.scen_a.to_bytes())
        for rgb in self.scen_b_rgb[:2]:
            buf.extend(_pack_rgb(rgb))
        for st in self.scen_b_steps[:3]:
            buf.extend(st.to_bytes())
        buf.extend(self.tail_timing[:4].ljust(4, b"\x00"))
        buf.extend(self.padding[:60].ljust(60, b"\x00"))
        assert len(buf) == self.EXPECTED_LEN - 4, f"8W0 72C1 body mismatch: {len(buf)}"
        buf.extend(calc_crc32(bytes(buf)).to_bytes(4, "big"))
        return bytes(buf)


@dataclass
class SlaveRecord4_8W0:
    flags: int
    lin_id: int
    zone_id: int
    active: int

    @classmethod
    def from_bytes(cls, b: bytes) -> "SlaveRecord4_8W0":
        return cls(flags=b[0], lin_id=b[1], zone_id=b[2], active=b[3])

    def to_bytes(self) -> bytes:
        return bytes((self.flags & 0xFF, self.lin_id & 0xFF, self.zone_id & 0xFF, self.active & 0xFF))


@dataclass
class Dataset72C2_8W0:
    version: str                        # 4 B (0x000..0x003)
    zone_group_masks: List[int]         # 7 x uint16 BE (0x004..0x011)
    zone_config: List[Tuple[int, int]]  # 21 x (zone_id, mode) (0x012..0x03B)
    slaves: List[SlaveRecord4_8W0]      # 56 x 4 B (0x03C..0x11B)
    global_slave_cfg: bytes             # 4 B (0x11C..0x11F)
    padding: bytes                      # 218 B (0x120..0x1F9)
    meta: XmlContainerMeta = field(default_factory=XmlContainerMeta)
    original_raw: bytes = b""

    EXPECTED_LEN = 510
    PLATFORM = "8W0"

    @classmethod
    def from_bytes(cls, data: bytes, meta: Optional[XmlContainerMeta] = None) -> "Dataset72C2_8W0":
        if len(data) != cls.EXPECTED_LEN:
            raise ValueError(f"8W0 72C2 must be {cls.EXPECTED_LEN} bytes, got {len(data)}")
        zm = [int.from_bytes(data[0x004 + i * 2 : 0x006 + i * 2], "big") for i in range(7)]
        zc = [(data[0x012 + i * 2], data[0x013 + i * 2]) for i in range(21)]
        sl = [SlaveRecord4_8W0.from_bytes(data[0x03C + i * 4 : 0x040 + i * 4]) for i in range(56)]
        return cls(
            version=data[0:4].decode("ascii", errors="replace"),
            zone_group_masks=zm,
            zone_config=zc,
            slaves=sl,
            global_slave_cfg=bytes(data[0x11C:0x120]),
            padding=bytes(data[0x120:0x1FA]),
            meta=meta or XmlContainerMeta(start_adr="72C2"),
            original_raw=bytes(data),
        )

    def to_bytes(self) -> bytes:
        buf = bytearray()
        buf.extend(self.version.encode("ascii", errors="replace")[:4].ljust(4, b" "))
        for m in self.zone_group_masks[:7]:
            buf.extend((m & 0xFFFF).to_bytes(2, "big"))
        for z_id, mode in self.zone_config[:21]:
            buf.extend(bytes((z_id & 0xFF, mode & 0xFF)))
        for s in self.slaves[:56]:
            buf.extend(s.to_bytes())
        buf.extend(self.global_slave_cfg[:4].ljust(4, b"\x00"))
        buf.extend(self.padding[:218].ljust(218, b"\x00"))
        assert len(buf) == self.EXPECTED_LEN - 4, f"8W0 72C2 body mismatch: {len(buf)}"
        buf.extend(calc_crc32(bytes(buf)).to_bytes(4, "big"))
        return bytes(buf)


# Firmware-verified (FL_8W0907063H_0313 @ 0x0011CD68..0x0011D030):
# 18 slave slots in 8W0 72C2 whose comm_dtc & open_dtc IDs are hardcoded 0x0000.
# Placing an active slave here keeps the LED ON normally while preventing DTCs.
ZERO_DTC_SLOTS_8W0 = frozenset({
    2, 4, 6, 8, 10, 14, 16, 18, 20, 22, 25, 31, 45, 47, 49, 51, 53, 55,
})


def move_8w0_slave_to_zero_dtc_slot(ds: Dataset72C2_8W0, src_idx: int) -> Optional[int]:
    """
    Move an 8W0 72C2 slave record (active or inactive, as long as lin_id != 0)
    from a DTC-monitored slot to a free firmware ZERO-DTC slot so DTCs are silenced.
    Returns the destination slot index, or None if no free ZERO-DTC slot exists.
    """
    if not (0 <= src_idx < 56) or src_idx in ZERO_DTC_SLOTS_8W0:
        return None
    src = ds.slaves[src_idx]
    if src.lin_id == 0:
        return None

    def is_free(idx: int) -> bool:
        s = ds.slaves[idx]
        return (not s.active) and (s.lin_id == 0)

    # 1. Prefer adjacent twin slot (src_idx + 1) if it is a free ZERO-DTC slot
    dst = None
    if (src_idx + 1) in ZERO_DTC_SLOTS_8W0 and is_free(src_idx + 1):
        dst = src_idx + 1
    else:
        # 2. Prefer a free ZERO-DTC slot with matching zone_id
        for idx in sorted(ZERO_DTC_SLOTS_8W0):
            if is_free(idx) and ds.slaves[idx].zone_id == src.zone_id:
                dst = idx
                break
    if dst is None:
        # 3. Any free ZERO-DTC slot
        for idx in sorted(ZERO_DTC_SLOTS_8W0):
            if is_free(idx):
                dst = idx
                break
    if dst is None:
        return None

    ds.slaves[dst] = SlaveRecord4_8W0(
        flags=src.flags, lin_id=src.lin_id, zone_id=src.zone_id, active=src.active,
    )
    ds.slaves[src_idx] = SlaveRecord4_8W0(
        flags=0, lin_id=0, zone_id=src.zone_id, active=0,
    )
    return dst


# OEM-verified 1:1 mapping from 8W0907063 72C2 Slave index -> 4N0907063 72C2 Node:
# slave_idx: (node_4n0, color_group, zone_mask_idx_1to4_or_0, oem_4n0_diag_id)
_MAP_8W0_SLAVE_TO_4N0_NODE: Dict[int, Tuple[int, int, int, int]] = {
    44: (0,  1, 0, 0x90),  # Door FL Pocket
    46: (1,  1, 0, 0x92),  # Door FR Pocket
    48: (2,  1, 0, 0x94),  # Door RL Pocket
    50: (3,  1, 0, 0x96),  # Door RR Pocket
    3:  (4,  1, 0, 0xA0),  # Door FL Surface
    5:  (5,  1, 0, 0xA2),  # Door FR Surface
    7:  (6,  1, 0, 0xA4),  # Door RL Surface
    9:  (7,  1, 0, 0xA6),  # Door RR Surface
    15: (8,  2, 1, 0xB0),  # Door FL Contour (Zone FL)
    17: (9,  2, 2, 0xB2),  # Door FR Contour (Zone FR)
    19: (10, 2, 3, 0xB4),  # Door RL Contour (Zone RL)
    21: (11, 2, 4, 0xB6),  # Door RR Contour (Zone RR)
    26: (12, 1, 0, 0x20),  # Center Console / Front L
    27: (13, 1, 0, 0x21),  # Center Console / Front R
    40: (16, 5, 0, 0x00),  # Footwell FL (LIN Bus 2)
    41: (17, 5, 0, 0x02),  # Footwell FR (LIN Bus 2)
    42: (18, 5, 0, 0x04),  # Footwell RL (LIN Bus 2)
    43: (19, 5, 0, 0x06),  # Footwell RR (LIN Bus 2)
    0:  (20, 2, 0, 0x10),  # Dash / Center Contour L (LIN Bus 2)
    1:  (21, 2, 0, 0x11),  # Dash / Center Contour R (LIN Bus 2)
}


def convert_72c2_8w0_to_4n0(
    ds8: Dataset72C2_8W0,
    suppress_dtc: bool = True,
    include_pwm_int_defaults: bool = True,
) -> Dataset72C2:
    """
    Convert an 8W0907063 (B9/Q5/Q7, 510 B) 72C2 ALM Slave dataset into a
    4N0907063 (v3.6.2.0 STRUCT620, 522 B) 72C2 dataset for running 8W0 LEDs
    on a 4N0 BCM1.

    - suppress_dtc=True (recommended for 8W0 LEDs on 4N0 BCM1): fills 0x0A2..0x0D1
      with 0xFF so BCM1 drives all active LIN LEDs without logging LIN DTCs.
    - suppress_dtc=False: populates OEM 4N0 diagnostic IDs (90/A0/B0/20/00/10...).
    - include_pwm_int_defaults=True: enables standard B9/4N0 PWM (N50..N54) and
      Interior/White (N64..N73) channels matching OEM 8W0_X048.
    """
    active = [False] * 80
    groups = [0] * 80
    zones = [[False] * 80 for _ in range(5)]
    lin_diag = [0xFF] * 48

    # First-match zone -> bus mode lookup (matches firmware 0x0011D0D8..0x0011D0F6)
    zone_mode: Dict[int, int] = {}
    for z_id, mode in ds8.zone_config:
        if z_id not in zone_mode:
            zone_mode[z_id] = mode

    used_nodes = set()

    # Pass 1: Map all OEM-known B9/Q5/Q7 slaves 1-to-1
    for s_idx, (n4, grp, z_idx, oem_diag) in _MAP_8W0_SLAVE_TO_4N0_NODE.items():
        sl = ds8.slaves[s_idx]
        mode = zone_mode.get(sl.zone_id, 0)
        is_on = bool(sl.active) and (mode != 0)
        active[n4] = is_on
        groups[n4] = grp
        if z_idx and is_on:
            zones[z_idx][n4] = True
        if not suppress_dtc:
            lin_diag[n4] = oem_diag
        used_nodes.add(n4)

    # Pass 2: Map any additional active 8W0 slaves (e.g. Q7 4M extra channels)
    # into free slots on their matching LIN bus (mode 1 -> 0..15, mode 2 -> 16..31)
    for s_idx, sl in enumerate(ds8.slaves):
        if s_idx in _MAP_8W0_SLAVE_TO_4N0_NODE or not sl.active:
            continue
        mode = zone_mode.get(sl.zone_id, 0)
        if mode == 0:
            continue
        pref_range = range(0, 16) if mode == 1 else range(16, 32)
        target = next((n for n in pref_range if n not in used_nodes), None)
        if target is None:
            target = next((n for n in range(32, 48) if n not in used_nodes), None)
        if target is None:
            continue
        used_nodes.add(target)
        active[target] = True
        groups[target] = 2 if sl.zone_id in (0, 6, 7, 8, 9) else (5 if sl.zone_id == 13 else 1)
        if not suppress_dtc and 1 <= sl.lin_id <= 0xFE:
            lin_diag[target] = sl.lin_id

    # Pass 3: Standard B9/4N0 PWM (N50..N54) & Interior/White (N64..N73) channels
    if include_pwm_int_defaults:
        groups[48] = 1
        for n in (50, 51):
            active[n] = True
            groups[n] = 5
        for n in (52, 53, 54):
            active[n] = True
            groups[n] = 1
        for n in range(64, 74):
            active[n] = True
            groups[n] = 0

    src_ver = ds8.version.strip()[:4] or "B903"
    hdr = DatasetHeader(
        version="C8W0",
        variant_id=f"AU8W0{src_ver:4s}QQ2"[:12].ljust(12),
        struct_ver=b"\x03\x06\x02\x00",
    )
    tag = "NODTC" if suppress_dtc else "OEMDTC"
    meta = XmlContainerMeta(
        cnt_datei=f"DA_009_72C2_4N0_FROM_{src_ver}_{tag}.xml",
        cnt_version_inhalt="C8W0",
        daten_name=f"DA_009_72C2_4N0_FROM_{src_ver}_{tag}",
        start_adr="72C2",
        source_path=f"converted:{ds8.meta.source_path}",
    )
    return Dataset72C2(
        header=hdr,
        active_nodes=active,
        node_groups=groups,
        zone_masks=zones,
        slave_flags=b"\x01\x00",
        lin_addresses=lin_diag,
        reserved1=b"\x00\x00",
        padding=b"\x00" * 306,
        meta=meta,
        original_raw=b"",
    )


@dataclass
class ClimateZone10_8W0:
    zone_mask: int
    pairs: List[Tuple[int, int]]  # 4 x (lin_id, type)

    @classmethod
    def from_bytes(cls, b: bytes) -> "ClimateZone10_8W0":
        return cls(
            zone_mask=int.from_bytes(b[0:2], "big"),
            pairs=[(b[2 + i * 2], b[3 + i * 2]) for i in range(4)],
        )

    def to_bytes(self) -> bytes:
        buf = bytearray((self.zone_mask & 0xFFFF).to_bytes(2, "big"))
        for a, b in self.pairs[:4]:
            buf.extend(bytes((a & 0xFF, b & 0xFF)))
        return bytes(buf)


@dataclass
class Dataset72C3_8W0:
    version: str                            # 4 B (0x000..0x003)
    timing_thresholds: List[int]            # 4 x uint16 BE (0x004..0x00B)
    scale_levels: List[int]                 # 4 B (0x00C..0x00F)
    ramp_lut: List[int]                     # 11 x uint16 BE (0x010..0x025)
    decay_curve: List[int]                  # 10 B (0x026..0x02F)
    pulse_step_times: List[int]             # 5 x uint16 BE (0x030..0x039)
    dynamic_timing_cfg: List[int]           # 7 B (0x03A..0x040)
    climate_enable_mask: int                # 1 B (0x041)
    dynamic_brightness: List[int]           # 2 B (0x042..0x043)
    climate_zones: List[ClimateZone10_8W0]  # 4 x 10 B (0x044..0x06B)
    event_rgb: List[Tuple[int, int, int]]   # 10 x RGB (0x06C..0x089)
    padding: bytes                          # 112 B (0x08A..0x0F9)
    meta: XmlContainerMeta = field(default_factory=XmlContainerMeta)
    original_raw: bytes = b""

    EXPECTED_LEN = 254
    PLATFORM = "8W0"

    @classmethod
    def from_bytes(cls, data: bytes, meta: Optional[XmlContainerMeta] = None) -> "Dataset72C3_8W0":
        if len(data) != cls.EXPECTED_LEN:
            raise ValueError(f"8W0 72C3 must be {cls.EXPECTED_LEN} bytes, got {len(data)}")
        return cls(
            version=data[0:4].decode("ascii", errors="replace"),
            timing_thresholds=[int.from_bytes(data[0x004 + i * 2 : 0x006 + i * 2], "big") for i in range(4)],
            scale_levels=list(data[0x00C:0x010]),
            ramp_lut=[int.from_bytes(data[0x010 + i * 2 : 0x012 + i * 2], "big") for i in range(11)],
            decay_curve=list(data[0x026:0x030]),
            pulse_step_times=[int.from_bytes(data[0x030 + i * 2 : 0x032 + i * 2], "big") for i in range(5)],
            dynamic_timing_cfg=list(data[0x03A:0x041]),
            climate_enable_mask=data[0x041],
            dynamic_brightness=list(data[0x042:0x044]),
            climate_zones=[ClimateZone10_8W0.from_bytes(data[0x044 + i * 10 : 0x04E + i * 10]) for i in range(4)],
            event_rgb=_read_rgb_list(data, 0x06C, 10),
            padding=bytes(data[0x08A:0x0FA]),
            meta=meta or XmlContainerMeta(start_adr="72C3"),
            original_raw=bytes(data),
        )

    def to_bytes(self) -> bytes:
        buf = bytearray()
        buf.extend(self.version.encode("ascii", errors="replace")[:4].ljust(4, b" "))
        for v in self.timing_thresholds[:4]:
            buf.extend((v & 0xFFFF).to_bytes(2, "big"))
        buf.extend(bytes(x & 0xFF for x in self.scale_levels[:4]))
        for v in self.ramp_lut[:11]:
            buf.extend((v & 0xFFFF).to_bytes(2, "big"))
        buf.extend(bytes(x & 0xFF for x in self.decay_curve[:10]))
        for v in self.pulse_step_times[:5]:
            buf.extend((v & 0xFFFF).to_bytes(2, "big"))
        buf.extend(bytes(x & 0xFF for x in self.dynamic_timing_cfg[:7]))
        buf.append(self.climate_enable_mask & 0xFF)
        buf.extend(bytes(x & 0xFF for x in self.dynamic_brightness[:2]))
        for cz in self.climate_zones[:4]:
            buf.extend(cz.to_bytes())
        for rgb in self.event_rgb[:10]:
            buf.extend(_pack_rgb(rgb))
        buf.extend(self.padding[:112].ljust(112, b"\x00"))
        assert len(buf) == self.EXPECTED_LEN - 4, f"8W0 72C3 body mismatch: {len(buf)}"
        buf.extend(calc_crc32(bytes(buf)).to_bytes(4, "big"))
        return bytes(buf)


# ---------------------------------------------------------------------------
# XML / Binary / ZIP I/O (Auto-detects 4N0 v3.x vs 8W0 B9/4M)
# ---------------------------------------------------------------------------

DATASET_CLASSES = {
    "72C0": Dataset72C0,
    "72C1": Dataset72C1,
    "72C2": Dataset72C2,
    "72C3": Dataset72C3,
}

DATASET_CLASSES_8W0 = {
    "72C0": Dataset72C0_8W0,
    "72C1": Dataset72C1_8W0,
    "72C2": Dataset72C2_8W0,
    "72C3": Dataset72C3_8W0,
}


def is_8w0_dataset(ds: object) -> bool:
    return getattr(ds, "PLATFORM", "4N0") == "8W0"


def parse_xml_bytes(raw_xml: bytes, source_path: str = "") -> Tuple[str, object]:
    text = raw_xml.decode("utf-8", errors="replace")
    m_adr = re.search(r"<START-ADR>\s*(?:0[xX])?([0-9A-Fa-f]+)\s*</START-ADR>", text)
    m_data = re.search(r"<DATEN>(.*?)</DATEN>", text, re.S)
    if not m_adr or not m_data:
        raise ValueError("Not a valid VAG SW-CNT dataset XML (missing START-ADR or DATEN).")
    adr = m_adr.group(1).upper()
    if adr not in DATASET_CLASSES:
        raise ValueError(f"Unsupported dataset address: {adr} (expected 72C0..72C3)")
    m_cnt = re.search(r"<CNT-DATEI>\s*([^<]*)\s*</CNT-DATEI>", text)
    m_ver = re.search(r"<CNT-VERSION-INHALT>\s*([^<]*)\s*</CNT-VERSION-INHALT>", text)
    m_name = re.search(r"<DATEN-NAME>\s*([^<]*)\s*</DATEN-NAME>", text)
    meta = XmlContainerMeta(
        cnt_datei=m_cnt.group(1).strip() if m_cnt else os.path.basename(source_path),
        cnt_version_inhalt=m_ver.group(1).strip() if m_ver else "",
        daten_name=m_name.group(1).strip() if m_name else "",
        start_adr=adr,
        source_path=source_path,
    )
    hex_str = re.sub(r"[\s,]+", "", m_data.group(1))
    payload = bytes.fromhex(hex_str)
    cls_4n0 = DATASET_CLASSES[adr]
    cls_8w0 = DATASET_CLASSES_8W0[adr]
    if len(payload) == cls_4n0.EXPECTED_LEN:
        ds = cls_4n0.from_bytes(payload, meta=meta)
    elif len(payload) == cls_8w0.EXPECTED_LEN:
        ds = cls_8w0.from_bytes(payload, meta=meta)
    else:
        raise ValueError(
            f"Unsupported {adr} payload length {len(payload)} B "
            f"(expected {cls_4n0.EXPECTED_LEN} B for 4N0 or {cls_8w0.EXPECTED_LEN} B for 8W0)"
        )
    return adr, ds


def load_xml_file(path: str) -> Tuple[str, object]:
    with open(path, "rb") as f:
        return parse_xml_bytes(f.read(), source_path=path)


def load_zip_archive(zip_path: str) -> Dict[str, object]:
    """Load all 72C0..72C3 XML datasets found inside a vehicle dataset ZIP."""
    loaded: Dict[str, object] = {}
    with zipfile.ZipFile(zip_path, "r") as zf:
        for name in zf.namelist():
            up = name.upper()
            if up.endswith(".XML") and any(adr in up for adr in ("72C0", "72C1", "72C2", "72C3")):
                try:
                    adr, ds = parse_xml_bytes(zf.read(name), source_path=f"{zip_path}!{name}")
                    loaded[adr] = ds
                except Exception:
                    pass
    return loaded


def format_xml_output(adr: str, ds: object) -> str:
    """Serialize a dataset object (4N0 or 8W0) back into a valid VAG SW-CNT XML string."""
    raw = ds.to_bytes()
    meta: XmlContainerMeta = ds.meta
    if is_8w0_dataset(ds):
        ver = ds.version.strip() or meta.cnt_version_inhalt or "B901"
        daten_name = meta.daten_name or f"DA_009_{adr}_8W0_{ver}_CUSTOM"
        cnt_datei = meta.cnt_datei or f"{daten_name}.xml"
        # 8W0 SW-CNT XMLs store contiguous uppercase hex (or neat lines; contiguous matches OEM 8W0)
        daten_str = raw.hex().upper()
    else:
        ver = ds.header.version.strip() or meta.cnt_version_inhalt or "X001"
        daten_name = meta.daten_name or f"DA_009_{adr}_CUSTOM_{ver}_{ds.header.variant_id.strip()}"
        cnt_datei = meta.cnt_datei or f"{daten_name}.xml"
        # Format hex neatly: 4B ver, 12B id, 4B struct, then 16B lines, then 4B CRC
        lines = [
            raw[0:4].hex().upper(),
            raw[4:16].hex().upper(),
            raw[16:20].hex().upper(),
        ]
        body = raw[20:-4]
        for i in range(0, len(body), 16):
            lines.append(body[i:i+16].hex().upper())
        lines.append(raw[-4:].hex().upper())
        daten_str = "\n".join(lines)

    # Note: VAG SW-CNT XML uses hex size (GROESSE-DEKOMPRIMIERT)
    size_hex = f"{len(raw):X}"
    return (
        f"<SW-CNT>"
        f"<IDENT>"
        f"<CNT-DATEI>{cnt_datei}</CNT-DATEI>"
        f"<CNT-VERSION-INHALT>{ver}</CNT-VERSION-INHALT>"
        f"<CNT-VERSION-DATUM/>"
        f"</IDENT>"
        f"<DATENBEREICHE><DATENBEREICH>"
        f"<DATEN-NAME>{daten_name}</DATEN-NAME>"
        f"<DATEN-FORMAT-NAME>DFN_BYTE</DATEN-FORMAT-NAME>"
        f"<START-ADR>{adr}</START-ADR>"
        f"<GROESSE-DEKOMPRIMIERT>{size_hex}</GROESSE-DEKOMPRIMIERT>"
        f"<DATEN>{daten_str}</DATEN>"
        f"</DATENBEREICH></DATENBEREICHE>"
        f"</SW-CNT>\n"
    )
