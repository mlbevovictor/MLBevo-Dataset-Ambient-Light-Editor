"""
MLBevo BCM1 (Address 09) Ambient Light Dataset Editor GUI (72C0 - 72C3).

Firmware-verified against FL_4N0907063H_0810_011101S_V001_S.odx (PowerPC e200z4 VLE).

Run with:
    python alm_gui.py
"""

from __future__ import annotations

import os
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, colorchooser
from typing import List, Optional, Callable

from alm_codec import (
    Dataset72C0,
    Dataset72C0_8W0,
    Dataset72C1,
    Dataset72C1_8W0,
    Dataset72C2,
    Dataset72C2_8W0,
    Dataset72C3,
    Dataset72C3_8W0,
    LIN_ADDR_HINTS,
    PROFILE_NAMES_8W0,
    ZERO_DTC_SLOTS_8W0,
    ZONE_16_NAMES_8W0,
    convert_72c2_8w0_to_4n0,
    format_xml_output,
    is_8w0_dataset,
    load_xml_file,
    load_zip_archive,
    move_8w0_slave_to_zero_dtc_slot,
    nodemask_summary,
    verify_crc32,
    zonemask16_summary,
)

# Clean White / Light Theme Palette
BG = "#f4f6f9"
BG_PANEL = "#ffffff"
BG_CARD = "#eef2f7"
BG_INPUT = "#ffffff"
BORDER = "#d0d7de"
FG = "#1e293b"
FG_DIM = "#64748b"
ACCENT = "#2563eb"
GREEN = "#15803d"
AMBER = "#b45309"
DIFF_BG = "#fef3c7"
RED = "#dc2626"

DEFAULT_BROWSE_DIR = r"D:\REA\qq2" if os.path.isdir(r"D:\REA\qq2") else os.path.expanduser("~")

# Firmware-verified 72C2 80-node topology: 3x16 LIN Bus banks + PWM + Interior
# (key, short_tag, title, detail, start, end, badge_bg, badge_fg)
NODE_BANKS = [
    (
        "lin1", "LIN 1",
        "Ambient LIN Bus 1 (Nodes 0..15)",
        "LIN Table 0x0A2..0x0B1 · ActiveMask Word 0 (0x014..0x015) · Doors (typ. B0/A0/90)",
        0, 16, "#dbeafe", "#1d4ed8",
    ),
    (
        "lin2", "LIN 2",
        "Ambient LIN Bus 2 (Nodes 16..31)",
        "LIN Table 0x0B2..0x0C1 · ActiveMask Word 1 (0x016..0x017) · Dash / Console / Footwells / Seats",
        16, 32, "#dcfce7", "#15803d",
    ),
    (
        "lin3", "LIN 3",
        "Ambient LIN Bus 3 (Nodes 32..47)",
        "LIN Table 0x0C2..0x0D1 · ActiveMask Word 2 (0x018..0x019) · Extended / Rear / Special LIN Slaves",
        32, 48, "#fef3c7", "#b45309",
    ),
    (
        "pwm", "PWM",
        "Direct PWM / Analog Outputs (Nodes 48..63)",
        "ActiveMask Word 3 (0x01A..0x01B) · Hardwired BCM1 Pin Outputs",
        48, 64, "#f3e8ff", "#7e22ce",
    ),
    (
        "int", "INT",
        "Interior / White / Virtual Channels (Nodes 64..79)",
        "ActiveMask Word 4 (0x01C..0x01D) · White / Virtual Lighting Channels",
        64, 80, "#e2e8f0", "#475569",
    ),
]


# ---------------------------------------------------------------------------
# Reusable Scrollable Frame
# ---------------------------------------------------------------------------

class ScrollableFrame(ttk.Frame):
    def __init__(self, parent, bg=BG, **kwargs):
        super().__init__(parent, **kwargs)
        self.canvas = tk.Canvas(self, bg=bg, highlightthickness=0)
        self.vbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.inner = tk.Frame(self.canvas, bg=bg)
        self.inner.bind(
            "<Configure>",
            lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")),
        )
        self._win = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.canvas.configure(yscrollcommand=self.vbar.set)
        self.canvas.bind(
            "<Configure>",
            lambda e: self.canvas.itemconfigure(self._win, width=e.width),
        )
        self.canvas.pack(side="left", fill="both", expand=True)
        self.vbar.pack(side="right", fill="y")
        self.inner.bind("<Enter>", self._bind_wheel)
        self.inner.bind("<Leave>", self._unbind_wheel)

    def _bind_wheel(self, _event=None):
        self.canvas.bind_all("<MouseWheel>", self._on_wheel)

    def _unbind_wheel(self, _event=None):
        self.canvas.unbind_all("<MouseWheel>")

    def _on_wheel(self, event):
        self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")


# ---------------------------------------------------------------------------
# Visual 80-Node Bitmask Editor Dialog
# ---------------------------------------------------------------------------

class NodeMask80Dialog(tk.Toplevel):
    def __init__(
        self,
        parent,
        title: str,
        mask: List[bool],
        ds72c2: Optional[Dataset72C2],
        on_apply: Callable[[List[bool]], None],
    ):
        super().__init__(parent)
        self.title(title)
        self.configure(bg=BG)
        self.geometry("960x680")
        self.transient(parent)
        self.grab_set()
        self.on_apply = on_apply
        self.ds72c2 = ds72c2
        self.vars: List[tk.BooleanVar] = [tk.BooleanVar(value=b) for b in mask]

        top = tk.Frame(self, bg=BG_PANEL, pady=8, padx=10, highlightbackground=BORDER, highlightthickness=1)
        top.pack(fill="x")
        tk.Label(top, text=title, bg=BG_PANEL, fg=ACCENT, font=("Segoe UI", 11, "bold")).pack(side="left")
        ttk.Button(top, text="Clear All", command=self._clear_all).pack(side="right", padx=4)
        ttk.Button(top, text="Invert", command=self._invert).pack(side="right", padx=4)
        if ds72c2 is not None:
            ttk.Button(top, text="Select Active (72C2)", command=self._select_active_72c2).pack(side="right", padx=4)

        sf = ScrollableFrame(self, bg=BG)
        sf.pack(fill="both", expand=True, padx=8, pady=6)

        for _key, _tag, sec_title, sec_detail, start, end, _bg_b, fg_b in NODE_BANKS:
            box = tk.LabelFrame(
                sf.inner, text=f"{sec_title} — {sec_detail}", bg=BG_PANEL, fg=fg_b,
                font=("Segoe UI", 9, "bold"), padx=8, pady=6,
            )
            box.pack(fill="x", padx=4, pady=5)
            for idx in range(start, end):
                r = (idx - start) // 4
                c = (idx - start) % 4
                lbl = ds72c2.node_label(idx) if ds72c2 else f"Node {idx:02d}"
                is_inst = ds72c2.active_nodes[idx] if ds72c2 else True
                fg_col = FG if is_inst else FG_DIM
                cb = tk.Checkbutton(
                    box, text=lbl, variable=self.vars[idx],
                    bg=BG_PANEL, fg=fg_col, selectcolor=BG_INPUT,
                    activebackground=BG_PANEL, activeforeground=ACCENT,
                    font=("Consolas", 8), anchor="w", width=28,
                )
                cb.grid(row=r, column=c, sticky="w", padx=3, pady=2)

        bot = tk.Frame(self, bg=BG_PANEL, pady=8, padx=10, highlightbackground=BORDER, highlightthickness=1)
        bot.pack(fill="x")
        ttk.Button(bot, text="Cancel", command=self.destroy).pack(side="right", padx=4)
        ttk.Button(bot, text="Apply Mask", command=self._apply).pack(side="right", padx=4)

    def _clear_all(self):
        for v in self.vars:
            v.set(False)

    def _invert(self):
        for v in self.vars:
            v.set(not v.get())

    def _select_active_72c2(self):
        if self.ds72c2:
            for i, v in enumerate(self.vars):
                v.set(self.ds72c2.active_nodes[i])

    def _apply(self):
        self.on_apply([v.get() for v in self.vars])
        self.destroy()


# ---------------------------------------------------------------------------
# Visual 16-Zone Bitmask Editor Dialog (for 8W0907063 B9/4M)
# ---------------------------------------------------------------------------

class ZoneMask16Dialog(tk.Toplevel):
    def __init__(
        self,
        parent,
        title: str,
        mask: int,
        ds72c2_8w0: Optional[Dataset72C2_8W0],
        on_apply: Callable[[int], None],
    ):
        super().__init__(parent)
        self.title(title)
        self.configure(bg=BG)
        self.geometry("620x420")
        self.transient(parent)
        self.grab_set()
        self.on_apply = on_apply
        self.ds72c2 = ds72c2_8w0
        self.vars: List[tk.BooleanVar] = [tk.BooleanVar(value=bool((mask >> i) & 1)) for i in range(16)]

        top = tk.Frame(self, bg=BG_PANEL, pady=8, padx=10, highlightbackground=BORDER, highlightthickness=1)
        top.pack(fill="x")
        tk.Label(top, text=title, bg=BG_PANEL, fg=ACCENT, font=("Segoe UI", 10, "bold")).pack(side="left")
        ttk.Button(top, text="Clear All", command=self._clear_all).pack(side="right", padx=3)
        ttk.Button(top, text="Invert", command=self._invert).pack(side="right", padx=3)
        if ds72c2_8w0 is not None:
            ttk.Button(top, text="Select 72C2 Master", command=self._select_master).pack(side="right", padx=3)

        box = tk.LabelFrame(
            self, text="8W0907063 16-Bit Logical Zone Bitmask (uint16 BE · Bits 0..15)",
            bg=BG_PANEL, fg=ACCENT, font=("Segoe UI", 9, "bold"), padx=10, pady=8,
        )
        box.pack(fill="both", expand=True, padx=10, pady=8)

        master = ds72c2_8w0.zone_group_masks[0] if ds72c2_8w0 is not None else 0x7FFF
        for idx in range(16):
            r = idx // 2
            c = idx % 2
            lbl = ZONE_16_NAMES_8W0.get(idx, f"Z{idx:02d}")
            fg_col = FG if ((master >> idx) & 1) else FG_DIM
            tk.Checkbutton(
                box, text=f"Bit {idx:02d} (0x{1<<idx:04X}) — {lbl}", variable=self.vars[idx],
                bg=BG_PANEL, fg=fg_col, selectcolor=BG_INPUT,
                activebackground=BG_PANEL, activeforeground=ACCENT,
                font=("Consolas", 8), anchor="w", width=36,
            ).grid(row=r, column=c, sticky="w", padx=4, pady=3)

        bot = tk.Frame(self, bg=BG_PANEL, pady=8, padx=10, highlightbackground=BORDER, highlightthickness=1)
        bot.pack(fill="x")
        ttk.Button(bot, text="Cancel", command=self.destroy).pack(side="right", padx=4)
        ttk.Button(bot, text="Apply Mask", command=self._apply).pack(side="right", padx=4)

    def _clear_all(self):
        for v in self.vars:
            v.set(False)

    def _invert(self):
        for v in self.vars:
            v.set(not v.get())

    def _select_master(self):
        if self.ds72c2 is not None:
            m = self.ds72c2.zone_group_masks[0]
            for i, v in enumerate(self.vars):
                v.set(bool((m >> i) & 1))

    def _apply(self):
        val = 0
        for i, v in enumerate(self.vars):
            if v.get():
                val |= (1 << i)
        self.on_apply(val)
        self.destroy()


# ---------------------------------------------------------------------------
# Visual 72C0 Color Index Picker Dialog (System/Event + User + Profile Sentinels)
# ---------------------------------------------------------------------------

class ColorIndexPickerDialog(tk.Toplevel):
    def __init__(
        self,
        parent,
        title: str,
        current_idx: int,
        ds72c0: Optional[object],
        on_select: Callable[[int], None],
    ):
        super().__init__(parent)
        self.title(title)
        self.configure(bg=BG)
        self.geometry("820x540")
        self.transient(parent)
        self.grab_set()
        self.on_select = on_select

        top = tk.Frame(self, bg=BG_PANEL, pady=8, padx=10, highlightbackground=BORDER, highlightthickness=1)
        top.pack(fill="x")
        tk.Label(top, text=title, bg=BG_PANEL, fg=ACCENT, font=("Segoe UI", 10, "bold")).pack(side="left")
        tk.Label(
            top, text=f"Current: 0x{current_idx:02X}", bg=BG_CARD, fg=FG,
            font=("Consolas", 9, "bold"), padx=8, pady=2, relief="solid", bd=1,
        ).pack(side="right")

        sf = ScrollableFrame(self, bg=BG)
        sf.pack(fill="both", expand=True, padx=8, pady=6)

        has_4n0_72c0 = (ds72c0 is not None) and (not is_8w0_dataset(ds72c0)) and len(getattr(ds72c0, "lin_colors", [])) >= 50

        if not has_4n0_72c0:
            tk.Label(
                sf.inner,
                text="Tip: Load a 4N0/8W1 72C0 dataset to preview exact calibrated LIN RGB colors.",
                bg=DIFF_BG, fg=AMBER, font=("Segoe UI", 8, "bold"), padx=8, pady=4,
            ).pack(fill="x", padx=4, pady=(0, 6))

        def rgb_for(idx: int):
            if has_4n0_72c0 and 0 <= idx < 50:
                return ds72c0.lin_colors[idx]
            return (203, 213, 225)

        def contrast(r: int, g: int, b: int) -> str:
            return "#0f172a" if (0.299 * r + 0.587 * g + 0.114 * b) > 140 else "#ffffff"

        # 1. 20 System / Event LIN Colors (0x1E..0x31 / indices 30..49)
        sys_box = tk.LabelFrame(
            sf.inner,
            text="20 System / Event Colors from 72C0 (0x1E..0x31 · Click to Select)",
            bg=BG_PANEL, fg=ACCENT, font=("Segoe UI", 9, "bold"), padx=8, pady=6,
        )
        sys_box.pack(fill="x", padx=4, pady=4)
        sys_hints = {
            0x23: "White", 0x24: "Red", 0x25: "Blue", 0x26: "Yellow", 0x27: "Orange", 0x31: "Off",
        }
        for j in range(20):
            idx = 30 + j
            r, g, b = rgb_for(idx)
            hx = f"#{r:02X}{g:02X}{b:02X}"
            tag = sys_hints.get(idx, hx[1:] if has_4n0_72c0 else f"#{idx}")
            sel_bd = 3 if idx == current_idx else 1
            btn_txt = f"0x{idx:02X}\n{tag}"
            tk.Button(
                sys_box, text=btn_txt, bg=hx, fg=contrast(r, g, b),
                font=("Consolas", 8, "bold"), width=9, relief="solid", bd=sel_bd, cursor="hand2",
                command=lambda ii=idx: self._pick(ii),
            ).grid(row=j // 10, column=j % 10, padx=3, pady=3, sticky="ew")
            sys_box.columnconfigure(j % 10, weight=1)

        # 2. 30 User Palette LIN Colors (0x00..0x1D / indices 0..29)
        usr_box = tk.LabelFrame(
            sf.inner,
            text="30 User Palette LIN Colors from 72C0 (0x00..0x1D · Click to Select)",
            bg=BG_PANEL, fg=ACCENT, font=("Segoe UI", 9, "bold"), padx=8, pady=6,
        )
        usr_box.pack(fill="x", padx=4, pady=4)
        for i in range(30):
            r, g, b = rgb_for(i)
            hx = f"#{r:02X}{g:02X}{b:02X}"
            sel_bd = 3 if i == current_idx else 1
            btn_txt = f"0x{i:02X}\n{hx[1:] if has_4n0_72c0 else f'#{i:02d}'}"
            tk.Button(
                usr_box, text=btn_txt, bg=hx, fg=contrast(r, g, b),
                font=("Consolas", 7, "bold"), width=8, relief="solid", bd=sel_bd, cursor="hand2",
                command=lambda ii=i: self._pick(ii),
            ).grid(row=i // 10, column=i % 10, padx=3, pady=3, sticky="ew")
            usr_box.columnconfigure(i % 10, weight=1)

        # 3. Special Active MMI Profile Sentinels (0xF0 / 0xF1)
        sent_box = tk.LabelFrame(
            sf.inner,
            text="Active MMI User Profile Sentinels (Dynamic User-Selected Color)",
            bg=BG_PANEL, fg=ACCENT, font=("Segoe UI", 9, "bold"), padx=8, pady=6,
        )
        sent_box.pack(fill="x", padx=4, pady=4)
        for idx, lbl in [
            (0xF0, "0xF0 — Active MMI Profile Color 1 (Surface)"),
            (0xF1, "0xF1 — Active MMI Profile Color 2 (Contour)"),
        ]:
            tk.Button(
                sent_box, text=lbl, bg=ACCENT if idx == current_idx else BG_CARD,
                fg="#ffffff" if idx == current_idx else FG,
                font=("Consolas", 8, "bold"), padx=12, pady=5, relief="solid", bd=1, cursor="hand2",
                command=lambda ii=idx: self._pick(ii),
            ).pack(side="left", padx=6, pady=2)

        bot = tk.Frame(self, bg=BG_PANEL, pady=6, padx=10, highlightbackground=BORDER, highlightthickness=1)
        bot.pack(fill="x")
        ttk.Button(bot, text="Cancel", command=self.destroy).pack(side="right", padx=4)

    def _pick(self, idx: int):
        self.on_select(idx)
        self.destroy()


# ---------------------------------------------------------------------------
# Main Application
# ---------------------------------------------------------------------------

class AlmEditorApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("MLBevo BCM1 Ambient Light Dataset Editor (72C0 / 72C1 / 72C2 / 72C3)")
        self.geometry("1380x880")
        self.configure(bg=BG)

        self.ds72c0: Optional[Dataset72C0] = None
        self.ds72c1: Optional[Dataset72C1] = None
        self.ds72c2: Optional[Dataset72C2] = None
        self.ds72c3: Optional[Dataset72C3] = None

        self._setup_style()
        self._build_toolbar()
        self._build_tabs()
        self._build_statusbar()

    def _setup_style(self):
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("TFrame", background=BG)
        style.configure("TLabel", background=BG, foreground=FG, font=("Segoe UI", 9))
        style.configure(
            "TButton", background="#ffffff", foreground=FG,
            bordercolor=BORDER, lightcolor="#ffffff", darkcolor=BORDER,
            font=("Segoe UI", 9), padding=(8, 4),
        )
        style.map("TButton", background=[("active", "#e2e8f0")], foreground=[("active", ACCENT)])
        style.configure("TNotebook", background=BG, borderwidth=0)
        style.configure(
            "TNotebook.Tab", background="#e2e8f0", foreground=FG_DIM,
            padding=(14, 7), font=("Segoe UI", 9, "bold"),
        )
        style.map(
            "TNotebook.Tab",
            background=[("selected", BG_PANEL)],
            foreground=[("selected", ACCENT)],
        )
        style.configure("TCombobox", fieldbackground=BG_INPUT, background=BG_PANEL, foreground=FG)

    def _entry(self, parent, **kwargs):
        defaults = dict(
            bg=BG_INPUT, fg=FG, insertbackground=FG,
            relief="solid", bd=1, highlightbackground=BORDER, highlightthickness=0,
        )
        defaults.update(kwargs)
        return tk.Entry(parent, **defaults)

    def _spinbox(self, parent, **kwargs):
        defaults = dict(
            bg=BG_INPUT, fg=FG, buttonbackground=BG_CARD,
            relief="solid", bd=1,
        )
        defaults.update(kwargs)
        return tk.Spinbox(parent, **defaults)

    def _build_toolbar(self):
        bar = tk.Frame(self, bg=BG_PANEL, padx=12, pady=8, highlightbackground=BORDER, highlightthickness=1)
        bar.pack(fill="x")

        tk.Label(
            bar, text="MLBevo BCM1 ALM Editor", bg=BG_PANEL, fg=ACCENT,
            font=("Segoe UI", 12, "bold"),
        ).pack(side="left", padx=(0, 16))

        ttk.Button(bar, text="Open Vehicle ZIP (All 4)", command=self.open_zip).pack(side="left", padx=3)
        ttk.Button(bar, text="Open Single XML", command=self.open_xml).pack(side="left", padx=3)
        ttk.Button(bar, text="Save Current Tab XML", command=self.save_current_xml).pack(side="left", padx=3)
        ttk.Button(bar, text="Save All Loaded XMLs", command=self.save_all_xml).pack(side="left", padx=3)
        ttk.Button(bar, text="Export Raw .bin", command=self.export_bin).pack(side="left", padx=3)

        self.crc_badge = tk.Label(
            bar, text="No dataset loaded", bg=BG_CARD, fg=FG_DIM,
            font=("Consolas", 9, "bold"), padx=10, pady=4,
            relief="solid", bd=1,
        )
        self.crc_badge.pack(side="right")

    def _build_tabs(self):
        self.nb = ttk.Notebook(self)
        self.nb.pack(fill="both", expand=True, padx=8, pady=6)
        self.nb.bind("<<NotebookTabChanged>>", lambda _e: self.refresh_status_and_hex())

        self.tab_72c0 = tk.Frame(self.nb, bg=BG)
        self.tab_72c2 = tk.Frame(self.nb, bg=BG)
        self.tab_72c1 = tk.Frame(self.nb, bg=BG)
        self.tab_72c3 = tk.Frame(self.nb, bg=BG)
        self.tab_hex = tk.Frame(self.nb, bg=BG)

        self.nb.add(self.tab_72c0, text=" 72C0 · ALM Color ")
        self.nb.add(self.tab_72c2, text=" 72C2 · ALM Slave (80 Nodes) ")
        self.nb.add(self.tab_72c1, text=" 72C1 · ALM Szenario ")
        self.nb.add(self.tab_72c3, text=" 72C3 · ALM Dynamik ")
        self.nb.add(self.tab_hex, text=" Hex & Diff Inspector ")

        self._render_empty(self.tab_72c0, "72C0 (ALM Color)")
        self._render_empty(self.tab_72c2, "72C2 (ALM Slave)")
        self._render_empty(self.tab_72c1, "72C1 (ALM Szenario)")
        self._render_empty(self.tab_72c3, "72C3 (ALM Dynamik)")
        self._build_hex_tab()

    def _build_statusbar(self):
        sb = tk.Frame(self, bg=BG_PANEL, padx=12, pady=5, highlightbackground=BORDER, highlightthickness=1)
        sb.pack(fill="x", side="bottom")
        self.status_lbl = tk.Label(
            sb, text="Ready — Open a vehicle ZIP or any DA_009_72C0..72C3 XML file to begin.",
            bg=BG_PANEL, fg=FG_DIM, font=("Segoe UI", 9), anchor="w",
        )
        self.status_lbl.pack(side="left", fill="x", expand=True)

    def _render_empty(self, frame: tk.Frame, name: str):
        for w in frame.winfo_children():
            w.destroy()
        tk.Label(
            frame, text=f"No {name} dataset loaded.\nUse 'Open Vehicle ZIP' or 'Open Single XML' above.",
            bg=BG, fg=FG_DIM, font=("Segoe UI", 11),
        ).pack(expand=True)

    # -----------------------------------------------------------------------
    # File Operations
    # -----------------------------------------------------------------------

    def open_zip(self):
        path = filedialog.askopenfilename(
            title="Open Vehicle BCM1 Dataset ZIP",
            initialdir=DEFAULT_BROWSE_DIR,
            filetypes=[("ZIP archives", "*.zip"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            loaded = load_zip_archive(path)
            if not loaded:
                messagebox.showwarning("No ALM Datasets", "No 72C0..72C3 XML datasets found inside this ZIP.")
                return
            if "72C0" in loaded:
                self.ds72c0 = loaded["72C0"]
            if "72C1" in loaded:
                self.ds72c1 = loaded["72C1"]
            if "72C2" in loaded:
                self.ds72c2 = loaded["72C2"]
            if "72C3" in loaded:
                self.ds72c3 = loaded["72C3"]
            self._rebuild_all_tabs()
            self.status_lbl.config(text=f"Loaded {', '.join(sorted(loaded.keys()))} from {os.path.basename(path)}")
        except Exception as e:
            messagebox.showerror("Load Error", str(e))

    def open_xml(self):
        path = filedialog.askopenfilename(
            title="Open 72C0 / 72C1 / 72C2 / 72C3 XML Dataset",
            initialdir=DEFAULT_BROWSE_DIR,
            filetypes=[("VAG Dataset XML", "*.xml"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            adr, ds = load_xml_file(path)
            if adr == "72C0":
                self.ds72c0 = ds
                self.nb.select(self.tab_72c0)
            elif adr == "72C1":
                self.ds72c1 = ds
                self.nb.select(self.tab_72c1)
            elif adr == "72C2":
                self.ds72c2 = ds
                self.nb.select(self.tab_72c2)
            elif adr == "72C3":
                self.ds72c3 = ds
                self.nb.select(self.tab_72c3)
            self._rebuild_all_tabs()
            self.status_lbl.config(text=f"Loaded {adr} from {os.path.basename(path)}")
        except Exception as e:
            messagebox.showerror("Load Error", str(e))

    def _active_dataset(self):
        idx = self.nb.index(self.nb.select())
        if idx == 0:
            return "72C0", self.ds72c0
        if idx == 1:
            return "72C2", self.ds72c2
        if idx == 2:
            return "72C1", self.ds72c1
        if idx == 3:
            return "72C3", self.ds72c3
        adr = self.hex_adr_var.get()
        return adr, getattr(self, f"ds{adr.lower()}", None)

    def save_current_xml(self):
        adr, ds = self._active_dataset()
        if ds is None:
            messagebox.showinfo("Nothing to Save", f"No {adr} dataset is currently loaded.")
            return
        default_name = ds.meta.cnt_datei or f"DA_009_{adr}_CUSTOM.xml"
        path = filedialog.asksaveasfilename(
            title=f"Save {adr} XML Dataset",
            initialfile=default_name,
            defaultextension=".xml",
            filetypes=[("VAG Dataset XML", "*.xml")],
        )
        if not path:
            return
        try:
            xml_text = format_xml_output(adr, ds)
            with open(path, "w", encoding="utf-8") as f:
                f.write(xml_text)
            raw = ds.to_bytes()
            crc = int.from_bytes(raw[-4:], "big")
            self.status_lbl.config(text=f"Saved {adr} XML -> {path} (CRC32: 0x{crc:08X})")
            messagebox.showinfo("Saved", f"Saved {adr} ({len(raw)} bytes)\nCRC-32: 0x{crc:08X}")
        except Exception as e:
            messagebox.showerror("Save Error", str(e))

    def save_all_xml(self):
        loaded = [
            (a, d)
            for a, d in (("72C0", self.ds72c0), ("72C1", self.ds72c1), ("72C2", self.ds72c2), ("72C3", self.ds72c3))
            if d is not None
        ]
        if not loaded:
            messagebox.showinfo("Nothing to Save", "No datasets are currently loaded.")
            return
        folder = filedialog.askdirectory(title="Select Output Folder for All Loaded XMLs")
        if not folder:
            return
        saved = []
        for adr, ds in loaded:
            fn = ds.meta.cnt_datei or f"DA_009_{adr}_CUSTOM.xml"
            outp = os.path.join(folder, fn)
            with open(outp, "w", encoding="utf-8") as f:
                f.write(format_xml_output(adr, ds))
            saved.append(fn)
        self.status_lbl.config(text=f"Saved {len(saved)} XML files to {folder}")
        messagebox.showinfo("Saved All", "Saved:\n" + "\n".join(saved))

    def export_bin(self):
        adr, ds = self._active_dataset()
        if ds is None:
            messagebox.showinfo("Nothing to Export", f"No {adr} dataset is currently loaded.")
            return
        if is_8w0_dataset(ds):
            init_fn = f"{adr}_8W0_{ds.version.strip()}.bin"
        else:
            init_fn = f"{adr}_{ds.header.version}_{ds.header.variant_id.strip()}.bin"
        path = filedialog.asksaveasfilename(
            title=f"Export Raw {adr} Binary",
            initialfile=init_fn,
            defaultextension=".bin",
            filetypes=[("Binary file", "*.bin")],
        )
        if not path:
            return
        raw = ds.to_bytes()
        with open(path, "wb") as f:
            f.write(raw)
        self.status_lbl.config(text=f"Exported raw {adr} binary ({len(raw)} B) -> {path}")

    def _rebuild_all_tabs(self):
        if self.ds72c0:
            self._build_72c0_tab()
        if self.ds72c2:
            self._build_72c2_tab()
        if self.ds72c1:
            self._build_72c1_tab()
        if self.ds72c3:
            self._build_72c3_tab()
        self.refresh_status_and_hex()

    def refresh_status_and_hex(self):
        adr, ds = self._active_dataset()
        if ds is None:
            self.crc_badge.config(text=f"{adr}: not loaded", fg=FG_DIM, bg=BG_CARD)
        else:
            raw = ds.to_bytes()
            _, calc, _ = verify_crc32(raw)
            modified = (raw != ds.original_raw)
            plat = "8W0" if is_8w0_dataset(ds) else "4N0/8W1"
            tag = "MODIFIED" if modified else "CLEAN"
            col = AMBER if modified else GREEN
            bg_c = DIFF_BG if modified else "#dcfce7"
            self.crc_badge.config(
                text=f"{adr} [{plat}] | {len(raw)} B | CRC32: 0x{calc:08X} | {tag}",
                fg=col, bg=bg_c,
            )
        self._update_hex_view()

    def _color_hex(self, idx: int) -> str:
        if idx in (0xF0, 0xF1):
            return ACCENT
        if self.ds72c0 and not is_8w0_dataset(self.ds72c0) and 0 <= idx < 50:
            return self.ds72c0.get_lin_rgb_hex(idx)
        return "#cbd5e1"

    def _contrast_hex(self, hex_col: str) -> str:
        try:
            h = hex_col.lstrip("#")
            if len(h) == 6:
                return self._contrast(int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
        except Exception:
            pass
        return "#0f172a"

    def _open_color_idx_picker(self, title: str, var: tk.StringVar):
        try:
            cur = int(var.get().strip(), 16) & 0xFF
        except Exception:
            cur = 0x23
        ds72c0_4n0 = self.ds72c0 if (self.ds72c0 and not is_8w0_dataset(self.ds72c0)) else None
        ColorIndexPickerDialog(self, title, cur, ds72c0_4n0, lambda idx: var.set(f"{idx:02X}"))

    # -----------------------------------------------------------------------
    # Header Widget Helper
    # -----------------------------------------------------------------------

    def _build_header_bar(self, parent, ds, sizeof_hint: str, extra_widgets_fn=None):
        bar = tk.LabelFrame(
            parent,
            text=f"Dataset Header (0x000..0x013) — FW v3.x Active Payload: {sizeof_hint}",
            bg=BG_PANEL, fg=ACCENT, font=("Segoe UI", 9, "bold"), padx=8, pady=6,
        )
        bar.pack(fill="x", padx=8, pady=6)

        tk.Label(bar, text="Version (4B):", bg=BG_PANEL, fg=FG).pack(side="left")
        v_var = tk.StringVar(value=ds.header.version)
        self._entry(bar, textvariable=v_var, width=6, font=("Consolas", 9)).pack(side="left", padx=(4, 12))

        tk.Label(bar, text="Variant ID (12B):", bg=BG_PANEL, fg=FG).pack(side="left")
        id_var = tk.StringVar(value=ds.header.variant_id)
        self._entry(bar, textvariable=id_var, width=15, font=("Consolas", 9)).pack(side="left", padx=(4, 12))

        tk.Label(
            bar, text=f"Struct: {ds.header.struct_ver.hex(' ').upper()}",
            bg=BG_PANEL, fg=FG_DIM, font=("Consolas", 9),
        ).pack(side="left", padx=(0, 12))

        def commit_hdr(*_):
            ds.header.version = v_var.get()[:4]
            ds.header.variant_id = id_var.get()[:12]
            self.refresh_status_and_hex()

        v_var.trace_add("write", commit_hdr)
        id_var.trace_add("write", commit_hdr)

        if extra_widgets_fn:
            extra_widgets_fn(bar)

    # -----------------------------------------------------------------------
    # TAB 1: 72C0 — ALM Color
    # -----------------------------------------------------------------------

    def _build_72c0_tab(self):
        for w in self.tab_72c0.winfo_children():
            w.destroy()
        ds = self.ds72c0
        if is_8w0_dataset(ds):
            return self._build_72c0_8w0_tab()
        sf = ScrollableFrame(self.tab_72c0, bg=BG)
        sf.pack(fill="both", expand=True)

        def extra_hdr(bar):
            tk.Label(bar, text="User Colors (0x018):", bg=BG_PANEL, fg=FG).pack(side="left")
            uc = tk.IntVar(value=ds.user_color_count)
            self._spinbox(bar, from_=0, to=30, textvariable=uc, width=4).pack(side="left", padx=(4, 10))

            tk.Label(bar, text="FeatureMask (0x1A3 hex):", bg=BG_PANEL, fg=FG).pack(side="left")
            fm = tk.StringVar(value=f"{ds.feature_mask:02X}")
            self._entry(bar, textvariable=fm, width=4, font=("Consolas", 9)).pack(side="left", padx=(4, 10))

            def on_hdr_chg(*_):
                try:
                    ds.user_color_count = max(0, min(30, int(uc.get())))
                except Exception:
                    pass
                try:
                    ds.feature_mask = int(fm.get(), 16) & 0xFF
                except Exception:
                    pass
                self.refresh_status_and_hex()

            uc.trace_add("write", on_hdr_chg)
            fm.trace_add("write", on_hdr_chg)

            ttk.Button(bar, text="Copy All HMI → LIN", command=self._copy_hmi_to_lin).pack(side="right", padx=4)

        self._build_header_bar(sf.inner, ds, "0x1AE (430 B) @ RAM 0x40045CC0", extra_hdr)

        # 30-Color HMI + LIN Palette Grid
        pal_box = tk.LabelFrame(
            sf.inner,
            text="30 User-Selectable Colors — Click HMI or LIN Swatch to Edit RGB (0x01A..0x0CD)",
            bg=BG_PANEL, fg=ACCENT, font=("Segoe UI", 9, "bold"), padx=8, pady=8,
        )
        pal_box.pack(fill="x", padx=8, pady=4)

        for i in range(30):
            r = i // 6
            c = i % 6
            card = tk.Frame(pal_box, bg=BG_CARD, padx=6, pady=4, highlightbackground=BORDER, highlightthickness=1)
            card.grid(row=r, column=c, padx=4, pady=4, sticky="ew")
            pal_box.columnconfigure(c, weight=1)

            hr, hg, hb = ds.hmi_colors[i]
            lr, lg, lb = ds.lin_colors[i]
            h_hex = f"#{hr:02X}{hg:02X}{hb:02X}"
            l_hex = f"#{lr:02X}{lg:02X}{lb:02X}"

            tk.Label(card, text=f"#{i:02d} (0x{i:02X})", bg=BG_CARD, fg=FG, font=("Consolas", 8, "bold")).pack(anchor="w")
            row = tk.Frame(card, bg=BG_CARD)
            row.pack(fill="x", pady=2)

            tk.Button(
                row, text=f"HMI\n{h_hex[1:]}", bg=h_hex, fg=self._contrast(hr, hg, hb),
                font=("Consolas", 7, "bold"), width=8, relief="solid", bd=1,
                command=lambda idx=i: self._pick_72c0_color("hmi", idx),
            ).pack(side="left", padx=(0, 3), expand=True, fill="x")

            tk.Button(
                row, text=f"LIN\n{l_hex[1:]}", bg=l_hex, fg=self._contrast(lr, lg, lb),
                font=("Consolas", 7, "bold"), width=8, relief="solid", bd=1,
                command=lambda idx=i: self._pick_72c0_color("lin", idx),
            ).pack(side="left", expand=True, fill="x")

        # 20 System / Event LIN Colors (idx 30..49)
        sys_box = tk.LabelFrame(
            sf.inner,
            text="20 System / Event LIN Colors (Indices 30..49 / 0x1E..0x31, Offsets 0x0CE..0x109; 0x23 = Audi Welcome White)",
            bg=BG_PANEL, fg=ACCENT, font=("Segoe UI", 9, "bold"), padx=8, pady=6,
        )
        sys_box.pack(fill="x", padx=8, pady=4)

        for j in range(20):
            idx = 30 + j
            r = j // 10
            c = j % 10
            lr, lg, lb = ds.lin_colors[idx]
            l_hex = f"#{lr:02X}{lg:02X}{lb:02X}"
            tk.Button(
                sys_box, text=f"0x{idx:02X}\n{l_hex[1:]}",
                bg=l_hex, fg=self._contrast(lr, lg, lb),
                font=("Consolas", 7, "bold"), width=9, relief="solid", bd=1,
                command=lambda ii=idx: self._pick_72c0_color("lin", ii),
            ).grid(row=r, column=c, padx=3, pady=3, sticky="ew")
            sys_box.columnconfigure(c, weight=1)

        # 8 Ambient Profiles + 8x9 Brightness Matrix
        prof_box = tk.LabelFrame(
            sf.inner,
            text="8 Ambient Profiles — Default Colors (0x140..0x14F) & 9-Group Brightness % (0x152..0x199)",
            bg=BG_PANEL, fg=ACCENT, font=("Segoe UI", 9, "bold"), padx=8, pady=6,
        )
        prof_box.pack(fill="x", padx=8, pady=4)

        headers = ["Profile", "Enabled ID", "Surf Idx", "Cont Idx"] + [f"Bri G{g}" for g in range(9)]
        for ci, h in enumerate(headers):
            tk.Label(prof_box, text=h, bg=BG_PANEL, fg=FG_DIM, font=("Segoe UI", 8, "bold")).grid(row=0, column=ci, padx=3, pady=2)

        for p in range(8):
            tk.Label(prof_box, text=f"Profile {p}", bg=BG_PANEL, fg=FG, font=("Consolas", 9)).grid(row=p+1, column=0, padx=4, pady=2)

            en_var = tk.StringVar(value=f"{ds.enabled_profiles[p]:02X}")
            self._entry(prof_box, textvariable=en_var, width=4, font=("Consolas", 9)).grid(row=p+1, column=1, padx=2)

            c1, c2 = ds.profile_colors[p]
            c1_var = tk.StringVar(value=f"{c1:02X}")
            c2_var = tk.StringVar(value=f"{c2:02X}")

            f1 = tk.Frame(prof_box, bg=BG_PANEL)
            f1.grid(row=p+1, column=2, padx=2)
            sw1 = tk.Label(f1, text="  ", bg=self._color_hex(c1), width=2, relief="solid", bd=1)
            sw1.pack(side="left", padx=(0, 3))
            self._entry(f1, textvariable=c1_var, width=4, font=("Consolas", 9)).pack(side="left")

            f2 = tk.Frame(prof_box, bg=BG_PANEL)
            f2.grid(row=p+1, column=3, padx=2)
            sw2 = tk.Label(f2, text="  ", bg=self._color_hex(c2), width=2, relief="solid", bd=1)
            sw2.pack(side="left", padx=(0, 3))
            self._entry(f2, textvariable=c2_var, width=4, font=("Consolas", 9)).pack(side="left")

            def make_prof_cb(pi, ev, v1, v2, s1, s2):
                def _cb(*_):
                    try:
                        ds.enabled_profiles[pi] = int(ev.get(), 16) & 0xFF
                    except Exception:
                        pass
                    try:
                        n1 = int(v1.get(), 16) & 0xFF
                        n2 = int(v2.get(), 16) & 0xFF
                        ds.profile_colors[pi] = (n1, n2)
                        s1.config(bg=self._color_hex(n1))
                        s2.config(bg=self._color_hex(n2))
                    except Exception:
                        pass
                    self.refresh_status_and_hex()
                return _cb

            cb = make_prof_cb(p, en_var, c1_var, c2_var, sw1, sw2)
            en_var.trace_add("write", cb)
            c1_var.trace_add("write", cb)
            c2_var.trace_add("write", cb)

            for g in range(9):
                bv = tk.IntVar(value=ds.profile_brightness[p][g])
                self._spinbox(prof_box, from_=0, to=100, textvariable=bv, width=4).grid(row=p+1, column=4+g, padx=2, pady=2)

                def make_bri_cb(pi, gi, var):
                    def _b(*_):
                        try:
                            ds.profile_brightness[pi][gi] = max(0, min(100, int(var.get())))
                            self.refresh_status_and_hex()
                        except Exception:
                            pass
                    return _b
                bv.trace_add("write", make_bri_cb(p, g, bv))

        # BAP Zone IDs & Dimming Params
        misc = tk.LabelFrame(
            sf.inner,
            text="BAP Zone IDs (0x10A..0x111) & Dimming Params (0x1A4..0x1AD)  [Note: 0x112..0x135 unused in Audi v3.x SW 0810]",
            bg=BG_PANEL, fg=ACCENT, font=("Segoe UI", 9, "bold"), padx=8, pady=6,
        )
        misc.pack(fill="x", padx=8, pady=4)

        tk.Label(misc, text="BAP Zones (8 hex bytes):", bg=BG_PANEL, fg=FG).pack(side="left")
        bap_var = tk.StringVar(value=" ".join(f"{b:02X}" for b in ds.bap_zone_ids))
        self._entry(misc, textvariable=bap_var, width=26, font=("Consolas", 9)).pack(side="left", padx=(4, 16))

        tk.Label(misc, text="Dimming Params (10 hex bytes):", bg=BG_PANEL, fg=FG).pack(side="left")
        dim_var = tk.StringVar(value=" ".join(f"{b:02X}" for b in ds.dimming_params))
        self._entry(misc, textvariable=dim_var, width=32, font=("Consolas", 9)).pack(side="left", padx=4)

        def on_misc(*_):
            try:
                bs = bytes.fromhex(bap_var.get())
                if len(bs) == 8:
                    ds.bap_zone_ids = list(bs)
            except Exception:
                pass
            try:
                bs = bytes.fromhex(dim_var.get())
                if len(bs) == 10:
                    ds.dimming_params = list(bs)
            except Exception:
                pass
            self.refresh_status_and_hex()

        bap_var.trace_add("write", on_misc)
        dim_var.trace_add("write", on_misc)

    def _pick_72c0_color(self, table: str, idx: int):
        ds = self.ds72c0
        if not ds:
            return
        cur = ds.hmi_colors[idx] if table == "hmi" else ds.lin_colors[idx]
        init_hex = f"#{cur[0]:02X}{cur[1]:02X}{cur[2]:02X}"
        rgb, _ = colorchooser.askcolor(color=init_hex, title=f"Select {table.upper()} Color #{idx} (0x{idx:02X})")
        if rgb is None:
            return
        r, g, b = int(round(rgb[0])), int(round(rgb[1])), int(round(rgb[2]))
        if table == "hmi":
            ds.hmi_colors[idx] = (r, g, b)
        else:
            ds.lin_colors[idx] = (r, g, b)
        self._build_72c0_tab()
        if table != "hmi":
            if self.ds72c1 and not is_8w0_dataset(self.ds72c1):
                self._build_72c1_tab()
            if self.ds72c3 and not is_8w0_dataset(self.ds72c3):
                self._build_72c3_tab()
        self.refresh_status_and_hex()

    def _copy_hmi_to_lin(self):
        if not self.ds72c0:
            return
        for i in range(30):
            self.ds72c0.lin_colors[i] = self.ds72c0.hmi_colors[i]
        self._build_72c0_tab()
        if self.ds72c1 and not is_8w0_dataset(self.ds72c1):
            self._build_72c1_tab()
        if self.ds72c3 and not is_8w0_dataset(self.ds72c3):
            self._build_72c3_tab()
        self.refresh_status_and_hex()

    @staticmethod
    def _contrast(r: int, g: int, b: int) -> str:
        lum = 0.299 * r + 0.587 * g + 0.114 * b
        return "#0f172a" if lum > 140 else "#ffffff"

    # -----------------------------------------------------------------------
    # TAB 2: 72C2 — ALM Slave (80-Node Topology · 3x16 LIN Bus Split)
    # -----------------------------------------------------------------------

    def _build_72c2_tab(self, filter_mode: Optional[str] = None):
        if is_8w0_dataset(self.ds72c2):
            return self._build_72c2_8w0_tab(filter_mode)
        if filter_mode is None:
            filter_mode = getattr(self, "_72c2_filter_mode", "all")
        else:
            self._72c2_filter_mode = filter_mode

        for w in self.tab_72c2.winfo_children():
            w.destroy()
        ds = self.ds72c2
        self._72c2_bank_count_labels = {}

        def extra_hdr(bar):
            ext_v = tk.BooleanVar(value=bool(ds.slave_flags[0]))
            tk.Checkbutton(
                bar, text="Ext. Slave Handler Enable (0x0A0, Audi/Bentley=ON)",
                variable=ext_v, bg=BG_PANEL, fg=FG, selectcolor=BG_INPUT,
                activebackground=BG_PANEL, activeforeground=ACCENT,
                command=lambda: self._set_72c2_ext(ext_v.get()),
            ).pack(side="left", padx=6)

            ttk.Button(bar, text="Silence All LIN DTCs (FF)", command=self._silence_all_4n0_lin_dtcs).pack(side="right", padx=3)
            ttk.Button(bar, text="Swap LHD ↔ RHD Zones", command=self._swap_72c2_lhd_rhd).pack(side="right", padx=3)
            ttk.Button(bar, text="Toggle Door Pockets (90/92)", command=self._toggle_72c2_pockets).pack(side="right", padx=3)

        self._build_header_bar(self.tab_72c2, ds, "0x0D4 (212 B) @ RAM 0x400460BC", extra_hdr)

        # Live 3x16 LIN Bus + Output Bank Summary Bar
        sbar = tk.Frame(
            self.tab_72c2, bg=BG_PANEL, padx=8, pady=5,
            highlightbackground=BORDER, highlightthickness=1,
        )
        sbar.pack(fill="x", padx=8, pady=(0, 4))
        tk.Label(
            sbar, text="3×16 LIN & Output Banks:", bg=BG_PANEL, fg=FG,
            font=("Segoe UI", 8, "bold"),
        ).pack(side="left", padx=(0, 8))

        for key, tag, _title, _detail, start, end, bg_b, fg_b in NODE_BANKS:
            active_cnt = sum(1 for i in range(start, end) if ds.active_nodes[i])
            if start < 48:
                addr_cnt = sum(1 for i in range(start, end) if ds.lin_addresses[i] != 0xFF)
                txt = f"{tag} ({start:02d}..{end-1:02d}): {active_cnt}/16 active ({addr_cnt} addr)"
            else:
                txt = f"{tag} ({start:02d}..{end-1:02d}): {active_cnt}/16 active"
            lbl = tk.Label(
                sbar, text=txt, bg=bg_b, fg=fg_b,
                font=("Consolas", 8, "bold"), padx=7, pady=2, relief="solid", bd=1,
            )
            lbl.pack(side="left", padx=3)
            self._72c2_bank_count_labels[key] = (lbl, tag, start, end)

        # Filter Bar (explicitly separates LIN Bus 1, LIN Bus 2, LIN Bus 3)
        fbar = tk.Frame(self.tab_72c2, bg=BG, padx=8, pady=2)
        fbar.pack(fill="x")
        tk.Label(fbar, text="Filter:", bg=BG, fg=FG_DIM).pack(side="left", padx=(0, 6))
        filter_opts = [
            ("all", "All 80 Nodes"),
            ("active", "Active Only"),
            ("lin1", "LIN Bus 1 (0..15)"),
            ("lin2", "LIN Bus 2 (16..31)"),
            ("lin3", "LIN Bus 3 (32..47)"),
            ("pwm", "PWM/Direct (48..63)"),
            ("int", "Interior/White (64..79)"),
        ]
        for key, label in filter_opts:
            is_cur = (key == filter_mode)
            btn_txt = f"● {label}" if is_cur else label
            ttk.Button(fbar, text=btn_txt, command=lambda k=key: self._build_72c2_tab(k)).pack(side="left", padx=2)

        sf = ScrollableFrame(self.tab_72c2, bg=BG)
        sf.pack(fill="both", expand=True, padx=8, pady=4)

        cols = [
            "Node", "Bus / Slot", "Type / Hint", "Active (Out)",
            "Color Group (0..6)", "LIN Diag/DTC ID (FF=Off)", "Zone FL", "Zone FR", "Zone RL", "Zone RR", "DTC Action",
        ]
        for ci, h in enumerate(cols):
            tk.Label(
                sf.inner, text=h, bg=BG_CARD, fg=ACCENT,
                font=("Segoe UI", 8, "bold"), padx=6, pady=5,
            ).grid(row=0, column=ci, sticky="ew")

        row_i = 1
        for key, tag, bank_title, bank_detail, start, end, bg_b, fg_b in NODE_BANKS:
            if filter_mode not in ("all", "active") and filter_mode != key:
                continue

            nodes_in_bank = [
                n for n in range(start, end)
                if filter_mode != "active" or ds.active_nodes[n]
            ]
            if not nodes_in_bank:
                continue

            # Section Banner Row for this Bank
            banner_txt = f"  {bank_title}  —  {bank_detail}"
            tk.Label(
                sf.inner, text=banner_txt, bg=bg_b, fg=fg_b,
                font=("Segoe UI", 8, "bold"), anchor="w", padx=6, pady=4,
            ).grid(row=row_i, column=0, columnspan=len(cols), sticky="ew", pady=(6, 2))
            row_i += 1

            for n in nodes_in_bank:
                slot = n - start
                bg_r = BG_PANEL if row_i % 2 == 1 else BG_CARD

                tk.Label(
                    sf.inner, text=f"N{n:02d}", bg=bg_r, fg=FG,
                    font=("Consolas", 9, "bold"), width=5,
                ).grid(row=row_i, column=0, sticky="ew", pady=1)

                slot_txt = f"{tag} [{slot:02d}]"
                tk.Label(
                    sf.inner, text=slot_txt, bg=bg_b, fg=fg_b,
                    font=("Consolas", 8, "bold"), width=10, padx=4,
                ).grid(row=row_i, column=1, padx=3, pady=1)

                if n < 48:
                    la = ds.lin_addresses[n]
                    hint = LIN_ADDR_HINTS.get(la, "LIN RGB Slave" if la != 0xFF else "Unused LIN Slot")
                elif n < 64:
                    hint = f"PWM / Direct Output #{slot}"
                else:
                    hint = f"Interior / White Channel #{slot}"
                hint_lbl = tk.Label(
                    sf.inner, text=hint, bg=bg_r, fg=FG_DIM,
                    font=("Segoe UI", 8), anchor="w", width=28,
                )
                hint_lbl.grid(row=row_i, column=2, sticky="ew", padx=4)

                act_v = tk.BooleanVar(value=ds.active_nodes[n])
                tk.Checkbutton(
                    sf.inner, variable=act_v, bg=bg_r, selectcolor=BG_INPUT, activebackground=bg_r,
                    command=lambda ni=n, v=act_v: self._set_72c2_active(ni, v.get()),
                ).grid(row=row_i, column=3)

                grp_v = tk.IntVar(value=ds.node_groups[n])
                self._spinbox(sf.inner, from_=0, to=6, textvariable=grp_v, width=4).grid(row=row_i, column=4, padx=4)
                grp_v.trace_add("write", lambda *_, ni=n, v=grp_v: self._set_72c2_group(ni, v))

                if n < 48:
                    lin_v = tk.StringVar(value=f"{ds.lin_addresses[n]:02X}")
                    self._entry(sf.inner, textvariable=lin_v, width=5, font=("Consolas", 9)).grid(row=row_i, column=5, padx=4)
                    lin_v.trace_add("write", lambda *_, ni=n, v=lin_v, hl=hint_lbl: self._set_72c2_lin(ni, v, hl))
                else:
                    tk.Label(sf.inner, text="—", bg=bg_r, fg=FG_DIM).grid(row=row_i, column=5)

                for z in range(1, 5):
                    zv = tk.BooleanVar(value=ds.zone_masks[z][n])
                    tk.Checkbutton(
                        sf.inner, variable=zv, bg=bg_r, selectcolor=BG_INPUT, activebackground=bg_r,
                        command=lambda ni=n, zi=z, v=zv: self._set_72c2_zone(zi, ni, v.get()),
                    ).grid(row=row_i, column=5 + z)

                if n < 48:
                    if ds.lin_addresses[n] != 0xFF:
                        ttk.Button(
                            sf.inner, text="Silence DTC (FF)",
                            command=lambda ni=n: self._silence_4n0_node_dtc(ni),
                        ).grid(row=row_i, column=10, padx=3, pady=1)
                    elif ds.active_nodes[n]:
                        tk.Label(
                            sf.inner, text="DTC Silenced", bg=bg_r, fg="#7e22ce",
                            font=("Segoe UI", 8, "bold"),
                        ).grid(row=row_i, column=10, padx=3)
                    else:
                        tk.Label(sf.inner, text="", bg=bg_r).grid(row=row_i, column=10)
                else:
                    tk.Label(sf.inner, text="—", bg=bg_r, fg=FG_DIM).grid(row=row_i, column=10)

                row_i += 1

    def _silence_4n0_node_dtc(self, node: int):
        if 0 <= node < 48:
            self.ds72c2.lin_addresses[node] = 0xFF
            self._build_72c2_tab()
            self.refresh_status_and_hex()
            self.status_lbl.config(
                text=f"Silenced DTC on 4N0/8W1 node N{node:02d} (set 0x{0x0A2+node:03X} = FF; LED output remains active)."
            )

    def _silence_all_4n0_lin_dtcs(self):
        ds = self.ds72c2
        changed = sum(1 for i in range(48) if ds.lin_addresses[i] != 0xFF)
        ds.lin_addresses = [0xFF] * 48
        self._build_72c2_tab()
        self.refresh_status_and_hex()
        self.status_lbl.config(
            text=f"Silenced all 4N0/8W1 LIN DTCs ({changed} slots set to FF in 0x0A2..0x0D1; LED outputs untouched)."
        )

    def _refresh_72c2_bank_counts(self):
        ds = self.ds72c2
        if not ds or not hasattr(self, "_72c2_bank_count_labels"):
            return
        for _key, (lbl, tag, start, end) in self._72c2_bank_count_labels.items():
            active_cnt = sum(1 for i in range(start, end) if ds.active_nodes[i])
            if start < 48:
                addr_cnt = sum(1 for i in range(start, end) if ds.lin_addresses[i] != 0xFF)
                txt = f"{tag} ({start:02d}..{end-1:02d}): {active_cnt}/16 active ({addr_cnt} addr)"
            else:
                txt = f"{tag} ({start:02d}..{end-1:02d}): {active_cnt}/16 active"
            lbl.config(text=txt)

    def _set_72c2_ext(self, val: bool):
        self.ds72c2.slave_flags = bytes((1 if val else 0, self.ds72c2.slave_flags[1]))
        self.refresh_status_and_hex()

    def _set_72c2_active(self, node: int, val: bool):
        self.ds72c2.active_nodes[node] = val
        self._refresh_72c2_bank_counts()
        self.refresh_status_and_hex()

    def _set_72c2_group(self, node: int, var: tk.IntVar):
        try:
            self.ds72c2.node_groups[node] = max(0, min(255, int(var.get())))
            self.refresh_status_and_hex()
        except Exception:
            pass

    def _set_72c2_lin(self, node: int, var: tk.StringVar, hint_lbl=None):
        try:
            val = int(var.get(), 16) & 0xFF
            self.ds72c2.lin_addresses[node] = val
            if hint_lbl is not None:
                hint = LIN_ADDR_HINTS.get(val, "LIN RGB Slave" if val != 0xFF else "Unused LIN Slot")
                hint_lbl.config(text=hint)
            self._refresh_72c2_bank_counts()
            self.refresh_status_and_hex()
        except Exception:
            pass

    def _set_72c2_zone(self, zone: int, node: int, val: bool):
        self.ds72c2.zone_masks[zone][node] = val
        self.refresh_status_and_hex()

    def _swap_72c2_lhd_rhd(self):
        ds = self.ds72c2
        ds.zone_masks[1], ds.zone_masks[2] = ds.zone_masks[2][:], ds.zone_masks[1][:]
        self._build_72c2_tab()
        self.refresh_status_and_hex()

    def _toggle_72c2_pockets(self):
        ds = self.ds72c2
        for n in range(48):
            if ds.lin_addresses[n] in (0x90, 0x92):
                ds.active_nodes[n] = not ds.active_nodes[n]
        self._build_72c2_tab()
        self.refresh_status_and_hex()

    # -----------------------------------------------------------------------
    # TAB 3: 72C1 — ALM Szenario
    # -----------------------------------------------------------------------

    def _build_72c1_tab(self):
        for w in self.tab_72c1.winfo_children():
            w.destroy()
        ds = self.ds72c1
        if is_8w0_dataset(ds):
            return self._build_72c1_8w0_tab()
        sf = ScrollableFrame(self.tab_72c1, bg=BG)
        sf.pack(fill="both", expand=True)

        self._build_header_bar(sf.inner, ds, "0x16C (364 B) @ RAM 0x40045E70")

        # Firmware-Verified Global Timing (0x014..0x01D)
        gt_box = tk.LabelFrame(
            sf.inner,
            text="Global Scenario Timing & Debounce (0x014..0x01D — Firmware-Verified)",
            bg=BG_PANEL, fg=ACCENT, font=("Segoe UI", 9, "bold"), padx=8, pady=6,
        )
        gt_box.pack(fill="x", padx=8, pady=4)

        gt = bytearray(ds.global_timing)
        c_a = tk.IntVar(value=gt[0])
        c_b = tk.IntVar(value=gt[1])
        deb_en = tk.BooleanVar(value=bool(gt[2]))
        t1_val = tk.IntVar(value=int.from_bytes(gt[4:6], "big"))
        deb_ms = tk.IntVar(value=int.from_bytes(gt[6:8], "big"))
        tout_s = tk.IntVar(value=gt[8])
        gflags = tk.StringVar(value=f"{gt[9]:02X}")

        def commit_gt(*_):
            try:
                buf = bytearray(ds.global_timing)
                buf[0] = max(0, min(255, int(c_a.get())))
                buf[1] = max(0, min(255, int(c_b.get())))
                buf[2] = 1 if deb_en.get() else 0
                buf[4:6] = max(0, min(65535, int(t1_val.get()))).to_bytes(2, "big")
                buf[6:8] = max(0, min(65535, int(deb_ms.get()))).to_bytes(2, "big")
                buf[8] = max(0, min(255, int(tout_s.get())))
                buf[9] = int(gflags.get(), 16) & 0xFF
                ds.global_timing = bytes(buf)
                self.refresh_status_and_hex()
            except Exception:
                pass

        tk.Label(gt_box, text="Curve A (0x014):", bg=BG_PANEL, fg=FG).pack(side="left")
        self._spinbox(gt_box, from_=0, to=255, textvariable=c_a, width=4).pack(side="left", padx=(2, 10))

        tk.Label(gt_box, text="Curve B (0x015):", bg=BG_PANEL, fg=FG).pack(side="left")
        self._spinbox(gt_box, from_=0, to=255, textvariable=c_b, width=4).pack(side="left", padx=(2, 10))

        tk.Checkbutton(
            gt_box, text="Debounce Enable (0x016)", variable=deb_en,
            bg=BG_PANEL, fg=FG, selectcolor=BG_INPUT, activebackground=BG_PANEL,
            command=commit_gt,
        ).pack(side="left", padx=(0, 10))

        tk.Label(gt_box, text="Timer1 (0x018):", bg=BG_PANEL, fg=FG).pack(side="left")
        self._spinbox(gt_box, from_=0, to=65535, textvariable=t1_val, width=6).pack(side="left", padx=(2, 10))

        tk.Label(gt_box, text="Debounce Timer ms (0x01A, 10ms ticks):", bg=BG_PANEL, fg=FG).pack(side="left")
        self._spinbox(gt_box, from_=0, to=16380, increment=10, textvariable=deb_ms, width=6).pack(side="left", padx=(2, 10))

        tk.Label(gt_box, text="Timeout (0x01C):", bg=BG_PANEL, fg=FG).pack(side="left")
        self._spinbox(gt_box, from_=0, to=255, textvariable=tout_s, width=4).pack(side="left", padx=(2, 10))

        tk.Label(gt_box, text="Flags (0x01D hex):", bg=BG_PANEL, fg=FG).pack(side="left")
        self._entry(gt_box, textvariable=gflags, width=4, font=("Consolas", 9)).pack(side="left", padx=2)

        for v in (c_a, c_b, t1_val, deb_ms, tout_s, gflags):
            v.trace_add("write", commit_gt)

        # Exit Warning Masks (4 doors)
        ew_box = tk.LabelFrame(
            sf.inner, text="Exit Warning (Ausstiegswarnung) — 4 Door NodeMasks (0x01E..0x045)",
            bg=BG_PANEL, fg=ACCENT, font=("Segoe UI", 9, "bold"), padx=8, pady=6,
        )
        ew_box.pack(fill="x", padx=8, pady=4)
        for i, dname in enumerate(["Front Left (FL)", "Front Right (FR)", "Rear Left (RL)", "Rear Right (RR)"]):
            row = tk.Frame(ew_box, bg=BG_PANEL)
            row.pack(fill="x", pady=2)
            tk.Label(row, text=dname, bg=BG_PANEL, fg=FG, width=18, anchor="w").pack(side="left")
            tk.Label(
                row, text=nodemask_summary(ds.exit_warn_masks[i]),
                bg=BG_CARD, fg=FG_DIM, font=("Consolas", 8), width=48, anchor="w", padx=6, relief="solid", bd=1,
            ).pack(side="left", padx=4)
            ttk.Button(
                row, text="Edit Mask...",
                command=lambda ii=i, dn=dname: NodeMask80Dialog(
                    self, f"72C1 Exit Warning — {dn}", ds.exit_warn_masks[ii], self.ds72c2,
                    lambda m, idx=ii: self._update_72c1_exit(idx, m),
                ),
            ).pack(side="left", padx=4)

        # Scenario Blocks (Welcome, Leaving, Scen2, Scen3)
        for blk in (ds.welcome, ds.leaving, ds.scen2, ds.scen3):
            bframe = tk.LabelFrame(
                sf.inner, text=f"{blk.name} ({len(blk.steps)} step(s))",
                bg=BG_PANEL, fg=ACCENT, font=("Segoe UI", 9, "bold"), padx=8, pady=6,
            )
            bframe.pack(fill="x", padx=8, pady=4)

            hdr = tk.Frame(bframe, bg=BG_PANEL)
            hdr.pack(fill="x", pady=(0, 4))
            c1v = tk.StringVar(value=f"{blk.color_grp1:02X}")
            c2v = tk.StringVar(value=f"{blk.color_grp2:02X}")

            tk.Label(hdr, text="Color Grp1:", bg=BG_PANEL, fg=FG).pack(side="left")
            sw1 = tk.Button(
                hdr, text="Pick", bg=self._color_hex(blk.color_grp1),
                fg=self._contrast_hex(self._color_hex(blk.color_grp1)),
                font=("Consolas", 7, "bold"), width=5, relief="solid", bd=1, cursor="hand2",
                command=lambda v=c1v, bn=blk.name: self._open_color_idx_picker(f"72C1 {bn} — Color Grp1", v),
            )
            sw1.pack(side="left", padx=3)
            self._entry(hdr, textvariable=c1v, width=4, font=("Consolas", 9)).pack(side="left", padx=(0, 12))

            tk.Label(hdr, text="Color Grp2:", bg=BG_PANEL, fg=FG).pack(side="left")
            sw2 = tk.Button(
                hdr, text="Pick", bg=self._color_hex(blk.color_grp2),
                fg=self._contrast_hex(self._color_hex(blk.color_grp2)),
                font=("Consolas", 7, "bold"), width=5, relief="solid", bd=1, cursor="hand2",
                command=lambda v=c2v, bn=blk.name: self._open_color_idx_picker(f"72C1 {bn} — Color Grp2", v),
            )
            sw2.pack(side="left", padx=3)
            self._entry(hdr, textvariable=c2v, width=4, font=("Consolas", 9)).pack(side="left", padx=(0, 12))

            tk.Label(hdr, text="(Click color swatch to pick from 72C0 System/Event & User colors, or 0xF0/0xF1)", bg=BG_PANEL, fg=FG_DIM, font=("Segoe UI", 8)).pack(side="left")

            def make_blk_cb(b, v1, v2, s1, s2):
                def _cb(*_):
                    try:
                        b.color_grp1 = int(v1.get(), 16) & 0xFF
                        hx = self._color_hex(b.color_grp1)
                        s1.config(bg=hx, fg=self._contrast_hex(hx))
                    except Exception:
                        pass
                    try:
                        b.color_grp2 = int(v2.get(), 16) & 0xFF
                        hx = self._color_hex(b.color_grp2)
                        s2.config(bg=hx, fg=self._contrast_hex(hx))
                    except Exception:
                        pass
                    self.refresh_status_and_hex()
                return _cb

            cb = make_blk_cb(blk, c1v, c2v, sw1, sw2)
            c1v.trace_add("write", cb)
            c2v.trace_add("write", cb)

            for si, st in enumerate(blk.steps):
                srow = tk.Frame(bframe, bg=BG_CARD, padx=6, pady=4, highlightbackground=BORDER, highlightthickness=1)
                srow.pack(fill="x", pady=2)
                tk.Label(srow, text=f"Step {si}", bg=BG_CARD, fg=ACCENT, font=("Consolas", 9, "bold"), width=7).pack(side="left")

                ttk.Button(
                    srow, text=f"OFF: {nodemask_summary(st.off_mask, 4)}", width=26,
                    command=lambda s=st, bn=blk.name, idx=si: NodeMask80Dialog(
                        self, f"{bn} Step {idx} — OFF Mask", s.off_mask, self.ds72c2,
                        lambda m, st_ref=s: self._set_step_mask(st_ref, "off", m),
                    ),
                ).pack(side="left", padx=3)

                ttk.Button(
                    srow, text=f"ON: {nodemask_summary(st.on_mask, 4)}", width=26,
                    command=lambda s=st, bn=blk.name, idx=si: NodeMask80Dialog(
                        self, f"{bn} Step {idx} — ON Mask", s.on_mask, self.ds72c2,
                        lambda m, st_ref=s: self._set_step_mask(st_ref, "on", m),
                    ),
                ).pack(side="left", padx=3)

                for lbl_t, attr in [("Bri%:", "brightness"), ("Fade:", "fade_time"), ("Hold:", "hold_time")]:
                    tk.Label(srow, text=lbl_t, bg=BG_CARD, fg=FG_DIM).pack(side="left", padx=(8, 2))
                    sv = tk.IntVar(value=getattr(st, attr))
                    self._spinbox(srow, from_=0, to=255, textvariable=sv, width=4).pack(side="left")
                    sv.trace_add("write", lambda *_, s_ref=st, a=attr, v=sv: self._set_attr_int(s_ref, a, v))

        # Per-Door Open Scenario (0x116..0x16B)
        dbox = tk.LabelFrame(
            sf.inner, text="Per-Door Open Scenario (Tür offen, 0x116..0x16B)",
            bg=BG_PANEL, fg=ACCENT, font=("Segoe UI", 9, "bold"), padx=8, pady=6,
        )
        dbox.pack(fill="x", padx=8, pady=4)
        dh = tk.Frame(dbox, bg=BG_PANEL)
        dh.pack(fill="x", pady=(0, 4))

        dc1v = tk.StringVar(value=f"{ds.door_color_grp1:02X}")
        dc2v = tk.StringVar(value=f"{ds.door_color_grp2:02X}")

        tk.Label(dh, text="Color Grp1:", bg=BG_PANEL, fg=FG).pack(side="left")
        dsw1 = tk.Button(
            dh, text="Pick", bg=self._color_hex(ds.door_color_grp1),
            fg=self._contrast_hex(self._color_hex(ds.door_color_grp1)),
            font=("Consolas", 7, "bold"), width=5, relief="solid", bd=1, cursor="hand2",
            command=lambda v=dc1v: self._open_color_idx_picker("72C1 Per-Door Open — Color Grp1", v),
        )
        dsw1.pack(side="left", padx=3)
        self._entry(dh, textvariable=dc1v, width=4, font=("Consolas", 9)).pack(side="left", padx=(0, 10))

        tk.Label(dh, text="Color Grp2:", bg=BG_PANEL, fg=FG).pack(side="left")
        dsw2 = tk.Button(
            dh, text="Pick", bg=self._color_hex(ds.door_color_grp2),
            fg=self._contrast_hex(self._color_hex(ds.door_color_grp2)),
            font=("Consolas", 7, "bold"), width=5, relief="solid", bd=1, cursor="hand2",
            command=lambda v=dc2v: self._open_color_idx_picker("72C1 Per-Door Open — Color Grp2", v),
        )
        dsw2.pack(side="left", padx=3)
        self._entry(dh, textvariable=dc2v, width=4, font=("Consolas", 9)).pack(side="left", padx=(0, 12))

        def on_door_col(*_):
            try:
                ds.door_color_grp1 = int(dc1v.get(), 16) & 0xFF
                hx = self._color_hex(ds.door_color_grp1)
                dsw1.config(bg=hx, fg=self._contrast_hex(hx))
            except Exception:
                pass
            try:
                ds.door_color_grp2 = int(dc2v.get(), 16) & 0xFF
                hx = self._color_hex(ds.door_color_grp2)
                dsw2.config(bg=hx, fg=self._contrast_hex(hx))
            except Exception:
                pass
            self.refresh_status_and_hex()

        dc1v.trace_add("write", on_door_col)
        dc2v.trace_add("write", on_door_col)

        for lbl_t, attr in [("Bri%:", "door_brightness"), ("Fade:", "door_fade_time"), ("Hold:", "door_hold_time")]:
            tk.Label(dh, text=lbl_t, bg=BG_PANEL, fg=FG).pack(side="left", padx=(6, 2))
            dv = tk.IntVar(value=getattr(ds, attr))
            self._spinbox(dh, from_=0, to=255, textvariable=dv, width=4).pack(side="left")
            dv.trace_add("write", lambda *_, a=attr, v=dv: self._set_attr_int(ds, a, v))

        for i, dname in enumerate(["Front Left Door", "Front Right Door", "Rear Left Door", "Rear Right Door"]):
            dp = ds.door_open_masks[i]
            drow = tk.Frame(dbox, bg=BG_CARD, padx=6, pady=3, highlightbackground=BORDER, highlightthickness=1)
            drow.pack(fill="x", pady=2)
            tk.Label(drow, text=dname, bg=BG_CARD, fg=FG, width=18, anchor="w").pack(side="left")
            ttk.Button(
                drow, text=f"OFF: {nodemask_summary(dp.off_mask, 5)}", width=30,
                command=lambda p_ref=dp, dn=dname: NodeMask80Dialog(
                    self, f"Door Open ({dn}) — OFF Mask", p_ref.off_mask, self.ds72c2,
                    lambda m, pr=p_ref: self._set_step_mask(pr, "off", m),
                ),
            ).pack(side="left", padx=4)
            ttk.Button(
                drow, text=f"ON: {nodemask_summary(dp.on_mask, 5)}", width=30,
                command=lambda p_ref=dp, dn=dname: NodeMask80Dialog(
                    self, f"Door Open ({dn}) — ON Mask", p_ref.on_mask, self.ds72c2,
                    lambda m, pr=p_ref: self._set_step_mask(pr, "on", m),
                ),
            ).pack(side="left", padx=4)

    def _update_72c1_exit(self, idx: int, mask: List[bool]):
        self.ds72c1.exit_warn_masks[idx] = mask
        self._build_72c1_tab()
        self.refresh_status_and_hex()

    def _set_step_mask(self, obj, which: str, mask: List[bool]):
        if which == "off":
            obj.off_mask = mask
        else:
            obj.on_mask = mask
        self._build_72c1_tab()
        self.refresh_status_and_hex()

    def _set_attr_int(self, obj, attr: str, var: tk.IntVar):
        try:
            setattr(obj, attr, max(0, min(255, int(var.get()))))
            self.refresh_status_and_hex()
        except Exception:
            pass

    # -----------------------------------------------------------------------
    # TAB 4: 72C3 — ALM Dynamik
    # -----------------------------------------------------------------------

    def _build_72c3_tab(self):
        for w in self.tab_72c3.winfo_children():
            w.destroy()
        ds = self.ds72c3
        if is_8w0_dataset(ds):
            return self._build_72c3_8w0_tab()
        sf = ScrollableFrame(self.tab_72c3, bg=BG)
        sf.pack(fill="both", expand=True)

        def extra_hdr(bar):
            tk.Label(bar, text="Active Keyframes (0x04F):", bg=BG_PANEL, fg=FG).pack(side="left")
            kv = tk.IntVar(value=ds.keyframe_count)
            self._spinbox(bar, from_=0, to=10, textvariable=kv, width=4).pack(side="left", padx=(4, 12))
            kv.trace_add("write", lambda *_: self._set_attr_int(ds, "keyframe_count", kv))

            tk.Label(bar, text="Dyn Bri % (0x040..0x041):", bg=BG_PANEL, fg=FG).pack(side="left")
            for bi in range(2):
                bv = tk.IntVar(value=ds.dynamic_brightness[bi])
                self._spinbox(bar, from_=0, to=100, textvariable=bv, width=4).pack(side="left", padx=2)
                bv.trace_add("write", lambda *_, i=bi, v=bv: self._set_list_int(ds.dynamic_brightness, i, v, 100))

            ttk.Button(bar, text="Preset: KLIMAKOMPLETT", command=self._preset_72c3_klimakomplett).pack(side="right", padx=4)

        self._build_header_bar(sf.inner, ds, "0x0DE (222 B) @ RAM 0x40045FDC", extra_hdr)

        # Climate & Seat Feedback Enable Bitmask (0x03F)
        cl_box = tk.LabelFrame(
            sf.inner, text="Climate & Seat Feedback Enables (Byte 0x03F Bitmask — Firmware-Verified)",
            bg=BG_PANEL, fg=ACCENT, font=("Segoe UI", 9, "bold"), padx=8, pady=6,
        )
        cl_box.pack(fill="x", padx=8, pady=4)
        bit_labels = [
            (0, "Bit 0: Temp Warmer (Red)"),
            (1, "Bit 1: Temp Colder (Blue)"),
            (2, "Bit 2: Seat Heating (SHZ)"),
            (3, "Bit 3: Seat Ventilation (SLFT)"),
            (4, "Bit 4: Steering / Aux Climate"),
        ]
        for bit, text in bit_labels:
            bv = tk.BooleanVar(value=bool((ds.climate_enable_mask >> bit) & 1))
            tk.Checkbutton(
                cl_box, text=text, variable=bv, bg=BG_PANEL, fg=FG,
                selectcolor=BG_INPUT, activebackground=BG_PANEL, activeforeground=ACCENT,
                command=lambda b=bit, v=bv: self._toggle_72c3_climate(b, v.get()),
            ).pack(side="left", padx=8)

        # Firmware-Verified Dynamic Timing & Climate Thresholds (0x034..0x03E)
        th_box = tk.LabelFrame(
            sf.inner,
            text="Dynamic Curve & Climate Thresholds (0x035..0x03E — Firmware-Verified from SW 0810)",
            bg=BG_PANEL, fg=ACCENT, font=("Segoe UI", 9, "bold"), padx=8, pady=6,
        )
        th_box.pack(fill="x", padx=8, pady=4)

        r1 = tk.Frame(th_box, bg=BG_PANEL)
        r1.pack(fill="x", pady=2)

        fb_bri = tk.IntVar(value=ds.dynamic_timing_cfg[1])  # 0x035
        cl_dur = tk.IntVar(value=(ds.dynamic_timing_cfg[2] << 8) | ds.dynamic_timing_cfg[3])  # 0x036..0x037
        lut_sec = tk.IntVar(value=ds.dynamic_timing_cfg[4])  # 0x038 (seconds * 1000ms)
        t_red = tk.IntVar(value=ds.dynamic_timing_cfg[5] - 31)   # 0x039 (stored as step + 31)
        t_blu = tk.IntVar(value=ds.dynamic_timing_cfg[6] - 31)   # 0x03A (stored as step + 31)

        tk.Label(r1, text="Fallback Bri% (0x035):", bg=BG_PANEL, fg=FG).pack(side="left")
        self._spinbox(r1, from_=0, to=100, textvariable=fb_bri, width=4).pack(side="left", padx=(2, 12))

        tk.Label(r1, text="Climate Dur (0x036 u16):", bg=BG_PANEL, fg=FG).pack(side="left")
        self._spinbox(r1, from_=0, to=65535, textvariable=cl_dur, width=6).pack(side="left", padx=(2, 12))

        tk.Label(r1, text="Curve Timeout sec (0x038, ×1000ms):", bg=BG_PANEL, fg=FG).pack(side="left")
        self._spinbox(r1, from_=0, to=255, textvariable=lut_sec, width=4).pack(side="left", padx=(2, 12))

        tk.Label(r1, text="Red Min Step (0x039-31):", bg=BG_PANEL, fg=RED).pack(side="left")
        self._spinbox(r1, from_=-31, to=60, textvariable=t_red, width=4).pack(side="left", padx=(2, 12))

        tk.Label(r1, text="Blue Max Step (0x03A-31):", bg=BG_PANEL, fg=ACCENT).pack(side="left")
        self._spinbox(r1, from_=-31, to=60, textvariable=t_blu, width=4).pack(side="left", padx=(2, 6))

        r2 = tk.Frame(th_box, bg=BG_PANEL)
        r2.pack(fill="x", pady=(4, 0))
        shz_th = tk.IntVar(value=ds.dynamic_timing_cfg[8])   # 0x03C
        slft_th = tk.IntVar(value=ds.dynamic_timing_cfg[9])  # 0x03D
        aux_th = tk.IntVar(value=ds.dynamic_timing_cfg[10])  # 0x03E

        tk.Label(r2, text="Seat Heat Thresh (0x03C):", bg=BG_PANEL, fg=FG).pack(side="left")
        self._spinbox(r2, from_=0, to=255, textvariable=shz_th, width=4).pack(side="left", padx=(2, 12))

        tk.Label(r2, text="Seat Vent Thresh (0x03D):", bg=BG_PANEL, fg=FG).pack(side="left")
        self._spinbox(r2, from_=0, to=255, textvariable=slft_th, width=4).pack(side="left", padx=(2, 12))

        tk.Label(r2, text="Steering/Aux Thresh (0x03E):", bg=BG_PANEL, fg=FG).pack(side="left")
        self._spinbox(r2, from_=0, to=255, textvariable=aux_th, width=4).pack(side="left", padx=(2, 12))

        tk.Label(
            r2,
            text="(KLIMAKOMPLETT: Red Min=0 [0x1F], Blue Max=26 [0x39], SHZ/SLFT/Aux=1/1/1)",
            bg=BG_PANEL, fg=FG_DIM, font=("Segoe UI", 8),
        ).pack(side="left")

        def commit_th(*_):
            try:
                ds.dynamic_timing_cfg[1] = max(0, min(100, int(fb_bri.get())))
                d16 = max(0, min(65535, int(cl_dur.get())))
                ds.dynamic_timing_cfg[2] = (d16 >> 8) & 0xFF
                ds.dynamic_timing_cfg[3] = d16 & 0xFF
                ds.dynamic_timing_cfg[4] = max(0, min(255, int(lut_sec.get())))
                ds.dynamic_timing_cfg[5] = max(0, min(255, int(t_red.get()) + 31))
                ds.dynamic_timing_cfg[6] = max(0, min(255, int(t_blu.get()) + 31))
                ds.dynamic_timing_cfg[8] = max(0, min(255, int(shz_th.get())))
                ds.dynamic_timing_cfg[9] = max(0, min(255, int(slft_th.get())))
                ds.dynamic_timing_cfg[10] = max(0, min(255, int(aux_th.get())))
                self.refresh_status_and_hex()
            except Exception:
                pass

        for v in (fb_bri, cl_dur, lut_sec, t_red, t_blu, shz_th, slft_th, aux_th):
            v.trace_add("write", commit_th)

        # Event Color Indices (0x042..0x04E)
        ev_box = tk.LabelFrame(
            sf.inner, text="Dynamic Event Color Indices into 72C0 LinColors[50] (0x042..0x04E — Click Swatch to Pick)",
            bg=BG_PANEL, fg=ACCENT, font=("Segoe UI", 9, "bold"), padx=8, pady=6,
        )
        ev_box.pack(fill="x", padx=8, pady=4)
        ev_names = [
            "Temp+", "Temp-", "Ev2", "Ev3", "Ev4", "Ev5", "Ev6",
            "SeatHeat", "Ev8", "SeatVent", "Ev10", "Ev11", "Ev12",
        ]
        for i, ename in enumerate(ev_names):
            col_f = tk.Frame(ev_box, bg=BG_CARD, padx=4, pady=3, highlightbackground=BORDER, highlightthickness=1)
            col_f.grid(row=0, column=i, padx=2, pady=2)
            tk.Label(col_f, text=ename, bg=BG_CARD, fg=FG_DIM, font=("Segoe UI", 7, "bold")).pack()
            ev_v = tk.StringVar(value=f"{ds.event_color_idx[i]:02X}")
            hx0 = self._color_hex(ds.event_color_idx[i])
            sw = tk.Button(
                col_f, text="Pick", bg=hx0, fg=self._contrast_hex(hx0),
                font=("Consolas", 7, "bold"), relief="solid", bd=1, cursor="hand2",
                command=lambda v=ev_v, en=ename: self._open_color_idx_picker(f"72C3 Event Color — {en}", v),
            )
            sw.pack(fill="x", pady=2)
            self._entry(col_f, textvariable=ev_v, width=4, font=("Consolas", 8), justify="center").pack()

            def make_ev_cb(ii, var, s_lbl):
                def _cb(*_):
                    try:
                        val = int(var.get(), 16) & 0xFF
                        ds.event_color_idx[ii] = val
                        hx = self._color_hex(val)
                        s_lbl.config(bg=hx, fg=self._contrast_hex(hx))
                        self.refresh_status_and_hex()
                    except Exception:
                        pass
                return _cb
            ev_v.trace_add("write", make_ev_cb(i, ev_v, sw))

        # 10-Keyframe Animation Sequencer (0x052..0x0DD)
        kf_box = tk.LabelFrame(
            sf.inner, text="10-Keyframe Animation Sequencer — Welcome Wipe / Pulse (0x052..0x0DD)",
            bg=BG_PANEL, fg=ACCENT, font=("Segoe UI", 9, "bold"), padx=8, pady=6,
        )
        kf_box.pack(fill="x", padx=8, pady=4)
        for s, kf in enumerate(ds.keyframes):
            row = tk.Frame(kf_box, bg=BG_CARD, padx=6, pady=3, highlightbackground=BORDER, highlightthickness=1)
            row.pack(fill="x", pady=2)
            active_tag = "●" if s < ds.keyframe_count else "○"
            fg_t = GREEN if s < ds.keyframe_count else FG_DIM
            tk.Label(row, text=f"{active_tag} Step {s}", bg=BG_CARD, fg=fg_t, font=("Consolas", 9, "bold"), width=9).pack(side="left")

            ttk.Button(
                row, text=f"Mask: {nodemask_summary(kf.node_mask, 5)}", width=34,
                command=lambda k_ref=kf, idx=s: NodeMask80Dialog(
                    self, f"72C3 Keyframe Step {idx} — NodeMask80", k_ref.node_mask, self.ds72c2,
                    lambda m, kr=k_ref: self._set_72c3_kf_mask(kr, m),
                ),
            ).pack(side="left", padx=4)

            tk.Label(row, text="Color:", bg=BG_CARD, fg=FG_DIM).pack(side="left", padx=(6, 2))
            cv = tk.StringVar(value=f"{kf.color_idx:02X}")
            hx0 = self._color_hex(kf.color_idx)
            sw = tk.Button(
                row, text="Pick", bg=hx0, fg=self._contrast_hex(hx0),
                font=("Consolas", 7, "bold"), width=5, relief="solid", bd=1, cursor="hand2",
                command=lambda v=cv, idx=s: self._open_color_idx_picker(f"72C3 Keyframe Step {idx} — Color", v),
            )
            sw.pack(side="left", padx=3)
            self._entry(row, textvariable=cv, width=4, font=("Consolas", 9)).pack(side="left")

            def make_kfc(k_ref, var, s_lbl):
                def _cb(*_):
                    try:
                        val = int(var.get(), 16) & 0xFF
                        k_ref.color_idx = val
                        hx = self._color_hex(val)
                        s_lbl.config(bg=hx, fg=self._contrast_hex(hx))
                        self.refresh_status_and_hex()
                    except Exception:
                        pass
                return _cb
            cv.trace_add("write", make_kfc(kf, cv, sw))

            for lbl_t, attr in [("Dur(×10ms):", "duration"), ("Fade:", "fade_time"), ("Bri%:", "brightness")]:
                tk.Label(row, text=lbl_t, bg=BG_CARD, fg=FG_DIM).pack(side="left", padx=(8, 2))
                sv = tk.IntVar(value=getattr(kf, attr))
                self._spinbox(row, from_=0, to=255, textvariable=sv, width=4).pack(side="left")
                sv.trace_add("write", lambda *_, kr=kf, a=attr, v=sv: self._set_attr_int(kr, a, v))

        # 16-Point Dynamic Curve LUT (0x014..0x033) + Mini Plot
        lut_box = tk.LabelFrame(
            sf.inner, text="16-Point Dynamic Curve LUT (10-Interval X Breakpoints 0x014..0x029 + Decay 0x02A..0x033)",
            bg=BG_PANEL, fg=ACCENT, font=("Segoe UI", 9, "bold"), padx=8, pady=6,
        )
        lut_box.pack(fill="x", padx=8, pady=4)
        tk.Label(
            lut_box,
            text="Values: " + ", ".join(str(x) for x in ds.curve_lut),
            bg=BG_PANEL, fg=FG_DIM, font=("Consolas", 8),
        ).pack(anchor="w")

        canvas = tk.Canvas(lut_box, height=82, bg=BG, highlightbackground=BORDER, highlightthickness=1)
        canvas.pack(fill="x", pady=4)
        mx = max(ds.curve_lut) if any(ds.curve_lut) else 1
        pts = []
        for i, val in enumerate(ds.curve_lut):
            x = 24 + i * 52
            y = 70 - int((val / mx) * 56)
            pts.extend((x, y))
            canvas.create_oval(x-3, y-3, x+3, y+3, fill=ACCENT, outline="")
        if len(pts) >= 4:
            canvas.create_line(*pts, fill=ACCENT, width=2)

    def _preset_72c3_klimakomplett(self):
        ds = self.ds72c3
        ds.climate_enable_mask = 0x1F
        ds.dynamic_timing_cfg[2] = 0x00
        ds.dynamic_timing_cfg[3] = 0x08
        ds.dynamic_timing_cfg[5] = 0x1F  # step 0
        ds.dynamic_timing_cfg[6] = 0x39  # step 26
        ds.dynamic_timing_cfg[8] = 0x01
        ds.dynamic_timing_cfg[9] = 0x01
        ds.dynamic_timing_cfg[10] = 0x01
        self._build_72c3_tab()
        self.refresh_status_and_hex()

    def _toggle_72c3_climate(self, bit: int, val: bool):
        if val:
            self.ds72c3.climate_enable_mask |= (1 << bit)
        else:
            self.ds72c3.climate_enable_mask &= ~(1 << bit)
        self.refresh_status_and_hex()

    def _set_72c3_kf_mask(self, kf, mask: List[bool]):
        kf.node_mask = mask
        self._build_72c3_tab()
        self.refresh_status_and_hex()

    def _set_list_int(self, lst: List[int], idx: int, var: tk.IntVar, max_v: int = 255):
        try:
            lst[idx] = max(0, min(max_v, int(var.get())))
            self.refresh_status_and_hex()
        except Exception:
            pass

    # -----------------------------------------------------------------------
    # 8W0907063 (B9 / Q5 FY / Q7 4M, SW 0215..0313) Tab Builders
    # -----------------------------------------------------------------------

    def _build_header_bar_8w0(self, parent, ds, sizeof_hint: str, extra_widgets_fn=None):
        bar = tk.LabelFrame(
            parent,
            text=f"8W0907063 (B9/4M) Header (0x000..0x003) — FW SW0313 Active Payload: {sizeof_hint}",
            bg=BG_PANEL, fg=ACCENT, font=("Segoe UI", 9, "bold"), padx=8, pady=6,
        )
        bar.pack(fill="x", padx=8, pady=6)

        tk.Label(bar, text="Version (4B):", bg=BG_PANEL, fg=FG).pack(side="left")
        v_var = tk.StringVar(value=ds.version)
        self._entry(bar, textvariable=v_var, width=6, font=("Consolas", 9)).pack(side="left", padx=(4, 12))

        def commit_v(*_):
            ds.version = v_var.get()[:4]
            self.refresh_status_and_hex()

        v_var.trace_add("write", commit_v)
        if extra_widgets_fn:
            extra_widgets_fn(bar)

    def _pick_rgb_tuple(self, current: tuple, title: str, on_picked: Callable[[tuple], None]):
        cur_hex = f"#{current[0]:02X}{current[1]:02X}{current[2]:02X}"
        rgb, _ = colorchooser.askcolor(color=cur_hex, title=title)
        if not rgb:
            return
        on_picked((int(round(rgb[0])), int(round(rgb[1])), int(round(rgb[2]))))

    def _build_72c0_8w0_tab(self):
        ds: Dataset72C0_8W0 = self.ds72c0
        sf = ScrollableFrame(self.tab_72c0, bg=BG)
        sf.pack(fill="both", expand=True)

        def extra_hdr(bar):
            tk.Label(bar, text="User Colors (0x010):", bg=BG_PANEL, fg=FG).pack(side="left")
            uc = tk.IntVar(value=ds.user_color_count)
            self._spinbox(bar, from_=0, to=30, textvariable=uc, width=4).pack(side="left", padx=(3, 10))

            tk.Label(bar, text="ProfileMask (0x071 hex):", bg=BG_PANEL, fg=FG).pack(side="left")
            pm = tk.StringVar(value=f"{ds.profile_enable_mask:02X}")
            self._entry(bar, textvariable=pm, width=4, font=("Consolas", 9)).pack(side="left", padx=(3, 10))

            bap_v = tk.BooleanVar(value=bool(ds.new_bap_menu))
            tk.Checkbutton(
                bar, text="New BAP Menu (0x137)", variable=bap_v,
                bg=BG_PANEL, fg=FG, selectcolor=BG_INPUT, activebackground=BG_PANEL,
                command=lambda: (setattr(ds, "new_bap_menu", 1 if bap_v.get() else 0), self.refresh_status_and_hex()),
            ).pack(side="left", padx=(0, 8))

            def on_hdr(*_):
                try:
                    ds.user_color_count = max(0, min(30, int(uc.get())))
                except Exception:
                    pass
                try:
                    ds.profile_enable_mask = int(pm.get(), 16) & 0xFF
                except Exception:
                    pass
                self.refresh_status_and_hex()

            uc.trace_add("write", on_hdr)
            pm.trace_add("write", on_hdr)

            ttk.Button(bar, text="Copy All HMI → LIN", command=self._copy_hmi_to_lin).pack(side="right", padx=4)

        self._build_header_bar_8w0(sf.inner, ds, "0x139 (313 B) @ RAM 0x400195EC", extra_hdr)

        # Fixed White Fallbacks (0x004..0x00F) + Climate Warm/Cold RGBs (0x06B..0x070)
        fw_box = tk.LabelFrame(
            sf.inner, text="Fixed White Fallbacks (0x004..0x00F) & Climate Feedback RGBs (0x06B..0x070)",
            bg=BG_PANEL, fg=ACCENT, font=("Segoe UI", 9, "bold"), padx=8, pady=6,
        )
        fw_box.pack(fill="x", padx=8, pady=4)
        for i in range(4):
            r, g, b = ds.fixed_white_rgb[i]
            hx = f"#{r:02X}{g:02X}{b:02X}"
            tk.Button(
                fw_box, text=f"White #{i}\n{hx[1:]}", bg=hx, fg=self._contrast(r, g, b),
                font=("Consolas", 8, "bold"), width=12, relief="solid", bd=1,
                command=lambda idx=i: self._pick_rgb_tuple(
                    ds.fixed_white_rgb[idx], f"8W0 72C0 Fixed White #{idx}",
                    lambda c, ii=idx: (ds.fixed_white_rgb.__setitem__(ii, c), self._build_72c0_tab(), self.refresh_status_and_hex()),
                ),
            ).pack(side="left", padx=4, pady=2)

        for lbl_t, attr in [("Climate Warm\n(0x06B)", "climate_warm_rgb"), ("Climate Cold\n(0x06E)", "climate_cold_rgb")]:
            r, g, b = getattr(ds, attr)
            hx = f"#{r:02X}{g:02X}{b:02X}"
            tk.Button(
                fw_box, text=f"{lbl_t}\n{hx[1:]}", bg=hx, fg=self._contrast(r, g, b),
                font=("Consolas", 8, "bold"), width=14, relief="solid", bd=1,
                command=lambda a=attr, lt=lbl_t: self._pick_rgb_tuple(
                    getattr(ds, a), f"8W0 72C0 {lt}",
                    lambda c, at=a: (setattr(ds, at, c), self._build_72c0_tab(), self.refresh_status_and_hex()),
                ),
            ).pack(side="left", padx=6, pady=2)

        # 30 User-Selectable Colors (HMI 0x011..0x06A + Calibrated LIN 0x0DD..0x136)
        pal_box = tk.LabelFrame(
            sf.inner,
            text="30 User-Selectable Colors — Click HMI (0x011..0x06A) or LIN (0x0DD..0x136) Swatch to Edit RGB",
            bg=BG_PANEL, fg=ACCENT, font=("Segoe UI", 9, "bold"), padx=8, pady=8,
        )
        pal_box.pack(fill="x", padx=8, pady=4)
        for i in range(30):
            r, c = i // 6, i % 6
            card = tk.Frame(pal_box, bg=BG_CARD, padx=6, pady=4, highlightbackground=BORDER, highlightthickness=1)
            card.grid(row=r, column=c, padx=4, pady=4, sticky="ew")
            pal_box.columnconfigure(c, weight=1)

            hr, hg, hb = ds.hmi_colors[i]
            lr, lg, lb = ds.lin_colors[i]
            h_hex, l_hex = f"#{hr:02X}{hg:02X}{hb:02X}", f"#{lr:02X}{lg:02X}{lb:02X}"
            tk.Label(card, text=f"#{i:02d} (0x{i:02X})", bg=BG_CARD, fg=FG, font=("Consolas", 8, "bold")).pack(anchor="w")
            row = tk.Frame(card, bg=BG_CARD)
            row.pack(fill="x", pady=2)
            tk.Button(
                row, text=f"HMI\n{h_hex[1:]}", bg=h_hex, fg=self._contrast(hr, hg, hb),
                font=("Consolas", 7, "bold"), width=8, relief="solid", bd=1,
                command=lambda idx=i: self._pick_72c0_color("hmi", idx),
            ).pack(side="left", padx=(0, 3), expand=True, fill="x")
            tk.Button(
                row, text=f"LIN\n{l_hex[1:]}", bg=l_hex, fg=self._contrast(lr, lg, lb),
                font=("Consolas", 7, "bold"), width=8, relief="solid", bd=1,
                command=lambda idx=i: self._pick_72c0_color("lin", idx),
            ).pack(side="left", expand=True, fill="x")

        # 7 Ambient / Drive Select Profiles (Direct RGB 0x07B..0x0A4) + 8x6 Brightness (0x0A5..0x0D4)
        prof_box = tk.LabelFrame(
            sf.inner,
            text="7 Ambient / Drive Select Profiles — Direct RGB (0x07B..0x0A4) & 8×6 Brightness % (0x0A5..0x0D4)",
            bg=BG_PANEL, fg=ACCENT, font=("Segoe UI", 9, "bold"), padx=8, pady=6,
        )
        prof_box.pack(fill="x", padx=8, pady=4)
        headers = ["Profile / Drive Mode", "Surface RGB", "Contour RGB"] + [f"Bri C{g}" for g in range(6)]
        for ci, h in enumerate(headers):
            tk.Label(prof_box, text=h, bg=BG_PANEL, fg=FG_DIM, font=("Segoe UI", 8, "bold")).grid(row=0, column=ci, padx=4, pady=2)

        for p in range(8):
            pname = PROFILE_NAMES_8W0[p] if p < 7 else "P7: Brightness Row 7"
            tk.Label(prof_box, text=pname, bg=BG_PANEL, fg=FG, font=("Consolas", 8), anchor="w").grid(row=p + 1, column=0, sticky="w", padx=4, pady=2)
            if p < 7:
                c1, c2 = ds.profile_rgb[p]
                h1, h2 = f"#{c1[0]:02X}{c1[1]:02X}{c1[2]:02X}", f"#{c2[0]:02X}{c2[1]:02X}{c2[2]:02X}"
                tk.Button(
                    prof_box, text=h1[1:], bg=h1, fg=self._contrast(*c1),
                    font=("Consolas", 8, "bold"), width=9, relief="solid", bd=1,
                    command=lambda idx=p: self._pick_rgb_tuple(
                        ds.profile_rgb[idx][0], f"8W0 72C0 {PROFILE_NAMES_8W0[idx]} Surface",
                        lambda c, ii=idx: (ds.profile_rgb.__setitem__(ii, (c, ds.profile_rgb[ii][1])), self._build_72c0_tab(), self.refresh_status_and_hex()),
                    ),
                ).grid(row=p + 1, column=1, padx=4, pady=2)
                tk.Button(
                    prof_box, text=h2[1:], bg=h2, fg=self._contrast(*c2),
                    font=("Consolas", 8, "bold"), width=9, relief="solid", bd=1,
                    command=lambda idx=p: self._pick_rgb_tuple(
                        ds.profile_rgb[idx][1], f"8W0 72C0 {PROFILE_NAMES_8W0[idx]} Contour",
                        lambda c, ii=idx: (ds.profile_rgb.__setitem__(ii, (ds.profile_rgb[ii][0], c)), self._build_72c0_tab(), self.refresh_status_and_hex()),
                    ),
                ).grid(row=p + 1, column=2, padx=4, pady=2)
            else:
                tk.Label(prof_box, text="—", bg=BG_PANEL, fg=FG_DIM).grid(row=p + 1, column=1)
                tk.Label(prof_box, text="—", bg=BG_PANEL, fg=FG_DIM).grid(row=p + 1, column=2)

            for g in range(6):
                bv = tk.IntVar(value=ds.profile_brightness[p][g])
                self._spinbox(prof_box, from_=0, to=100, textvariable=bv, width=4).grid(row=p + 1, column=3 + g, padx=2, pady=2)
                bv.trace_add("write", lambda *_, pr=p, gr=g, v=bv: self._set_list_int(ds.profile_brightness[pr], gr, v, 100))

    def _build_72c2_8w0_tab(self, filter_mode: Optional[str] = None):
        if filter_mode is None:
            filter_mode = getattr(self, "_72c2_8w0_filter", "all")
        else:
            self._72c2_8w0_filter = filter_mode

        for w in self.tab_72c2.winfo_children():
            w.destroy()
        ds: Dataset72C2_8W0 = self.ds72c2

        def extra_hdr(bar):
            act_cnt = sum(1 for s in ds.slaves if s.active)
            tk.Label(
                bar, text=f"Active Slaves: {act_cnt}/56  |  GlobalCfg (0x11C): {ds.global_slave_cfg.hex(' ').upper()}",
                bg=BG_PANEL, fg=FG_DIM, font=("Consolas", 9),
            ).pack(side="left", padx=8)
            ttk.Button(
                bar, text="Convert 8W0 → 4N0/8W1 (for 4N0/8W1 BCM1)...",
                command=self._open_convert_72c2_8w0_to_4n0_dialog,
            ).pack(side="right", padx=4)

        self._build_header_bar_8w0(self.tab_72c2, ds, "0x120 (288 B) @ RAM 0x400198EC", extra_hdr)

        sf = ScrollableFrame(self.tab_72c2, bg=BG)
        sf.pack(fill="both", expand=True, padx=8, pady=4)

        # 7 x uint16 Zone Group Masks (0x004..0x011)
        zm_box = tk.LabelFrame(
            sf.inner, text="7 Logical Zone Group Bitmasks (0x004..0x011 · uint16 BE)",
            bg=BG_PANEL, fg=ACCENT, font=("Segoe UI", 9, "bold"), padx=8, pady=6,
        )
        zm_box.pack(fill="x", pady=4)
        zm_labels = [
            "Mask[0] Master Active Zones (0x004)",
            "Mask[1] Door/Interior Contours (0x006)",
            "Mask[2] Surface / Ambient (0x008)",
            "Mask[3] 4-Door Surface/Contour (0x00A)",
            "Mask[4] 4-Door Primary (0x00C)",
            "Mask[5] Center Console / Front (0x00E)",
            "Mask[6] Rear / Extended (0x010)",
        ]
        for i in range(7):
            row = tk.Frame(zm_box, bg=BG_PANEL)
            row.pack(fill="x", pady=2)
            tk.Label(row, text=zm_labels[i], bg=BG_PANEL, fg=FG, width=36, anchor="w", font=("Segoe UI", 8)).pack(side="left")
            tk.Label(
                row, text=zonemask16_summary(ds.zone_group_masks[i]),
                bg=BG_CARD, fg=FG_DIM, font=("Consolas", 8), width=32, anchor="w", padx=6, relief="solid", bd=1,
            ).pack(side="left", padx=4)
            ttk.Button(
                row, text="Edit 16-Zone Mask...",
                command=lambda ii=i, lb=zm_labels[i]: ZoneMask16Dialog(
                    self, f"8W0 72C2 — {lb}", ds.zone_group_masks[ii], ds,
                    lambda m, idx=ii: (ds.zone_group_masks.__setitem__(idx, m), self._build_72c2_tab(), self.refresh_status_and_hex()),
                ),
            ).pack(side="left", padx=4)

        # 21-Entry ZoneConfig (0x012..0x03B): [zone_id, mode] (0=Off/No DTC, 1=LIN Bus 1, 2=LIN Bus 2)
        zc_box = tk.LabelFrame(
            sf.inner,
            text="21 Zone Routing & DTC Bus Config (0x012..0x03B — Mode: 0 = Off / No DTC, 1 = LIN Bus 1, 2 = LIN Bus 2)",
            bg=BG_PANEL, fg=ACCENT, font=("Segoe UI", 9, "bold"), padx=8, pady=6,
        )
        zc_box.pack(fill="x", pady=4)
        for i, (zid, zmode) in enumerate(ds.zone_config):
            r, c = i // 7, i % 7
            cell = tk.Frame(zc_box, bg=BG_CARD, padx=4, pady=3, highlightbackground=BORDER, highlightthickness=1)
            cell.grid(row=r, column=c, padx=3, pady=3, sticky="ew")
            zc_box.columnconfigure(c, weight=1)
            tk.Label(cell, text=f"#{i:02d} (0x{0x012+i*2:02X})", bg=BG_CARD, fg=FG_DIM, font=("Consolas", 7)).pack(anchor="w")
            rw = tk.Frame(cell, bg=BG_CARD)
            rw.pack(fill="x")
            tk.Label(rw, text="Z:", bg=BG_CARD, fg=FG, font=("Consolas", 8)).pack(side="left")
            zv = tk.IntVar(value=zid)
            self._spinbox(rw, from_=0, to=15, textvariable=zv, width=3).pack(side="left", padx=(1, 4))
            tk.Label(rw, text="M:", bg=BG_CARD, fg=FG, font=("Consolas", 8)).pack(side="left")
            mv = tk.IntVar(value=zmode)
            self._spinbox(rw, from_=0, to=2, textvariable=mv, width=2).pack(side="left")
            zv.trace_add("write", lambda *_, idx=i, v=zv: self._set_8w0_zone_cfg(idx, 0, v))
            mv.trace_add("write", lambda *_, idx=i, v=mv: self._set_8w0_zone_cfg(idx, 1, v))

        # Helper to compute firmware 3-gate DTC bus for a slave (0x0011CD68..0x0011D136)
        def slave_dtc_status(idx, sl):
            is_z = idx in ZERO_DTC_SLOTS_8W0
            if not sl.active:
                return ("Zero-DTC Free", "#f3e8ff", "#7e22ce") if is_z else ("OFF", "#e2e8f0", "#64748b")
            if not (1 <= sl.lin_id <= 20):
                return ("No DTC (ID)", "#fef3c7", "#b45309")
            mode = 0
            for z_id, z_m in ds.zone_config:
                if z_id == sl.zone_id:
                    mode = z_m
                    break
            if mode == 0:
                return ("No DTC (M=0)", "#fef3c7", "#b45309")
            if is_z:
                return (f"ZERO-DTC L{mode}", "#f3e8ff", "#7e22ce")
            if mode == 1:
                return ("LIN 1 DTC", "#dbeafe", "#1d4ed8")
            return ("LIN 2 DTC", "#dcfce7", "#15803d")

        # 56 x 4-Byte Slave Descriptor Table (0x03C..0x11B)
        fbar = tk.Frame(sf.inner, bg=BG, pady=4)
        fbar.pack(fill="x")
        tk.Label(fbar, text="56 Slave Records (0x03C..0x11B) — Filter:", bg=BG, fg=FG, font=("Segoe UI", 9, "bold")).pack(side="left", padx=(0, 8))
        for k, lb in [
            ("all", "All 56 Slaves"),
            ("active", "Active / Populated Only"),
            ("zerodtc", "Zero-DTC Slots (18)"),
        ]:
            ttk.Button(
                fbar, text=(f"● {lb}" if filter_mode == k else lb),
                command=lambda mode=k: self._build_72c2_tab(mode),
            ).pack(side="left", padx=3)

        tbl = tk.Frame(sf.inner, bg=BG_PANEL, highlightbackground=BORDER, highlightthickness=1)
        tbl.pack(fill="x", pady=4)
        for ci, h in enumerate(["Slave #", "Offset", "Flags", "LIN ID (01..14)", "Zone ID", "Zone Hint", "DTC / Bus Gate", "Active", "DTC Action"]):
            tk.Label(tbl, text=h, bg=BG_CARD, fg=ACCENT, font=("Segoe UI", 8, "bold"), padx=6, pady=4).grid(row=0, column=ci, sticky="ew")

        row_i = 1
        for i, sl in enumerate(ds.slaves):
            if filter_mode == "active" and not (sl.active or sl.lin_id):
                continue
            if filter_mode == "zerodtc" and i not in ZERO_DTC_SLOTS_8W0:
                continue
            bg_r = BG_PANEL if row_i % 2 == 1 else BG_CARD
            s_fg = "#7e22ce" if i in ZERO_DTC_SLOTS_8W0 else FG
            tk.Label(tbl, text=f"S{i:02d}", bg=bg_r, fg=s_fg, font=("Consolas", 9, "bold"), width=5).grid(row=row_i, column=0, sticky="ew", pady=1)
            tk.Label(tbl, text=f"0x{0x03C + i*4:03X}", bg=bg_r, fg=FG_DIM, font=("Consolas", 8), width=7).grid(row=row_i, column=1, sticky="ew")

            fl_v = tk.StringVar(value=f"{sl.flags:02X}")
            self._entry(tbl, textvariable=fl_v, width=4, font=("Consolas", 9)).grid(row=row_i, column=2, padx=4)
            fl_v.trace_add("write", lambda *_, s_ref=sl, v=fl_v: self._set_attr_hex(s_ref, "flags", v))

            li_v = tk.StringVar(value=f"{sl.lin_id:02X}")
            self._entry(tbl, textvariable=li_v, width=5, font=("Consolas", 9)).grid(row=row_i, column=3, padx=4)
            li_v.trace_add("write", lambda *_, s_ref=sl, v=li_v: self._set_attr_hex(s_ref, "lin_id", v))

            z_hint = tk.Label(tbl, text=ZONE_16_NAMES_8W0.get(sl.zone_id, f"Z{sl.zone_id:02d}"), bg=bg_r, fg=FG_DIM, font=("Segoe UI", 8), anchor="w", width=24)
            z_hint.grid(row=row_i, column=5, sticky="ew", padx=4)

            d_txt, d_bg, d_fg = slave_dtc_status(i, sl)
            dtc_lbl = tk.Label(tbl, text=d_txt, bg=d_bg, fg=d_fg, font=("Consolas", 8, "bold"), width=14, padx=4)
            dtc_lbl.grid(row=row_i, column=6, padx=3, pady=1)

            zi_v = tk.IntVar(value=sl.zone_id)
            self._spinbox(tbl, from_=0, to=15, textvariable=zi_v, width=4).grid(row=row_i, column=4, padx=4)
            zi_v.trace_add(
                "write",
                lambda *_, s_ref=sl, v=zi_v, hl=z_hint: self._set_8w0_slave_zone(s_ref, v, hl),
            )

            ac_v = tk.BooleanVar(value=bool(sl.active))
            tk.Checkbutton(
                tbl, variable=ac_v, bg=bg_r, selectcolor=BG_INPUT, activebackground=bg_r,
                command=lambda ii=i, s_ref=sl, v=ac_v, dl=dtc_lbl: (
                    setattr(s_ref, "active", 1 if v.get() else 0),
                    dl.config(**dict(zip(("text", "bg", "fg"), slave_dtc_status(ii, s_ref)))),
                    self.refresh_status_and_hex(),
                ),
            ).grid(row=row_i, column=7)

            if sl.active and sl.lin_id != 0 and i not in ZERO_DTC_SLOTS_8W0:
                ttk.Button(
                    tbl, text="Silence DTC →",
                    command=lambda ii=i: self._move_8w0_slave_zero_dtc(ii),
                ).grid(row=row_i, column=8, padx=3, pady=1)
            elif i in ZERO_DTC_SLOTS_8W0 and sl.active:
                tk.Label(tbl, text="DTC Silenced", bg=bg_r, fg="#7e22ce", font=("Segoe UI", 8, "bold")).grid(row=row_i, column=8, padx=3)
            else:
                tk.Label(tbl, text="", bg=bg_r).grid(row=row_i, column=8)
            row_i += 1

    def _move_8w0_slave_zero_dtc(self, src_idx: int):
        dst = move_8w0_slave_to_zero_dtc_slot(self.ds72c2, src_idx)
        if dst is None:
            messagebox.showwarning("No Free Zero-DTC Slot", "All 18 firmware ZERO-DTC slots are currently occupied.")
            return
        self._build_72c2_tab()
        self.refresh_status_and_hex()
        self.status_lbl.config(
            text=f"Moved 8W0 slave S{src_idx:02d} → S{dst:02d} (firmware ZERO-DTC slot: LED stays ON, DTCs deactivated)."
        )

    def _set_8w0_zone_cfg(self, idx: int, which: int, var: tk.IntVar):
        try:
            val = max(0, min(15 if which == 0 else 2, int(var.get())))
            z_id, z_m = self.ds72c2.zone_config[idx]
            self.ds72c2.zone_config[idx] = (val, z_m) if which == 0 else (z_id, val)
            self.refresh_status_and_hex()
        except Exception:
            pass

    def _open_convert_72c2_8w0_to_4n0_dialog(self):
        if not is_8w0_dataset(self.ds72c2):
            return
        dlg = tk.Toplevel(self)
        dlg.title("Convert 72C2: 8W0907063 → 4N0907063 / 8W1907063 (STRUCT620)")
        dlg.configure(bg=BG)
        dlg.geometry("560x240")
        dlg.transient(self)
        dlg.grab_set()

        box = tk.LabelFrame(
            dlg, text="8W0 LEDs on 4N0/8W1 BCM1 — Conversion Options",
            bg=BG_PANEL, fg=ACCENT, font=("Segoe UI", 9, "bold"), padx=12, pady=10,
        )
        box.pack(fill="both", expand=True, padx=10, pady=10)

        tk.Label(
            box,
            text=(
                f"Source: 8W0 72C2 ({self.ds72c2.version.strip()}, 510 B)  →  "
                f"Target: 4N0/8W1 72C2 (STRUCT620 v3.6.2.0, 522 B)\n"
                "Maps 8W0 LIN Bus 1/2 slaves 1:1 to 4N0/8W1 Nodes 0..47, color groups, and door zones."
            ),
            bg=BG_PANEL, fg=FG, justify="left", font=("Segoe UI", 8),
        ).pack(anchor="w", pady=(0, 8))

        sup_v = tk.BooleanVar(value=True)
        tk.Checkbutton(
            box,
            text="Suppress LIN DTCs (set 0x0A2..0x0D1 = FF — recommended for 8W0 LEDs on 4N0/8W1 BCM1)",
            variable=sup_v, bg=BG_PANEL, fg=FG, selectcolor=BG_INPUT, activebackground=BG_PANEL,
        ).pack(anchor="w", pady=3)

        pwm_v = tk.BooleanVar(value=True)
        tk.Checkbutton(
            box,
            text="Enable standard B9/4N0/8W1 PWM (N50..N54) & Interior/White (N64..N73) channels",
            variable=pwm_v, bg=BG_PANEL, fg=FG, selectcolor=BG_INPUT, activebackground=BG_PANEL,
        ).pack(anchor="w", pady=3)

        bot = tk.Frame(dlg, bg=BG_PANEL, padx=10, pady=8, highlightbackground=BORDER, highlightthickness=1)
        bot.pack(fill="x")

        def do_convert():
            converted = convert_72c2_8w0_to_4n0(
                self.ds72c2,
                suppress_dtc=sup_v.get(),
                include_pwm_int_defaults=pwm_v.get(),
            )
            self.ds72c2 = converted
            dlg.destroy()
            self._build_72c2_tab("all")
            self.refresh_status_and_hex()
            act_lin = sum(1 for i in range(48) if converted.active_nodes[i])
            mode_txt = "0xFF (DTCs suppressed)" if sup_v.get() else "OEM 4N0/8W1 Diag IDs"
            self.status_lbl.config(
                text=f"Converted 8W0 72C2 → 4N0/8W1 72C2 ({act_lin} active LIN nodes, Diag={mode_txt}). Click 'Save Current XML' to export."
            )

        ttk.Button(bot, text="Cancel", command=dlg.destroy).pack(side="right", padx=4)
        ttk.Button(bot, text="Convert to 4N0/8W1 (522 B)", command=do_convert).pack(side="right", padx=4)

    def _set_attr_hex(self, obj, attr: str, var: tk.StringVar):
        try:
            setattr(obj, attr, int(var.get(), 16) & 0xFF)
            self.refresh_status_and_hex()
        except Exception:
            pass

    def _set_8w0_slave_zone(self, sl, var: tk.IntVar, hint_lbl):
        try:
            val = max(0, min(15, int(var.get())))
            sl.zone_id = val
            hint_lbl.config(text=ZONE_16_NAMES_8W0.get(val, f"Z{val:02d}"))
            self.refresh_status_and_hex()
        except Exception:
            pass

    def _build_72c1_8w0_tab(self):
        ds: Dataset72C1_8W0 = self.ds72c1
        ds72c2_8w0 = self.ds72c2 if is_8w0_dataset(self.ds72c2) else None
        sf = ScrollableFrame(self.tab_72c1, bg=BG)
        sf.pack(fill="both", expand=True)

        self._build_header_bar_8w0(sf.inner, ds, "0xBE (190 B) @ RAM 0x400197EC")

        # Global Timing (0x004..0x011)
        gt_box = tk.LabelFrame(
            sf.inner, text="Global Scenario Timing & Debounce (0x004..0x011)",
            bg=BG_PANEL, fg=ACCENT, font=("Segoe UI", 9, "bold"), padx=8, pady=6,
        )
        gt_box.pack(fill="x", padx=8, pady=4)
        gt = bytearray(ds.global_timing)
        c_a = tk.IntVar(value=gt[1])
        c_b = tk.IntVar(value=gt[2])
        deb_en = tk.BooleanVar(value=bool(gt[3]))
        max_b = tk.IntVar(value=gt[5])
        t_out = tk.IntVar(value=gt[6])
        t1_ms = tk.IntVar(value=int.from_bytes(gt[8:10], "big"))
        deb_ms = tk.IntVar(value=int.from_bytes(gt[12:14], "big"))

        def commit_gt(*_):
            try:
                buf = bytearray(ds.global_timing)
                buf[1] = max(0, min(255, int(c_a.get())))
                buf[2] = max(0, min(255, int(c_b.get())))
                buf[3] = 1 if deb_en.get() else 0
                buf[5] = max(0, min(100, int(max_b.get())))
                buf[6] = max(0, min(255, int(t_out.get())))
                buf[8:10] = max(0, min(65535, int(t1_ms.get()))).to_bytes(2, "big")
                buf[12:14] = max(0, min(65535, int(deb_ms.get()))).to_bytes(2, "big")
                ds.global_timing = bytes(buf)
                self.refresh_status_and_hex()
            except Exception:
                pass

        for txt, var, hi, w in [
            ("Curve A (0x005):", c_a, 255, 4), ("Curve B (0x006):", c_b, 255, 4),
            ("Max Bri% (0x009):", max_b, 100, 4), ("Timeout (0x00A):", t_out, 255, 4),
            ("Timer1 (0x00C):", t1_ms, 65535, 6), ("Debounce ms (0x010):", deb_ms, 65535, 6),
        ]:
            tk.Label(gt_box, text=txt, bg=BG_PANEL, fg=FG).pack(side="left")
            self._spinbox(gt_box, from_=0, to=hi, textvariable=var, width=w).pack(side="left", padx=(2, 8))
            var.trace_add("write", commit_gt)
        tk.Checkbutton(
            gt_box, text="Debounce En (0x007)", variable=deb_en,
            bg=BG_PANEL, fg=FG, selectcolor=BG_INPUT, activebackground=BG_PANEL, command=commit_gt,
        ).pack(side="left", padx=4)

        # Exit Warning Masks (4 x uint16 @ 0x012..0x019)
        ew_box = tk.LabelFrame(
            sf.inner, text="Exit Warning (Ausstiegswarnung) — 4 Door 16-Zone Masks (0x012..0x019)",
            bg=BG_PANEL, fg=ACCENT, font=("Segoe UI", 9, "bold"), padx=8, pady=6,
        )
        ew_box.pack(fill="x", padx=8, pady=4)
        for i, dname in enumerate(["Front Left (FL)", "Front Right (FR)", "Rear Left (RL)", "Rear Right (RR)"]):
            row = tk.Frame(ew_box, bg=BG_PANEL)
            row.pack(fill="x", pady=2)
            tk.Label(row, text=dname, bg=BG_PANEL, fg=FG, width=18, anchor="w").pack(side="left")
            tk.Label(
                row, text=zonemask16_summary(ds.exit_warn_masks[i]),
                bg=BG_CARD, fg=FG_DIM, font=("Consolas", 8), width=32, anchor="w", padx=6, relief="solid", bd=1,
            ).pack(side="left", padx=4)
            ttk.Button(
                row, text="Edit Mask...",
                command=lambda ii=i, dn=dname: ZoneMask16Dialog(
                    self, f"8W0 72C1 Exit Warning — {dn}", ds.exit_warn_masks[ii], ds72c2_8w0,
                    lambda m, idx=ii: (ds.exit_warn_masks.__setitem__(idx, m), self._build_72c1_tab(), self.refresh_status_and_hex()),
                ),
            ).pack(side="left", padx=4)

        def add_step_row(parent_box, label: str, st):
            srow = tk.Frame(parent_box, bg=BG_CARD, padx=6, pady=3, highlightbackground=BORDER, highlightthickness=1)
            srow.pack(fill="x", pady=2)
            tk.Label(srow, text=label, bg=BG_CARD, fg=ACCENT, font=("Consolas", 8, "bold"), width=18, anchor="w").pack(side="left")
            for m_lbl, m_attr in [("Grp1", "mask_grp1"), ("Grp2", "mask_grp2"), ("Aux", "mask_aux")]:
                val = getattr(st, m_attr)
                ttk.Button(
                    srow, text=f"{m_lbl}: 0x{val:04X}", width=14,
                    command=lambda s_ref=st, a=m_attr, ml=m_lbl, lb=label: ZoneMask16Dialog(
                        self, f"8W0 72C1 {lb} — {ml}", getattr(s_ref, a), ds72c2_8w0,
                        lambda m, sr=s_ref, at=a: (setattr(sr, at, m), self._build_72c1_tab(), self.refresh_status_and_hex()),
                    ),
                ).pack(side="left", padx=2)
            for l_t, attr in [("Bri%:", "brightness"), ("Fade:", "fade_time")]:
                tk.Label(srow, text=l_t, bg=BG_CARD, fg=FG_DIM).pack(side="left", padx=(8, 2))
                sv = tk.IntVar(value=getattr(st, attr))
                self._spinbox(srow, from_=0, to=255, textvariable=sv, width=4).pack(side="left")
                sv.trace_add("write", lambda *_, sr=st, a=attr, v=sv: self._set_attr_int(sr, a, v))

        # Multi-step blocks: Welcome, Leaving, Scenario B
        for bname, rgb_list, steps in [
            ("Welcome Scenario (0x01A..0x037)", ds.welcome_rgb, ds.welcome_steps),
            ("Leaving Scenario (0x038..0x055)", ds.leaving_rgb, ds.leaving_steps),
            ("Scenario B (0x09C..0x0B9)", ds.scen_b_rgb, ds.scen_b_steps),
        ]:
            bframe = tk.LabelFrame(sf.inner, text=bname, bg=BG_PANEL, fg=ACCENT, font=("Segoe UI", 9, "bold"), padx=8, pady=6)
            bframe.pack(fill="x", padx=8, pady=4)
            hdr = tk.Frame(bframe, bg=BG_PANEL)
            hdr.pack(fill="x", pady=(0, 4))
            for idx, r_lbl in enumerate(["RGB Grp 1:", "RGB Grp 2:"]):
                tk.Label(hdr, text=r_lbl, bg=BG_PANEL, fg=FG).pack(side="left", padx=(0, 3))
                c = rgb_list[idx]
                hx = f"#{c[0]:02X}{c[1]:02X}{c[2]:02X}"
                tk.Button(
                    hdr, text=hx[1:], bg=hx, fg=self._contrast(*c), font=("Consolas", 8, "bold"),
                    width=8, relief="solid", bd=1,
                    command=lambda rl=rgb_list, ii=idx, bn=bname: self._pick_rgb_tuple(
                        rl[ii], f"{bn} RGB {ii+1}",
                        lambda col, rlist=rl, j=ii: (rlist.__setitem__(j, col), self._build_72c1_tab(), self.refresh_status_and_hex()),
                    ),
                ).pack(side="left", padx=(0, 12))
            for si, st in enumerate(steps):
                add_step_row(bframe, f"Step {si}", st)

        # Single-step blocks: 4 Doors + Scenario A
        dbox = tk.LabelFrame(
            sf.inner, text="Per-Door Open (0x056..0x08D) & Scenario A (0x08E..0x09B) — Inline RGB + 16-Zone Masks",
            bg=BG_PANEL, fg=ACCENT, font=("Segoe UI", 9, "bold"), padx=8, pady=6,
        )
        dbox.pack(fill="x", padx=8, pady=4)
        for blk in ds.door_open + [ds.scen_a]:
            row = tk.Frame(dbox, bg=BG_CARD, padx=6, pady=3, highlightbackground=BORDER, highlightthickness=1)
            row.pack(fill="x", pady=2)
            tk.Label(row, text=blk.name, bg=BG_CARD, fg=FG, font=("Segoe UI", 8, "bold"), width=22, anchor="w").pack(side="left")
            for attr, tag in [("rgb1", "RGB1"), ("rgb2", "RGB2")]:
                c = getattr(blk, attr)
                hx = f"#{c[0]:02X}{c[1]:02X}{c[2]:02X}"
                tk.Button(
                    row, text=f"{tag}:{hx[1:]}", bg=hx, fg=self._contrast(*c), font=("Consolas", 7, "bold"),
                    width=11, relief="solid", bd=1,
                    command=lambda b_ref=blk, a=attr: self._pick_rgb_tuple(
                        getattr(b_ref, a), f"8W0 72C1 {b_ref.name} {a}",
                        lambda col, br=b_ref, at=a: (setattr(br, at, col), self._build_72c1_tab(), self.refresh_status_and_hex()),
                    ),
                ).pack(side="left", padx=2)
            st = blk.step
            for m_lbl, m_attr in [("G1", "mask_grp1"), ("G2", "mask_grp2"), ("Aux", "mask_aux")]:
                val = getattr(st, m_attr)
                ttk.Button(
                    row, text=f"{m_lbl}:{val:04X}", width=10,
                    command=lambda s_ref=st, a=m_attr, bn=blk.name, ml=m_lbl: ZoneMask16Dialog(
                        self, f"8W0 72C1 {bn} — {ml}", getattr(s_ref, a), ds72c2_8w0,
                        lambda m, sr=s_ref, at=a: (setattr(sr, at, m), self._build_72c1_tab(), self.refresh_status_and_hex()),
                    ),
                ).pack(side="left", padx=2)
            for l_t, attr in [("Bri:", "brightness"), ("Fade:", "fade_time")]:
                tk.Label(row, text=l_t, bg=BG_CARD, fg=FG_DIM).pack(side="left", padx=(4, 1))
                sv = tk.IntVar(value=getattr(st, attr))
                self._spinbox(row, from_=0, to=255, textvariable=sv, width=3).pack(side="left")
                sv.trace_add("write", lambda *_, sr=st, a=attr, v=sv: self._set_attr_int(sr, a, v))

    def _build_72c3_8w0_tab(self):
        ds: Dataset72C3_8W0 = self.ds72c3
        ds72c2_8w0 = self.ds72c2 if is_8w0_dataset(self.ds72c2) else None
        sf = ScrollableFrame(self.tab_72c3, bg=BG)
        sf.pack(fill="both", expand=True)

        def extra_hdr(bar):
            tk.Label(bar, text="Dyn Bri % (0x042..0x043):", bg=BG_PANEL, fg=FG).pack(side="left")
            for idx in range(2):
                bv = tk.IntVar(value=ds.dynamic_brightness[idx])
                self._spinbox(bar, from_=0, to=100, textvariable=bv, width=4).pack(side="left", padx=2)
                bv.trace_add("write", lambda *_, i=idx, v=bv: self._set_list_int(ds.dynamic_brightness, i, v, 100))

        self._build_header_bar_8w0(sf.inner, ds, "0x8A (138 B) @ RAM 0x40019C0C", extra_hdr)

        # Climate Enable Mask (0x041)
        cm_box = tk.LabelFrame(
            sf.inner, text="Climate & Seat Dynamic Feedback Enable Mask (0x041)",
            bg=BG_PANEL, fg=ACCENT, font=("Segoe UI", 9, "bold"), padx=8, pady=6,
        )
        cm_box.pack(fill="x", padx=8, pady=4)
        for bit, label in [
            (0, "Bit 0: Temp Warmer (+)"), (1, "Bit 1: Temp Colder (-)"),
            (2, "Bit 2: Seat Heating (SHZ)"), (3, "Bit 3: Seat Ventilation (SLFT)"),
            (4, "Bit 4: Aux / Steering / Air"),
        ]:
            bv = tk.BooleanVar(value=bool((ds.climate_enable_mask >> bit) & 1))
            tk.Checkbutton(
                cm_box, text=label, variable=bv, bg=BG_PANEL, fg=FG, selectcolor=BG_INPUT,
                activebackground=BG_PANEL, command=lambda b=bit, v=bv: self._toggle_72c3_climate(b, v.get()),
            ).pack(side="left", padx=8)

        # 10 Direct Event RGBs (0x06C..0x089)
        ev_box = tk.LabelFrame(
            sf.inner, text="10 Direct Event RGBs (0x06C..0x089 — Click Swatch to Edit; [7]=Red Heating, [8]=Blue Cooling)",
            bg=BG_PANEL, fg=ACCENT, font=("Segoe UI", 9, "bold"), padx=8, pady=6,
        )
        ev_box.pack(fill="x", padx=8, pady=4)
        ev_names = ["Ev0", "Ev1", "Ev2", "Ev3", "Ev4", "Ev5", "Ev6", "Ev7 (Warm/SHZ)", "Ev8 (Cold/SLFT)", "Ev9"]
        for i in range(10):
            r, g, b = ds.event_rgb[i]
            hx = f"#{r:02X}{g:02X}{b:02X}"
            tk.Button(
                ev_box, text=f"{ev_names[i]}\n{hx[1:]}", bg=hx, fg=self._contrast(r, g, b),
                font=("Consolas", 7, "bold"), width=12, relief="solid", bd=1,
                command=lambda idx=i: self._pick_rgb_tuple(
                    ds.event_rgb[idx], f"8W0 72C3 EventRGB[{idx}]",
                    lambda c, ii=idx: (ds.event_rgb.__setitem__(ii, c), self._build_72c3_tab(), self.refresh_status_and_hex()),
                ),
            ).grid(row=i // 5, column=i % 5, padx=4, pady=3, sticky="ew")
            ev_box.columnconfigure(i % 5, weight=1)

        # 4 Climate Zone Feedback Maps (0x044..0x06B)
        cz_box = tk.LabelFrame(
            sf.inner, text="4 Climate Feedback Zones (0x044..0x06B — 16-Zone Mask + 4× [LIN ID, Type])",
            bg=BG_PANEL, fg=ACCENT, font=("Segoe UI", 9, "bold"), padx=8, pady=6,
        )
        cz_box.pack(fill="x", padx=8, pady=4)
        for zi, zname in enumerate(["Front Left (0x044)", "Front Right (0x04E)", "Rear Left (0x058)", "Rear Right (0x062)"]):
            cz = ds.climate_zones[zi]
            row = tk.Frame(cz_box, bg=BG_CARD, padx=6, pady=3, highlightbackground=BORDER, highlightthickness=1)
            row.pack(fill="x", pady=2)
            tk.Label(row, text=zname, bg=BG_CARD, fg=FG, font=("Consolas", 8, "bold"), width=20, anchor="w").pack(side="left")
            ttk.Button(
                row, text=f"Mask: {zonemask16_summary(cz.zone_mask)}", width=26,
                command=lambda c_ref=cz, zn=zname: ZoneMask16Dialog(
                    self, f"8W0 72C3 Climate Zone — {zn}", c_ref.zone_mask, ds72c2_8w0,
                    lambda m, cr=c_ref: (setattr(cr, "zone_mask", m), self._build_72c3_tab(), self.refresh_status_and_hex()),
                ),
            ).pack(side="left", padx=4)
            pairs_txt = "  ".join(f"({a:02X},{b:02X})" for a, b in cz.pairs)
            tk.Label(row, text=f"Pairs (LIN,Type): {pairs_txt}", bg=BG_CARD, fg=FG_DIM, font=("Consolas", 8)).pack(side="left", padx=8)

        # Ramp LUT (11 x u16 @ 0x010..0x025) & Decay Curve (10 B @ 0x026..0x02F)
        lut_box = tk.LabelFrame(
            sf.inner, text="11-Point Linear Ramp LUT (0x010..0x025) & 10-Point Decay Curve % (0x026..0x02F)",
            bg=BG_PANEL, fg=ACCENT, font=("Segoe UI", 9, "bold"), padx=8, pady=6,
        )
        lut_box.pack(fill="x", padx=8, pady=4)
        r_row = tk.Frame(lut_box, bg=BG_PANEL)
        r_row.pack(fill="x", pady=2)
        tk.Label(r_row, text="Ramp (u16):", bg=BG_PANEL, fg=FG, width=12, anchor="w").pack(side="left")
        for i in range(11):
            rv = tk.IntVar(value=ds.ramp_lut[i])
            self._spinbox(r_row, from_=0, to=65535, textvariable=rv, width=6).pack(side="left", padx=2)
            rv.trace_add("write", lambda *_, idx=i, v=rv: self._set_list_int(ds.ramp_lut, idx, v, 65535))

        d_row = tk.Frame(lut_box, bg=BG_PANEL)
        d_row.pack(fill="x", pady=2)
        tk.Label(d_row, text="Decay (%):", bg=BG_PANEL, fg=FG, width=12, anchor="w").pack(side="left")
        for i in range(10):
            dv = tk.IntVar(value=ds.decay_curve[i])
            self._spinbox(d_row, from_=0, to=100, textvariable=dv, width=4).pack(side="left", padx=2)
            dv.trace_add("write", lambda *_, idx=i, v=dv: self._set_list_int(ds.decay_curve, idx, v, 100))

    # -----------------------------------------------------------------------
    # TAB 5: Hex & Diff Inspector
    # -----------------------------------------------------------------------

    def _build_hex_tab(self):
        top = tk.Frame(self.tab_hex, bg=BG_PANEL, padx=8, pady=6, highlightbackground=BORDER, highlightthickness=1)
        top.pack(fill="x", padx=8, pady=(6, 0))
        tk.Label(top, text="Inspect Dataset:", bg=BG_PANEL, fg=FG).pack(side="left", padx=(0, 6))
        self.hex_adr_var = tk.StringVar(value="72C0")
        cb = ttk.Combobox(top, textvariable=self.hex_adr_var, values=["72C0", "72C1", "72C2", "72C3"], state="readonly", width=8)
        cb.pack(side="left")
        cb.bind("<<ComboboxSelected>>", lambda _e: self._update_hex_view())

        self.hex_diff_lbl = tk.Label(top, text="", bg=BG_PANEL, fg=AMBER, font=("Consolas", 9, "bold"))
        self.hex_diff_lbl.pack(side="right")

        self.hex_text = tk.Text(
            self.tab_hex, bg=BG_PANEL, fg=FG, font=("Consolas", 10),
            relief="solid", bd=1, padx=10, pady=8, state="disabled",
        )
        self.hex_text.tag_configure("diff", foreground=AMBER, background=DIFF_BG)
        self.hex_text.tag_configure("offset", foreground=ACCENT)
        self.hex_text.tag_configure("crc", foreground=GREEN)
        self.hex_text.pack(fill="both", expand=True, padx=8, pady=6)

    def _update_hex_view(self):
        if not hasattr(self, "hex_text"):
            return
        adr = self.hex_adr_var.get()
        ds = getattr(self, f"ds{adr.lower()}", None)
        self.hex_text.config(state="normal")
        self.hex_text.delete("1.0", "end")
        if ds is None:
            self.hex_text.insert("end", f"Dataset {adr} is not loaded.\n")
            self.hex_diff_lbl.config(text="")
            self.hex_text.config(state="disabled")
            return
        raw = ds.to_bytes()
        orig = ds.original_raw if len(ds.original_raw) == len(raw) else raw
        diffs = sum(1 for a, b in zip(raw, orig) if a != b)
        self.hex_diff_lbl.config(
            text=f"{len(raw)} bytes | Changed bytes vs loaded: {diffs} | CRC32: 0x{int.from_bytes(raw[-4:], 'big'):08X}"
        )

        for off in range(0, len(raw), 16):
            self.hex_text.insert("end", f"{off:04X}: ", "offset")
            chunk = raw[off:off+16]
            for i, b in enumerate(chunk):
                pos = off + i
                tag = "crc" if pos >= len(raw) - 4 else ("diff" if b != orig[pos] else "")
                self.hex_text.insert("end", f"{b:02X} ", tag)
            if len(chunk) < 16:
                self.hex_text.insert("end", "   " * (16 - len(chunk)))
            asc = "".join(chr(b) if 32 <= b <= 126 else "." for b in chunk)
            self.hex_text.insert("end", f" |{asc}|\n")
        self.hex_text.config(state="disabled")


if __name__ == "__main__":
    app = AlmEditorApp()
    app.mainloop()
