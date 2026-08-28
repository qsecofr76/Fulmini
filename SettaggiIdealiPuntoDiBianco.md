# 🌌 Guida alla Calibrazione Colore e Bilanciamento del Bianco per Sensori Sony IMX294 (ZWO ASI294MC)
### *Documento di Transito e Reimplementazione per Progetti di Astrofotografia & Stacking Via Lattea (Milky Way)*

---

## 📌 Indice
1. [Introduzione & Differenza tra RAW Astronomico e Immagine Reflex](#1-introduzione--differenza-tra-raw-astronomico-e-immagine-reflex)
2. [La Regola Fondamentale del Debayering OpenCV](#2-la-regola-fondamentale-del-debayering-opencv)
3. [Pipeline Colore in 3 Stadi](#3-pipeline-colore-in-3-stadi)
4. [Condivisione della Matrice di Calibrazione (`camera_calibration.json`)](#4-condivisione-della-matrice-di-calibrazione-cameracalibrationjson)
5. [Modulo Python Universale Pronto per il Progetto Via Lattea](#5-modulo-python-universale-pronto-per-il-progetto-via-lattea)
6. [Valori Consigliati e Settaggi di Default](#6-valori-consigliati-e-settaggi-di-default)

---

## 1. Introduzione & Differenza tra RAW Astronomico e Immagine Reflex

Le fotocamere reflex e mirrorless (Canon, Nikon, Sony) integrano un processore d'immagine hardware (**ISP - Image Signal Processor**) che applica automaticamente:
1. Bilanciamento del bianco per la temperatura colore della scena.
2. Matrice di rotazione colore $3 \times 3$ per sottrarre il *crosstalk* spettrale dei filtri Bayer.
3. Curva tonale non-lineare a "S" (S-Curve) con roll-off morbido delle alte luci.

Le camere astronomiche One-Shot-Color (OSC) come la **ZWO ASI294MC Pro** trasmettono dati **puramente lineari e non corretti**. Senza una pipeline dedicata:
* I filtri Bayer in silicio hanno un'efficienza quantica nel verde ($\approx 75\%$) superiore a quella del rosso ($\approx 60\%$) e del blu ($\approx 65\%$).
* L'immagine grezza appare sbiadita, con dominante verde/ciano e scarsa separazione tra nebulose rosse ($H\alpha$), polveri della Via Lattea e fondo cielo.

---

## 2. La Regola Fondamentale del Debayering OpenCV

> [!CAUTION]
> **ATTENZIONE ALL'ORDINE DEI CANALI IN OPENCV**
> In OpenCV, la costante `cv2.COLOR_BayerRG2RGB` produce un array con canali **`[Blu, Verde, Rosso]`** (BGR).  
> Se passato a librerie o interfacce che si aspettano `[Rosso, Verde, Blu]` (come Matplotlib, PIL, Qt, TIFF RGB), **il Rosso e il Blu risulteranno invertiti** (gli oggetti rossi appariranno blu scuro e viceversa).

### Mappatura Corretta per Sensore RGGB (ASI294MC):
Per ottenere un array NumPy con `Canale 0 = Rosso`, `Canale 1 = Verde`, `Canale 2 = Blu`:

```python
import cv2

# Per ASI294MC (Bayer Pattern 0 = RGGB):
# cv2.COLOR_BayerRG2BGR produce Canale 0 = Rosso, Canale 1 = Verde, Canale 2 = Blu
rgb_frame = cv2.cvtColor(bayer_raw8_or_raw16, cv2.COLOR_BayerRG2BGR)
```

---

## 3. Pipeline Colore in 3 Stadi

```
Matrice RAW Bayer (RGGB)
         │
         ▼
[ 1. Debayering Corretto cv2.COLOR_BayerRG2BGR ]
         │
         ▼
[ 2. Bilanciamento del Bianco (Moltiplicatori R, G, B) ]
         │
         ▼
[ 3. Matrice di Correzione Colore 3x3 (CCM D65 / Calibrata) ]
         │
         ▼
[ 4. Curva Tonale Fotografica S-Curve (Non-lineare) ]
         │
         ▼
Immagine Via Lattea Calibrata e Naturale (.TIFF / .JPG)
```

### A. Stadio 1: Guadagni di Bilanciamento del Bianco
I moltiplicatori bilanciano la sensibilità relativa del sensore:
$$R' = R \cdot WB_R, \quad G' = G \cdot 1.00, \quad B' = B \cdot WB_B$$

### B. Stadio 2: Matrice di Correzione Colore 3x3 (CCM)
Sottrae la sovrapposizione spettrale (crosstalk) e proietta i colori nello spazio standard **sRGB**:
$$\begin{bmatrix} R_{out} \\ G_{out} \\ B_{out} \end{bmatrix} = \begin{bmatrix} M_{00} & M_{01} & M_{02} \\ M_{10} & M_{11} & M_{12} \\ M_{20} & M_{21} & M_{22} \end{bmatrix} \begin{bmatrix} R' \\ G' \\ B' \end{bmatrix}$$

*I coefficienti su ciascuna riga sommano a $1.000$ per preservare esattamente la luminosità globale.*

### C. Stadio 3: Curva Tonale S-Curve (Photographic Tone Curve)
Per astrofotografia della Via Lattea, la curva S-Curve:
* **Abbassa e pulisce il fondo cielo** eliminando il rumore termico e l'inquinamento luminoso diffuso.
* **Aumenta il contrasto nei mediotoni** facendo risaltare le strutture di polveri scure e le nebulose.
* **Protegge le stelle e i centri galattici dalla saturazione dura (highlight roll-off)**.

Equazione polomiale morbida normalizzata su $[0, 1]$:
$$f(x) = x^2 \cdot (3 - 2x)$$

---

## 4. Condivisione della Matrice di Calibrazione (`camera_calibration.json`)

Non è necessario rifare la calibrazione nell'altro progetto. Il file JSON generato dal programma di calibrazione contiene già tutti i parametri ottimali calcolati sul tuo sensore:

```json
{
    "camera_model": "ZWO ASI294MC Pro",
    "calibration_date": "2026-08-25 14:10:00",
    "white_balance_gains": {
        "WB_R": 1.15,
        "WB_G": 1.00,
        "WB_B": 1.05
    },
    "color_correction_matrix_3x3": [
        [ 1.25, -0.20, -0.05 ],
        [-0.10,  1.20, -0.10 ],
        [-0.05, -0.20,  1.25 ]
    ],
    "mean_color_error_delta": 4.2
}
```

---

## 5. Modulo Python Universale Pronto per il Progetto Via Lattea

Puoi copiare direttamente questa funzione nel tuo progetto di stacking della Via Lattea. Funziona sia su singoli fotogrammi sia sul master stack finale:

```python
import os
import json
import numpy as np
import cv2
import tifffile

def load_calibration(json_path="camera_calibration.json"):
    """Carica la matrice di calibrazione e i guadagni WB dal file JSON condiviso."""
    if os.path.exists(json_path):
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        ccm = np.array(data.get("color_correction_matrix_3x3", np.eye(3)), dtype=np.float32)
        wb = data.get("white_balance_gains", {})
        wb_r = float(wb.get("WB_R", 1.15))
        wb_b = float(wb.get("WB_B", 1.05))
        return ccm, wb_r, wb_b
    else:
        # Valori di default calibrati per ASI294MC
        ccm_def = np.array([
            [ 1.25, -0.20, -0.05],
            [-0.10,  1.20, -0.10],
            [-0.05, -0.20,  1.25]
        ], dtype=np.float32)
        return ccm_def, 1.15, 1.05

def process_milkyway_color(raw_bayer_image, calib_json_path="camera_calibration.json", 
                           saturation=1.10, apply_scurve=True, gamma=1.0):
    """
    Applica l'intera pipeline colore Reflex sul frame o sullo stack della Via Lattea.
    
    :param raw_bayer_image: Array NumPy 2D Bayer (RAW8 o RAW16 da ASI294MC)
    :param calib_json_path: Percorso del file camera_calibration.json
    :param saturation: Moltiplicatore saturazione (es. 1.10 per esaltare i colori galattici)
    :param apply_scurve: Se True, applica la curva tonale fotografica per fondo cielo nero
    :param gamma: Valore Gamma per la luminosità dei mediotoni (default 1.0)
    :return: Array NumPy 3D RGB calibrato pronto per il salvataggio in TIFF/JPG
    """
    # 1. Debayering con corretta mappatura canali (Ch0=R, Ch1=G, Ch2=B)
    if len(raw_bayer_image.shape) == 2:
        rgb = cv2.cvtColor(raw_bayer_image, cv2.COLOR_BayerRG2BGR)
    else:
        rgb = raw_bayer_image.copy()

    # 2. Caricamento Calibrazione
    ccm, wb_r, wb_b = load_calibration(calib_json_path)

    # 3. Applicazione Guadagni WB + Matrice 3x3 Combinata
    wb_matrix = np.diag([wb_r, 1.0, wb_b]).astype(np.float32)
    combined_matrix = ccm @ wb_matrix

    # Modulazione Saturazione opzionale
    if saturation != 1.0:
        lum_r, lum_g, lum_b = 0.2126, 0.7152, 0.0722
        s = saturation
        sat_mat = np.array([
            [(1 - s) * lum_r + s, (1 - s) * lum_g,     (1 - s) * lum_b],
            [(1 - s) * lum_r,     (1 - s) * lum_g + s, (1 - s) * lum_b],
            [(1 - s) * lum_r,     (1 - s) * lum_g,     (1 - s) * lum_b + s]
        ], dtype=np.float32)
        combined_matrix = sat_mat @ combined_matrix

    # Trasformazione Colore Vettorializzata (< 1 ms in C++)
    transformed = cv2.transform(rgb, combined_matrix)

    # 4. Spazio continuo float32 per elaborazione ad alta dinamica
    if rgb.dtype == np.uint16:
        img_float = rgb.astype(np.float32) / 65535.0
    else:
        img_float = rgb.astype(np.float32) / 255.0

    # Trasformazione Colore Vettorializzata
    transformed = cv2.transform(img_float, combined_matrix)
    transformed = np.clip(transformed, 0.0, 1.0)

    # Curva Tonale S-Curve e Gamma su spazio continuo
    if apply_scurve:
        transformed = transformed * transformed * (3.0 - 2.0 * transformed)
        transformed = np.clip(transformed, 0.0, 1.0)

    if gamma != 1.0:
        inv_gamma = 1.0 / max(0.1, gamma)
        transformed = np.power(transformed, inv_gamma)

    # Generazione Array Finale a 16-BIT (uint16: 0 - 65535)
    final_16bit = np.clip(transformed * 65535.0, 0, 65535).astype(np.uint16)
    return final_16bit

# Esempio di utilizzo:
# master_stack = tifffile.imread("milkyway_raw_stack.tiff")
# color_stack_16bit = process_milkyway_color(master_stack, calib_json_path="camera_calibration.json")
# tifffile.imwrite("milkyway_calibrated_reflex_16bit.tiff", color_stack_16bit)
```

---

## 6. Valori Consigliati e Settaggi di Default

| Parametro | Valore Ottimale | Descrizione |
| :--- | :--- | :--- |
| **Bayer Conversion** | `cv2.COLOR_BayerRG2BGR` | Canale 0 = Rosso, Canale 1 = Verde, Canale 2 = Blu. |
| **Guadagno Rosso (`WB_R`)** | **`1.15`** | Corregge la minore sensibilità del silicio nel rosso. |
| **Guadagno Blu (`WB_B`)** | **`1.05`** | Bilancia il canale blu rispetto al verde di riferimento. |
| **Guadagno Verde (`WB_G`)** | **`1.00`** | Canale di riferimento fisso (peak QE sensore). |
| **Saturazione** | **`1.10x - 1.20x`** | Fa risaltare la nebulosità a emissione e le stelle gialle/blu. |
| **Curva S-Curve** | `Abilitata` | Mantiene il cielo notturno profondo senza clippare le stelle. |
