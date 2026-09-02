import base64
import math
import os
import cv2
import numpy as np
import qrcode

# --- EINSTELLUNGEN ---
INPUT_FILE = "file.zip"
CHUNK_SIZE = 1200
DISPLAY_TIME_SEC = 0.5
CANVAS_SIZE = 1080
# ---------------------


def play_qr_presentation():
  if not os.path.exists(INPUT_FILE):
    print(
        f"Fehler: Die Datei '{INPUT_FILE}' wurde im Ordner nicht gefunden!"
    )
    return

  print(f"Lese '{INPUT_FILE}' ein...")
  with open(INPUT_FILE, "rb") as f:
    file_bytes = f.read()

  encoded_data = base64.b85encode(file_bytes).decode("utf-8")
  total_chunks = math.ceil(len(encoded_data) / CHUNK_SIZE)
  chunks = []
  for i in range(total_chunks):
    start = i * CHUNK_SIZE
    end = start + CHUNK_SIZE
    chunks.append(f"{i+1}/{total_chunks}:" + encoded_data[start:end])

  print(f"\n[BEREIT] {total_chunks} Chunks geladen.")
  print("--- BEDIENUNG ---")
  print("1. Starten Sie Ihre Bildschirmaufnahme.")
  print("2. Drücken Sie im Fenster eine Taste zum Starten.")

  window_name = "QR-Code Streamer (mit Pause)"
  cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)
  cv2.resizeWindow(window_name, CANVAS_SIZE, CANVAS_SIZE)
  cv2.setWindowProperty(window_name, cv2.WND_PROP_TOPMOST, 1)

  # Startbild
  intro_canvas = np.ones((CANVAS_SIZE, CANVAS_SIZE, 3), dtype=np.uint8) * 255
  cv2.putText(
      intro_canvas,
      "BEREIT? Taste druecken zum Start!",
      (50, CANVAS_SIZE // 2),
      cv2.FONT_HERSHEY_SIMPLEX,
      1.0,
      (0, 0, 0),
      2,
  )
  cv2.imshow(window_name, intro_canvas)

  if cv2.waitKey(0) == 27:
    cv2.destroyAllWindows()
    return

  print("[START] Präsentation läuft im Vordergrund...")
  delay_ms = int(DISPLAY_TIME_SEC * 1000)

  while True:
    for i, chunk_text in enumerate(chunks):
      qr = qrcode.QRCode(
          version=40,
          error_correction=qrcode.constants.ERROR_CORRECT_M,
          box_size=4,
          border=2,
      )
      qr.add_data(chunk_text)
      qr.make(fit=True)

      img = qr.make_image(fill_color="black", back_color="white").convert("RGB")
      qr_np = np.array(img)

      canvas = np.ones((CANVAS_SIZE, CANVAS_SIZE, 3), dtype=np.uint8) * 255
      h, w, _ = qr_np.shape
      y_offset = (CANVAS_SIZE - h) // 2
      x_offset = (CANVAS_SIZE - w) // 2
      canvas[y_offset : y_offset + h, x_offset : x_offset + w] = qr_np

      # Fortschritt unten rechts
      progress_text = f"Chunk: {i+1} / {total_chunks}"
      font = cv2.FONT_HERSHEY_SIMPLEX
      text_size, _ = cv2.getTextSize(progress_text, font, 1.0, 2)
      text_x = CANVAS_SIZE - text_size[0] - 40
      text_y = CANVAS_SIZE - 40

      cv2.putText(
          canvas,
          progress_text,
          (text_x, text_y),
          font,
          1.0,
          (50, 50, 50),
          2,
          cv2.LINE_AA,
      )

      print(
          f"[ZEIGE] Chunk {i+1} von {total_chunks}...", end="\r", flush=True
      )
      cv2.imshow(window_name, canvas)

      if cv2.waitKey(delay_ms) == 27:
        break
    else:
      # --- 5 SEKUNDEN WEISSER PAUSENBILDSCHIRM AM ENDE DES DURCHLAUFS ---
      print(
          "\n[INFO] Durchlauf beendet. Zeige 5 Sekunden weißen"
          " Pausenbildschirm..."
      )
      pause_canvas = np.ones((CANVAS_SIZE, CANVAS_SIZE, 3), dtype=np.uint8) * 255
      cv2.putText(
          pause_canvas,
          "DURCHLAUF BEENDET - Neustart in 5s...",
          (150, CANVAS_SIZE // 2),
          cv2.FONT_HERSHEY_SIMPLEX,
          0.8,
          (100, 100, 100),
          2,
          cv2.LINE_AA,
      )
      cv2.imshow(window_name, pause_canvas)

      # 5000 ms warten (währenddessen prüft ESC ob abgebrochen wird)
      if cv2.waitKey(5000) == 27:
        break
      continue
    break

  cv2.destroyAllWindows()
  print("\n[BEENDET] Präsentation geschlossen.")


if __name__ == "__main__":
  play_qr_presentation()