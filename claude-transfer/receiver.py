#!/usr/bin/env python3
"""
receiver.py -- Dekodiert ein abgefilmtes Screen-Recording (MP4) der QR-Frames
zurueck in das urspruengliche ZIP.

Das MP4 muss NICHT sauber sein: fehlende/verwackelte Frames werden toleriert,
solange jeder Chunk mindestens einmal lesbar auftaucht (darum die Loops im Sender).

Abhaengigkeiten:
    pip install opencv-python numpy base45
    optional (empfohlen, deutlich robuster):  pip install pyzbar
    (Windows: DLL ist im pip-Wheel dabei. Linux: zusaetzlich `apt install libzbar0`)
"""

import argparse
import hashlib
import struct
import sys
import zlib

import numpy as np
import cv2
import base45

try:
    from pyzbar.pyzbar import decode as _zbar_decode_raw
    from pyzbar.pyzbar import ZBarSymbol
    HAVE_ZBAR = True

    def zbar_decode(img):
        # Nur QR-Codes: verhindert, dass zbars DataBar/Barcode-Decoder auf
        # Rauschen Falschtreffer liefert und dabei die QR-Erkennung stoert.
        return _zbar_decode_raw(img, symbols=[ZBarSymbol.QRCODE])
except Exception:
    HAVE_ZBAR = False

# ---- Protokoll (identisch zu sender.py) ------------------------------------
MAGIC = b"OZ"
VERSION = 1
TYPE_META = 0
TYPE_DATA = 1

_cv2_det = cv2.QRCodeDetector()


def parse_packet(text: str):
    try:
        raw = base45.b45decode(text)
    except Exception:
        return None
    if len(raw) < 8 or raw[:2] != MAGIC:
        return None
    if (zlib.crc32(raw[:-4]) & 0xFFFFFFFF) != struct.unpack(">I", raw[-4:])[0]:
        return None
    ptype, body = raw[3], raw[4:-4]
    if ptype == TYPE_META:
        if len(body) < 50:
            return None
        total, chunk_size, file_size = struct.unpack(">IIQ", body[:16])
        sha = body[16:48]
        nlen = struct.unpack(">H", body[48:50])[0]
        name = body[50:50 + nlen].decode("utf-8", "replace")
        return ("meta", dict(total=total, chunk_size=chunk_size,
                             file_size=file_size, sha=sha, name=name))
    if ptype == TYPE_DATA:
        if len(body) < 4:
            return None
        return ("data", struct.unpack(">I", body[:4])[0], body[4:])
    return None


def _decode_gray(g):
    out = []
    if HAVE_ZBAR:
        for s in zbar_decode(g):
            try:
                out.append(s.data.decode("ascii"))
            except Exception:
                pass
    if not out:
        ok, decoded, _, _ = _cv2_det.detectAndDecodeMulti(g)
        if ok:
            out.extend([d for d in decoded if d])
    return out


def decode_frame(gray, heavy=True):
    # 1) roh  2) entrauscht (Blur mittelt Sensorrauschen weg)  3) Threshold
    texts = _decode_gray(gray)
    if not texts:
        texts = _decode_gray(cv2.GaussianBlur(gray, (3, 3), 0))
    if not texts and heavy:
        blurred = cv2.GaussianBlur(gray, (3, 3), 0)
        texts = _decode_gray(cv2.adaptiveThreshold(blurred, 255,
                             cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 5))
    return texts


def main():
    ap = argparse.ArgumentParser(description="Abgefilmtes QR-Video (MP4) -> ZIP")
    ap.add_argument("mp4", help="abgefilmtes Video")
    ap.add_argument("-o", "--out", default=None, help="Ausgabedatei (Default: Name aus Metadaten)")
    ap.add_argument("--step", type=int, default=1, help="nur jeden N-ten Frame scannen (Speed)")
    ap.add_argument("--max-side", type=int, default=1600, help="groessere Frames auf diese Kantenlaenge herunterskalieren")
    ap.add_argument("--fast", action="store_true", help="schwere Threshold-Fallbacks deaktivieren")
    args = ap.parse_args()

    if not HAVE_ZBAR:
        print("Hinweis: pyzbar nicht gefunden -> nur OpenCV-Decoder (weniger robust). "
              "Fuer bessere Ergebnisse: pip install pyzbar", file=sys.stderr)

    cap = cv2.VideoCapture(args.mp4)
    if not cap.isOpened():
        sys.exit(f"Kann Video nicht oeffnen: {args.mp4}")
    n_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 0

    meta = None
    chunks = {}
    idx = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        idx += 1
        if args.step > 1 and (idx % args.step):
            continue
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        h, w = gray.shape
        if max(h, w) > args.max_side:
            s = args.max_side / max(h, w)
            gray = cv2.resize(gray, (int(w * s), int(h * s)), interpolation=cv2.INTER_AREA)

        for t in decode_frame(gray, heavy=not args.fast):
            r = parse_packet(t)
            if r is None:
                continue
            if r[0] == "meta":
                if meta is None:
                    meta = r[1]
                    print(f"\nMetadaten erkannt: '{meta['name']}', {meta['file_size']} B, {meta['total']} Chunks")
            elif r[0] == "data" and r[1] not in chunks:
                chunks[r[1]] = r[2]

        if idx % 30 == 0 or (meta and len(chunks) >= meta["total"]):
            have = f"{len(chunks)}/{meta['total']}" if meta else f"{len(chunks)}/?"
            prog = f" [{idx}/{n_frames}]" if n_frames else f" [{idx}]"
            print(f"\rFrames{prog}  Chunks {have}", end="", flush=True)

        if meta and all(i in chunks for i in range(meta["total"])):
            print("\nAlle Chunks vollstaendig - stoppe frueh.")
            break
    cap.release()
    print()

    if meta is None:
        sys.exit("Keine Metadaten gefunden. Video-Anfang mitfilmen oder --loops im Sender erhoehen.")

    missing = [i for i in range(meta["total"]) if i not in chunks]
    if missing:
        preview = ", ".join(map(str, missing[:20])) + (" ..." if len(missing) > 20 else "")
        sys.exit(f"Unvollstaendig: {len(missing)} Chunks fehlen ({preview}).\n"
                 f"-> Laenger/ruhiger abfilmen, mehr --loops im Sender, oder kleineres --chunk.")

    data = b"".join(chunks[i] for i in range(meta["total"]))
    out = args.out or meta["name"] or "recovered.zip"
    ok_hash = hashlib.sha256(data).digest() == meta["sha"]
    if not ok_hash:
        out += ".corrupt"
        print("WARNUNG: SHA256 stimmt nicht - Datei koennte beschaedigt sein.")
    with open(out, "wb") as f:
        f.write(data)
    print(f"Geschrieben: {out} ({len(data)} B){'  [Integritaet OK]' if ok_hash else ''}")


if __name__ == "__main__":
    main()
