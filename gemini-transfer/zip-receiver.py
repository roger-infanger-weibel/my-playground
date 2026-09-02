from multiprocessing import Pool, cpu_count
import base64
import time
import cv2

VIDEO_FILENAME = "file.mp4"
BATCH_SIZE = 200  # Verarbeitet jeweils 200 Frames gleichzeitig im Batch


def _decode_single_frame(args):
  """Worker-Funktion, die auf einem separaten CPU-Kern läuft."""
  frame_idx, frame_data = args
  try:
    detector = cv2.wechat_qrcode.WeChatQRCode()
    decoded_info, _ = detector.detectAndDecode(frame_data)
    if decoded_info:
      for data in decoded_info:
        if data and "/" in data and ":" in data:
          return data
  except Exception:
    pass
  return None


def run_streaming_parallel_receiver():
  cap = cv2.VideoCapture(VIDEO_FILENAME)
  if not cap.isOpened():
    print(f"Fehler: Konnte die Videodatei '{VIDEO_FILENAME}' nicht öffnen!")
    return

  total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
  print(f"\n--- GESTREAMTER PARALLEL-EMPFÄNGER ---")
  print(
      f"Video hat {total_frames} Frames. Starte sofortige Verarbeitung mit"
      f" {cpu_count()} Kernen...\n"
  )

  chunks = {}
  total_chunks = None
  processed_frames = 0
  t_start = time.time()

  active_threads = cpu_count()

  with Pool(processes=active_threads) as pool_worker:
    while True:
      # Einen Batch von Frames einlesen (schont den RAM)
      batch_payloads = []
      for _ in range(BATCH_SIZE):
        ret, frame = cap.read()
        if not ret:
          break
        processed_frames += 1
        batch_payloads.append((processed_frames, frame))

      if not batch_payloads:
        # Ende des Videos erreicht -> Schleife von vorn beginnen (Looping-Unterstützung)
        print(
            "\n[INFO] Ende des Videos erreicht. Starte Schleife von vorn..."
        )
        cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
        processed_frames = 0
        continue

      # Batch parallel über alle CPU-Kerne jagen
      results = pool_worker.map(_decode_single_frame, batch_payloads)

      # Ergebnisse auswerten und direkt im Terminal ausgeben
      for result_data in results:
        if result_data:
          try:
            header, chunk_content = result_data.split(":", 1)
            idx_str, total_str = header.split("/")
            idx = int(idx_str)
            total = int(total_str)

            if total_chunks is None:
              total_chunks = total
              print(
                  f"\n[INFO] Gesamtanzahl Chunks im Stream erkannt:"
                  f" {total_chunks}"
              )

            if idx not in chunks:
              chunks[idx] = chunk_content
              p_val = int((len(chunks) / total_chunks) * 100)
              print(
                  f"\n[TREFFER!] Neuer Chunk #{idx} eingelesen. Gesamt:"
                  f" {len(chunks)} / {total_chunks} ({p_val}%)"
              )
            else:
              print(
                  f"[info] Chunk #{idx} im Video gefunden (bereits vorhanden).",
                  end="\r",
              )
          except Exception:
            pass

      # Abbruch-Bedingung: Wenn alle Chunks da sind
      if total_chunks and len(chunks) == total_chunks:
        print("\n\n[ERFOLG] Alle Chunks komplett eingesammelt!")
        break

  cap.release()
  elapsed = time.time() - t_start
  print(f"Benötigte Zeit: {elapsed:.1f} Sekunden.")

  # Datei zusammenbauen
  if total_chunks and len(chunks) == total_chunks:
    print("Rekonstruiere 'received_file.zip'...")
    sorted_chunks = [chunks[i] for i in sorted(chunks.keys())]
    full_encoded = "".join(sorted_chunks)

    zip_bytes = base64.b85decode(full_encoded.encode("utf-8"))
    with open("received_file.zip", "wb") as f:
      f.write(zip_bytes)

    print(
        "[FERTIG] Datei erfolgreich als 'received_file.zip' gespeichert!\n"
    )
  else:
    print(
        f"[WARNUNG] Es wurden {len(chunks)} von {total_chunks or '?'}"
        " Chunks gefunden."
    )


if __name__ == "__main__":
  run_streaming_parallel_receiver()