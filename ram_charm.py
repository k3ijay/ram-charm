#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
RAM Charm: メモリ使用率に合わせて動きが変わるデスクトップマスコット (ram-charm)
Python + tkinter + Pillow + psutil
"""

from __future__ import annotations

import base64
import ctypes
import io
import math
import os
import random
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# 1. 不足ライブラリの自動インストール & Tcl/Tk パス補正
# ---------------------------------------------------------------------------
def _fix_tcl_tk_env() -> None:
    """macOS / uv 等の環境で TCL_LIBRARY / TK_LIBRARY が未設定の場合に自動補完する。"""
    if "TCL_LIBRARY" in os.environ and "TK_LIBRARY" in os.environ:
        return

    search_roots = [
        os.path.join(sys.base_prefix, "lib"),
        os.path.join(sys.prefix, "lib"),
        os.path.expanduser("~/.local/share/uv/python"),
        "/opt/homebrew/opt/tcl-tk/lib",
        "/usr/local/opt/tcl-tk/lib",
    ]
    for root_dir in search_roots:
        if not os.path.isdir(root_dir):
            continue
        try:
            for dirpath, dirnames, _ in os.walk(root_dir):
                # 深すぎる走査を防止
                rel_depth = dirpath[len(root_dir):].count(os.sep)
                if rel_depth >= 4:
                    dirnames.clear()
                    continue
                for d in dirnames:
                    full_p = os.path.join(dirpath, d)
                    if "TCL_LIBRARY" not in os.environ and d.startswith("tcl8."):
                        if os.path.exists(os.path.join(full_p, "init.tcl")):
                            os.environ["TCL_LIBRARY"] = full_p
                    if "TK_LIBRARY" not in os.environ and d.startswith("tk8."):
                        if os.path.exists(os.path.join(full_p, "tk.tcl")):
                            os.environ["TK_LIBRARY"] = full_p
                if "TCL_LIBRARY" in os.environ and "TK_LIBRARY" in os.environ:
                    return
        except Exception:
            pass


_fix_tcl_tk_env()


def ensure_dependencies() -> None:
    """Pillow と psutil が未導入なら自動でインストールする。"""
    required = [
        ("PIL", "pillow"),
        ("psutil", "psutil"),
    ]
    missing: List[str] = []
    for import_name, pip_name in required:
        try:
            __import__(import_name)
        except ImportError:
            missing.append(pip_name)

    if not missing:
        return

    cmd = [sys.executable, "-m", "pip", "install", "--quiet", *missing]
    kwargs = {}
    if sys.platform == "win32":
        # Windows で黒いコンソール画面を出さない
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)

    try:
        subprocess.check_call(cmd, **kwargs)
    except Exception as exc:
        # tkinter でエラー通知を試みる
        try:
            import tkinter as _tk
            from tkinter import messagebox as _mb

            _r = _tk.Tk()
            _r.withdraw()
            _mb.showerror(
                "依存ライブラリのインストール失敗",
                f"必要なライブラリ ({', '.join(missing)}) の自動インストールに失敗しました。\n\n"
                f"コマンドプロンプト等で次を実行してください:\n"
                f"pip install {' '.join(missing)}\n\n詳細: {exc}",
            )
            _r.destroy()
        except Exception:
            print(f"Failed to install {missing}: {exc}", file=sys.stderr)
        sys.exit(1)


ensure_dependencies()

import psutil  # noqa: E402
import tkinter as tk  # noqa: E402
from tkinter import messagebox  # noqa: E402
from PIL import Image, ImageDraw, ImageTk  # noqa: E402


# ---------------------------------------------------------------------------
# 2. Windows 高DPI対応
# ---------------------------------------------------------------------------
def enable_windows_dpi_awareness() -> None:
    """Windows の高DPI環境でもドット絵や文字がぼやけないようにする。"""
    if sys.platform != "win32":
        return
    try:
        # Per-Monitor DPI Aware (Windows 8.1+)
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            # System DPI Aware
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            try:
                # Vista / 7 fallback
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# 3. 画面作業領域（タスクバー / Dock を除いた領域）の取得
# ---------------------------------------------------------------------------
def get_work_area(root: tk.Tk) -> Tuple[int, int, int, int]:
    """(left, top, right, bottom) の作業領域を返す。"""
    if sys.platform == "win32":
        try:
            class RECT(ctypes.Structure):
                _fields_ = [
                    ("left", ctypes.c_long),
                    ("top", ctypes.c_long),
                    ("right", ctypes.c_long),
                    ("bottom", ctypes.c_long),
                ]

            rect = RECT()
            SPI_GETWORKAREA = 0x0030
            if ctypes.windll.user32.SystemParametersInfoW(
                SPI_GETWORKAREA, 0, ctypes.byref(rect), 0
            ):
                return (rect.left, rect.top, rect.right, rect.bottom)
        except Exception:
            pass

    screen_w = root.winfo_screenwidth()
    screen_h = root.winfo_screenheight()

    if sys.platform == "darwin":
        # macOS: NSScreen.mainScreen.visibleFrame から Dock / メニューバーを除外した領域を取得
        try:
            from ctypes import c_double, c_void_p, Structure

            class NSPoint(Structure):
                _fields_ = [("x", c_double), ("y", c_double)]

            class NSSize(Structure):
                _fields_ = [("width", c_double), ("height", c_double)]

            class NSRect(Structure):
                _fields_ = [("origin", NSPoint), ("size", NSSize)]

            objc = ctypes.cdll.LoadLibrary("/usr/lib/libobjc.A.dylib")
            ctypes.cdll.LoadLibrary(
                "/System/Library/Frameworks/AppKit.framework/AppKit"
            )

            objc.objc_getClass.restype = c_void_p
            objc.objc_getClass.argtypes = [ctypes.c_char_p]
            objc.sel_registerName.restype = c_void_p
            objc.sel_registerName.argtypes = [ctypes.c_char_p]

            msg_send_ptr = objc.objc_msgSend
            msg_send_ptr.restype = c_void_p
            msg_send_ptr.argtypes = [c_void_p, c_void_p]

            ns_screen_cls = objc.objc_getClass(b"NSScreen")
            main_screen = msg_send_ptr(
                ns_screen_cls, objc.sel_registerName(b"mainScreen")
            )
            if main_screen:
                # arm64 は objc_msgSend、x86_64 の構造体戻り値は objc_msgSend_stret
                import platform

                if platform.machine() == "arm64":
                    msg_rect = ctypes.CFUNCTYPE(NSRect, c_void_p, c_void_p)(
                        ("objc_msgSend", objc)
                    )
                else:
                    msg_rect = ctypes.CFUNCTYPE(NSRect, c_void_p, c_void_p)(
                        ("objc_msgSend_stret", objc)
                    )
                full = msg_rect(main_screen, objc.sel_registerName(b"frame"))
                vis = msg_rect(main_screen, objc.sel_registerName(b"visibleFrame"))
                # Cocoa 座標系（左下原点）→ Tk 座標系（左上原点）
                left = int(round(vis.origin.x))
                right = int(round(vis.origin.x + vis.size.width))
                top = int(round(full.size.height - (vis.origin.y + vis.size.height)))
                bottom = int(round(full.size.height - vis.origin.y))
                if right > left and bottom > top:
                    return (left, top, right, bottom)
        except Exception:
            pass
        # フォールバック（メニューバー 25px, Dock 70px 想定）
        return (0, 25, screen_w, screen_h - 70)

    # Linux 等フォールバック
    return (0, 0, screen_w, screen_h - 48)


# ---------------------------------------------------------------------------
# 4. 白黒ハチワレ猫ドット絵 (28x22) の定義と4倍拡大レンダラ
# ---------------------------------------------------------------------------
SPRITE_W = 28
SPRITE_H = 22
PIXEL_SCALE = 4  # 28x22 -> 112x88

PALETTE: Dict[str, Tuple[int, int, int, int]] = {
    ".": (0, 0, 0, 0),          # 透明
    "K": (22, 24, 30, 255),     # 輪郭の黒
    "B": (46, 50, 62, 255),     # ハチワレの黒毛
    "D": (32, 35, 44, 255),     # 黒毛の濃い影
    "W": (253, 253, 255, 255),  # 白毛
    "S": (218, 224, 236, 255),  # 白毛の影
    "P": (255, 162, 186, 255),  # 耳の中・鼻・ほっぺ（ピンク）
    "R": (240, 110, 138, 255),  # 濃いピンク（口・ほっぺ）
    "E": (18, 20, 26, 255),     # 目の黒
    "H": (255, 255, 255, 255),  # 目のハイライト
    "Y": (248, 202, 76, 255),   # 目の琥珀色
    "C": (72, 198, 255, 255),   # 汗しぶき（水色）
    "L": (212, 244, 255, 255),  # 汗ハイライト
    "Z": (96, 138, 224, 255),   # 眠り Zzz
}

# すべてのフレームは必ず 28文字 x 22行（右向きベース）
# --- 歩く (walk: 4コマ) ---
WALK_FRAMES: List[List[str]] = [
    # Frame 0: 足を前後に広げて一歩
    [
        "............................",
        "...............KK.....KK....",
        "..............KBBK...KBBK...",
        "..............KBPK...KPBK...",
        "..............KBBKKKKKBBK...",
        ".............KBBBBBWBBBBBK..",
        ".............KBBBBWWWBBBBK..",
        ".............KBBBEWWWEBBBK..",
        "....KK.......KBBWYWWWYWWBK..",
        "...KWWK......KWWPWWPWWPWWK..",
        "...KBBK.......KWWWWWWWWWK...",
        "....KBBK..KKKKKWWWWWSSWWK...",
        ".....KBBKKBBBBBBBBWWWWKK....",
        "......KKBBBBBBBBBBBWWWK.....",
        ".......KBBBBBBBBBBWWWWK.....",
        ".......KBBDDDDWWWWWWWWK.....",
        ".......KSSWWWWWWWWWWSSK.....",
        ".......KWWKKK....KKKWWK.....",
        "......KWWK..........KWWK....",
        "......KWWK..........KWWK....",
        ".......KK............KK.....",
        "............................",
    ],
    # Frame 1: 中間ステップ（やや沈み込み）
    [
        "............................",
        "............................",
        "...............KK.....KK....",
        "..............KBBK...KBBK...",
        "..............KBPK...KPBK...",
        "..............KBBKKKKKBBK...",
        ".............KBBBBBWBBBBBK..",
        ".....KK......KBBBBWWWBBBBK..",
        "....KWWK.....KBBBEWWWEBBBK..",
        "....KBBK.....KBBWYWWWYWWBK..",
        ".....KBBK....KWWPWWPWWPWWK..",
        ".....KBBK.....KWWWWWWWWWK...",
        "......KBBKKKKKKWWWWWSSWWK...",
        ".......KBBBBBBBBBBWWWWKK....",
        ".......KBBBBBBBBBBBWWWK.....",
        ".......KBBDDDDWWWWWWWWK.....",
        ".......KSSWWWWWWWWWWSSK.....",
        "........KKKWWKKKKWWKKK......",
        "..........KWWK..KWWK........",
        "..........KWWK..KWWK........",
        "...........KK....KK.........",
        "............................",
    ],
    # Frame 2: 反対側の足で一歩
    [
        "............................",
        "...............KK.....KK....",
        "..............KBBK...KBBK...",
        "..............KBPK...KPBK...",
        "..............KBBKKKKKBBK...",
        ".............KBBBBBWBBBBBK..",
        ".............KBBBBWWWBBBBK..",
        ".............KBBBEWWWEBBBK..",
        ".....KK......KBBWYWWWYWWBK..",
        "....KWWK.....KWWPWWPWWPWWK..",
        "...KBBK.......KWWWWWWWWWK...",
        "...KBBK...KKKKKWWWWWSSWWK...",
        "....KBBBKKBBBBBBBBWWWWKK....",
        ".....KKBBBBBBBBBBBBWWWK.....",
        ".......KBBBBBBBBBBWWWWK.....",
        ".......KBBDDDDWWWWWWWWK.....",
        ".......KSSWWWWWWWWWWSSK.....",
        "........KKWWKK..KKWWKK......",
        "........KWWK......KWWK......",
        "........KWWK......KWWK......",
        ".........KK........KK.......",
        "............................",
    ],
    # Frame 3: 交差ステップ（しっぽ揺れ）
    [
        "............................",
        "............................",
        "...............KK.....KK....",
        "..............KBBK...KBBK...",
        "..............KBPK...KPBK...",
        "..............KBBKKKKKBBK...",
        ".............KBBBBBWBBBBBK..",
        "....KK.......KBBBBWWWBBBBK..",
        "...KWWK......KBBBEWWWEBBBK..",
        "...KBBK......KBBWYWWWYWWBK..",
        "....KBBK.....KWWPWWPWWPWWK..",
        ".....KBBK.....KWWWWWWWWWK...",
        "......KBBKKKKKKWWWWWSSWWK...",
        ".......KBBBBBBBBBBWWWWKK....",
        ".......KBBBBBBBBBBBWWWK.....",
        ".......KBBDDDDWWWWWWWWK.....",
        ".......KSSWWWWWWWWWWSSK.....",
        ".........KKWWKKKKWWKK.......",
        ".........KWWK....KWWK.......",
        ".........KWWK....KWWK.......",
        "..........KK......KK........",
        "............................",
    ],
]

# --- 座って休む (sit: 4コマ・まばたき＆しっぽゆらゆら＆Zz) ---
SIT_FRAMES: List[List[str]] = [
    # Frame 0: パチクリお座り
    [
        "............................",
        "............................",
        "............KK.....KK.......",
        "...........KBBK...KBBK......",
        "...........KBPK...KPBK......",
        "...........KBBKKKKKBBK......",
        "..........KBBBBBWBBBBBK.....",
        "..........KBBBBWWWBBBBK.....",
        "..........KBBBEWWWEBBBK.....",
        "..........KBBWYWWWYWWBK.....",
        "..........KWWPWWPWWPWWK.....",
        "...........KWWWWWWWWWK......",
        ".........KKKBBWWWWWWWK......",
        "........KBBBBBBWWWWWWK......",
        ".......KBBBBBBBWWWWWWK......",
        "......KBBBBBBBBWWWWWWK......",
        "....KKKBBBBDDDWWWWWWWK......",
        "...KWWKBBBBDDDWWWWWWWK......",
        "...KBBKKSSSWWWWWWWWWK.......",
        "....KKKK..KWWK..KWWK........",
        "...........KK....KK.........",
        "............................",
    ],
    # Frame 1: うとうと目を閉じる＋しっぽ揺れ＋Z
    [
        ".......................ZZZ..",
        ".........................Z..",
        "............KK.....KK...Z...",
        "...........KBBK...KBBK.ZZZ..",
        "...........KBPK...KPBK......",
        "...........KBBKKKKKBBK......",
        "..........KBBBBBWBBBBBK.....",
        "..........KBBBBWWWBBBBK.....",
        "..........KBBBBWWWBBBBK.....",
        "..........KBBKKWWWKKWBK.....",
        "..........KWWPWWPWWPWWK.....",
        "...........KWWWWWWWWWK......",
        ".........KKKBBWWWWWWWK......",
        "........KBBBBBBWWWWWWK......",
        ".......KBBBBBBBWWWWWWK......",
        ".....KKBBBBBBBBWWWWWWK......",
        "....KWWKBBBDDDWWWWWWWK......",
        "....KBBKBBBDDDWWWWWWWK......",
        ".....KKKSSSWWWWWWWWWK.......",
        "..........KWWK..KWWK........",
        "...........KK....KK.........",
        "............................",
    ],
    # Frame 2: うとうと深め（少し沈む）＋Zが上がる
    [
        "........................ZZZ.",
        "..........................Z.",
        ".........................Z..",
        "............KK.....KK...ZZZ.",
        "...........KBBK...KBBK......",
        "...........KBPK...KPBK......",
        "...........KBBKKKKKBBK......",
        "..........KBBBBBWBBBBBK.....",
        "..........KBBBBWWWBBBBK.....",
        "..........KBBBBWWWBBBBK.....",
        "..........KBBKKWWWKKWBK.....",
        "..........KWWPWWPWWPWWK.....",
        "...........KWWWWWWWWWK......",
        "........KKKKBBWWWWWWWK......",
        ".......KBBBBBBBWWWWWWK......",
        "......KBBBBBBBBWWWWWWK......",
        "...KKKBBBBBBDDWWWWWWWK......",
        "..KWWKBBBBBBDDWWWWWWWK......",
        "...KKKBBSSSWWWWWWWWWK.......",
        ".....KKKK.KWWK..KWWK........",
        "...........KK....KK.........",
        "............................",
    ],
    # Frame 3: パチクリ戻る＋しっぽ揺れ
    [
        "............................",
        "............................",
        "............KK.....KK.......",
        "...........KBBK...KBBK......",
        "...........KBPK...KPBK......",
        "...........KBBKKKKKBBK......",
        "..........KBBBBBWBBBBBK.....",
        "..........KBBBBWWWBBBBK.....",
        "..........KBBBEWWWEBBBK.....",
        "..........KBBWYWWWYWWBK.....",
        "..........KWWPWWPWWPWWK.....",
        "...........KWWWWWWWWWK......",
        ".........KKKBBWWWWWWWK......",
        "........KBBBBBBWWWWWWK......",
        ".......KBBBBBBBWWWWWWK......",
        "......KBBBBBBBBWWWWWWK......",
        ".....KKBBBBDDDWWWWWWWK......",
        "....KWWKBBBDDDWWWWWWWK......",
        "....KBBKSSSWWWWWWWWWK.......",
        ".....KKK..KWWK..KWWK........",
        "...........KK....KK.........",
        "............................",
    ],
]

# --- 走る (run: 4コマ・前傾姿勢で軽快なギャロップ) ---
RUN_FRAMES: List[List[str]] = [
    # Frame 0: 体をぐっと伸ばしてジャンプ
    [
        "............................",
        "............................",
        "................KK....KK....",
        "...............KBBK..KBBK...",
        "...............KBPK..KPBK...",
        "...............KBBKKKKBBK...",
        "..............KBBBBWBBBBBK..",
        "..............KBBBWWWBBBBK..",
        "..............KBBBEWWEBBBK..",
        "..............KBBWYWWYWWBK..",
        "..KK..........KWWPWWPWWPWK..",
        ".KWWKKKK.......KWWWWWWWWK...",
        ".KBBBBBBKKKKKKKWWWWWSSWWK...",
        "..KKKKBBBBBBBBBBBBBWWWKK....",
        "......KBBBBBBBBBBBBWWWK.....",
        "......KBBDDDDWWWWWWWWK......",
        ".....KWWKSSWWWWWWWWKKWWK....",
        "....KWWK..KKKKKKKK..KWWK....",
        "....KK...............KK.....",
        "............................",
        "............................",
        "............................",
    ],
    # Frame 1: 着地して前足を引く
    [
        "............................",
        "............................",
        "............................",
        "................KK....KK....",
        "...............KBBK..KBBK...",
        "...............KBPK..KPBK...",
        "...............KBBKKKKBBK...",
        "..............KBBBBWBBBBBK..",
        "..............KBBBWWWBBBBK..",
        "...KK.........KBBBEWWEBBBK..",
        "..KWWK........KBBWYWWYWWBK..",
        "..KBBKK.......KWWPWWPWWPWK..",
        "...KBBBKKKKKKK.KWWWWWWWWK...",
        "....KKBBBBBBBBBBWWWWSSWWK...",
        "......KBBBBBBBBBBBBWWWKK....",
        "......KBBDDDDWWWWWWWWWK.....",
        ".......KSSWWWWWWWWWWSSK.....",
        ".......KWWKKK....KKKWWK.....",
        "........KWWK......KWWK......",
        ".........KK........KK.......",
        "............................",
        "............................",
    ],
    # Frame 2: 体をギュッと丸めてバネを溜める
    [
        "............................",
        "............................",
        "............................",
        "...............KK....KK.....",
        "..............KBBK..KBBK....",
        "..............KBPK..KPBK....",
        "..............KBBKKKKBBK....",
        ".............KBBBBWBBBBBK...",
        ".............KBBBWWWBBBBK...",
        ".............KBBBEWWEBBBK...",
        "....KK.......KBBWYWWYWWBK...",
        "...KWWK......KWWPWWPWWPWK...",
        "...KBBK.......KWWWWWWWWK....",
        "....KBBKKKKKKKWWWWWSSWWK....",
        ".....KBBBBBBBBBBBBWWWKK.....",
        ".....KBBBBBBBBBBBBWWWK......",
        "......KBBDDDDWWWWWWWWK......",
        ".......KSSWWWWWWWWSSK.......",
        ".........KWWKKWWKK..........",
        ".........KWWKKWWK...........",
        "..........KK..KK............",
        "............................",
    ],
    # Frame 3: 後ろ足で力強く蹴り出し
    [
        "............................",
        "............................",
        "................KK....KK....",
        "...............KBBK..KBBK...",
        "...............KBPK..KPBK...",
        "...............KBBKKKKBBK...",
        "..............KBBBBWBBBBBK..",
        "..............KBBBWWWBBBBK..",
        "..............KBBBEWWEBBBK..",
        "..............KBBWYWWYWWBK..",
        "...KK.........KWWPWWPWWPWK..",
        "..KWWKKK.......KWWWWWWWWK...",
        "..KBBBBBKKKKKKKWWWWWSSWWK...",
        "...KKKBBBBBBBBBBBBBWWWKK....",
        "......KBBBBBBBBBBBBWWWK.....",
        "......KBBDDDDWWWWWWWWK......",
        "......KSSWWWWWWWWWWWSSK.....",
        ".....KWWK..KKKKKK..KWWK.....",
        "....KWWK............KWWK....",
        ".....KK..............KK.....",
        "............................",
        "............................",
    ],
]

# --- 猛ダッシュ (dash: 4コマ・汗しぶき＆必死の表情＆フルスピード) ---
DASH_FRAMES: List[List[str]] = [
    # Frame 0: 汗しぶき大＋フル伸展＋スピード線
    [
        "..........CL................",
        ".........CLL................",
        ".............KK......KK.....",
        "............KBBK....KBBK....",
        "......CL....KBPK....KPBK....",
        "......CC....KBBKKKKKKBBK....",
        "...........KBBBBBWBBBBBBK...",
        "...........KBBBBWWWBBBBBK...",
        "...........KBBBEWWWEBBBBK...",
        "...........KBBWHWWWHWWBBK...",
        ".KK........KWWPWWPWWPWWWK...",
        "KWWKKKK.....KWWWWWRRWWWK....",
        "KBBBBBBKKKKKKWWWWWWWSSWK....",
        ".KKKKBBBBBBBBBBBBBBWWKK.....",
        ".....KBBBBBBBBBBBBBWWK......",
        "....KWWBBDDDDWWWWWWWWKWWK...",
        "...KWWK.KSSWWWWWWWWKKKWWK...",
        "...KK....KKKKKKKKKK...KK....",
        ".CC.........................",
        "............................",
        "............................",
        "............................",
    ],
    # Frame 1: 汗しぶき飛び散り＋高速ステップ
    [
        "........CL..................",
        "........CC....CL............",
        "..............CC............",
        "..............KK......KK....",
        ".............KBBK....KBBK...",
        ".............KBPK....KPBK...",
        ".............KBBKKKKKKBBK...",
        "............KBBBBBWBBBBBBK..",
        "............KBBBBWWWBBBBBK..",
        "............KBBBEWWWEBBBBK..",
        "..KK........KBBWHWWWHWWBBK..",
        ".KWWKKKK....KWWPWWPWWPWWWK..",
        ".KBBBBBBKKKK.KWWWWWRRWWWK...",
        "..KKKKBBBBBBBBBBWWWWSSWWK...",
        "......KBBBBBBBBBBBBWWKK.....",
        "......KBBDDDDWWWWWWWWWK.....",
        ".......KSSWWWWWWWWWWSSK.....",
        "......KWWKKK......KKKWWK....",
        ".......KK............KK.....",
        "....CC......................",
        "............................",
        "............................",
    ],
    # Frame 2: 汗しぶき上＋ギュッと収縮
    [
        "............CL..............",
        "...........CLL..............",
        "............................",
        ".............KK......KK.....",
        ".......CL...KBBK....KBBK....",
        ".......CC...KBPK....KPBK....",
        "............KBBKKKKKKBBK....",
        "...........KBBBBBWBBBBBBK...",
        "...........KBBBBWWWBBBBBK...",
        "...........KBBBEWWWEBBBBK...",
        "...KK......KBBWHWWWHWWBBK...",
        "..KWWKK....KWWPWWPWWPWWWK...",
        "..KBBBBKKKK.KWWWWWRRWWWK....",
        "...KKKBBBBBBBBBBWWWWSSWK....",
        ".....KBBBBBBBBBBBBBWWKK.....",
        ".....KBBDDDDWWWWWWWWWK......",
        "......KSSWWWWWWWWWWSSK......",
        ".........KWWKK..KKWWK.......",
        "..........KK......KK........",
        "..CC........................",
        "............................",
        "............................",
    ],
    # Frame 3: 汗しぶき後ろ＋全力キック
    [
        ".........CL.................",
        ".........CC.................",
        "..............KK......KK....",
        ".....CL......KBBK....KBBK...",
        ".....CC......KBPK....KPBK...",
        ".............KBBKKKKKKBBK...",
        "............KBBBBBWBBBBBBK..",
        "............KBBBBWWWBBBBBK..",
        "............KBBBEWWWEBBBBK..",
        "............KBBWHWWWHWWBBK..",
        ".KK.........KWWPWWPWWPWWWK..",
        "KWWKKKKK.....KWWWWWRRWWWK...",
        "KBBBBBBBKKKKKWWWWWWWSSWWK...",
        ".KKKKKBBBBBBBBBBBBBWWKK.....",
        "......KBBBBBBBBBBBBWWK......",
        ".....KWWBBDDDDWWWWWWWWK.....",
        "....KWWKKSSWWWWWWWWWKKWWK...",
        ".....KK...KKKKKKKKK..KWWK...",
        "......................KK....",
        "...CC.......................",
        "............................",
        "............................",
    ],
]

# --- クリック反応 (happy: 2コマ・ニッコリ＆ぴょん！) ---
HAPPY_FRAMES: List[List[str]] = [
    [
        "............................",
        "...............KK.....KK....",
        "..............KBBK...KBBK...",
        "..............KBPK...KPBK...",
        "..............KBBKKKKKBBK...",
        ".............KBBBBBWBBBBBK..",
        ".............KBBBBWWWBBBBK..",
        ".............KBBBWWWWBBBBK..",
        "....KK.......KBBKKWWWKKBBK..",
        "...KWWK......KWWPWWPWWPWWK..",
        "...KBBK.......KWWWWRWWWWK...",
        "....KBBK..KKKKKWWWWWSSWWK...",
        ".....KBBKKBBBBBBBBWWWWKK....",
        "......KKBBBBBBBBBBBWWWK.....",
        ".......KBBBBBBBBBBWWWWK.....",
        ".......KBBDDDDWWWWWWWWK.....",
        ".......KSSWWWWWWWWWWSSK.....",
        ".......KWWKK......KKWWK.....",
        "........KK..........KK......",
        "............................",
        "............................",
        "............................",
    ],
    [
        "...............KK.....KK....",
        "..............KBBK...KBBK...",
        "..............KBPK...KPBK...",
        "..............KBBKKKKKBBK...",
        ".............KBBBBBWBBBBBK..",
        ".............KBBBBWWWBBBBK..",
        ".............KBBBWWWWBBBBK..",
        ".....KK......KBBKKWWWKKBBK..",
        "....KWWK.....KWWPWWPWWPWWK..",
        "....KBBK......KWWWWRWWWWK...",
        ".....KBBK.KKKKKWWWWWSSWWK...",
        "......KBBKBBBBBBBBWWWWKK....",
        ".......KBBBBBBBBBBBWWWK.....",
        ".......KBBBBBBBBBBWWWWK.....",
        ".......KBBDDDDWWWWWWWWK.....",
        ".......KSSWWWWWWWWWWSSK.....",
        "......KWWKK........KKWWK....",
        ".......KK............KK.....",
        "............................",
        "............................",
        "............................",
        "............................",
    ],
]


def render_sprite_image(grid: List[str], scale: int = PIXEL_SCALE) -> Image.Image:
    """28x22 のドット絵定義から RGBA 画像を生成し、NEAREST でくっきり拡大する。"""
    assert len(grid) == SPRITE_H, f"Expected {SPRITE_H} rows, got {len(grid)}"
    img = Image.new("RGBA", (SPRITE_W, SPRITE_H), (0, 0, 0, 0))
    pixels = img.load()
    for y, row in enumerate(grid):
        assert len(row) == SPRITE_W, f"Row {y}: expected {SPRITE_W} cols, got {len(row)}"
        for x, ch in enumerate(row):
            pixels[x, y] = PALETTE[ch]
    return img.resize(
        (SPRITE_W * scale, SPRITE_H * scale),
        resample=Image.Resampling.NEAREST,
    )


def pil_to_tk_photo(img: Image.Image) -> tk.PhotoImage:
    """PIL.Image を Tk の PhotoImage に変換する（ImageTk 失敗時は PNG 経由でフォールバック）。"""
    try:
        return ImageTk.PhotoImage(img)
    except Exception:
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return tk.PhotoImage(data=base64.b64encode(buf.getvalue()))


def _build_vector_rects(
    grid: List[str], scale: int = PIXEL_SCALE, flip_x: bool = False
) -> List[Tuple[int, int, int, int, str]]:
    """ドット絵グリッドを横方向ランレングス結合した (x1, y1, x2, y2, hex_color) リストに変換する。"""
    rects: List[Tuple[int, int, int, int, str]] = []
    for y, row in enumerate(grid):
        src_row = row[::-1] if flip_x else row
        x = 0
        n = len(src_row)
        while x < n:
            ch = src_row[x]
            if ch == ".":
                x += 1
                continue
            x_end = x + 1
            while x_end < n and src_row[x_end] == ch:
                x_end += 1
            r, g, b, _ = PALETTE[ch]
            color = f"#{r:02x}{g:02x}{b:02x}"
            rects.append(
                (x * scale, y * scale, x_end * scale, (y + 1) * scale, color)
            )
            x = x_end
    return rects


class CatSpriteBank:
    """全アニメーション・左右向きの PhotoImage とベクター矩形データを事前生成してキャッシュする。"""

    def __init__(self, scale: int = PIXEL_SCALE) -> None:
        self.scale = scale
        self.width = SPRITE_W * scale
        self.height = SPRITE_H * scale
        self.pil_frames: Dict[str, Dict[int, List[Image.Image]]] = {}
        self.tk_frames: Dict[str, Dict[int, List[tk.PhotoImage]]] = {}
        self.vector_frames: Dict[str, Dict[int, List[List[Tuple[int, int, int, int, str]]]]] = {}
        self.imagetk_ok: bool = False

        raw_sets = {
            "walk": WALK_FRAMES,
            "sit": SIT_FRAMES,
            "run": RUN_FRAMES,
            "dash": DASH_FRAMES,
            "happy": HAPPY_FRAMES,
        }
        for name, grids in raw_sets.items():
            right_imgs = [render_sprite_image(g, scale=scale) for g in grids]
            left_imgs = [
                im.transpose(Image.Transpose.FLIP_LEFT_RIGHT) for im in right_imgs
            ]
            self.pil_frames[name] = {1: right_imgs, -1: left_imgs}
            self.vector_frames[name] = {
                1: [_build_vector_rects(g, scale=scale, flip_x=False) for g in grids],
                -1: [_build_vector_rects(g, scale=scale, flip_x=True) for g in grids],
            }

    def build_tk_images(self) -> None:
        """Tk ルート生成後に PhotoImage を作成する。"""
        imagetk_works = True
        for name, dirs in self.pil_frames.items():
            dir_map: Dict[int, List[tk.PhotoImage]] = {}
            for direction, imgs in dirs.items():
                photos: List[tk.PhotoImage] = []
                for im in imgs:
                    try:
                        photos.append(ImageTk.PhotoImage(im))
                    except Exception:
                        imagetk_works = False
                        photos.append(pil_to_tk_photo(im))
                dir_map[direction] = photos
            self.tk_frames[name] = dir_map
        self.imagetk_ok = imagetk_works

    def get(self, anim: str, direction: int, frame_idx: int) -> tk.PhotoImage:
        frames = self.tk_frames[anim][1 if direction >= 0 else -1]
        return frames[frame_idx % len(frames)]

    def get_vector(
        self, anim: str, direction: int, frame_idx: int
    ) -> List[Tuple[int, int, int, int, str]]:
        frames = self.vector_frames[anim][1 if direction >= 0 else -1]
        return frames[frame_idx % len(frames)]


# ---------------------------------------------------------------------------
# 5. メモリ監視スレッド（1秒ごとに別スレッドで取得）
# ---------------------------------------------------------------------------
@dataclass
class MemorySnapshot:
    percent: float
    used_gb: float
    total_gb: float
    available_gb: float


class MemoryMonitor:
    """1秒ごとに別スレッドで psutil.virtual_memory() を取得する。"""

    def __init__(self, interval_sec: float = 1.0) -> None:
        self._interval = interval_sec
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._override_percent: Optional[float] = None
        self._snapshot = self._read_now()
        self._thread = threading.Thread(
            target=self._run, name="MemoryMonitorThread", daemon=True
        )

    @staticmethod
    def _read_now() -> MemorySnapshot:
        vm = psutil.virtual_memory()
        gb = 1024 ** 3
        # macOS では vm.used が wired+active のみとなり vm.percent と乖離するため、
        # OS 共通で vm.percent と整合する (total - available) を使用量として扱う
        effective_used = max(0, vm.total - vm.available)
        return MemorySnapshot(
            percent=float(vm.percent),
            used_gb=effective_used / gb,
            total_gb=vm.total / gb,
            available_gb=vm.available / gb,
        )

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread.is_alive() and threading.current_thread() != self._thread:
            try:
                self._thread.join(timeout=0.5)
            except Exception:
                pass

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                snap = self._read_now()
                with self._lock:
                    self._snapshot = snap
            except Exception:
                break
            self._stop_event.wait(self._interval)

    def set_override(self, percent: Optional[float]) -> None:
        with self._lock:
            self._override_percent = percent

    def get_override(self) -> Optional[float]:
        with self._lock:
            return self._override_percent

    def get_state(self) -> Tuple[float, MemorySnapshot, bool]:
        """(有効な使用率%, 実メモリスナップショット, テストモード中か) を返す。"""
        with self._lock:
            snap = self._snapshot
            ov = self._override_percent
        if ov is not None:
            return (ov, snap, True)
        return (snap.percent, snap, False)


# ---------------------------------------------------------------------------
# 6. デスクトップ常駐ネコ アプリケーション本体
# ---------------------------------------------------------------------------
# Windows 透過キー色（ドット絵や吹き出しでは一切使わない色）
WIN_TRANSPARENT_KEY = "#010203"

CLICK_REACTIONS = [
    "ニャーン♪",
    "ゴロゴロ…♡",
    "なでて〜ニャ！",
    "ミャオ♪",
    "おやつまだニャ？",
    "ぷにぷに…♪",
    "ニャッ！",
]


def choose_font_family() -> str:
    if sys.platform == "win32":
        return "Meiryo UI"
    if sys.platform == "darwin":
        return "Hiragino Sans"
    return "Sans"


class RAMCharmApp:
    """デスクトップ常駐マスコット本体 (RAMCharm)。"""

    # ウィンドウ内のレイアウト定数
    WIN_W = 264
    BUBBLE_AREA_H = 58
    CAT_W = SPRITE_W * PIXEL_SCALE  # 112
    CAT_H = SPRITE_H * PIXEL_SCALE  # 88
    WIN_H = BUBBLE_AREA_H + CAT_H + 8  # 154

    # 猫画像内の足底位置（22行中 20行目が足底なので下端から約 1〜2 ドット分調整）
    FOOT_OFFSET_FROM_WIN_BOTTOM = 12
    SNAP_DISTANCE_PX = 64

    def __init__(self, initial_test_percent: Optional[float] = None) -> None:
        self.root = tk.Tk()
        self.root.title("RAM Charm")

        self.is_win = sys.platform == "win32"
        self.is_mac = sys.platform == "darwin"

        # 枠なし・常に最前面・背景透明
        self.root.overrideredirect(True)
        self.root.wm_attributes("-topmost", True)

        if self.is_win:
            self.bg_color = WIN_TRANSPARENT_KEY
            self.root.config(bg=self.bg_color)
            self.root.wm_attributes("-transparentcolor", self.bg_color)
        elif self.is_mac:
            self.bg_color = "systemTransparent"
            self.root.wm_attributes("-transparent", True)
            self.root.config(bg=self.bg_color)
        else:
            self.bg_color = WIN_TRANSPARENT_KEY
            self.root.config(bg=self.bg_color)

        self.canvas = tk.Canvas(
            self.root,
            width=self.WIN_W,
            height=self.WIN_H,
            bg=self.bg_color,
            highlightthickness=0,
            bd=0,
        )
        self.canvas.pack(fill=tk.BOTH, expand=True)

        # スプライト生成（4倍拡大キャッシュ）
        self.sprites = CatSpriteBank(scale=PIXEL_SCALE)
        self.sprites.build_tk_images()

        # メモリ監視スレッド起動
        self.monitor = MemoryMonitor(interval_sec=1.0)
        if initial_test_percent is not None:
            self.monitor.set_override(initial_test_percent)
        self.monitor.start()

        # 作業領域と初期位置（タスクバーのすぐ上）
        self.work_left, self.work_top, self.work_right, self.work_bottom = (
            get_work_area(self.root)
        )
        self.ground_y = (
            self.work_bottom - self.WIN_H + self.FOOT_OFFSET_FROM_WIN_BOTTOM
        )

        # 座標・移動状態
        self.pos_x = float(
            self.work_left + max(20, (self.work_right - self.work_left - self.WIN_W) // 2)
        )
        self.pos_y = float(self.ground_y)
        self.direction = 1  # 1: 右向き, -1: 左向き

        # アニメーション＆行動状態
        self.mode = "chill"  # "chill" | "work" | "dash"
        self.is_resting = False
        self.rest_until = 0.0
        self.next_rest_at = time.monotonic() + random.uniform(4.5, 8.5)

        self.frame_idx = 0
        self.tick_count = 0
        self.last_work_area_check = time.monotonic()

        # 一時リアクション（クリック・ダブルクリック）
        self.temp_bubble_text: Optional[str] = None
        self.temp_bubble_kind: Optional[str] = None  # "click" | "detail"
        self.temp_bubble_until = 0.0
        self.happy_until = 0.0
        self.jump_ticks = 0

        # ドラッグ状態
        self.dragging = False
        self.drag_moved = False
        self.drag_start_root_x = 0
        self.drag_start_root_y = 0
        self.drag_win_start_x = 0.0
        self.drag_win_start_y = 0.0
        self._pending_click_after: Optional[str] = None
        self._tick_after_id: Optional[str] = None

        # Canvas 上のアイテム作成
        self.cat_center_x = self.WIN_W // 2
        self.cat_base_y = self.BUBBLE_AREA_H + self.CAT_H // 2
        self.cat_top_x = self.cat_center_x - self.CAT_W // 2
        self.cat_top_y = self.BUBBLE_AREA_H
        self.cat_tag = "cat_sprite"
        # macOS (systemTransparent) や ImageTk 不可環境ではベクター矩形描画を使用して確実に表示する
        self.use_vector_sprite = self.is_mac or not self.sprites.imagetk_ok
        self._last_cat_signature: Optional[Tuple[str, int, int]] = None
        self._last_cat_jump_y: float = 0.0

        if self.use_vector_sprite:
            self.cat_item = None
            self._draw_vector_cat("walk", 1, 0, 0.0)
        else:
            self.cat_item = self.canvas.create_image(
                self.cat_center_x,
                self.cat_base_y,
                image=self.sprites.get("walk", 1, 0),
            )

        self.font_family = choose_font_family()
        self.bubble_font = (self.font_family, 10, "bold")
        self.bubble_tag = "speech_bubble"
        self._last_bubble_signature: Optional[Tuple] = None

        # 右クリックメニュー構築
        self.mode_var = tk.StringVar(
            value="auto"
            if initial_test_percent is None
            else str(int(initial_test_percent))
        )
        self._build_context_menu()

        # イベントバインド
        self.canvas.bind("<ButtonPress-1>", self._on_left_press)
        self.canvas.bind("<B1-Motion>", self._on_left_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_left_release)
        self.canvas.bind("<Double-Button-1>", self._on_double_click)

        # 右クリック（Windows は Button-3、macOS は Button-2 または Control+左クリック）
        self.canvas.bind("<Button-3>", self._on_right_click)
        self.canvas.bind("<Button-2>", self._on_right_click)
        self.canvas.bind("<Control-Button-1>", self._on_right_click)

        self.root.protocol("WM_DELETE_WINDOW", self.quit_app)

        # 初期ウィンドウ位置適用
        self._apply_geometry()

        # 30fps ループ開始 (約 33ms)
        self._running = True
        self._tick()

    # --- 右クリックメニュー ---
    def _build_context_menu(self) -> None:
        self.menu = tk.Menu(self.root, tearoff=0)
        self.menu.add_command(
            label="現在のメモリ情報", command=self._show_memory_info_dialog
        )
        self.menu.add_separator()

        self.test_menu = tk.Menu(self.menu, tearoff=0)
        self.test_menu.add_radiobutton(
            label="自動（実際のメモリ使用率）",
            variable=self.mode_var,
            value="auto",
            command=lambda: self.set_test_mode(None),
        )
        self.test_menu.add_separator()
        self.test_menu.add_radiobutton(
            label="のんびりモード (35%)",
            variable=self.mode_var,
            value="35",
            command=lambda: self.set_test_mode(35.0),
        )
        self.test_menu.add_radiobutton(
            label="お仕事中モード (65%)",
            variable=self.mode_var,
            value="65",
            command=lambda: self.set_test_mode(65.0),
        )
        self.test_menu.add_radiobutton(
            label="猛ダッシュモード (85%)",
            variable=self.mode_var,
            value="85",
            command=lambda: self.set_test_mode(85.0),
        )
        self.menu.add_cascade(label="モード切替テスト", menu=self.test_menu)

        self.menu.add_command(
            label="タスクバーの上に戻る", command=self._snap_to_taskbar
        )
        self.menu.add_separator()
        # メニューのポップアップループを安全に抜けた直後に終了処理を走らせる
        self.menu.add_command(
            label="終了", command=lambda: self.root.after_idle(self.quit_app)
        )

    def set_test_mode(self, percent: Optional[float]) -> None:
        """モード切替テスト（None=自動、35/65/85=擬似再現）。"""
        self.monitor.set_override(percent)
        self.mode_var.set("auto" if percent is None else str(int(percent)))
        # 一時吹き出しやハッピー硬直をクリアして即座に状態を見せる
        self.temp_bubble_text = None
        self.temp_bubble_until = 0.0
        self.happy_until = 0.0
        self.is_resting = False
        self.next_rest_at = time.monotonic() + random.uniform(3.5, 7.0)
        self._last_bubble_signature = None

    def _on_right_click(self, event: tk.Event) -> None:
        try:
            self.menu.tk_popup(event.x_root, event.y_root)
        except Exception:
            pass
        finally:
            try:
                if self.menu.winfo_exists():
                    self.menu.grab_release()
            except Exception:
                pass

    def _show_memory_info_dialog(self) -> None:
        eff_pct, snap, is_test = self.monitor.get_state()
        if eff_pct < 50.0:
            mode_label = "のんびり（50%未満）"
        elif eff_pct < 75.0:
            mode_label = "お仕事中（50〜75%）"
        else:
            mode_label = "猛ダッシュ（75%以上）"

        test_note = (
            f"\n※ テストモード中（擬似 {eff_pct:.0f}%）" if is_test else ""
        )
        # 一時吹き出しでも表示
        self._set_temp_bubble(
            f"{snap.used_gb:.1f}GB / {snap.total_gb:.1f}GB ({snap.percent:.0f}%)",
            kind="detail",
            duration=3.5,
        )
        messagebox.showinfo(
            "現在のメモリ情報",
            f"現在の状態: {mode_label}{test_note}\n\n"
            f"実メモリ使用率: {snap.percent:.1f}%\n"
            f"使用中: {snap.used_gb:.2f} GB\n"
            f"合計:   {snap.total_gb:.2f} GB\n"
            f"空き:   {snap.available_gb:.2f} GB",
            parent=self.root,
        )

    def _snap_to_taskbar(self) -> None:
        self._refresh_work_area()
        self.pos_y = float(self.ground_y)
        self._apply_geometry()

    # --- マウス操作（ドラッグ / クリック / ダブルクリック） ---
    def _on_left_press(self, event: tk.Event) -> None:
        self.dragging = True
        self.drag_moved = False
        self.drag_start_root_x = event.x_root
        self.drag_start_root_y = event.y_root
        self.drag_win_start_x = self.pos_x
        self.drag_win_start_y = self.pos_y

    def _on_left_drag(self, event: tk.Event) -> None:
        if not self.dragging:
            return
        dx = event.x_root - self.drag_start_root_x
        dy = event.y_root - self.drag_start_root_y
        if abs(dx) >= 4 or abs(dy) >= 4:
            self.drag_moved = True
        if self.drag_moved:
            self.pos_x = self.drag_win_start_x + dx
            self.pos_y = self.drag_win_start_y + dy
            self._apply_geometry()

    def _on_left_release(self, event: tk.Event) -> None:
        if not self.dragging:
            return
        was_dragged = self.drag_moved
        self.dragging = False
        self.drag_moved = False

        if was_dragged:
            # 画面内に軽く収めつつ、タスクバー付近で離したら吸着
            self._refresh_work_area()
            min_x = self.work_left - 24
            max_x = self.work_right - self.WIN_W + 24
            self.pos_x = max(min_x, min(max_x, self.pos_x))

            if (
                abs(self.pos_y - self.ground_y) <= self.SNAP_DISTANCE_PX
                or self.pos_y > self.ground_y
            ):
                self.pos_y = float(self.ground_y)
                self.jump_ticks = 6  # 着地ポヨン演出
            else:
                self.pos_y = max(float(self.work_top), self.pos_y)
            self._apply_geometry()
        else:
            # クリック（ダブルクリックが来るか少しだけ待つ）
            if self._pending_click_after is not None:
                self.root.after_cancel(self._pending_click_after)
            self._pending_click_after = self.root.after(200, self._handle_single_click)

    def _handle_single_click(self) -> None:
        self._pending_click_after = None
        msg = random.choice(CLICK_REACTIONS)
        self._set_temp_bubble(msg, kind="click", duration=2.2)
        self.happy_until = time.monotonic() + 1.0
        self.jump_ticks = 10

    def _on_double_click(self, _event: tk.Event) -> None:
        if self._pending_click_after is not None:
            self.root.after_cancel(self._pending_click_after)
            self._pending_click_after = None
        _, snap, _ = self.monitor.get_state()
        self._set_temp_bubble(
            f"{snap.used_gb:.1f}GB / {snap.total_gb:.1f}GB",
            kind="detail",
            duration=3.5,
        )
        self.jump_ticks = 8

    def _set_temp_bubble(self, text: str, kind: str, duration: float) -> None:
        self.temp_bubble_text = text
        self.temp_bubble_kind = kind
        self.temp_bubble_until = time.monotonic() + duration
        self._last_bubble_signature = None

    # --- 座標・作業領域 ---
    def _refresh_work_area(self) -> None:
        self.work_left, self.work_top, self.work_right, self.work_bottom = (
            get_work_area(self.root)
        )
        self.ground_y = (
            self.work_bottom - self.WIN_H + self.FOOT_OFFSET_FROM_WIN_BOTTOM
        )

    def _apply_geometry(self) -> None:
        self.root.geometry(
            f"{self.WIN_W}x{self.WIN_H}+{int(round(self.pos_x))}+{int(round(self.pos_y))}"
        )

    # --- ベクタースプライト描画（macOS / ImageTk不可環境向け） ---
    def _draw_vector_cat(
        self, anim: str, direction: int, frame_idx: int, jump_offset: float
    ) -> None:
        sig = (anim, 1 if direction >= 0 else -1, frame_idx % 4)
        if sig == self._last_cat_signature:
            dy = jump_offset - self._last_cat_jump_y
            if abs(dy) > 0.01:
                self.canvas.move(self.cat_tag, 0, dy)
                self._last_cat_jump_y = jump_offset
            return

        self._last_cat_signature = sig
        self._last_cat_jump_y = jump_offset
        self.canvas.delete(self.cat_tag)

        ox = self.cat_top_x
        oy = self.cat_top_y + jump_offset
        for x1, y1, x2, y2, color in self.sprites.get_vector(
            anim, direction, frame_idx
        ):
            self.canvas.create_rectangle(
                ox + x1,
                oy + y1,
                ox + x2,
                oy + y2,
                fill=color,
                outline="",
                width=0,
                tags=self.cat_tag,
            )

    # --- 吹き出し描画 ---
    def _draw_rounded_rect(
        self,
        x1: float,
        y1: float,
        x2: float,
        y2: float,
        radius: float,
        fill: str,
        outline: str,
        width: int = 2,
    ) -> None:
        r = min(radius, (x2 - x1) / 2, (y2 - y1) / 2)
        points = [
            x1 + r, y1,
            x2 - r, y1,
            x2, y1,
            x2, y1 + r,
            x2, y2 - r,
            x2, y2,
            x2 - r, y2,
            x1 + r, y2,
            x1, y2,
            x1, y2 - r,
            x1, y1 + r,
            x1, y1,
        ]
        self.canvas.create_polygon(
            points,
            smooth=True,
            splinesteps=12,
            fill=fill,
            outline=outline,
            width=width,
            tags=self.bubble_tag,
        )

    def _update_bubble(
        self,
        text: str,
        fill: str,
        outline: str,
        text_color: str,
        shake: bool,
    ) -> None:
        dx = random.randint(-3, 3) if shake else 0
        dy = random.randint(-2, 2) if shake else 0
        sig = (text, fill, outline, text_color, dx, dy)
        if sig == self._last_bubble_signature:
            return
        self._last_bubble_signature = sig

        self.canvas.delete(self.bubble_tag)

        cx = self.WIN_W // 2 + dx
        cy = 24 + dy

        # テキストを一時作成して幅を測る
        text_id = self.canvas.create_text(
            cx,
            cy,
            text=text,
            font=self.bubble_font,
            fill=text_color,
            tags=self.bubble_tag,
        )
        bbox = self.canvas.bbox(text_id)
        if not bbox:
            return
        pad_x = 12
        pad_y = 6
        x1 = max(4, bbox[0] - pad_x)
        y1 = max(3, bbox[1] - pad_y)
        x2 = min(self.WIN_W - 4, bbox[2] + pad_x)
        y2 = min(self.BUBBLE_AREA_H - 10, bbox[3] + pad_y)

        # 本体（角丸）
        self._draw_rounded_rect(x1, y1, x2, y2, radius=10, fill=fill, outline=outline, width=2)

        # 吹き出しのしっぽ（三角形）
        tail_x = cx
        self.canvas.create_polygon(
            tail_x - 6, y2 - 1,
            tail_x + 6, y2 - 1,
            tail_x, y2 + 8,
            fill=fill,
            outline=outline,
            width=2,
            tags=self.bubble_tag,
        )
        # しっぽ付け根の枠線を塗りつぶして滑らかに接続
        self.canvas.create_line(
            tail_x - 5, y2,
            tail_x + 5, y2,
            fill=fill,
            width=3,
            tags=self.bubble_tag,
        )

        # テキストを最前面へ
        self.canvas.tag_raise(text_id)

    # --- メイン 30fps ループ ---
    def _tick(self) -> None:
        if not self._running:
            return
        try:
            if not self.root.winfo_exists():
                return
        except Exception:
            return

        now = time.monotonic()
        self.tick_count += 1

        # 5秒ごとに作業領域を再確認（タスクバーや解像度変更対応）
        if now - self.last_work_area_check >= 5.0:
            was_on_ground = abs(self.pos_y - self.ground_y) <= 2
            self._refresh_work_area()
            if was_on_ground and not self.dragging:
                self.pos_y = float(self.ground_y)
            self.last_work_area_check = now

        # メモリ状態取得
        eff_pct, _snap, _is_test = self.monitor.get_state()
        pct_int = int(round(eff_pct))

        if eff_pct < 50.0:
            self.mode = "chill"
        elif eff_pct < 75.0:
            self.mode = "work"
        else:
            self.mode = "dash"

        # 休憩制御（50%未満のときだけときどき座って休む）
        if self.mode == "chill" and not self.dragging:
            if self.is_resting:
                if now >= self.rest_until:
                    self.is_resting = False
                    self.next_rest_at = now + random.uniform(4.5, 9.0)
            else:
                if now >= self.next_rest_at:
                    self.is_resting = True
                    self.rest_until = now + random.uniform(2.5, 4.5)
        else:
            self.is_resting = False

        # アニメーション種別・速度決定
        if self.dragging:
            anim = "sit"
            speed = 0.0
            anim_div = 8
        elif now < self.happy_until:
            anim = "happy"
            speed = 0.0
            anim_div = 5
        elif self.mode == "chill":
            if self.is_resting:
                anim = "sit"
                speed = 0.0
                anim_div = 10
            else:
                anim = "walk"
                speed = 1.4
                anim_div = 6
        elif self.mode == "work":
            anim = "run"
            speed = 3.8
            anim_div = 3
        else:  # dash
            anim = "dash"
            speed = 7.4
            anim_div = 2

        # 横移動＆画面端での折り返し
        if not self.dragging and speed > 0.0:
            self.pos_x += self.direction * speed
            # 猫の体自体が画面端で折り返すようにマージン計算
            pad_side = (self.WIN_W - self.CAT_W) // 2
            min_x = self.work_left - pad_side + 4
            max_x = self.work_right - self.WIN_W + pad_side - 4
            if self.pos_x <= min_x:
                self.pos_x = float(min_x)
                self.direction = 1
            elif self.pos_x >= max_x:
                self.pos_x = float(max_x)
                self.direction = -1
            self._apply_geometry()

        # フレーム進行
        if self.tick_count % anim_div == 0:
            self.frame_idx = (self.frame_idx + 1) % 4

        # ジャンプ演出オフセット
        jump_offset = 0.0
        if self.jump_ticks > 0:
            # 放物線ジャンプ
            phase = self.jump_ticks / 10.0
            jump_offset = -math.sin(phase * math.pi) * 14.0
            self.jump_ticks -= 1

        # 猫スプライト更新
        if self.use_vector_sprite:
            self._draw_vector_cat(anim, self.direction, self.frame_idx, jump_offset)
        else:
            img = self.sprites.get(anim, self.direction, self.frame_idx)
            self.canvas.itemconfig(self.cat_item, image=img)
            self.canvas.coords(
                self.cat_item, self.cat_center_x, self.cat_base_y + jump_offset
            )

        # 吹き出し更新
        if self.temp_bubble_text and now < self.temp_bubble_until:
            if self.temp_bubble_kind == "detail":
                self._update_bubble(
                    self.temp_bubble_text,
                    fill="#EBF5FF",
                    outline="#2B74C7",
                    text_color="#123861",
                    shake=False,
                )
            else:
                self._update_bubble(
                    self.temp_bubble_text,
                    fill="#FFF0F6",
                    outline="#D64F84",
                    text_color="#591532",
                    shake=False,
                )
        else:
            self.temp_bubble_text = None
            if self.mode == "chill":
                self._update_bubble(
                    f"のんびり〜 ({pct_int}%)",
                    fill="#FFFFFF",
                    outline="#3A404D",
                    text_color="#22262E",
                    shake=False,
                )
            elif self.mode == "work":
                self._update_bubble(
                    f"お仕事中ニャ ({pct_int}%)",
                    fill="#FFE566",
                    outline="#996F00",
                    text_color="#3D2900",
                    shake=False,
                )
            else:
                self._update_bubble(
                    f"重いよ〜！({pct_int}%)",
                    fill="#FF4D4D",
                    outline="#8F1111",
                    text_color="#FFFFFF",
                    shake=True,
                )

        # 約 30fps (33ms) で次フレームを予約
        try:
            if self._running and self.root.winfo_exists():
                self._tick_after_id = self.root.after(33, self._tick)
        except Exception:
            self._tick_after_id = None

    def run(self) -> None:
        try:
            self.root.mainloop()
        except KeyboardInterrupt:
            pass
        finally:
            self.quit_app()

    def quit_app(self) -> None:
        if not self._running:
            return
        self._running = False

        # 予約済みタイマーを安全に解除
        if self._tick_after_id is not None:
            try:
                self.root.after_cancel(self._tick_after_id)
            except Exception:
                pass
            self._tick_after_id = None

        if self._pending_click_after is not None:
            try:
                self.root.after_cancel(self._pending_click_after)
            except Exception:
                pass
            self._pending_click_after = None

        # メモリ監視スレッド停止
        if hasattr(self, "monitor") and self.monitor is not None:
            self.monitor.stop()

        # イベントループ停止とウィンドウ破棄
        try:
            self.root.quit()
        except Exception:
            pass

        try:
            self.root.destroy()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# 7. スプライト目視確認用エクスポート（開発・確認用）
# ---------------------------------------------------------------------------
def export_sprite_sheet(output_path: str) -> None:
    """全アニメーションのドット絵を一覧PNGとして出力する。"""
    bank = CatSpriteBank(scale=PIXEL_SCALE)
    order = ["walk", "sit", "run", "dash", "happy"]
    cell_w = bank.width + 16
    cell_h = bank.height + 24
    cols = 4
    rows = len(order)
    sheet = Image.new("RGBA", (cols * cell_w + 16, rows * cell_h + 16), (235, 240, 248, 255))
    draw = ImageDraw.Draw(sheet)
    for r, name in enumerate(order):
        imgs = bank.pil_frames[name][1]
        for c, im in enumerate(imgs):
            x = 16 + c * cell_w
            y = 16 + r * cell_h
            # チェック柄背景で透明領域を分かりやすく
            draw.rectangle([x, y, x + bank.width - 1, y + bank.height - 1], fill=(210, 220, 232, 255), outline=(160, 170, 185, 255))
            sheet.paste(im, (x, y), im)
            draw.text((x + 2, y + bank.height + 4), f"{name}[{c}]", fill=(30, 35, 45, 255))
    sheet.save(output_path)


def main() -> None:
    if "--export-sprites" in sys.argv:
        idx = sys.argv.index("--export-sprites")
        out = sys.argv[idx + 1] if idx + 1 < len(sys.argv) else "sprites_preview.png"
        export_sprite_sheet(out)
        print(f"Exported sprite sheet to {out}")
        return

    initial_test: Optional[float] = None
    if "--test-percent" in sys.argv:
        idx = sys.argv.index("--test-percent")
        if idx + 1 < len(sys.argv):
            initial_test = float(sys.argv[idx + 1])

    enable_windows_dpi_awareness()
    app = RAMCharmApp(initial_test_percent=initial_test)

    # SIGINT (Ctrl+C) / SIGTERM で確実にクリーン終了
    try:
        import signal

        def _sig_handler(_sig: int, _frame: object) -> None:
            app.quit_app()

        signal.signal(signal.SIGINT, _sig_handler)
        signal.signal(signal.SIGTERM, _sig_handler)
    except Exception:
        pass

    try:
        app.run()
    except (KeyboardInterrupt, SystemExit):
        pass
    except Exception:
        app.quit_app()
        raise


# 後方互換性・別名用エイリアス
RAMCharm = RAMCharmApp
RamCharmApp = RAMCharmApp
MemoryCatApp = RAMCharmApp


if __name__ == "__main__":
    main()
