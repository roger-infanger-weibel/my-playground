#!/usr/bin/env python3
"""
sender_web.py -- Browser-Sender: zeigt die QR-Codes als Vollbild-Slideshow
direkt im Browser an. Kein MP4, kein Codec-Problem, kein GUI-Fenster und
OHNE cv2/numpy -- nur Flask + qrcode/PIL.

Ideal fuer headless Linux mit Browser-Zugriff (z.B. VS Code im Browser).
Der Browser ist die Anzeige, die du mit der Kamera abfilmst.

Protokoll identisch zu sender.py -> receiver.py dekodiert unveraendert.

    pip install flask "qrcode[pil]"
    python sender_web.py datei.zip --port 8000
    # Browser oeffnen (URL wird ausgegeben), "Start (Vollbild)" klicken, abfilmen.
"""

import argparse
import hashlib
import io
import os
import struct
import zlib

import qrcode
from flask import Flask, Response, jsonify, render_template_string

# ---- Protokoll (identisch zu sender.py / receiver.py) ----------------------
MAGIC = b"OZ"
VERSION = 1
TYPE_META = 0
TYPE_DATA = 1


def build_packet(ptype: int, body: bytes) -> str:
    raw = MAGIC + bytes([VERSION, ptype]) + body
    raw += struct.pack(">I", zlib.crc32(raw) & 0xFFFFFFFF)
    import base45
    return base45.b45encode(raw).decode("ascii")


def meta_body(total, chunk_size, file_size, sha, name: bytes) -> bytes:
    return (struct.pack(">IIQ", total, chunk_size, file_size)
            + sha + struct.pack(">H", len(name)) + name)


def data_body(seq: int, data: bytes) -> bytes:
    return struct.pack(">I", seq) + data


_ECC = {"L": qrcode.constants.ERROR_CORRECT_L,
        "M": qrcode.constants.ERROR_CORRECT_M,
        "Q": qrcode.constants.ERROR_CORRECT_Q,
        "H": qrcode.constants.ERROR_CORRECT_H}


def make_qr_png(text: str, ecc: str, box: int = 10, border: int = 4) -> bytes:
    qr = qrcode.QRCode(error_correction=_ECC[ecc], box_size=box, border=border)
    qr.add_data(text)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white").convert("RGB")
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def build_frames(path, chunk, ecc, meta_interval):
    data = open(path, "rb").read()
    sha = hashlib.sha256(data).digest()
    name = os.path.basename(path).encode("utf-8")
    chunks = [data[i:i + chunk] for i in range(0, len(data), chunk)] or [b""]
    total = len(chunks)

    meta_text = build_packet(TYPE_META, meta_body(total, chunk, len(data), sha, name))
    seq_texts = [meta_text]
    for i, c in enumerate(chunks):
        seq_texts.append(build_packet(TYPE_DATA, data_body(i, c)))
        if (i + 1) % meta_interval == 0:
            seq_texts.append(meta_text)

    pngs, order, cache, max_ver = [], [], {}, 0
    for t in seq_texts:
        if t not in cache:
            e = "H" if t == meta_text else ecc
            cache[t] = len(pngs)
            pngs.append(make_qr_png(t, e))
            # QR-Version nur zur Info
            q = qrcode.QRCode(error_correction=_ECC[e]); q.add_data(t); q.make(fit=True)
            max_ver = max(max_ver, q.version)
        order.append(cache[t])
    info = dict(total=total, chunk=chunk, size=len(data),
                name=name.decode("utf-8", "replace"), max_ver=max_ver,
                n_unique=len(pngs), n_pos=len(order))
    return pngs, order, info


PAGE = """<!doctype html><html><head><meta charset="utf-8">
<title>QR-Sender</title>
<style>
  html,body{margin:0;height:100%;background:#fff;overflow:hidden;font-family:sans-serif}
  #stage{position:fixed;inset:0;display:flex;align-items:center;justify-content:center;background:#fff}
  #q{width:96vmin;height:96vmin;image-rendering:pixelated;display:none}
  #pause{display:none;width:96vmin;height:96vmin;background:#c8c8c8;color:#282828;
         align-items:center;justify-content:center;font-size:4vmin;border-radius:1vmin}
  #label{position:fixed;left:1vmin;bottom:1vmin;color:#333;font-size:2.2vmin;
         background:rgba(255,255,255,.7);padding:.3em .6em;border-radius:.3em}
  #start{position:fixed;inset:0;display:flex;flex-direction:column;align-items:center;
         justify-content:center;background:#fff;gap:2vmin;cursor:pointer}
  #start button{font-size:3vmin;padding:.6em 1.2em;cursor:pointer}
  .hint{color:#666;font-size:2.2vmin;max-width:70vw;text-align:center}
</style></head><body>
<div id="stage">
  <img id="q">
  <div id="pause">-- Pause --</div>
</div>
<div id="label"></div>
<div id="start">
  <div style="font-size:4vmin">QR-Transfer</div>
  <button id="go">Start (Vollbild)</button>
  <div class="hint">Danach mit der Kamera abfilmen. Tasten: F = Vollbild, Leertaste = Pause, Esc = Stopp.</div>
</div>
<script>
const IMG=document.getElementById('q'), PAUSE=document.getElementById('pause'),
      LABEL=document.getElementById('label'), START=document.getElementById('start');
let M, frames=[], paused=false, running=false;
const sleep=ms=>new Promise(r=>setTimeout(r,ms));

async function load(){
  M=await (await fetch('manifest')).json();
  for(let i=0;i<M.n_unique;i++){const im=new Image();im.src='frame/'+i;frames.push(im);}
  await Promise.all(frames.map(im=>im.decode().catch(()=>{})));
}
async function run(){
  running=true; IMG.style.display='block';
  const interval=Math.max(1,Math.round(1000/M.qr_fps));
  const pause_ms=Math.max(0,Math.round(M.loop_pause*1000));
  let loop=0;
  while(running){
    loop++;
    for(let pos=0; pos<M.order.length; pos++){
      while(paused){await sleep(80);}
      if(!running) break;
      IMG.src=frames[M.order[pos]].src;
      LABEL.textContent=`loop ${loop}  pkt ${pos+1}/${M.order.length}`;
      await sleep(interval);
    }
    if(running && pause_ms){
      IMG.style.display='none'; PAUSE.style.display='flex';
      PAUSE.textContent=`-- Pause (Loop ${loop} fertig) --`;
      await sleep(pause_ms);
      PAUSE.style.display='none'; IMG.style.display='block';
    }
  }
}
function fs(){ if(document.fullscreenElement){document.exitFullscreen();}
              else{document.documentElement.requestFullscreen().catch(()=>{});} }
document.getElementById('go').onclick=async()=>{
  START.style.display='none';
  try{await document.documentElement.requestFullscreen();}catch(e){}
  if(!running) run();
};
document.addEventListener('keydown',e=>{
  if(e.key===' '){paused=!paused;e.preventDefault();}
  else if(e.key.toLowerCase()==='f'){fs();}
  else if(e.key==='Escape'){running=false;}
});
load();
</script></body></html>"""


def create_app(pngs, order, info, qr_fps, loop_pause):
    app = Flask(__name__)

    @app.route("/")
    def index():
        return render_template_string(PAGE)

    @app.route("/manifest")
    def manifest():
        return jsonify(order=order, qr_fps=qr_fps, loop_pause=loop_pause,
                       n_unique=info["n_unique"], total=info["total"],
                       name=info["name"], size=info["size"])

    @app.route("/frame/<int:i>")
    def frame(i):
        if 0 <= i < len(pngs):
            return Response(pngs[i], mimetype="image/png")
        return ("not found", 404)

    return app


def main():
    ap = argparse.ArgumentParser(description="Browser-Sender (Flask): QR-Slideshow im Browser")
    ap.add_argument("zipfile")
    ap.add_argument("--chunk", type=int, default=384, help="Rohdaten-Bytes pro QR")
    ap.add_argument("--ecc", choices=list("LMQH"), default="M")
    ap.add_argument("--qr-fps", type=float, default=3.0, help="QR-Codes pro Sekunde")
    ap.add_argument("--loop-pause", type=float, default=0.7, help="Pause (s) nach jedem Durchlauf")
    ap.add_argument("--meta-interval", type=int, default=12)
    ap.add_argument("--host", default="127.0.0.1", help="0.0.0.0 fuer Zugriff von anderem Geraet")
    ap.add_argument("--port", type=int, default=8000)
    args = ap.parse_args()

    pngs, order, info = build_frames(args.zipfile, args.chunk, args.ecc, args.meta_interval)
    print(f"Datei: {info['size']} B, {info['total']} Chunks a {args.chunk} B, {info['n_pos']} Positionen/Durchlauf")
    print(f"QR-Version bis {info['max_ver']} (Daten ECC {args.ecc}, META ECC H)")
    print(f"Im Browser oeffnen:  http://{args.host}:{args.port}/")
    print("Dann 'Start (Vollbild)' klicken und den Bildschirm abfilmen. Strg+C zum Beenden.")

    app = create_app(pngs, order, info, args.qr_fps, args.loop_pause)
    app.run(host=args.host, port=args.port, threaded=True)


if __name__ == "__main__":
    main()
