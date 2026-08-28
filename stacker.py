# ==============================================================================
# MODULO POST-PROCESSING: STACKING .SER -> .TIFF CON MOTORE COLORE REFLEX
# ==============================================================================
import os
import sys
import json
import argparse
import numpy as np
import tifffile
import cv2
from ser_writer import read_ser, COLOR_MONO, COLOR_BAYER_RGGB, COLOR_BAYER_BGGR, COLOR_BAYER_GRBG, COLOR_BAYER_GBRG, COLOR_RGB

def get_calibration_matrix():
    """Recupera la matrice di calibrazione da file JSON se esiste, altrimenti usa default calibrato."""
    json_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "camera_calibration.json")
    if os.path.exists(json_path):
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            ccm = np.array(data.get("color_correction_matrix_3x3", np.eye(3)), dtype=np.float32)
            wb = data.get("white_balance_gains", {})
            wb_r = float(wb.get("WB_R", 1.15))
            wb_b = float(wb.get("WB_B", 1.05))
            return ccm, wb_r, wb_b
        except Exception:
            pass
    # Default calibrato e moderato
    ccm_default = np.array([
        [ 1.25, -0.20, -0.05],
        [-0.10,  1.20, -0.10],
        [-0.05, -0.20,  1.25]
    ], dtype=np.float32)
    return ccm_default, 1.15, 1.05

def stack_frames(frames, method="MAX"):
    """
    Effettua lo stacking di una lista di frame.
    - "MAX": Maximum Intensity Projection (Lighten Blend) - Ideale per fulmini.
    - "SUM": Somma cumulativa lineare con clipping.
    - "AVERAGE": Media dei frame.
    """
    if not frames:
        raise ValueError("Nessun frame da sommare.")

    stack_array = np.stack(frames, axis=0)

    if method.upper() == "MAX":
        stacked = np.max(stack_array, axis=0)
    elif method.upper() == "SUM":
        summed = np.sum(stack_array.astype(np.float32), axis=0)
        max_val = 65535 if frames[0].dtype == np.uint16 else 255
        stacked = np.clip(summed, 0, max_val).astype(frames[0].dtype)
    elif method.upper() == "AVERAGE":
        avg = np.mean(stack_array.astype(np.float32), axis=0)
        stacked = avg.astype(frames[0].dtype)
    else:
        stacked = np.max(stack_array, axis=0)

    return stacked

def apply_reflex_color_pipeline(rgb_float, wb_r=None, wb_b=None, saturation=1.00, use_scurve=False):
    """
    Applica la pipeline colore calibrata su immagine normalizzata float32 [0.0, 1.0]:
    1. Guadagni di Bilanciamento del Bianco
    2. Matrice di Correzione Colore 3x3 (da file JSON o calibrata)
    3. Saturazione colore
    4. Curva Tonale S-Curve continua (opzionale)
    """
    ccm, def_r, def_b = get_calibration_matrix()
    if wb_r is None:
        wb_r = def_r
    if wb_b is None:
        wb_b = def_b

    # 1. Bilanciamento Bianco + Matrice
    wb_matrix = np.diag([wb_r, 1.0, wb_b]).astype(np.float32)
    ccm_combined = ccm @ wb_matrix

    # 2. Saturazione
    if saturation != 1.0:
        lum_r, lum_g, lum_b = 0.2126, 0.7152, 0.0722
        sat_mat = np.array([
            [(1 - saturation) * lum_r + saturation, (1 - saturation) * lum_g, (1 - saturation) * lum_b],
            [(1 - saturation) * lum_r, (1 - saturation) * lum_g + saturation, (1 - saturation) * lum_b],
            [(1 - saturation) * lum_r, (1 - saturation) * lum_g, (1 - saturation) * lum_b + saturation]
        ], dtype=np.float32)
        ccm_combined = sat_mat @ ccm_combined

    # 3. Applicazione CCM su spazio continuo float32
    transformed = cv2.transform(rgb_float, ccm_combined)
    transformed = np.clip(transformed, 0.0, 1.0)

    # 4. Curva Tonale S-Curve continua
    if use_scurve:
        transformed = transformed * transformed * (3.0 - 2.0 * transformed)
        transformed = np.clip(transformed, 0.0, 1.0)

    return transformed

def process_ser_file(ser_path, output_tiff_path=None, method="MAX", save_jpg=True, apply_dslr_color=True):
    """
    Legge un file .SER, esegue lo stacking, esegue il debayering a colori se applicabile,
    applica il profilo colore Reflex a 32-bit float e salva il file .TIFF a 16-BIT (uint16) e .JPG.
    """
    if not os.path.exists(ser_path):
        print(f"[ERRORE] File {ser_path} non trovato.")
        return None

    if output_tiff_path is None:
        output_tiff_path = os.path.splitext(ser_path)[0] + f"_stack_{method.lower()}.tiff"

    print(f"[STACKER] Lettura del file {ser_path}...")
    metadata, frames = read_ser(ser_path)
    print(f"[STACKER] Caricati {len(frames)} frame ({metadata['width']}x{metadata['height']}, {metadata['pixel_depth']} bit).")

    if not frames:
        print("[ERRORE] Il file .SER non contiene frame.")
        return None

    # Stacking dei frame
    stacked = stack_frames(frames, method=method)

    # Debayering se è un formato Bayer
    color_id = metadata.get('color_id', 0)
    
    cv2_bayer_map = {
        COLOR_BAYER_RGGB: cv2.COLOR_BayerRG2BGR,
        COLOR_BAYER_BGGR: cv2.COLOR_BayerBG2BGR,
        COLOR_BAYER_GRBG: cv2.COLOR_BayerGR2BGR,
        COLOR_BAYER_GBRG: cv2.COLOR_BayerGB2BGR
    }

    if color_id in cv2_bayer_map and len(stacked.shape) == 2:
        bayer_code = cv2_bayer_map[color_id]
        rgb_stacked = cv2.cvtColor(stacked, bayer_code)
    elif color_id == COLOR_RGB or len(stacked.shape) == 3:
        rgb_stacked = stacked
    else:
        rgb_stacked = stacked # Monocromatico

    # Conversione in spazio continuo float32 [0.0, 1.0] ad alta dinamica
    if rgb_stacked.dtype == np.uint16:
        img_float = rgb_stacked.astype(np.float32) / 65535.0
    else:
        img_float = rgb_stacked.astype(np.float32) / 255.0

    # Applicazione Pipeline Colore Reflex a 32-bit float
    if apply_dslr_color and len(img_float.shape) == 3:
        processed_float = apply_reflex_color_pipeline(img_float)
    else:
        processed_float = np.clip(img_float, 0.0, 1.0)

    # Generazione TIFF finale a 16-BIT REALI (uint16, 0 - 65535)
    final_16bit = np.clip(processed_float * 65535.0, 0, 65535).astype(np.uint16)

    # Salvataggio TIFF a 16 bit
    tifffile.imwrite(output_tiff_path, final_16bit)
    print(f"[STACKER] Salvato file TIFF a 16-BIT (uint16): {output_tiff_path}")

    # Salvataggio eventuale JPG per anteprima rapida (8-bit)
    if save_jpg:
        jpg_path = os.path.splitext(output_tiff_path)[0] + ".jpg"
        preview_8bit = (final_16bit >> 8).astype(np.uint8)
        
        if len(preview_8bit.shape) == 2:
            cv2.imwrite(jpg_path, preview_8bit)
        else:
            cv2.imwrite(jpg_path, cv2.cvtColor(preview_8bit, cv2.COLOR_RGB2BGR))
        print(f"[STACKER] Salvata anteprima JPG (8-bit): {jpg_path}")

    return output_tiff_path

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Stacker .SER -> .TIFF con Profilo Colore Reflex")
    parser.add_argument("file", nargs="?", help="Percorso del file .ser da elaborare")
    parser.add_argument("--dir", default="captures", help="Cartella con i file .ser da elaborare in batch")
    parser.add_argument("--method", default="MAX", choices=["MAX", "SUM", "AVERAGE"], help="Metodo di stacking")
    args = parser.parse_args()

    if args.file:
        process_ser_file(args.file, method=args.method)
    else:
        if os.path.exists(args.dir):
            ser_files = [os.path.join(args.dir, f) for f in os.listdir(args.dir) if f.lower().endswith(".ser")]
            if not ser_files:
                print(f"Nessun file .ser trovato in '{args.dir}'.")
            for ser in ser_files:
                process_ser_file(ser, method=args.method)
        else:
            print(f"Cartella '{args.dir}' non trovata.")
