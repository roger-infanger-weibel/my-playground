#!/usr/bin/env python3
"""
sender.py -- Kodiert ein ZIP-File in eine Folge von QR-Code-Frames (MP4),
die du am Bildschirm im Vollbild abspielst und mit der Kamera abfilmst.

Pipeline:  zip-bytes -> chunks -> pakete (+CRC32) -> base45 -> QR -> MP4

Gegenstueck: receiver.py dekodiert das abgefilmte MP4 zurueck ins ZIP.

Abhaengigkeiten:
    pip install "qrcode[pil]" opencv-python numpy base45
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


def render_frame(qr_img, canvas_px, label):
    canvas = np.full((canvas_px, canvas_px), 255, np.uint8)
    target = int(canvas_px * 0.92)               # QR fuellt ~92%, Rest = Ruhezone
    qr_r = cv2.resize(qr_img, (target, target), interpolation=cv2.INTER_NEAREST)
    off = (canvas_px - target) // 2
    canvas[off:off + target, off:off + target] = qr_r
    frame = cv2.cvtColor(canvas, cv2.COLOR_GRAY2BGR)
    cv2.rectangle(frame, (2, 2), (canvas_px - 3, canvas_px - 3), (0, 0, 0), 2)  # Rahmen fuers Kamera-Framing
    cv2.putText(frame, label, (10, canvas_px - 10),
                cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 0), 2, cv2.LINE_AA)
    return frame


def main():
    ap = argparse.ArgumentParser(description="ZIP -> QR-Video (MP4)")
    ap.add_argument("zipfile", help="Eingabe-ZIP")
    ap.add_argument("-o", "--out", default="transfer.mp4")
    ap.add_argument("--chunk", type=int, default=256, help="Rohdaten-Bytes pro QR (kleiner = robuster)")
    ap.add_argument("--ecc", choices=list("LMQH"), default="M", help="QR-Fehlerkorrektur")
    ap.add_argument("--qr-fps", type=float, default=5.0, help="QR-Codes pro Sekunde")
    ap.add_argument("--video-fps", type=int, default=30, help="Video-Framerate")
    ap.add_argument("--loops", type=int, default=3, help="Wie oft die ganze Sequenz wiederholt wird")
    ap.add_argument("--canvas", type=int, default=1000, help="Frame-Groesse px (quadratisch)")
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

    repeat = max(1, round(args.video_fps / args.qr_fps))
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(args.out, fourcc, args.video_fps, (args.canvas, args.canvas))
    if not writer.isOpened():
        sys.exit("VideoWriter konnte nicht geoeffnet werden (Codec/OpenCV-Backend).")

    written = 0
    for loop in range(args.loops):
        for j, t in enumerate(seq_texts):
            frame = render_frame(cache[t], args.canvas, f"loop {loop+1}/{args.loops}  pkt {j+1}/{len(seq_texts)}")
            for _ in range(repeat):
                writer.write(frame)
                written += 1
    writer.release()

    dur = written / args.video_fps
    print(f"Fertig: {args.out}")
    print(f"  Datei-Groesse : {len(data)} Bytes, {total} Chunks a {args.chunk} B")
    print(f"  QR-Version    : bis {max_ver} (ECC {args.ecc}) - je hoeher, desto dichter/schwerer abfilmbar")
    print(f"  Video         : {written} Frames, {args.video_fps} fps, {args.loops} Loops -> {dur:.1f}s")
    print(f"  Jeder QR wird {repeat} Frames lang angezeigt (~{args.qr_fps} QR/s).")
    if max_ver > 18:
        print("  Tipp: QR-Version >18 ist beim Abfilmen heikel - --chunk verkleinern.")


if __name__ == "__main__":
    main()
