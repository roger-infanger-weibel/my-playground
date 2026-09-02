# Optischer Datentransfer via Screen-Recording (QR)

Zwei Python-Programme, um ein **ZIP-File** über den Bildschirm zu übertragen und
mit einer **Kamera abzufilmen** (nicht Screen-Capture aus Windows).

- **`sender.py`** kodiert ein ZIP in eine Folge von QR-Code-Frames und schreibt ein MP4.
- **`receiver.py`** dekodiert das abgefilmte (unsaubere) MP4 zurück ins ZIP.

## Wie es funktioniert

Pipeline: `ZIP → Chunks → Pakete (+CRC32) → base45 → QR → MP4`

QR-Codes sind für "vom Bildschirm abgefilmt" der robusteste Kanal: eingebaute
Reed-Solomon-Fehlerkorrektur, Positionsmarker für Perspektivenkorrektur und
ausgereifte Decoder. Gegen fehlende/verwackelte Frames läuft die Redundanz über
**Loops** — der Sender wiederholt die ganze Sequenz mehrmals, der Receiver
sammelt jeden Chunk per Sequenznummer ein (dedupliziert), prüft CRC32 pro Paket
und am Schluss den SHA256 der Gesamtdatei.

## Installation

```bash
pip install "qrcode[pil]" opencv-python numpy base45 pyzbar
```

`pyzbar` ist optional, aber klar empfohlen (deutlich robuster als der reine
OpenCV-Decoder). Unter Windows ist die nötige DLL im pip-Wheel enthalten — läuft
direkt. Unter Linux zusätzlich: `sudo apt install libzbar0`.

## Nutzung

### Variante A – Live abfilmen (empfohlen, kein MP4)

```bash
# Sender zeigt die QR-Codes direkt im Vollbild an (Schleife bis ESC)
python sender.py meine_daten.zip --live

# -> Kamera starten, Bildschirm abfilmen, ein paar Durchläufe aufnehmen, ESC drücken
# Sample output
# | Datei: 6009 Bytes, 16 Chunks a 384 B, 18 Pakete/Durchlauf
# | QR-Version: bis 15 (Daten ECC M, META ECC H)
# | Live: 1920x1080, ~3.0 QR/s (333 ms). ESC/q zum Beenden.
# | Beendet nach 7 Durchlauf/Durchlaeufen.

# Receiver: abgefilmtes Video -> ZIP zurück
python receiver.py aufnahme.mp4 -o wiederhergestellt.zip

# Sample Output
# | Frames [30/1305]  Chunks 0/?
# | Metadaten erkannt: 'input-file.zip', 6009 B, 16 Chunks
# | Frames [237/1305]  Chunks 16/16
# | Alle Chunks vollstaendig - stoppe frueh.
# | Geschrieben: wiederhergestellt.zip (6009 B)  [Integritaet OK]
```

Für `--live` wird **opencv-python mit GUI** benötigt (nicht `opencv-python-headless`).
Die Bildschirmauflösung wird automatisch erkannt; bei Bedarf mit `--screen 1920x1080`
überschreiben. Der QR bleibt quadratisch und zentriert (keine Verzerrung auf 16:9).

### Variante B – über MP4

```bash
# Sender: ZIP -> MP4
python sender.py meine_daten.zip -o transfer.mp4

# transfer.mp4 im VOLLBILD abspielen und mit der Kamera abfilmen

# Receiver: abgefilmtes MP4 -> ZIP zurück
python receiver.py aufnahme.mp4 -o wiederhergestellt.zip
```

Ohne `-o` beim Receiver wird der ursprüngliche Dateiname aus den Metadaten
verwendet. Stimmt der SHA256 nicht, wird die Datei mit der Endung `.corrupt`
geschrieben und eine Warnung ausgegeben.

## Wichtige Stellschrauben (Sender)

Zwei Dinge sind beim Abfilmen entscheidend:

| Option | Default | Bedeutung |
|---|---|---|
| `--live` | aus | QR-Codes direkt im Vollbild anzeigen (Schleife bis ESC), kein MP4. |
| `--screen` | automatisch | Live-Auflösung `BxH`, z. B. `1920x1080`. Nur mit `--live`. |
| `--qr-fps` | 3 | QR-Codes pro Sekunde. Niedrig halten: jeder QR steht länger und überlebt Bewegungsunschärfe/Rolling-Shutter. Höher = mehr Durchsatz, aber fragiler. Gilt auch für `--live` (Anzeigedauer pro QR). |
| `--loop-pause` | 0.7 | Pause in Sekunden nach jedem Durchlauf (klare Loop-Grenze; Kamera stabilisiert Belichtung/Fokus). |
| `--loops` | 3 | Wie oft die ganze Sequenz wiederholt wird. Bei wackligen Aufnahmen 4–5. |
| `--chunk` | 384 | Rohdaten-Bytes pro QR. Grösser = mehr Daten pro Code, aber dichter (schwerer filmbar) und langsamer zu dekodieren. Sweet Spot 384 (QR-Version 15). Kleiner (256) = robuster; grösser (512, v18) nur mit grossem Vollbild-Display sinnvoll, ab ~v20 wird's unlesbar. |
| `--ecc` | M | QR-Fehlerkorrektur (`L`/`M`/`Q`/`H`) für Datenpakete. Das Metadaten-Paket nutzt automatisch immer ECC `H`. |
| `--video-fps` | 30 | Video-Framerate. |
| `--canvas` | 1000 | Frame-Grösse in px (quadratisch). |
| `--meta-interval` | 12 | Metadaten-Paket alle N Datenpakete einstreuen. |

## Optionen (Receiver)

| Option | Default | Bedeutung |
|---|---|---|
| `-o`, `--out` | Name aus Metadaten | Ausgabedatei. |
| `--step` | 1 | Nur jeden N-ten Frame scannen (Tempo). |
| `--max-side` | 1200 | Grössere Frames (z. B. 4K) auf diese Kantenlänge herunterskalieren (Tempo). |
| `--fast` | aus | Schwere Threshold-Fallbacks deaktivieren (schneller, weniger robust). |

Der Receiver bricht früh ab, sobald alle Chunks vollständig sind. Fehlt am Ende
etwas, meldet er, **welche** Chunks fehlen — dann einfach länger/ruhiger nochmal
filmen oder `--loops` im Sender erhöhen.

## Datendichte & Durchsatz

Roher Durchsatz ≈ `--chunk` × `--qr-fps`. Mit den Defaults (384 B × 3/s) sind das
gut **1 KB/s**. Optisches Abfilmen ist also grundsätzlich langsam — ein 1-MB-File
dauert grössenordnungsmässig ~15 min pro Durchlauf.

Gemessener Kompromiss (Kamera braucht genug Pixel pro QR-Modul):

| Chunk | QR-Version | Daten/Code | Eignung |
|---|---|---|---|
| 256 | v12 | 1× | sehr robust, für schwierige Bedingungen |
| **384** | **v15** | **1.5×** | **Default, robuster Sweet Spot** |
| 512 | v18 | 2× | nur mit grossem Vollbild-Display + mehr `--loops` |
| ≥768 | ≥v23 | ≥3× | für Kamera-Abfilmen nicht mehr lesbar |

Wichtig: dichter = grösser anzeigen. Der Live-Vollbildmodus auf dem grössten
Monitor hilft den dichten Codes am meisten.

## Grenzen des Loop-Prinzips (für grosse Dateien)

Das aktuelle Schema braucht *jeden einzelnen* Chunk mindestens einmal lesbar. Je
grösser die Datei (mehr Chunks), desto stärker der „Coupon-Collector"-Effekt: die
letzten paar Chunks einzusammeln dauert überproportional lange, und ein einziger
schwer lesbarer Code hält den ganzen Transfer auf. Für grosse Datenmengen ist der
saubere Ausweg ein **Fountain-Code (LT/Raptor)**: ein endloser Strom zufälliger
XOR-Kombinationen, aus dem *irgendwelche* ~5–10 % mehr als K Pakete genügen — kein
„genau dieser Chunk fehlt" mehr.

## Kamera-Tipps

- QR möglichst **frontal** und **formatfüllend** aufnehmen.
- Autofokus scharfstellen lassen, dann ruhig halten.
- Blitz und Reflexionen/Glare auf dem Bildschirm vermeiden.
- Falls Codes nicht lesbar sind: `--chunk` verkleinern (weniger dichte QR-Codes).

## Beispiel-Workflow für ein grösseres/wackliges Transfer

```bash
# robuster: kleinere Chunks, lange Anzeigedauer, mehr Loops
python sender.py archiv.zip -o transfer.mp4 --chunk 200 --qr-fps 4 --loops 5

# abfilmen ...

python receiver.py aufnahme.mp4 -o archiv.zip
```

## Format-Details (für Neugierige)

Paket-Layout vor der base45-Kodierung (alle Ints big-endian):

```
magic[2]="OZ" | version[1] | type[1] | body... | crc32[4]

META-body: total_chunks[4] | chunk_size[4] | file_size[8] | sha256[32] | name_len[2] | name
DATA-body: seq[4] | data[...]
```

base45 mappt exakt auf den QR-Alphanumeric-Zeichensatz und ist damit
platzsparender als z. B. Base64.
