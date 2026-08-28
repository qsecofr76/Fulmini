import os
import sys

def get_app_dir():
    """Restituisce la cartella dell'eseguibile compilato o dello script sorgente."""
    if getattr(sys, 'frozen', False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))

def get_resource_path(relative_path):
    """Restituisce il percorso di una risorsa (icona, dll, json), gestendo PyInstaller."""
    # 1. Controlla prima nella cartella dell'applicazione (permettendo personalizzazioni utente)
    app_dir = get_app_dir()
    cand = os.path.join(app_dir, relative_path)
    if os.path.exists(cand):
        return cand

    # 2. Controlla nella cartella temporanea interna di PyInstaller
    if hasattr(sys, '_MEIPASS'):
        cand_meipass = os.path.join(sys._MEIPASS, relative_path)
        if os.path.exists(cand_meipass):
            return cand_meipass

    return cand

# --- Percorsi ---
BASE_DIR = get_app_dir()
SDK_DLL_PATH = get_resource_path("ASICamera2.dll")
OUTPUT_DIR = os.path.join(BASE_DIR, "captures")

# --- Parametri Camera ---
CAMERA_INDEX = 0             # Indice camera (0 se collegata una sola camera)
EXPOSURE_MS = 25.0           # Tempo di esposizione in millisecondi (25 ms = ~40 fps max)
GAIN = 80                    # Guadagno sensore (valore basso per alta dinamica ed evitare saturazione)
BANDWIDTH_OVERLOAD = 90      # Velocità bus USB 3.0 (40-100, 90 consigliato)
HIGH_SPEED_MODE = True       # Modalità High Speed 10-bit (aumenta nettamente gli FPS a piena risoluzione)
IMAGE_TYPE = "RAW8"          # "RAW8", "RAW16", o "RGB24" (RAW8 è il più veloce e leggero)
BINNING = 1                  # 1 = piena risoluzione (4144x2822), 2 = 2x2 binning (2072x1411, 45-60+ FPS!)

# --- Parametri Trigger e Buffer ---
PRE_EVENT_FRAMES = 6         # Numero di frame antecedenti il fulmine da salvare (es. 5-6)
POST_EVENT_SECONDS = 1.0     # Durata di registrazione post-fulmine in secondi
POST_EVENT_EXTRA_FRAMES = 25 # Frame post-evento calcolati (verranno adattati agli FPS effettivi)

# --- Algoritmo di Rilevamento Lampo ---
# Modalità di trigger:
# "DELTA": Scatta se la luminosità media supera la media mobile di DELTA_THRESHOLD
# "PIXEL_COUNT": Scatta se almeno MIN_ACTIVE_PIXELS superano la soglia di contrasto (+60% vs N frame prima)
# "MAX_PIXEL": Scatta se un gruppo di pixel supera MAX_PIXEL_THRESHOLD
# "HYBRID": Scatta se uno qualsiasi dei criteri è verificato (Consigliato per fulmini + laser)
TRIGGER_MODE = "HYBRID"

DELTA_THRESHOLD = 10.0         # Incremento luminosità media globale (scala 0-255)
PIXEL_CONTRAST_PERCENT = 60    # Incremento luminosità singolo pixel per essere 'acceso' (60% di 255 = ~153 ADU)
MIN_ACTIVE_PIXELS = 20         # Minimo numero di pixel accesi per far scattare il trigger (es. laser o scariche)
DIFF_FRAME_OFFSET = 6          # Numero di frame antecedenti con cui confrontare il frame corrente (default 6)
MAX_PIXEL_THRESHOLD = 235      # Soglia pixel singolo/cluster quasi saturo (scala 0-255)
EMA_ALPHA = 0.05               # Velocità di adattamento dello sfondo alle variazioni naturali di luce (0.01 - 0.1)
COOLDOWN_SECONDS = 0.5         # Pausa minima tra due trigger consecutivi

# --- Post-Processing Stacking (.TIFF) ---
AUTO_STACK_TIFF = False      # Scrive solo file .SER durante l'acquisizione. Lo stacking avviene dopo su richiesta o in batch.
STACK_METHOD = "MAX"         # "MAX" (Maximum Intensity Projection - Consigliato per fulmini) o "SUM" o "AVERAGE"
SAVE_JPEG_PREVIEW = True     # Salva anche un file .jpg per visualizzazione rapida durante lo stacking
