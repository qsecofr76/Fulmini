# ==============================================================================
# STRUMENTO DI CALIBRAZIONE COLORE & BILANCIAMENTO DEL BIANCO
# ==============================================================================
import os
import sys
import time
import logging
logging.getLogger().setLevel(logging.ERROR)
import numpy as np
import cv2
import zwoasi as asi
import config

def main():
    dll_path = os.path.abspath(config.SDK_DLL_PATH)
    if not os.path.exists(dll_path):
        print(f"[ERRORE] File SDK '{dll_path}' non trovato!")
        return

    try:
        asi.init(dll_path)
    except Exception as e:
        print(f"[ERRORE] Inizializzazione SDK fallita: {e}")
        return

    if asi.get_num_cameras() == 0:
        print("[ERRORE] Nessuna camera ZWO rilevata via USB.")
        return

    camera = asi.Camera(config.CAMERA_INDEX)
    props = camera.get_camera_property()
    print("=" * 65)
    print(f"   CALIBRAZIONE SPETTRALE COLORE PER: {props['Name']}")
    print("=" * 65)
    print("\nISTRUZIONI:")
    print(" 1. Inquadra una superficie bianca/grigia neutra (un foglio bianco o cartoncino grigio 18%).")
    print(" 2. Assicurati che l'illuminazione sia uniforme (luce diurna o lampada diffusa).")
    print(" 3. L'esposizione verra' regolata per evitare la saturazione.\n")

    camera.set_control_value(asi.ASI_EXPOSURE, 30000)
    camera.set_control_value(asi.ASI_GAIN, 50)
    camera.set_control_value(asi.ASI_BANDWIDTHOVERLOAD, 90)
    camera.set_image_type(asi.ASI_IMG_RAW8)

    w = props['MaxWidth'] // 2
    h = props['MaxHeight'] // 2
    w -= (w % 8)
    h -= (h % 2)
    camera.set_roi_format(w, h, 2, asi.ASI_IMG_RAW8)

    camera.start_video_capture()
    print("Campionamento in corso di 15 frame consecutivi...")
    
    r_means = []
    g_means = []
    b_means = []

    for i in range(15):
        time.sleep(0.1)
        raw_frame = camera.capture_video_frame(timeout=1000)
        frame = np.frombuffer(raw_frame, dtype=np.uint8).reshape((h, w))
        
        # Debayering RGGB
        rgb = cv2.cvtColor(frame, cv2.COLOR_BayerRG2RGB)
        
        # Campionamento zona centrale (50% centrale dell'immagine)
        cy, cx = h // 2, w // 2
        dy, dx = h // 4, w // 4
        crop = rgb[cy - dy : cy + dy, cx - dx : cx + dx]

        r_means.append(np.mean(crop[:, :, 0]))
        g_means.append(np.mean(crop[:, :, 1]))
        b_means.append(np.mean(crop[:, :, 2]))

    camera.stop_video_capture()
    camera.close()

    avg_r = np.mean(r_means)
    avg_g = np.mean(g_means)
    avg_b = np.mean(b_means)

    print("\n--- RISULTATI RISPOSTA SPETTRALE NATIVA SENSORE ---")
    print(f"  Livello Medio Rosso (R) : {avg_r:.2f} / 255")
    print(f"  Livello Medio Verde (G) : {avg_g:.2f} / 255  (Riferimento)")
    print(f"  Livello Medio Blu (B)   : {avg_b:.2f} / 255")

    if avg_r > 0 and avg_b > 0:
        wb_r_gain = avg_g / avg_r
        wb_b_gain = avg_g / avg_b
        
        print("\n--- GUADAGNI BILANCIAMENTO BIANCO CALCOLATI ---")
        print(f"  Guadagno Ottimale Rosso (WB_R): {wb_r_gain:.2f}")
        print(f"  Guadagno Ottimale Verde (WB_G): 1.00 (Fisso)")
        print(f"  Guadagno Ottimale Blu (WB_B)  : {wb_b_gain:.2f}")
        print("\nPuoi inserire direttamente questi valori negli slider WB della GUI!")
    else:
        print("[ATTENZIONE] Livelli luce troppo bassi per una misura attendibile.")

    print("=" * 65)

if __name__ == "__main__":
    main()
