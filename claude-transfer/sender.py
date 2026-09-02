#!/usr/bin/env python3
"""
sender.py -- Kodiert ein ZIP-File in eine Folge von QR-Code-Frames.

Zwei Modi:
  --live   QR-Codes direkt im Vollbild anzeigen und live abfilmen (kein MP4).
  (Default) ein MP4 schreiben, das du abspielst und abfilmst.

Pipeline:  zip-bytes -> chunks -> pakete (+CRC32) -> base45 -> QR

Gegenstueck: receiver.py dekodiert das abgefilmte Video zurueck ins ZIP.

Abhaengigkeiten:
    pip install "qrcode[pil]" opencv-python numpy base45
    (fuer --live wird opencv-python mit GUI benoetigt, NICHT opencv-python-headless)
"""

import argparse
import hashlib
import os
import struct
import sys
import zlib

import numpy as np
import cv2
import qrcode
import base45

# ---- Protokoll (identisch in sender.py und receiver.py) --------------------
MAGIC = b"OZ"          # "optical zip"
VERSION = 1
TYPE_META = 0
TYPE_DATA = 1
# Paket-Layout (vor base45):
#   magic[2] | version[1] | type[1] | body... | crc32[4]  (alle Ints big-endian)
# META-body: total_chunks[4] | chunk_size[4] | file_size[8] | sha256[32] | name_len[2] | name
# DATA-body: seq[4] | data[...]


def build_packet(ptype: int, body: bytes) -> str:
    raw = MAGIC + bytes([VERSION, ptype]) + body
    raw += struct.pack(">I", zlib.crc32(raw) & 0xFFFFFFFF)
    return base45.b45encode(raw).decode("ascii")


def meta_body(total, chunk_size, file_size, sha, name: bytes) -> bytes:
    return (struct.pack(">IIQ", total, chunk_size, file_size)
            + sha + struct.pack(">H", len(name)) + name)


def data_body(seq: int, data: bytes) -> bytes:
    return struct.pack(">I", seq) + data


# ---- QR-Rendering ----------------------------------------------------------
_ECC = {"L": qrcode.constants.ERROR_CORRECT_L,
        "M": qrcode.constants.ERROR_CORRECT_M,
        "Q": qrcode.constants.ERROR_CORRECT_Q,
        "H": qrcode.constants.ERROR_CORRECT_H}


def make_qr(text: str, ecc: str):
    qr = qrcode.QRCode(error_correction=_ECC[ecc], box_size=10, border=4)
    qr.add_data(text)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white").convert("L")
    return np.array(img, dtype=np.uint8), qr.version


def render_frame(qr_img, w, h, label):
    # Weisser Hintergrund WxH mit quadratischem QR zentriert -> keine Verzerrung
    # auf 16:9-Bildschirmen. Fuer MP4: w == h.
    canvas = np.full((h, w), 255, np.uint8)
    side = int(min(w, h) * 0.92)                 # QR fuellt ~92%, Rest = Ruhezone
    qr_r = cv2.resize(qr_img, (side, side), interpolation=cv2.INTER_NEAREST)
    ox, oy = (w - side) // 2, (h - side) // 2
    canvas[oy:oy + side, ox:ox + side] = qr_r
    frame = cv2.cvtColor(canvas, cv2.COLOR_GRAY2BGR)
    cv2.rectangle(frame, (2, 2), (w - 3, h - 3), (0, 0, 0), 2)  # Rahmen fuers Kamera-Framing
    cv2.putText(frame, label, (10, h - 12),
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 2, cv2.LINE_AA)
    return frame


def screen_size(default=(1920, 1080)):
    """Aktuelle Bildschirmaufloesung (Windows via ctypes), sonst Default."""
    try:
        import ctypes
        u = ctypes.windll.user32
        u.SetProcessDPIAware()
        return int(u.GetSystemMetrics(0)), int(u.GetSystemMetrics(1))
    except Exception:
        return default


def render_pause(w, h, text):
    # Neutral-grauer Marker zwischen den Durchlaeufen: klare Loop-Grenze,
    # und die Kamera kann Belichtung/Fokus kurz stabilisieren.
    frame = np.full((h, w, 3), 200, np.uint8)
    cv2.putText(frame, text, (int(w * 0.06), h // 2),
                cv2.FONT_HERSHEY_SIMPLEX, min(w, h) / 700, (40, 40, 40), 3, cv2.LINE_AA)
    return frame


def main():
    ap = argparse.ArgumentParser(description="ZIP -> QR-Codes: live anzeigen (--live) oder als MP4")
    ap.add_argument("zipfile", help="Eingabe-ZIP")
    ap.add_argument("--live", action="store_true", help="QR-Codes direkt im Vollbild anzeigen (kein MP4)")
    ap.add_argument("-o", "--out", default="transfer.mp4", help="MP4-Ausgabe (nur ohne --live)")
    ap.add_argument("--chunk", type=int, default=384, help="Rohdaten-Bytes pro QR (groesser = mehr Daten/Code, aber dichter/langsamer)")
    ap.add_argument("--ecc", choices=list("LMQH"), default="M", help="QR-Fehlerkorrektur")
    ap.add_argument("--qr-fps", type=float, default=3.0, help="QR-Codes pro Sekunde (kleiner = robuster)")
    ap.add_argument("--loop-pause", type=float, default=0.7, help="Pause in Sekunden nach jedem Durchlauf")
    ap.add_argument("--video-fps", type=int, default=30, help="Video-Framerate (nur MP4)")
    ap.add_argument("--loops", type=int, default=3, help="Sequenz-Wiederholungen (nur MP4; live laeuft bis ESC)")
    ap.add_argument("--canvas", type=int, default=1000, help="Frame-Groesse px, quadratisch (nur MP4)")
    ap.add_argument("--screen", default=None, help="Live-Aufloesung 'BxH', z.B. 1920x1080 (Default: automatisch)")
    ap.add_argument("--meta-interval", type=int, default=12, help="META-Paket alle N Datenpakete einstreuen")
    args = ap.parse_args()

    data = open(args.zipfile, "rb").read()
    if data[:2] != b"PK":
        print("Warnung: Datei beginnt nicht mit 'PK' - ist das wirklich ein ZIP?", file=sys.stderr)
    sha = hashlib.sha256(data).digest()
    name = os.path.basename(args.zipfile).encode("utf-8")
    chunks = [data[i:i + args.chunk] for i in range(0, len(data), args.chunk)] or [b""]
    total = len(chunks)

    # Paket-Texte bauen (META am Anfang + periodisch eingestreut).
    # META traegt die kritischen Infos -> immer hoechste Fehlerkorrektur.
    META_ECC = "H"
    meta_text = build_packet(TYPE_META, meta_body(total, args.chunk, len(data), sha, name))
    seq_texts = [meta_text]
    for i, c in enumerate(chunks):
        seq_texts.append(build_packet(TYPE_DATA, data_body(i, c)))
        if (i + 1) % args.meta_interval == 0:
            seq_texts.append(meta_text)

    # QR-Bilder cachen (META wiederholt sich)
    cache, max_ver = {}, 0
    for t in seq_texts:
        if t not in cache:
            ecc = META_ECC if t == meta_text else args.ecc
            try:
                cache[t], ver = make_qr(t, ecc)
            except qrcode.exceptions.DataOverflowError:
                sys.exit(f"QR-Ueberlauf: --chunk {args.chunk} ist zu gross. Reduziere ihn.")
            max_ver = max(max_ver, ver)

    print(f"Datei: {len(data)} Bytes, {total} Chunks a {args.chunk} B, {len(seq_texts)} Pakete/Durchlauf")
    print(f"QR-Version: bis {max_ver} (Daten ECC {args.ecc}, META ECC {META_ECC})")
    if max_ver > 18:
        print("Tipp: QR-Version >18 ist beim Abfilmen heikel - --chunk verkleinern.")

    if args.live:
        run_live(seq_texts, cache, args)
    else:
        write_mp4(seq_texts, cache, args)


def write_mp4(seq_texts, cache, args):
    repeat = max(1, round(args.video_fps / args.qr_fps))
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(args.out, fourcc, args.video_fps, (args.canvas, args.canvas))
    if not writer.isOpened():
        sys.exit("VideoWriter konnte nicht geoeffnet werden (Codec/OpenCV-Backend).")
    written = 0
    pause_frames = max(0, int(round(args.loop_pause * args.video_fps)))
    for loop in range(args.loops):
        for j, t in enumerate(seq_texts):
            frame = render_frame(cache[t], args.canvas, args.canvas,
                                 f"loop {loop+1}/{args.loops}  pkt {j+1}/{len(seq_texts)}")
            for _ in range(repeat):
                writer.write(frame)
                written += 1
        if loop < args.loops - 1 and pause_frames:
            pause = render_pause(args.canvas, args.canvas, f"-- Pause (Loop {loop+1} fertig) --")
            for _ in range(pause_frames):
                writer.write(pause)
                written += 1
    writer.release()
    dur = written / args.video_fps
    print(f"Fertig: {args.out}")
    print(f"  Video: {written} Frames, {args.video_fps} fps, {args.loops} Loops -> {dur:.1f}s")
    print(f"  Jeder QR wird {repeat} Frames lang angezeigt (~{args.qr_fps} QR/s).")


def run_live(seq_texts, cache, args):
    if args.screen:
        w, h = (int(x) for x in args.screen.lower().split("x"))
    else:
        w, h = screen_size()
    delay = max(1, int(round(1000.0 / args.qr_fps)))   # ms pro QR

    # Anzeige-Frames vorab rendern (pro eindeutigem Paket-Text)
    frames = {}
    for j, t in enumerate(seq_texts):
        if t not in frames:
            frames[t] = render_frame(cache[t], w, h, "")   # Label pro Anzeige neu

    win = "OZ-Sender"
    cv2.namedWindow(win, cv2.WINDOW_NORMAL)
    cv2.setWindowProperty(win, cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN)

    # Intro-Karte: Zeit, Aufnahme zu starten und den Bildschirm anzupeilen
    intro = np.full((h, w, 3), 255, np.uint8)
    cv2.putText(intro, "QR-TRANSFER", (int(w*0.08), int(h*0.42)),
                cv2.FONT_HERSHEY_SIMPLEX, min(w, h)/500, (0, 0, 0), 3, cv2.LINE_AA)
    cv2.putText(intro, "Aufnahme starten - ESC beendet", (int(w*0.08), int(h*0.55)),
                cv2.FONT_HERSHEY_SIMPLEX, min(w, h)/900, (0, 0, 0), 2, cv2.LINE_AA)
    cv2.imshow(win, intro)
    if (cv2.waitKey(2500) & 0xFF) == 27:
        cv2.destroyAllWindows()
        return

    print(f"Live: {w}x{h}, ~{args.qr_fps} QR/s ({delay} ms). ESC/q zum Beenden.")
    pause_ms = max(0, int(round(args.loop_pause * 1000)))
    loop = 0
    stop = False
    while not stop:
        loop += 1
        for j, t in enumerate(seq_texts):
            f = frames[t].copy()
            cv2.putText(f, f"loop {loop}  pkt {j+1}/{len(seq_texts)}  [ESC=Ende]",
                        (10, h - 12), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 2, cv2.LINE_AA)
            cv2.imshow(win, f)
            key = cv2.waitKey(delay) & 0xFF
            if key in (27, ord("q")):
                stop = True
                break
            # Fenster ueber X geschlossen?
            if cv2.getWindowProperty(win, cv2.WND_PROP_VISIBLE) < 1:
                stop = True
                break
        if not stop and pause_ms:
            cv2.imshow(win, render_pause(w, h, f"-- Pause (Loop {loop} fertig) --"))
            if (cv2.waitKey(pause_ms) & 0xFF) in (27, ord("q")):
                stop = True
    cv2.destroyAllWindows()
    print(f"Beendet nach {loop} Durchlauf/Durchlaeufen.")


if __name__ == "__main__":
    main()
