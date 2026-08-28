# ==============================================================================
# SISTEMA AUTOMATICO DI CATTURA FULMINI (ZWO ASI CAMERA)
# ==============================================================================
import os
import sys
import time
import logging
logging.getLogger().setLevel(logging.ERROR)
import threading
import queue
from collections import deque
import numpy as np
import cv2
import zwoasi as asi

import config
from ser_writer import write_ser, COLOR_MONO, COLOR_BAYER_RGGB, COLOR_BAYER_BGGR, COLOR_BAYER_GRBG, COLOR_BAYER_GBRG, COLOR_RGB
from stacker import process_ser_file

# Coda di salvataggio asincrono per non bloccare l'acquisizione video
save_queue = queue.Queue()
running = True
total_events_captured = 0

def map_bayer_to_ser_color_id(bayer_pattern, is_color, image_type):
    """Mappa il pattern bayer della camera ZWO al codice ColorID del formato SER."""
    if not is_color or "MONO" in image_type:
        return COLOR_MONO
    if image_type == "RGB24":
        return COLOR_RGB
    
    # Mappatura pattern numerici SDK ZWO:
    # 0: RGGB, 1: BGGR, 2: GRBG, 3: GBRG
    mapping = {
        0: COLOR_BAYER_RGGB,
        1: COLOR_BAYER_BGGR,
        2: COLOR_BAYER_GRBG,
        3: COLOR_BAYER_GBRG,
    }
    return mapping.get(bayer_pattern, COLOR_BAYER_RGGB)

def async_saver_worker(color_id, pixel_depth, cam_name):
    """Worker in background per la scrittura su disco del file .SER e creazione del .TIFF."""
    global total_events_captured
    while running or not save_queue.empty():
        try:
            task = save_queue.get(timeout=0.5)
        except queue.Empty:
            continue
        
        if task is None:
            break

        timestamp_str, frames_to_save, width, height = task
        ser_path = os.path.join(config.OUTPUT_DIR, f"lightning_{timestamp_str}.ser")
        tiff_path = os.path.join(config.OUTPUT_DIR, f"lightning_{timestamp_str}_sum.tiff")

        print(f"\n[SAVER] >>> Scrittura su disco di {len(frames_to_save)} frame ({ser_path})...")
        try:
            # 1. Scrittura file .SER
            write_ser(
                filename=ser_path,
                frames=frames_to_save,
                width=width,
                height=height,
                color_id=color_id,
                pixel_depth=pixel_depth,
                observer="Fulmini",
                instrument=cam_name
            )
            print(f"[SAVER] >>> File .SER salvato con successo!")

            # 2. Generazione automatica TIFF somma (Max-Stack)
            if config.AUTO_STACK_TIFF:
                process_ser_file(
                    ser_path=ser_path,
                    output_tiff_path=tiff_path,
                    method=config.STACK_METHOD,
                    save_jpg=config.SAVE_JPEG_PREVIEW
                )
            
            total_events_captured += 1
            print(f"[SAVER] >>> Evento #{total_events_captured} completato.\n")
        except Exception as e:
            print(f"[ERRORE SAVER] Impossibile salvare l'evento: {e}")
        finally:
            save_queue.task_done()

def main():
    global running
    dll_path = os.path.abspath(config.SDK_DLL_PATH)
    if not os.path.exists(dll_path):
        print(f"[ERRORE] File SDK '{dll_path}' non trovato!")
        return

    os.makedirs(config.OUTPUT_DIR, exist_ok=True)
    
    print("=" * 65)
    print("           ⚡ FULMINI - CATTURA AUTOMATICA ZWO ASI")
    print("=" * 65)
    print(f"Directory salvataggi : {os.path.abspath(config.OUTPUT_DIR)}")
    print(f"Esposizione          : {config.EXPOSURE_MS} ms | Gain: {config.GAIN}")
    print(f"Pre-Trigger Buffer   : {config.PRE_EVENT_FRAMES} frame")
    print(f"Post-Trigger Durata  : {config.POST_EVENT_SECONDS} secondi")
    print(f"Soglia Delta         : +{config.DELTA_THRESHOLD} rispetto al cielo")
    print("=" * 65)

    try:
        asi.init(dll_path)
    except Exception as e:
        print(f"[ERRORE] Inizializzazione SDK ZWO fallita: {e}")
        return

    if asi.get_num_cameras() == 0:
        print("[ERRORE] Nessuna camera ZWO collegata rilevata!")
        return

    camera = asi.Camera(config.CAMERA_INDEX)
    props = camera.get_camera_property()
    cam_name = props['Name']
    print(f"[CAMERA] Connesso a: {cam_name}")

    # Configurazione modalità immagine
    is_color = props['IsColorCam']
    bayer_pattern = props['BayerPattern']
    
    if config.IMAGE_TYPE == "RAW16":
        img_type = asi.ASI_IMG_RAW16
        pixel_depth = 16
        dtype = np.uint16
    elif config.IMAGE_TYPE == "RGB24" and is_color:
        img_type = asi.ASI_IMG_RGB24
        pixel_depth = 8
        dtype = np.uint8
    else:
        img_type = asi.ASI_IMG_RAW8
        pixel_depth = 8
        dtype = np.uint8

    color_id = map_bayer_to_ser_color_id(bayer_pattern, is_color, config.IMAGE_TYPE)

    # Impostazione controlli camera
    camera.set_control_value(asi.ASI_EXPOSURE, int(config.EXPOSURE_MS * 1000))
    camera.set_control_value(asi.ASI_GAIN, config.GAIN)
    camera.set_control_value(asi.ASI_BANDWIDTHOVERLOAD, config.BANDWIDTH_OVERLOAD)
    try:
        camera.set_control_value(asi.ASI_HIGH_SPEED_MODE, 1 if getattr(config, 'HIGH_SPEED_MODE', True) else 0)
    except Exception:
        pass
    camera.set_image_type(img_type)
    
    w_raw = props['MaxWidth'] // config.BINNING
    h_raw = props['MaxHeight'] // config.BINNING
    width = w_raw - (w_raw % 8)
    height = h_raw - (h_raw % 2)
    camera.set_roi_format(width, height, config.BINNING, img_type)

    # Avvio worker thread di salvataggio
    saver_thread = threading.Thread(
        target=async_saver_worker,
        args=(color_id, pixel_depth, cam_name),
        daemon=True
    )
    saver_thread.start()

    # Avvio stream video continuo
    camera.start_video_capture()
    print("[STATUS] Streaming video avviato. Monitoraggio continuo del cielo in corso...")
    print("Premi Ctrl+C per fermare la registrazione.\n")
    # Buffer circolare per i frame precedenti l'evento
    diff_offset = int(getattr(config, 'DIFF_FRAME_OFFSET', 6))
    contrast_pct = int(getattr(config, 'PIXEL_CONTRAST_PERCENT', 60))
    contrast_th = int(contrast_pct * 255.0 / 100.0)
    min_active_px = int(getattr(config, 'MIN_ACTIVE_PIXELS', 20))
    buf_size = max(int(config.PRE_EVENT_FRAMES), diff_offset + 1)
    
    pre_buffer = deque(maxlen=buf_size)
    
    rolling_baseline = None
    is_capturing_event = False
    event_frames = []
    frames_remaining_post = 0
    last_trigger_time = 0
    
    # Contatori per statistiche FPS
    fps_timer = time.time()
    frame_counter = 0
    current_fps = 0.0

    channels = 3 if config.IMAGE_TYPE == "RGB24" else 1

    try:
        while running:
            # Cattura frame a bassissima latenza
            try:
                frame_bytes = camera.capture_video_frame(timeout=500)
            except Exception as ex:
                time.sleep(0.005)
                continue

            if channels == 3:
                frame = np.frombuffer(frame_bytes, dtype=dtype).reshape((height, width, 3))
                eval_sample = frame[::2, ::2, 1]
            else:
                frame = np.frombuffer(frame_bytes, dtype=dtype).reshape((height, width))
                eval_sample = frame[::2, ::2]

            frame_counter += 1
            now = time.time()
            if now - fps_timer >= 1.0:
                current_fps = frame_counter / (now - fps_timer)
                frame_counter = 0
                fps_timer = now

            if is_capturing_event:
                # REGISTRA E BASTA: nessun calcolo trigger o differenze durante l'evento
                event_frames.append(frame.copy())
                frames_remaining_post -= 1
                
                if frames_remaining_post <= 0:
                    ts_str = time.strftime("%Y%m%d_%H%M%S")
                    save_queue.put((ts_str, event_frames, width, height))
                    print(f"\n[EVENTO CATTURATO] {len(event_frames)} frame inviati alla scrittura .SER ({ts_str}).")
                    
                    is_capturing_event = False
                    event_frames = []
                    pre_buffer.clear()
                    rolling_baseline = None
                active_pixels_count = 0
                delta = 0.0
                current_max = 0.0
            else:
                # Metriche di luminosità rapide (< 0.4 ms)
                current_mean = float(np.mean(eval_sample))
                current_max = float(np.max(eval_sample))

                if rolling_baseline is None:
                    rolling_baseline = current_mean
                else:
                    rolling_baseline = (1.0 - config.EMA_ALPHA) * rolling_baseline + config.EMA_ALPHA * current_mean

                delta = current_mean - rolling_baseline

                # Calcolo Punti Accesi ad Alto Contrasto vs Frame di Riferimento (es. 6 frame fa)
                active_pixels_count = 0
                if len(pre_buffer) >= diff_offset:
                    ref_frame = pre_buffer[-diff_offset]
                    ref_eval = ref_frame[::2, ::2, 1] if channels == 3 else ref_frame[::2, ::2]
                    if ref_eval.shape == eval_sample.shape:
                        diff = cv2.subtract(eval_sample, ref_eval)
                        active_mask = diff >= contrast_th
                        active_pixels_count = int(np.count_nonzero(active_mask)) * 4
                    else:
                        pre_buffer.clear()
                        active_pixels_count = 0

                # Logica di Trigger Multi-Criterio
                triggered = False
                trigger_reason = ""
                if (now - last_trigger_time > config.COOLDOWN_SECONDS):
                    if config.TRIGGER_MODE in ("PIXEL_COUNT", "HYBRID") and active_pixels_count >= min_active_px:
                        triggered = True
                        trigger_reason = f"Laser/Punti: {active_pixels_count} px (+{contrast_pct}%)"
                    elif config.TRIGGER_MODE in ("DELTA", "HYBRID") and delta >= config.DELTA_THRESHOLD:
                        triggered = True
                        trigger_reason = f"Delta luminosità +{delta:.1f}"
                    elif config.TRIGGER_MODE in ("MAX_PIXEL", "HYBRID") and current_max >= config.MAX_PIXEL_THRESHOLD:
                        triggered = True
                        trigger_reason = f"Pixel di picco {current_max:.0f}"

                    if triggered:
                        last_trigger_time = now
                        is_capturing_event = True
                        # Calcola i frame post-evento in base al framerate effettivo corrente
                        fps_estimate = current_fps if current_fps > 10 else (1000.0 / config.EXPOSURE_MS)
                        frames_remaining_post = int(fps_estimate * config.POST_EVENT_SECONDS)

                        print(f"\n[*** LAMPO RILEVATO ***] Motivo: {trigger_reason} | Punti: {active_pixels_count} px | Delta: {delta:+.1f} | Pre: {len(pre_buffer)} | Post: {frames_remaining_post}")
                        
                        # Copia i frame precedenti + il frame del lampo attuale
                        event_frames = [f.copy() for f in pre_buffer]
                        event_frames.append(frame.copy())
                    else:
                        pre_buffer.append(frame.copy())
                else:
                    pre_buffer.append(frame.copy())

            # Visualizzazione riga di stato in tempo reale
            sys.stdout.write(
                f"\r[STATUS] FPS: {current_fps:4.1f} | Punti Accesi: {active_pixels_count:4d} px | Delta: {delta:+5.1f} | Picco: {current_max:3.0f} | Catturati: {total_events_captured}   "
            )
            sys.stdout.flush()

    except KeyboardInterrupt:
        print("\n\n[INFO] Arresto richiesto dall'utente...")
    finally:
        running = False
        camera.stop_video_capture()
        camera.close()
        save_queue.put(None)
        print("[INFO] Attesa completamento salvataggio code su disco...")
        saver_thread.join()
        print("[INFO] Chiusura completata con successo. Arrivederci!")

if __name__ == "__main__":
    main()
