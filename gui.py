# ==============================================================================
# INTERFACCIA GRAFICA (GUI) PER CATTURA FULMINI - ZWO ASI CAMERA
# MOTORE COLORE CALIBRATO, AWB ROBUSTO & RENDERING AD ALTE PRESTAZIONI
# ==============================================================================
import os
import sys
import time
import queue
import logging
import threading
from collections import deque
import numpy as np
import cv2
import tifffile

logging.getLogger().setLevel(logging.ERROR)
import zwoasi as asi

from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer, QSize
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QPushButton, QSlider, QSpinBox, QDoubleSpinBox, QCheckBox, QComboBox,
    QGroupBox, QTabWidget, QScrollArea, QListWidget, QListWidgetItem, QProgressBar,
    QFileDialog, QFrame, QSplitter, QMessageBox
)
from PyQt6.QtGui import QImage, QPixmap, QFont, QIcon, QColor, QPalette

import config
from ser_writer import write_ser, COLOR_MONO, COLOR_BAYER_RGGB, COLOR_BAYER_BGGR, COLOR_BAYER_GRBG, COLOR_BAYER_GBRG, COLOR_RGB
from stacker import stack_frames, process_ser_file

# ==============================================================================
# MATRICI DI CALIBRAZIONE COLORE MODERATE (SENZA SATURAZIONE ARTIFICIALE)
# ==============================================================================
CCM_D65_SOFT = np.array([
    [ 1.25, -0.20, -0.05],
    [-0.10,  1.20, -0.10],
    [-0.05, -0.20,  1.25]
], dtype=np.float32)

CCM_IDENTITY = np.eye(3, dtype=np.float32)

# ==============================================================================
# WORKER THREAD ACQUISIZIONE CAMERA (THREAD-SAFE & DEBAYERING RGB OTTIMALE)
# ==============================================================================
class CameraWorker(QThread):
    preview_ready = pyqtSignal(np.ndarray) # Invia frame RGB a colori naturale
    stats_updated = pyqtSignal(float, float, float, float, float, float, float, float, int, float) # fps, base, mean, delta, temp, R, G, B, active_px, max_px
    lightning_detected = pyqtSignal(dict)
    camera_error = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.running = False
        self.camera = None
        self.camera_props = {}
        self.is_armed = False
        
        # Parametri runtime
        self.exposure_ms = float(config.EXPOSURE_MS)
        self.gain = int(config.GAIN)
        self.bandwidth = int(config.BANDWIDTH_OVERLOAD)
        self.high_speed = bool(getattr(config, 'HIGH_SPEED_MODE', True))
        self.binning = int(config.BINNING)
        self.pre_event_frames_count = int(config.PRE_EVENT_FRAMES)
        self.post_event_seconds = float(config.POST_EVENT_SECONDS)
        self.trigger_mode = str(config.TRIGGER_MODE)
        self.delta_threshold = float(config.DELTA_THRESHOLD)
        self.pixel_contrast_percent = int(getattr(config, 'PIXEL_CONTRAST_PERCENT', 60))
        self.min_active_pixels = int(getattr(config, 'MIN_ACTIVE_PIXELS', 20))
        self.diff_frame_offset = int(getattr(config, 'DIFF_FRAME_OFFSET', 6))
        self.max_pixel_threshold = float(config.MAX_PIXEL_THRESHOLD)
        self.cooldown_sec = float(config.COOLDOWN_SECONDS)
        
        self.cmd_queue = queue.Queue()
        self.saver_queue = queue.Queue()
        
        buf_size = max(self.pre_event_frames_count, self.diff_frame_offset + 1)
        self.pre_buffer = deque(maxlen=buf_size)
        self.rolling_baseline = None
        self.is_capturing_event = False
        self.event_frames = []
        self.frames_remaining_post = 0
        self.last_trigger_time = 0
        self.width = 0
        self.height = 0
        self.color_id = COLOR_MONO

    def update_trigger_settings(self, mode, delta_th, contrast_pct, min_px, diff_offset, pre_count, post_sec):
        self.trigger_mode = str(mode)
        self.delta_threshold = float(delta_th)
        self.pixel_contrast_percent = int(contrast_pct)
        self.min_active_pixels = int(min_px)
        self.diff_frame_offset = int(diff_offset)
        self.pre_event_frames_count = int(pre_count)
        self.post_event_seconds = float(post_sec)
        
        buf_size = max(self.pre_event_frames_count, self.diff_frame_offset + 1)
        if self.pre_buffer.maxlen != buf_size:
            old_items = list(self.pre_buffer)
            self.pre_buffer = deque(old_items, maxlen=buf_size)

    def initialize_camera(self):
        dll_path = os.path.abspath(config.SDK_DLL_PATH)
        if not os.path.exists(dll_path):
            self.camera_error.emit(f"DLL SDK non trovata in: {dll_path}")
            return False

        try:
            asi.init(dll_path)
        except Exception as e:
            self.camera_error.emit(f"Errore inizializzazione SDK ZWO: {e}")
            return False

        num_cams = asi.get_num_cameras()
        if num_cams == 0:
            self.camera_error.emit("Nessuna camera ZWO rilevata via USB.")
            return False

        try:
            self.camera = asi.Camera(config.CAMERA_INDEX)
            self.camera_props = self.camera.get_camera_property()
            self._apply_camera_reconfig()
            return True
        except Exception as e:
            self.camera_error.emit(f"Errore connessione camera: {e}")
            return False

    def _apply_camera_reconfig(self):
        if not self.camera:
            return

        max_w = self.camera_props['MaxWidth']
        max_h = self.camera_props['MaxHeight']

        w = max_w // self.binning
        w -= (w % 8)
        h = max_h // self.binning
        h -= (h % 2)

        self.width = w
        self.height = h

        self.camera.set_image_type(asi.ASI_IMG_RAW8)
        self.camera.set_roi_format(w, h, self.binning, asi.ASI_IMG_RAW8)

        self.camera.set_control_value(asi.ASI_EXPOSURE, int(self.exposure_ms * 1000))
        self.camera.set_control_value(asi.ASI_GAIN, int(self.gain))
        self.camera.set_control_value(asi.ASI_BANDWIDTHOVERLOAD, int(self.bandwidth))
        
        controls = self.camera.get_controls()
        if 'HighSpeedMode' in controls:
            try:
                self.camera.set_control_value(asi.ASI_HIGH_SPEED_MODE, 1 if self.high_speed else 0)
            except Exception:
                pass

        bayer_pat = self.camera_props.get('BayerPattern', 0)
        is_col = self.camera_props.get('IsColorCam', False)
        mapping = {0: COLOR_BAYER_RGGB, 1: COLOR_BAYER_BGGR, 2: COLOR_BAYER_GRBG, 3: COLOR_BAYER_GBRG}
        self.color_id = mapping.get(bayer_pat, COLOR_BAYER_RGGB) if is_col else COLOR_MONO

    def update_exposure(self, val_ms):
        self.exposure_ms = float(val_ms)
        self.cmd_queue.put(('SET_CONTROL', asi.ASI_EXPOSURE, int(val_ms * 1000)))

    def update_gain(self, val_gain):
        self.gain = int(val_gain)
        self.cmd_queue.put(('SET_CONTROL', asi.ASI_GAIN, int(val_gain)))

    def update_binning(self, bin_val):
        self.binning = int(bin_val)
        self.cmd_queue.put(('RECONFIG', None, None))

    def update_high_speed(self, enabled):
        self.high_speed = bool(enabled)
        self.cmd_queue.put(('RECONFIG', None, None))

    def set_cooler(self, on, target_temp=0):
        self.cmd_queue.put(('COOLER', on, target_temp))

    def _process_pending_commands(self):
        while not self.cmd_queue.empty():
            try:
                cmd, arg1, arg2 = self.cmd_queue.get_nowait()
                if cmd == 'SET_CONTROL' and self.camera:
                    self.camera.set_control_value(arg1, arg2)
                elif cmd == 'RECONFIG' and self.camera:
                    try:
                        self.camera.stop_video_capture()
                    except Exception:
                        pass
                    self._apply_camera_reconfig()
                    self.camera.start_video_capture()
                elif cmd == 'COOLER' and self.camera:
                    if 'CoolerOn' in self.camera.get_controls():
                        try:
                            self.camera.set_control_value(asi.ASI_COOLER_ON, 1 if arg1 else 0)
                            self.camera.set_control_value(asi.ASI_TARGET_TEMP, int(arg2))
                        except Exception:
                            pass
                self.cmd_queue.task_done()
            except queue.Empty:
                break
            except Exception as e:
                print(f"[CMD ERR] {e}")

    def run(self):
        if not self.camera and not self.initialize_camera():
            return

        try:
            self.camera.start_video_capture()
        except Exception as e:
            self.camera_error.emit(f"Errore avvio video: {e}")
            return

        self.running = True
        fps_timer = time.time()
        frame_counter = 0
        current_fps = 0.0
        temp_timer = 0
        current_temp = 0.0
        last_preview_time = 0

        is_color = self.camera_props.get('IsColorCam', False)
        bayer_pat = self.camera_props.get('BayerPattern', 0)

        # Mappatura OpenCV Bayer (ASI294MC è RGGB -> COLOR_BayerRG2BGR produce Ch0=Rosso, Ch1=Verde, Ch2=Blu)
        cv2_bayer_map = {
            0: cv2.COLOR_BayerRG2BGR,
            1: cv2.COLOR_BayerBG2BGR,
            2: cv2.COLOR_BayerGR2BGR,
            3: cv2.COLOR_BayerGB2BGR
        }
        bayer_code = cv2_bayer_map.get(bayer_pat, cv2.COLOR_BayerRG2BGR)

        while self.running:
            self._process_pending_commands()
            timeout_ms = int(max(600, (self.exposure_ms * 2) + 500))

            try:
                raw_frame = self.camera.capture_video_frame(timeout=timeout_ms)
            except Exception:
                time.sleep(0.01)
                continue

            if raw_frame is None:
                continue

            if isinstance(raw_frame, np.ndarray):
                frame = raw_frame
            else:
                frame = np.frombuffer(raw_frame, dtype=np.uint8).reshape((self.height, self.width))

            if len(frame.shape) >= 2:
                if frame.shape[0] != self.height or frame.shape[1] != self.width:
                    self.height, self.width = frame.shape[0], frame.shape[1]

            frame_counter += 1
            now = time.time()
            if now - fps_timer >= 0.5:
                current_fps = frame_counter / (now - fps_timer)
                frame_counter = 0
                fps_timer = now

            if now - temp_timer >= 2.0:
                temp_timer = now
                try:
                    current_temp = self.camera.get_control_value(asi.ASI_TEMPERATURE)[0] / 10.0
                except Exception:
                    current_temp = 0.0

            # 1. Metriche luminosità rapide calcolate sempre per la GUI
            eval_sample = frame[::2, ::2] # Sottocampionamento 2x2 per precisione e velocità (< 0.4 ms)
            current_mean = float(np.mean(eval_sample))
            current_max = float(np.max(eval_sample))

            if self.rolling_baseline is None or not isinstance(self.rolling_baseline, (int, float)):
                self.rolling_baseline = current_mean
            elif not self.is_capturing_event:
                self.rolling_baseline = 0.95 * float(self.rolling_baseline) + 0.05 * current_mean

            delta = float(current_mean - float(self.rolling_baseline))

            # 2. Gestione Acquisizione Evento o Ascolto Trigger
            if self.is_capturing_event:
                # REGISTRA E BASTA: nessun calcolo differenziale o trigger durante la scrittura
                self.event_frames.append(frame.copy())
                self.frames_remaining_post -= 1
                
                if self.frames_remaining_post <= 0:
                    ts_str = time.strftime("%Y%m%d_%H%M%S")
                    self.saver_queue.put((
                        ts_str, self.event_frames, self.width, self.height,
                        self.color_id, self.camera_props.get('Name', 'ASI Camera')
                    ))
                    self.is_capturing_event = False
                    self.event_frames = []
                    self.pre_buffer.clear()
                    self.rolling_baseline = current_mean
                active_pixels_count = 0
            else:
                # Calcolo Punti Accesi ad Alto Contrasto vs Frame di Riferimento (es. 6 frame fa)
                contrast_threshold = int(self.pixel_contrast_percent * 255.0 / 100.0)
                active_pixels_count = 0
                
                if len(self.pre_buffer) >= self.diff_frame_offset:
                    ref_frame = self.pre_buffer[-self.diff_frame_offset]
                    ref_eval = ref_frame[::2, ::2]
                    if ref_eval.shape == eval_sample.shape:
                        diff = cv2.subtract(eval_sample, ref_eval)
                        active_mask = diff >= contrast_threshold
                        active_pixels_count = int(np.count_nonzero(active_mask)) * 4
                    else:
                        self.pre_buffer.clear()
                        active_pixels_count = 0
                else:
                    active_pixels_count = 0

                # Logica Trigger Multi-Criterio (valutata solo se esplicitamente ARMATO dall'utente)
                triggered = False
                trigger_reason = ""
                if self.is_armed and (now - self.last_trigger_time > self.cooldown_sec):
                    # Criterio A: Punti accesi ad alto contrasto (Laser, Scariche filiformi, Lampi locali)
                    if self.trigger_mode in ("PIXEL_COUNT", "HYBRID") and active_pixels_count >= self.min_active_pixels:
                        triggered = True
                        trigger_reason = f"Laser/Punti: {active_pixels_count} px (+{self.pixel_contrast_percent}%)"
                    # Criterio B: Flash diffuso globale (Aumento luminosità media dell'intera scena)
                    elif self.trigger_mode in ("DELTA", "HYBRID") and delta >= self.delta_threshold:
                        triggered = True
                        trigger_reason = f"Flash Diffuso: Delta +{delta:.1f}"
                    # Criterio C: Solo se impostato esplicitamente su "Solo Picco" (evita falsi scatti su lampadine)
                    elif self.trigger_mode == "MAX_PIXEL" and current_max >= self.max_pixel_threshold:
                        triggered = True
                        trigger_reason = f"Picco Saturazione: {current_max:.0f}"

                    if triggered:
                        self.last_trigger_time = now
                        self.is_capturing_event = True
                        fps_est = current_fps if current_fps > 10 else (1000.0 / self.exposure_ms)
                        self.frames_remaining_post = int(fps_est * self.post_event_seconds)

                        self.event_frames = [f.copy() for f in self.pre_buffer]
                        self.event_frames.append(frame.copy())
                        
                        self.lightning_detected.emit({
                            "reason": trigger_reason,
                            "delta": delta,
                            "active_pixels": active_pixels_count,
                            "pre_count": len(self.pre_buffer),
                            "post_count": self.frames_remaining_post
                        })
                    else:
                        self.pre_buffer.append(frame.copy())
                else:
                    self.pre_buffer.append(frame.copy())

            # Generazione Preview (Debayering completo e corretto prima del resize)
            if now - last_preview_time >= 0.033:
                last_preview_time = now
                
                if is_color:
                    rgb_full = cv2.cvtColor(frame, bayer_code)
                else:
                    rgb_full = cv2.cvtColor(frame, cv2.COLOR_GRAY2RGB)

                # Calcolo canali R, G, B reali dal frame
                sample_rgb = rgb_full[::16, ::16]
                mean_r = float(np.mean(sample_rgb[:, :, 0]))
                mean_g = float(np.mean(sample_rgb[:, :, 1]))
                mean_b = float(np.mean(sample_rgb[:, :, 2]))

                # Scalatura per il display per non appesantire la GUI
                if self.width > 1200:
                    rgb_preview = rgb_full[::2, ::2, :]
                else:
                    rgb_preview = rgb_full

                self.preview_ready.emit(rgb_preview)
                safe_base = float(self.rolling_baseline) if self.rolling_baseline is not None else float(current_mean)
                self.stats_updated.emit(
                    float(current_fps),
                    safe_base,
                    float(current_mean),
                    float(delta),
                    float(current_temp),
                    float(mean_r),
                    float(mean_g),
                    float(mean_b),
                    int(active_pixels_count),
                    float(current_max)
                )

        try:
            self.camera.stop_video_capture()
            self.camera.close()
        except Exception:
            pass

    def stop(self):
        self.running = False
        self.wait(2000)

# ==============================================================================
# WORKER THREAD SALVATAGGIO ASINCRONO (SOLO FILE .SER DURANTE L'ACQUISIZIONE)
# ==============================================================================
class SaverWorker(QThread):
    save_completed = pyqtSignal(str, str) # ser_path, ts_str

    def __init__(self, in_queue):
        super().__init__()
        self.in_queue = in_queue
        self.running = True

    def run(self):
        while self.running or not self.in_queue.empty():
            try:
                task = self.in_queue.get(timeout=0.5)
            except queue.Empty:
                continue

            if task is None:
                break

            ts_str, frames_to_save, width, height, color_id, cam_name = task
            ser_path = os.path.join(config.OUTPUT_DIR, f"lightning_{ts_str}.ser")

            try:
                # Durante l'acquisizione salviamo SOLO il file .SER per massima reattività e zero carico CPU
                write_ser(
                    filename=ser_path,
                    frames=frames_to_save,
                    width=width,
                    height=height,
                    color_id=color_id,
                    pixel_depth=8,
                    observer="Fulmini",
                    instrument=cam_name
                )
                print(f"[SAVER] Scritto file .SER: {ser_path} ({len(frames_to_save)} frame)")
                self.save_completed.emit(ser_path, ts_str)
            except Exception as e:
                print(f"[ERRORE SAVER] {e}")
            finally:
                self.in_queue.task_done()

    def stop(self):
        self.running = False
        self.in_queue.put(None)
        self.wait(3000)

# ==============================================================================
# WORKER THREAD BATCH STACKING .SER -> .TIFF (POST-PROCESSING INTERROMP砕LE)
# ==============================================================================
class BatchStackerWorker(QThread):
    progress = pyqtSignal(int, int, str)      # current, total, filename
    item_processed = pyqtSignal(str, str)    # ser_path, tiff_path
    finished_batch = pyqtSignal(int, bool)   # total_processed, was_interrupted

    def __init__(self, captures_dir, stack_method="MAX"):
        super().__init__()
        self.captures_dir = captures_dir
        self.stack_method = stack_method
        self.running = True

    def run(self):
        if not os.path.exists(self.captures_dir):
            self.finished_batch.emit(0, False)
            return

        all_ser = [
            os.path.join(self.captures_dir, f)
            for f in os.listdir(self.captures_dir)
            if f.lower().endswith(".ser")
        ]
        all_ser.sort(reverse=True) # Elabora i più recenti per primi
        
        # Filtra i file .ser che non hanno ancora generato il file _sum.tiff
        to_process = []
        for ser in all_ser:
            base_name = os.path.splitext(ser)[0]
            tiff_cand = f"{base_name}_sum.tiff"
            if not os.path.exists(tiff_cand):
                to_process.append((ser, tiff_cand))

        total = len(to_process)
        if total == 0:
            self.finished_batch.emit(0, False)
            return

        processed_count = 0
        was_interrupted = False
        for i, (ser_path, tiff_path) in enumerate(to_process):
            if not self.running:
                was_interrupted = True
                break
            
            fname = os.path.basename(ser_path)
            self.progress.emit(i + 1, total, fname)
            
            try:
                process_ser_file(
                    ser_path=ser_path,
                    output_tiff_path=tiff_path,
                    method=self.stack_method,
                    save_jpg=True
                )
                self.item_processed.emit(ser_path, tiff_path)
                processed_count += 1
            except Exception as e:
                print(f"[BATCH STACK ERR] {fname}: {e}")

        if not self.running:
            was_interrupted = True

        self.finished_batch.emit(processed_count, was_interrupted)

    def stop(self):
        self.running = False

# ==============================================================================
# MAIN WINDOW GUI (PYQT6) - MOTORE COLORE NATURALE & AWB ROBUSTO
# ==============================================================================
class LightningHunterGUI(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("⚡ Fulmini - Rilevamento & Cattura Fulmini ZWO ASI")
        self.resize(1380, 890)
        self.setMinimumSize(1080, 720)

        # Icona applicazione
        icon_path = config.get_resource_path("icon.png")
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))

        os.makedirs(config.OUTPUT_DIR, exist_ok=True)
        
        # --- Parametri Motore Colore Naturale Calibrato ---
        # Guadagni normalizzati intorno a 1.0 (senza forzature distruttive)
        self.wb_r_val = 1.15
        self.wb_b_val = 1.05
        self.saturation_val = 1.00
        self.gamma_val = 1.00
        self.use_s_curve = False
        self.color_mode = "NATURAL" # "NATURAL", "REFLEX_SOFT", "RAW"
        
        self.auto_stretch = False
        self.stretch_black_point = 0.0
        self.stretch_white_point = 255.0
        self.request_calc_stretch = False
        self.request_calc_awb = False
        
        self.cached_lut = np.arange(256, dtype=np.uint8)
        self.total_captures = 0
        self.custom_calib_data = None
        self.batch_worker = None
        self.load_custom_calibration()

        self.update_lut()
        self.apply_dark_theme()
        self.init_ui()
        self.scan_and_populate_gallery()
        self.start_camera_threads()

    def load_custom_calibration(self):
        """Carica il file camera_calibration.json se presente."""
        json_path = config.get_resource_path("camera_calibration.json")
        if os.path.exists(json_path):
            try:
                import json
                with open(json_path, "r", encoding="utf-8") as f:
                    self.custom_calib_data = json.load(f)
                
                wb = self.custom_calib_data.get("white_balance_gains", {})
                self.wb_r_val = float(wb.get("WB_R", 1.15))
                self.wb_b_val = float(wb.get("WB_B", 1.05))
                self.color_mode = "CUSTOM_JSON"
                print(f"[CALIB] Caricata calibrazione personalizzata da {json_path}")
            except Exception as e:
                print(f"[CALIB ERR] {e}")

    def update_lut(self):
        """Calcola la Look-Up Table (LUT) a 256 byte per Stretch, Gamma ed eventuale S-Curve."""
        bp = float(self.stretch_black_point) if self.auto_stretch else 0.0
        wp = float(self.stretch_white_point) if self.auto_stretch else 255.0
        if wp <= bp:
            wp = bp + 1.0
        
        inv_gamma = 1.0 / max(0.1, self.gamma_val)
        lut = np.zeros(256, dtype=np.uint8)
        
        for i in range(256):
            norm = np.clip((float(i) - bp) / (wp - bp), 0.0, 1.0)
            
            if self.use_s_curve:
                x = norm
                norm = x * x * (3.0 - 2.0 * x)

            val = (norm ** inv_gamma) * 255.0
            lut[i] = int(np.clip(val, 0.0, 255.0))
            
        self.cached_lut = lut

    def apply_dark_theme(self):
        self.setStyleSheet("""
            QMainWindow { background-color: #121418; color: #e0e6ed; }
            QWidget { color: #d1d8e0; font-family: 'Segoe UI', Arial, sans-serif; font-size: 12px; }
            QGroupBox {
                border: 1px solid #2d3436; border-radius: 6px; margin-top: 6px;
                font-weight: bold; color: #00d2ff; padding: 10px 6px 6px 6px; background-color: #181b22;
            }
            QGroupBox::title { subcontrol-origin: margin; subcontrol-position: top left; padding: 0 4px; }
            QTabWidget::pane { border: 1px solid #2d3436; background-color: #181b22; border-radius: 6px; }
            QTabBar::tab {
                background: #121418; border: 1px solid #2d3436; padding: 6px 12px;
                border-top-left-radius: 6px; border-top-right-radius: 6px; margin-right: 2px; color: #8395a7; font-weight: bold;
            }
            QTabBar::tab:selected {
                background: #181b22; color: #00d2ff; font-weight: bold; border-bottom: 2px solid #00d2ff;
            }
            QPushButton {
                background-color: #2b303c; border: 1px solid #3d4454; border-radius: 5px;
                padding: 6px 10px; font-weight: bold; color: #ffffff;
            }
            QPushButton:hover { background-color: #3b4252; border-color: #00d2ff; }
            QPushButton:pressed { background-color: #1f232b; }
            QPushButton#armBtn {
                background-color: #10ac84; border: 1px solid #1dd1a1; font-size: 13px; padding: 9px;
            }
            QPushButton#armBtn:hover { background-color: #1dd1a1; }
            QPushButton#armBtn[armed="true"] { background-color: #ee5253; border-color: #ff6b6b; }
            QSlider::groove:horizontal { height: 5px; background: #2b303c; border-radius: 2px; }
            QSlider::sub-page:horizontal { background: #00d2ff; border-radius: 2px; }
            QSlider::handle:horizontal {
                background: #ffffff; border: 1px solid #00d2ff; width: 14px;
                margin-top: -5px; margin-bottom: -5px; border-radius: 7px;
            }
            QSpinBox, QDoubleSpinBox {
                background-color: #242833; border: 1px solid #3d4454; border-radius: 4px;
                padding: 3px 6px; color: #ffffff; min-height: 20px;
            }
            QComboBox {
                background-color: #242833; border: 1px solid #3d4454; border-radius: 4px;
                padding: 4px 8px; color: #ffffff; min-height: 20px;
            }
            QComboBox:hover { border-color: #00d2ff; }
            QComboBox::drop-down {
                subcontrol-origin: padding; subcontrol-position: top right; width: 22px;
                border-left: 1px solid #3d4454; background-color: #1f232d;
                border-top-right-radius: 4px; border-bottom-right-radius: 4px;
            }
            QComboBox::down-arrow {
                border-left: 4px solid transparent; border-right: 4px solid transparent;
                border-top: 5px solid #00d2ff; width: 0; height: 0;
            }
            QComboBox QAbstractItemView {
                background-color: #181b22; color: #ffffff; border: 1px solid #00d2ff;
                border-radius: 4px; selection-background-color: #00d2ff; selection-color: #000000;
                padding: 4px; outline: none;
            }
            QComboBox QAbstractItemView::item {
                min-height: 26px; padding: 4px 8px; background-color: #181b22; color: #ffffff; border-radius: 3px;
            }
            QComboBox QAbstractItemView::item:hover { background-color: #2b3548; color: #00d2ff; }
            QComboBox QAbstractItemView::item:selected { background-color: #00d2ff; color: #000000; font-weight: bold; }
            QListWidget {
                background-color: #14171d; border: 1px solid #2d3436; border-radius: 5px; padding: 3px;
            }
            QListWidget::item { padding: 6px; border-bottom: 1px solid #20242e; border-radius: 3px; }
            QListWidget::item:selected { background-color: #2b3548; color: #00d2ff; }
            QProgressBar {
                background-color: #14171d; border: 1px solid #2d3436; border-radius: 3px;
                text-align: center; color: #ffffff; font-weight: bold; font-size: 11px;
            }
            QProgressBar::chunk { background-color: #00d2ff; border-radius: 2px; }
        """)

    def init_ui(self):
        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        main_layout = QVBoxLayout(main_widget)
        main_layout.setContentsMargins(10, 8, 10, 8)
        main_layout.setSpacing(8)

        # --- TOP STATUS BAR ---
        top_bar = QHBoxLayout()
        self.status_badge = QLabel("● STATO: MONITORAGGIO (DISARMATO)")
        self.status_badge.setStyleSheet("font-size: 13px; font-weight: bold; color: #8395a7;")
        top_bar.addWidget(self.status_badge)

        top_bar.addStretch()

        self.exposure_warning_label = QLabel("")
        self.exposure_warning_label.setStyleSheet("color: #ff4757; font-weight: bold; margin-right: 15px;")
        top_bar.addWidget(self.exposure_warning_label)

        self.rgb_levels_label = QLabel("R: -- | G: -- | B: --")
        self.rgb_levels_label.setStyleSheet("font-weight: bold; color: #dfe4ea; margin-right: 12px;")
        top_bar.addWidget(self.rgb_levels_label)

        self.fps_label = QLabel("FPS: --")
        self.fps_label.setStyleSheet("font-weight: bold; color: #00d2ff; font-size: 13px; margin-right: 12px;")
        top_bar.addWidget(self.fps_label)

        self.temp_label = QLabel("Temp: -- °C")
        self.temp_label.setStyleSheet("font-weight: bold; color: #a4b0be; margin-right: 12px;")
        top_bar.addWidget(self.temp_label)

        self.open_folder_btn = QPushButton("📁 Cartella")
        self.open_folder_btn.clicked.connect(self.open_captures_folder)
        top_bar.addWidget(self.open_folder_btn)

        self.cleaner_btn = QPushButton("🧹 Pulizia")
        self.cleaner_btn.setStyleSheet("background-color: #eb4d4b; color: #ffffff; font-weight: bold;")
        self.cleaner_btn.setToolTip("Apri l'utility di visualizzazione ed eliminazione catture (JPG, TIFF, SER)")
        self.cleaner_btn.clicked.connect(self.open_cleaner_tool)
        top_bar.addWidget(self.cleaner_btn)

        main_layout.addLayout(top_bar)

        # --- CENTRAL SPLITTER ---
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # 1. VIEWPORT VIDEO
        video_container = QWidget()
        video_layout = QVBoxLayout(video_container)
        video_layout.setContentsMargins(0, 0, 0, 0)
        video_layout.setSpacing(4)

        self.video_label = QLabel("In attesa dello streaming camera ZWO...")
        self.video_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.video_label.setStyleSheet("background-color: #000000; border: 2px solid #2d3436; border-radius: 8px;")
        self.video_label.setMinimumSize(600, 440)
        video_layout.addWidget(self.video_label, stretch=1)

        # DASHBOARD MONITOR TRIGGER IN TEMPO REALE (ATTIVA ANCHE DA DISARMATO)
        trig_mon_box = QGroupBox("📊 Monitor Trigger Live (Attivo anche in Monitoraggio per Test)")
        trig_mon_layout = QGridLayout(trig_mon_box)
        trig_mon_layout.setContentsMargins(8, 4, 8, 4)
        trig_mon_layout.setHorizontalSpacing(8)
        trig_mon_layout.setVerticalSpacing(2)

        # 1. Punti Accesi ad Alto Contrasto
        trig_mon_layout.addWidget(QLabel("⚡ <b>Punti Accesi (+60% vs 6f):</b>"), 0, 0)
        self.active_px_bar = QProgressBar()
        self.active_px_bar.setRange(0, 100)
        self.active_px_bar.setValue(0)
        self.active_px_bar.setFixedHeight(14)
        trig_mon_layout.addWidget(self.active_px_bar, 0, 1)
        self.active_px_lbl = QLabel("0 px")
        self.active_px_lbl.setStyleSheet("font-weight: bold; color: #00d2ff; min-width: 60px;")
        trig_mon_layout.addWidget(self.active_px_lbl, 0, 2)

        # 2. Delta Medio Globale
        trig_mon_layout.addWidget(QLabel("☁️ <b>Delta Medio:</b>"), 0, 3)
        self.delta_bar = QProgressBar()
        self.delta_bar.setRange(0, 100)
        self.delta_bar.setValue(0)
        self.delta_bar.setFixedHeight(14)
        trig_mon_layout.addWidget(self.delta_bar, 0, 4)
        self.delta_val_label = QLabel("+0.0")
        self.delta_val_label.setStyleSheet("font-weight: bold; color: #dfe4ea; min-width: 45px;")
        trig_mon_layout.addWidget(self.delta_val_label, 0, 5)

        # 3. Picco Luminosità
        trig_mon_layout.addWidget(QLabel("🔴 <b>Picco Max:</b>"), 0, 6)
        self.peak_px_lbl = QLabel("0 / 255")
        self.peak_px_lbl.setStyleSheet("font-weight: bold; color: #ffa502; min-width: 55px;")
        trig_mon_layout.addWidget(self.peak_px_lbl, 0, 7)

        video_layout.addWidget(trig_mon_box)

        splitter.addWidget(video_container)

        # 2. PANNELLO CONTROLLI A SCHEDE
        controls_widget = QWidget()
        controls_layout = QVBoxLayout(controls_widget)
        controls_layout.setContentsMargins(0, 0, 0, 0)
        controls_layout.setSpacing(6)

        self.arm_btn = QPushButton("🛡️ ARMA CATTURA FULMINI")
        self.arm_btn.setObjectName("armBtn")
        self.arm_btn.setProperty("armed", "false")
        self.arm_btn.setCheckable(True)
        self.arm_btn.clicked.connect(self.toggle_arm_capture)
        controls_layout.addWidget(self.arm_btn)

        tabs = QTabWidget()

        # ==========================================
        # TAB 1: CAMERA & RISOLUZIONE
        # ==========================================
        tab_cam = QWidget()
        cam_layout = QVBoxLayout(tab_cam)
        cam_layout.setSpacing(6)
        cam_layout.setContentsMargins(8, 8, 8, 8)
        
        exp_box = QGroupBox("Esposizione & Guadagno (Gain)")
        exp_grid = QGridLayout(exp_box)
        exp_grid.setContentsMargins(6, 8, 6, 6)
        exp_grid.setVerticalSpacing(4)
        
        exp_grid.addWidget(QLabel("Esposizione (ms):"), 0, 0)
        self.exp_slider = QSlider(Qt.Orientation.Horizontal)
        self.exp_slider.setRange(1, 100)
        self.exp_slider.setValue(int(config.EXPOSURE_MS))
        self.exp_slider.valueChanged.connect(self.on_exp_slider_changed)
        exp_grid.addWidget(self.exp_slider, 0, 1)
        self.exp_spin = QDoubleSpinBox()
        self.exp_spin.setRange(0.5, 200.0)
        self.exp_spin.setValue(config.EXPOSURE_MS)
        self.exp_spin.valueChanged.connect(self.on_exp_spin_changed)
        exp_grid.addWidget(self.exp_spin, 0, 2)

        exp_grid.addWidget(QLabel("Guadagno (Gain):"), 1, 0)
        self.gain_slider = QSlider(Qt.Orientation.Horizontal)
        self.gain_slider.setRange(0, 450)
        self.gain_slider.setValue(config.GAIN)
        self.gain_slider.valueChanged.connect(self.on_gain_slider_changed)
        exp_grid.addWidget(self.gain_slider, 1, 1)
        self.gain_spin = QSpinBox()
        self.gain_spin.setRange(0, 600)
        self.gain_spin.setValue(config.GAIN)
        self.gain_spin.valueChanged.connect(self.on_gain_spin_changed)
        exp_grid.addWidget(self.gain_spin, 1, 2)
        cam_layout.addWidget(exp_box)

        fps_box = QGroupBox("Risoluzione & FPS")
        fps_grid = QGridLayout(fps_box)
        fps_grid.setContentsMargins(6, 8, 6, 6)
        fps_grid.addWidget(QLabel("Binning:"), 0, 0)
        self.bin_combo = QComboBox()
        self.bin_combo.addItem("Bin 1x1 (Piena Risoluzione ~17-25 FPS)", 1)
        self.bin_combo.addItem("Bin 2x2 (Campo Pieno, Ris. 1/2 ~45-60 FPS) ⭐", 2)
        self.bin_combo.setCurrentIndex(0 if config.BINNING == 1 else 1)
        self.bin_combo.currentIndexChanged.connect(self.on_binning_changed)
        fps_grid.addWidget(self.bin_combo, 0, 1)

        self.hs_check = QCheckBox("High Speed Mode (10-bit ADC per max FPS)")
        self.hs_check.setChecked(getattr(config, 'HIGH_SPEED_MODE', True))
        self.hs_check.toggled.connect(self.on_high_speed_toggled)
        fps_grid.addWidget(self.hs_check, 1, 0, 1, 2)
        cam_layout.addWidget(fps_box)

        tec_box = QGroupBox("Raffreddamento Sensore TEC Pro")
        tec_grid = QGridLayout(tec_box)
        tec_grid.setContentsMargins(6, 8, 6, 6)
        self.tec_check = QCheckBox("Attiva Raffreddamento (Cooler)")
        self.tec_check.toggled.connect(self.on_tec_toggled)
        tec_grid.addWidget(self.tec_check, 0, 0)
        tec_grid.addWidget(QLabel("Target (°C):"), 0, 1)
        self.target_temp_spin = QSpinBox()
        self.target_temp_spin.setRange(-20, 20)
        self.target_temp_spin.setValue(0)
        self.target_temp_spin.valueChanged.connect(self.on_tec_toggled)
        tec_grid.addWidget(self.target_temp_spin, 0, 2)
        cam_layout.addWidget(tec_box)
        cam_layout.addStretch()
        tabs.addTab(tab_cam, "📷 Camera")

        # ==========================================
        # TAB 2: BILANCIAMENTO COLORE (WB)
        # ==========================================
        tab_col = QWidget()
        col_layout = QVBoxLayout(tab_col)
        col_layout.setSpacing(6)
        col_layout.setContentsMargins(8, 8, 8, 8)

        mode_box = QGroupBox("Modalità Elaborazione Colore")
        mode_grid = QGridLayout(mode_box)
        mode_grid.setContentsMargins(6, 8, 6, 6)
        mode_grid.addWidget(QLabel("Modalità:"), 0, 0)
        self.mode_combo = QComboBox()
        if self.custom_calib_data:
            self.mode_combo.addItem("🎯 Calibrazione Monitor (JSON) ⭐", "CUSTOM_JSON")
        self.mode_combo.addItem("Naturale Bilanciato (Lineare WB)", "NATURAL")
        self.mode_combo.addItem("Profilo Reflex D65 (Matrice Morbida)", "REFLEX_SOFT")
        self.mode_combo.addItem("Raw Diretto (Senza Correzioni)", "RAW")
        self.mode_combo.currentIndexChanged.connect(self.on_color_mode_changed)
        mode_grid.addWidget(self.mode_combo, 0, 1, 1, 2)

        mode_grid.addWidget(QLabel("Saturazione:"), 1, 0)
        self.sat_slider = QSlider(Qt.Orientation.Horizontal)
        self.sat_slider.setRange(0, 200)
        self.sat_slider.setValue(100)
        self.sat_slider.valueChanged.connect(self.on_saturation_changed)
        mode_grid.addWidget(self.sat_slider, 1, 1)
        self.sat_lbl = QLabel("1.00x")
        mode_grid.addWidget(self.sat_lbl, 1, 2)
        col_layout.addWidget(mode_box)

        wb_box = QGroupBox("Bilanciamento del Bianco (WB)")
        wb_grid = QGridLayout(wb_box)
        wb_grid.setContentsMargins(6, 8, 6, 6)
        wb_grid.setVerticalSpacing(4)

        self.awb_btn = QPushButton("⚡ Calcola Auto White Balance (AWB)")
        self.awb_btn.setStyleSheet("background-color: #2ed573; color: #000000; font-weight: bold; padding: 7px;")
        self.awb_btn.clicked.connect(self.trigger_awb)
        wb_grid.addWidget(self.awb_btn, 0, 0, 1, 3)

        wb_grid.addWidget(QLabel("Guadagno Rosso (WB_R):"), 1, 0)
        self.wb_r_slider = QSlider(Qt.Orientation.Horizontal)
        self.wb_r_slider.setRange(20, 250)
        self.wb_r_slider.setValue(int(self.wb_r_val * 100))
        self.wb_r_slider.valueChanged.connect(self.on_wb_changed)
        wb_grid.addWidget(self.wb_r_slider, 1, 1)
        self.wb_r_lbl = QLabel(f"{self.wb_r_val:.2f}")
        wb_grid.addWidget(self.wb_r_lbl, 1, 2)

        wb_grid.addWidget(QLabel("Guadagno Blu (WB_B):"), 2, 0)
        self.wb_b_slider = QSlider(Qt.Orientation.Horizontal)
        self.wb_b_slider.setRange(20, 250)
        self.wb_b_slider.setValue(int(self.wb_b_val * 100))
        self.wb_b_slider.valueChanged.connect(self.on_wb_changed)
        wb_grid.addWidget(self.wb_b_slider, 2, 1)
        self.wb_b_lbl = QLabel(f"{self.wb_b_val:.2f}")
        wb_grid.addWidget(self.wb_b_lbl, 2, 2)

        self.reset_wb_btn = QPushButton("Ripristina Neutro (WB_R=1.15, WB_B=1.05)")
        self.reset_wb_btn.clicked.connect(self.reset_wb)
        wb_grid.addWidget(self.reset_wb_btn, 3, 0, 1, 3)

        col_layout.addWidget(wb_box)
        col_layout.addStretch()
        tabs.addTab(tab_col, "🎨 Colore & WB")

        # ==========================================
        # TAB 3: GAMMA & ISTOGRAMMA
        # ==========================================
        tab_hist = QWidget()
        hist_layout = QVBoxLayout(tab_hist)
        hist_layout.setSpacing(6)
        hist_layout.setContentsMargins(8, 8, 8, 8)

        gamma_box = QGroupBox("Curva Tonale & Gamma")
        gamma_grid = QGridLayout(gamma_box)
        gamma_grid.setContentsMargins(6, 8, 6, 6)
        
        self.scurve_check = QCheckBox("Curva Tonale S-Curve (Morbidezza alte luci)")
        self.scurve_check.setChecked(False)
        self.scurve_check.toggled.connect(self.on_scurve_toggled)
        gamma_grid.addWidget(self.scurve_check, 0, 0, 1, 3)

        gamma_grid.addWidget(QLabel("Gamma:"), 1, 0)
        self.gamma_slider = QSlider(Qt.Orientation.Horizontal)
        self.gamma_slider.setRange(20, 300)
        self.gamma_slider.setValue(100)
        self.gamma_slider.valueChanged.connect(self.on_gamma_changed)
        gamma_grid.addWidget(self.gamma_slider, 1, 1)
        self.gamma_lbl = QLabel("1.00")
        gamma_grid.addWidget(self.gamma_lbl, 1, 2)
        hist_layout.addWidget(gamma_box)

        stretch_box = QGroupBox("Stretch Istogramma")
        stretch_grid = QGridLayout(stretch_box)
        stretch_grid.setContentsMargins(6, 8, 6, 6)
        stretch_grid.setVerticalSpacing(4)

        self.calc_stretch_btn = QPushButton("⚡ Calcola Auto-Stretch (One-Shot)")
        self.calc_stretch_btn.setStyleSheet("background-color: #0984e3; color: #ffffff; font-weight: bold; padding: 6px;")
        self.calc_stretch_btn.clicked.connect(self.trigger_calc_stretch)
        stretch_grid.addWidget(self.calc_stretch_btn, 0, 0, 1, 3)

        self.stretch_check = QCheckBox("Abilita Stretch")
        self.stretch_check.setChecked(False)
        self.stretch_check.toggled.connect(self.on_stretch_toggled)
        stretch_grid.addWidget(self.stretch_check, 1, 0, 1, 3)

        stretch_grid.addWidget(QLabel("Punto di Nero:"), 2, 0)
        self.bp_slider = QSlider(Qt.Orientation.Horizontal)
        self.bp_slider.setRange(0, 240)
        self.bp_slider.setValue(0)
        self.bp_slider.valueChanged.connect(self.on_bp_slider_changed)
        stretch_grid.addWidget(self.bp_slider, 2, 1)
        self.bp_spin = QSpinBox()
        self.bp_spin.setRange(0, 240)
        self.bp_spin.setValue(0)
        self.bp_spin.valueChanged.connect(self.on_bp_spin_changed)
        stretch_grid.addWidget(self.bp_spin, 2, 2)

        stretch_grid.addWidget(QLabel("Punto di Bianco:"), 3, 0)
        self.wp_slider = QSlider(Qt.Orientation.Horizontal)
        self.wp_slider.setRange(15, 255)
        self.wp_slider.setValue(255)
        self.wp_slider.valueChanged.connect(self.on_wp_slider_changed)
        stretch_grid.addWidget(self.wp_slider, 3, 1)
        self.wp_spin = QSpinBox()
        self.wp_spin.setRange(15, 255)
        self.wp_spin.setValue(255)
        self.wp_spin.valueChanged.connect(self.on_wp_spin_changed)
        stretch_grid.addWidget(self.wp_spin, 3, 2)

        self.reset_stretch_btn = QPushButton("Reset Stretch (0 - 255)")
        self.reset_stretch_btn.clicked.connect(self.reset_stretch)
        stretch_grid.addWidget(self.reset_stretch_btn, 4, 0, 1, 3)

        hist_layout.addWidget(stretch_box)
        hist_layout.addStretch()
        tabs.addTab(tab_hist, "🌓 Gamma & Stretch")

        # ==========================================
        # TAB 4: TRIGGER FULMINI & LASER
        # ==========================================
        tab_trig = QWidget()
        trig_layout = QVBoxLayout(tab_trig)
        trig_layout.setSpacing(6)
        trig_layout.setContentsMargins(8, 8, 8, 8)

        trig_box = QGroupBox("Algoritmo di Rilevamento Fulmini & Laser")
        trig_grid = QGridLayout(trig_box)
        trig_grid.setContentsMargins(6, 8, 6, 6)
        trig_grid.setVerticalSpacing(4)

        trig_grid.addWidget(QLabel("Modalità Rilevamento:"), 0, 0)
        self.trig_mode_combo = QComboBox()
        self.trig_mode_combo.addItem("⚡ Ibrida Intelligente (Punti Accesi + Delta) ⭐", "HYBRID")
        self.trig_mode_combo.addItem("🎯 Conteggio Punti Accesi (Laser / Scariche)", "PIXEL_COUNT")
        self.trig_mode_combo.addItem("☁️ Delta Luminosità Globale (Flash Diffusi)", "DELTA")
        self.trig_mode_combo.addItem("🔴 Solo Picco di Saturazione", "MAX_PIXEL")
        self.trig_mode_combo.currentIndexChanged.connect(self.on_trigger_params_changed)
        trig_grid.addWidget(self.trig_mode_combo, 0, 1, 1, 2)

        trig_grid.addWidget(QLabel("Soglia Contrasto Pixel (% Gamma):"), 1, 0)
        self.contrast_slider = QSlider(Qt.Orientation.Horizontal)
        self.contrast_slider.setRange(10, 90)
        self.contrast_slider.setValue(int(getattr(config, 'PIXEL_CONTRAST_PERCENT', 60)))
        self.contrast_slider.valueChanged.connect(self.on_contrast_slider_changed)
        trig_grid.addWidget(self.contrast_slider, 1, 1)
        self.contrast_spin = QSpinBox()
        self.contrast_spin.setRange(10, 90)
        self.contrast_spin.setValue(int(getattr(config, 'PIXEL_CONTRAST_PERCENT', 60)))
        self.contrast_spin.setSuffix(" %")
        self.contrast_spin.valueChanged.connect(self.on_contrast_spin_changed)
        trig_grid.addWidget(self.contrast_spin, 1, 2)

        trig_grid.addWidget(QLabel("Minimo Punti Accesi per Trigger:"), 2, 0)
        self.min_px_slider = QSlider(Qt.Orientation.Horizontal)
        self.min_px_slider.setRange(1, 200)
        self.min_px_slider.setValue(int(getattr(config, 'MIN_ACTIVE_PIXELS', 20)))
        self.min_px_slider.valueChanged.connect(self.on_min_px_slider_changed)
        trig_grid.addWidget(self.min_px_slider, 2, 1)
        self.min_px_spin = QSpinBox()
        self.min_px_spin.setRange(1, 1000)
        self.min_px_spin.setValue(int(getattr(config, 'MIN_ACTIVE_PIXELS', 20)))
        self.min_px_spin.setSuffix(" px")
        self.min_px_spin.valueChanged.connect(self.on_min_px_spin_changed)
        trig_grid.addWidget(self.min_px_spin, 2, 2)

        trig_grid.addWidget(QLabel("Confronto Temporale (Frame Fa):"), 3, 0)
        self.diff_offset_spin = QSpinBox()
        self.diff_offset_spin.setRange(1, 15)
        self.diff_offset_spin.setValue(int(getattr(config, 'DIFF_FRAME_OFFSET', 6)))
        self.diff_offset_spin.setSuffix(" frame")
        self.diff_offset_spin.valueChanged.connect(self.on_trigger_params_changed)
        trig_grid.addWidget(self.diff_offset_spin, 3, 1, 1, 2)

        trig_grid.addWidget(QLabel("Soglia Delta Luminosità Globale:"), 4, 0)
        self.delta_spin = QDoubleSpinBox()
        self.delta_spin.setRange(1.0, 50.0)
        self.delta_spin.setValue(config.DELTA_THRESHOLD)
        self.delta_spin.valueChanged.connect(self.on_trigger_params_changed)
        trig_grid.addWidget(self.delta_spin, 4, 1, 1, 2)

        trig_grid.addWidget(QLabel("Frame Pre-Evento (Buffer):"), 5, 0)
        self.pre_spin = QSpinBox()
        self.pre_spin.setRange(1, 20)
        self.pre_spin.setValue(config.PRE_EVENT_FRAMES)
        self.pre_spin.valueChanged.connect(self.on_trigger_params_changed)
        trig_grid.addWidget(self.pre_spin, 5, 1, 1, 2)

        trig_grid.addWidget(QLabel("Secondi Post-Evento da Salvare:"), 6, 0)
        self.post_spin = QDoubleSpinBox()
        self.post_spin.setRange(0.2, 5.0)
        self.post_spin.setValue(config.POST_EVENT_SECONDS)
        self.post_spin.setSuffix(" s")
        self.post_spin.valueChanged.connect(self.on_trigger_params_changed)
        trig_grid.addWidget(self.post_spin, 6, 1, 1, 2)

        trig_grid.addWidget(QLabel("Metodo Stacking TIFF:"), 7, 0)
        self.stack_combo = QComboBox()
        self.stack_combo.addItems(["MAX (Consigliato per fulmini)", "AVERAGE (Media)"])
        trig_grid.addWidget(self.stack_combo, 7, 1, 1, 2)

        trig_layout.addWidget(trig_box)
        trig_layout.addStretch()
        tabs.addTab(tab_trig, "⚡ Trigger")

        controls_layout.addWidget(tabs, stretch=1)

        # 3. GALLERIA EVENTI & BATCH PROCESSING
        events_box = QGroupBox("Galleria Registrazioni Fulmini (.SER)")
        events_layout = QVBoxLayout(events_box)
        events_layout.setContentsMargins(6, 8, 6, 6)
        events_layout.setSpacing(4)

        # Barra Filtri & Ricarica
        filter_layout = QHBoxLayout()
        filter_layout.addWidget(QLabel("Visualizza:"))
        self.filter_combo = QComboBox()
        self.filter_combo.addItem("📂 Tutti i File (.SER)", "ALL")
        self.filter_combo.addItem("🟠 Solo Da Sommare (Senza TIFF)", "MISSING")
        self.filter_combo.addItem("🟢 Solo Conclusi (TIFF Pronto)", "DONE")
        self.filter_combo.currentIndexChanged.connect(self.scan_and_populate_gallery)
        filter_layout.addWidget(self.filter_combo, stretch=1)

        self.refresh_gallery_btn = QPushButton("🔄 Aggiorna")
        self.refresh_gallery_btn.setToolTip("Ricarica lista file dalla cartella")
        self.refresh_gallery_btn.clicked.connect(self.scan_and_populate_gallery)
        filter_layout.addWidget(self.refresh_gallery_btn)
        events_layout.addLayout(filter_layout)

        # Pulsante Batch Stacking & Interruzione
        self.batch_stack_btn = QPushButton("⚡ Elabora / Somma SER Mancanti (Batch)")
        self.batch_stack_btn.setStyleSheet("background-color: #6c5ce7; color: #ffffff; font-weight: bold; padding: 7px;")
        self.batch_stack_btn.clicked.connect(self.toggle_batch_stacking)
        events_layout.addWidget(self.batch_stack_btn)

        # Barra di Progresso Batch Stacking (visibile solo durante elaborazione)
        self.batch_progress_bar = QProgressBar()
        self.batch_progress_bar.setRange(0, 100)
        self.batch_progress_bar.setValue(0)
        self.batch_progress_bar.setFixedHeight(15)
        self.batch_progress_bar.setVisible(False)
        events_layout.addWidget(self.batch_progress_bar)

        self.batch_status_lbl = QLabel("")
        self.batch_status_lbl.setStyleSheet("font-size: 11px; color: #00d2ff; font-weight: bold;")
        self.batch_status_lbl.setVisible(False)
        events_layout.addWidget(self.batch_status_lbl)

        self.events_list = QListWidget()
        self.events_list.itemDoubleClicked.connect(self.on_event_item_clicked)
        events_layout.addWidget(self.events_list)
        controls_layout.addWidget(events_box, stretch=1)

        splitter.addWidget(controls_widget)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        main_layout.addWidget(splitter)

    # --- CONTROLLO THREAD ---
    def start_camera_threads(self):
        self.cam_worker = CameraWorker()
        self.cam_worker.preview_ready.connect(self.on_preview_received)
        self.cam_worker.stats_updated.connect(self.on_stats_updated)
        self.cam_worker.lightning_detected.connect(self.on_lightning_detected)
        self.cam_worker.camera_error.connect(self.on_camera_error)

        self.saver_worker = SaverWorker(self.cam_worker.saver_queue)
        self.saver_worker.save_completed.connect(self.on_save_completed)

        self.saver_worker.start()
        self.cam_worker.start()

    # --- ULTRA-FAST PREVIEW RENDERING & COLOR PIPELINE ---
    def on_preview_received(self, rgb_preview):
        # 1. Calcolo Auto White Balance (AWB) robusto: ignora pixel saturi (> 220) e bui (< 20)
        if self.request_calc_awb:
            self.request_calc_awb = False
            gray = cv2.cvtColor(rgb_preview, cv2.COLOR_RGB2GRAY)
            mask = (gray > 20) & (gray < 225)
            
            if np.count_nonzero(mask) > 200:
                mean_r = float(np.mean(rgb_preview[:, :, 0][mask]))
                mean_g = float(np.mean(rgb_preview[:, :, 1][mask]))
                mean_b = float(np.mean(rgb_preview[:, :, 2][mask]))
                
                if mean_r > 5 and mean_g > 5 and mean_b > 5:
                    new_wb_r = float(np.clip(mean_g / mean_r, 0.3, 2.5))
                    new_wb_b = float(np.clip(mean_g / mean_b, 0.3, 2.5))
                    self.wb_r_val = new_wb_r
                    self.wb_b_val = new_wb_b
                    
                    self.wb_r_slider.blockSignals(True)
                    self.wb_r_slider.setValue(int(new_wb_r * 100))
                    self.wb_r_slider.blockSignals(False)
                    self.wb_r_lbl.setText(f"{new_wb_r:.2f}")

                    self.wb_b_slider.blockSignals(True)
                    self.wb_b_slider.setValue(int(new_wb_b * 100))
                    self.wb_b_slider.blockSignals(False)
                    self.wb_b_lbl.setText(f"{new_wb_b:.2f}")

        # 2. Calcolo Auto-Stretch One-Shot su richiesta
        if self.request_calc_stretch:
            self.request_calc_stretch = False
            sample = rgb_preview[::8, ::8]
            p_low = float(np.percentile(sample, 1.0))
            p_high = float(np.percentile(sample, 99.0))
            
            if p_high > p_low + 3:
                self.stretch_black_point = p_low
                self.stretch_white_point = p_high
                self.auto_stretch = True
                
                self.stretch_check.blockSignals(True)
                self.stretch_check.setChecked(True)
                self.stretch_check.blockSignals(False)

                self.bp_spin.blockSignals(True)
                self.bp_slider.blockSignals(True)
                self.bp_spin.setValue(int(p_low))
                self.bp_slider.setValue(int(p_low))
                self.bp_spin.blockSignals(False)
                self.bp_slider.blockSignals(False)

                self.wp_spin.blockSignals(True)
                self.wp_slider.blockSignals(True)
                self.wp_spin.setValue(int(p_high))
                self.wp_slider.setValue(int(p_high))
                self.wp_spin.blockSignals(False)
                self.wp_slider.blockSignals(False)

                self.update_lut()

        # 3. Applicazione Guadagni di Bilanciamento del Bianco (WB) e Matrice
        if self.color_mode == "RAW":
            processed = rgb_preview
        elif self.color_mode == "CUSTOM_JSON" and self.custom_calib_data:
            ccm_json = np.array(self.custom_calib_data.get("color_correction_matrix_3x3", np.eye(3)), dtype=np.float32)
            wb_mat = np.diag([self.wb_r_val, 1.0, self.wb_b_val]).astype(np.float32)
            combined_mat = ccm_json @ wb_mat
            processed = cv2.transform(rgb_preview, combined_mat)
        elif self.color_mode == "REFLEX_SOFT":
            wb_mat = np.diag([self.wb_r_val, 1.0, self.wb_b_val]).astype(np.float32)
            combined_mat = CCM_D65_SOFT @ wb_mat
            processed = cv2.transform(rgb_preview, combined_mat)
        else: # "NATURAL" (Consigliato: applicazione diretta lineare dei moltiplicatori)
            # 3-channel LUT per applicare WB R, G=1.0, B in meno di 0.05 ms senza overflow
            r_lut = np.clip(np.arange(256) * self.wb_r_val, 0, 255).astype(np.uint8)
            g_lut = np.arange(256, dtype=np.uint8)
            b_lut = np.clip(np.arange(256) * self.wb_b_val, 0, 255).astype(np.uint8)
            
            processed = np.empty_like(rgb_preview)
            processed[:, :, 0] = cv2.LUT(rgb_preview[:, :, 0], r_lut)
            processed[:, :, 1] = rgb_preview[:, :, 1]
            processed[:, :, 2] = cv2.LUT(rgb_preview[:, :, 2], b_lut)

        # 4. Modulazione Saturazione (se diversa da 1.0)
        if self.saturation_val != 1.0 and self.color_mode != "RAW":
            hsv = cv2.cvtColor(processed, cv2.COLOR_RGB2HSV).astype(np.float32)
            hsv[:, :, 1] = np.clip(hsv[:, :, 1] * self.saturation_val, 0, 255)
            processed = cv2.cvtColor(hsv.astype(np.uint8), cv2.COLOR_HSV2RGB)

        # 5. Applicazione Look-Up Table (Stretch + Gamma + S-Curve)
        rendered = cv2.LUT(processed, self.cached_lut)

        # 6. Conversione Diretta QPixmap
        h, w, ch = rendered.shape
        bytes_per_line = ch * w
        q_img = QImage(rendered.data, w, h, bytes_per_line, QImage.Format.Format_RGB888)
        pix = QPixmap.fromImage(q_img)

        scaled_pix = pix.scaled(
            self.video_label.size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.FastTransformation
        )
        self.video_label.setPixmap(scaled_pix)

    def on_stats_updated(self, fps, baseline, current_mean, delta, temp, r_mean, g_mean, b_mean, active_px, current_max):
        self.fps_label.setText(f"FPS: {fps:.1f}")
        self.temp_label.setText(f"Temp: {temp:.1f} °C")
        self.rgb_levels_label.setText(f"R:{r_mean:.0f} | G:{g_mean:.0f} | B:{b_mean:.0f}")

        # Indicatore visivo sovraesposizione
        if current_mean > 210 or r_mean > 240 or g_mean > 240:
            self.exposure_warning_label.setText("⚠️ IMMAGINE SOVRAESPOSTA! Riduci Esposizione/Gain")
        else:
            self.exposure_warning_label.setText("")
        
        # 1. Telemetria Punti Accesi ad Alto Contrasto
        self.active_px_lbl.setText(f"{active_px} px")
        req_px = max(1, self.min_px_spin.value())
        px_percent = int(min(max(active_px * 100 / req_px, 0), 100))
        self.active_px_bar.setValue(px_percent)
        if active_px >= req_px:
            self.active_px_bar.setStyleSheet("QProgressBar::chunk { background-color: #ff4757; }")
            self.active_px_lbl.setStyleSheet("font-weight: bold; color: #ff4757; min-width: 60px;")
        else:
            self.active_px_bar.setStyleSheet("QProgressBar::chunk { background-color: #00d2ff; }")
            self.active_px_lbl.setStyleSheet("font-weight: bold; color: #00d2ff; min-width: 60px;")

        # 2. Telemetria Delta Luminosità Globale
        self.delta_val_label.setText(f"{delta:+4.1f}")
        req_delta = max(0.1, self.delta_spin.value())
        delta_percent = int(min(max(delta * 100 / req_delta, 0), 100))
        self.delta_bar.setValue(delta_percent)
        if delta >= req_delta:
            self.delta_bar.setStyleSheet("QProgressBar::chunk { background-color: #ff4757; }")
            self.delta_val_label.setStyleSheet("font-weight: bold; color: #ff4757; min-width: 45px;")
        else:
            self.delta_bar.setStyleSheet("QProgressBar::chunk { background-color: #00d2ff; }")
            self.delta_val_label.setStyleSheet("font-weight: bold; color: #dfe4ea; min-width: 45px;")

        # 3. Telemetria Picco Luminosità
        self.peak_px_lbl.setText(f"{current_max:.0f} / 255")
        if current_max >= 240:
            self.peak_px_lbl.setStyleSheet("font-weight: bold; color: #ff4757; min-width: 55px;")
        else:
            self.peak_px_lbl.setStyleSheet("font-weight: bold; color: #ffa502; min-width: 55px;")

    def on_lightning_detected(self, info):
        self.status_badge.setText(f"⚡ LAMPO IN CORSO! ({info['reason']})")
        self.status_badge.setStyleSheet("font-size: 13px; font-weight: bold; color: #ff4757;")
        QTimer.singleShot(1500, self.restore_status_badge)

    def restore_status_badge(self):
        if self.arm_btn.isChecked():
            self.status_badge.setText("● STATO: ARMATO (IN ASCOLTO FULMINI)")
            self.status_badge.setStyleSheet("font-size: 13px; font-weight: bold; color: #1dd1a1;")
        else:
            self.status_badge.setText("● STATO: MONITORAGGIO (DISARMATO)")
            self.status_badge.setStyleSheet("font-size: 13px; font-weight: bold; color: #8395a7;")

    def scan_and_populate_gallery(self):
        self.events_list.clear()
        captures_dir = os.path.abspath(config.OUTPUT_DIR)
        if not os.path.exists(captures_dir):
            return

        filter_mode = self.filter_combo.currentData() if hasattr(self, 'filter_combo') else "ALL"

        ser_files = [f for f in os.listdir(captures_dir) if f.lower().endswith(".ser")]
        ser_files.sort(reverse=True) # Più recenti in alto

        missing_count = 0
        done_count = 0

        for f in ser_files:
            ser_path = os.path.join(captures_dir, f)
            base_name = os.path.splitext(ser_path)[0]
            tiff_path = f"{base_name}_sum.tiff"
            
            has_tiff = os.path.exists(tiff_path)
            if has_tiff:
                done_count += 1
            else:
                missing_count += 1

            if filter_mode == "MISSING" and has_tiff:
                continue
            if filter_mode == "DONE" and not has_tiff:
                continue

            try:
                size_mb = os.path.getsize(ser_path) / (1024 * 1024)
                size_str = f"{size_mb:.1f} MB"
            except Exception:
                size_str = ""

            if has_tiff:
                item_text = f"🟢 [TIFF PRONTO]  {f}  ({size_str})"
            else:
                item_text = f"🟠 [DA SOMMARE]   {f}  ({size_str})"

            item = QListWidgetItem(item_text)
            item.setData(Qt.ItemDataRole.UserRole, (ser_path, tiff_path, has_tiff))
            if has_tiff:
                item.setForeground(QColor("#2ed573"))
            else:
                item.setForeground(QColor("#ffa502"))
            self.events_list.addItem(item)

        if not (self.batch_worker and self.batch_worker.isRunning()):
            if missing_count > 0:
                self.batch_stack_btn.setText(f"⚡ Elabora {missing_count} SER Mancanti (Batch)")
                self.batch_stack_btn.setStyleSheet("background-color: #6c5ce7; color: #ffffff; font-weight: bold; padding: 7px;")
            else:
                self.batch_stack_btn.setText("⚡ Nessun SER da Elaborare (Tutti Pronti)")
                self.batch_stack_btn.setStyleSheet("background-color: #2f3542; color: #a4b0be; font-weight: bold; padding: 7px;")

    def on_save_completed(self, ser_path, ts):
        self.total_captures += 1
        self.scan_and_populate_gallery()

    def on_event_item_clicked(self, item):
        data = item.data(Qt.ItemDataRole.UserRole)
        if not data:
            return
        ser_path, tiff_path, has_tiff = data
        
        if has_tiff and os.path.exists(tiff_path):
            os.startfile(tiff_path)
        elif os.path.exists(ser_path):
            # Elabora istantaneamente il singolo SER e apri il TIFF risultante
            method_str = self.stack_combo.currentText().split()[0]
            try:
                self.statusBar().showMessage(f"Elaborazione di {os.path.basename(ser_path)} in corso...", 2500)
                process_ser_file(ser_path, tiff_path, method=method_str, save_jpg=True)
                self.scan_and_populate_gallery()
                if os.path.exists(tiff_path):
                    os.startfile(tiff_path)
            except Exception as e:
                QMessageBox.warning(self, "Errore Stacking", f"Impossibile sommare il file: {e}")

    def toggle_batch_stacking(self):
        # 1. Se è già in esecuzione, il pulsante serve a INTERROMPERE
        if self.batch_worker and self.batch_worker.isRunning():
            self.batch_status_lbl.setText("⏳ Richiesta interruzione... arresto al termine del frame corrente.")
            self.batch_worker.stop()
            return

        # 2. La somma deve funzionare SOLO ad acquisizione terminata / disarmata
        if self.arm_btn.isChecked():
            self.arm_btn.setChecked(False)
            self.toggle_arm_capture(False)
            self.statusBar().showMessage("Acquisizione disarmata automaticamente per dedicare la CPU allo Stacking.", 3000)

        method_str = self.stack_combo.currentText().split()[0]
        self.batch_worker = BatchStackerWorker(os.path.abspath(config.OUTPUT_DIR), stack_method=method_str)
        self.batch_worker.progress.connect(self.on_batch_progress)
        self.batch_worker.item_processed.connect(self.on_batch_item_done)
        self.batch_worker.finished_batch.connect(self.on_batch_finished)

        self.batch_stack_btn.setText("🛑 INTERROMPI BATCH STACKING")
        self.batch_stack_btn.setStyleSheet("background-color: #ff4757; color: #ffffff; font-weight: bold; padding: 7px;")
        self.batch_progress_bar.setVisible(True)
        self.batch_progress_bar.setValue(0)
        self.batch_status_lbl.setVisible(True)
        self.batch_status_lbl.setText("Avvio elaborazione file .SER mancanti...")
        self.batch_worker.start()

    def on_batch_progress(self, current, total, fname):
        pct = int(current * 100 / max(1, total))
        self.batch_progress_bar.setValue(pct)
        self.batch_status_lbl.setText(f"Elaborazione [{current}/{total}]: {fname}")

    def on_batch_item_done(self, ser_path, tiff_path):
        pass

    def on_batch_finished(self, total_processed, was_interrupted):
        self.batch_progress_bar.setVisible(False)
        self.batch_status_lbl.setVisible(False)
        self.scan_and_populate_gallery()
        
        if was_interrupted:
            QMessageBox.warning(
                self,
                "Batch Stacking Interrotto",
                f"Elaborazione interrotta dall'utente.\nFile elaborati prima dello stop: {total_processed}"
            )
        elif total_processed > 0:
            QMessageBox.information(
                self,
                "Batch Stacking Completato",
                f"Elaborazione completata con successo!\nGenerati {total_processed} file TIFF e JPG."
            )
        else:
            QMessageBox.information(
                self,
                "Nessun file da elaborare",
                "Tutti i file .SER nella cartella hanno già la relativa immagine TIFF somma!"
            )

    def on_camera_error(self, err_msg):
        QMessageBox.critical(self, "Errore Camera", err_msg)

    # --- SLOTS INTERFACCIA ---
    def toggle_arm_capture(self, checked):
        if checked:
            self.arm_btn.setText("🛑 DISARMA CATTURA FULMINI")
            self.arm_btn.setProperty("armed", "true")
            self.arm_btn.setStyle(self.arm_btn.style())
            self.cam_worker.is_armed = True
            self.status_badge.setText("● STATO: ARMATO (IN ASCOLTO FULMINI)")
            self.status_badge.setStyleSheet("font-size: 13px; font-weight: bold; color: #1dd1a1;")
        else:
            self.arm_btn.setText("🛡️ ARMA CATTURA FULMINI")
            self.arm_btn.setProperty("armed", "false")
            self.arm_btn.setStyle(self.arm_btn.style())
            self.cam_worker.is_armed = False
            self.status_badge.setText("● STATO: MONITORAGGIO (DISARMATO)")
            self.status_badge.setStyleSheet("font-size: 13px; font-weight: bold; color: #8395a7;")

    def on_exp_slider_changed(self, val):
        self.exp_spin.blockSignals(True)
        self.exp_spin.setValue(float(val))
        self.exp_spin.blockSignals(False)
        self.cam_worker.update_exposure(float(val))

    def on_exp_spin_changed(self, val):
        self.exp_slider.blockSignals(True)
        self.exp_slider.setValue(int(val))
        self.exp_slider.blockSignals(False)
        self.cam_worker.update_exposure(float(val))

    def on_gain_slider_changed(self, val):
        self.gain_spin.blockSignals(True)
        self.gain_spin.setValue(int(val))
        self.gain_spin.blockSignals(False)
        self.cam_worker.update_gain(int(val))

    def on_gain_spin_changed(self, val):
        self.gain_slider.blockSignals(True)
        self.gain_slider.setValue(int(val))
        self.gain_slider.blockSignals(False)
        self.cam_worker.update_gain(int(val))

    def on_binning_changed(self, idx):
        bin_val = self.bin_combo.currentData()
        if bin_val is not None:
            self.cam_worker.update_binning(bin_val)

    def on_high_speed_toggled(self, checked):
        self.cam_worker.update_high_speed(checked)

    def on_tec_toggled(self):
        self.cam_worker.set_cooler(self.tec_check.isChecked(), self.target_temp_spin.value())

    # --- SLOTS MOTORE COLORE & STRETCH ---
    def on_color_mode_changed(self, idx):
        self.color_mode = self.mode_combo.currentData()

    def on_saturation_changed(self, val):
        self.saturation_val = val / 100.0
        self.sat_lbl.setText(f"{self.saturation_val:.2f}x")

    def on_scurve_toggled(self, checked):
        self.use_s_curve = checked
        self.update_lut()

    def trigger_awb(self):
        self.request_calc_awb = True

    def on_wb_changed(self):
        self.wb_r_val = self.wb_r_slider.value() / 100.0
        self.wb_b_val = self.wb_b_slider.value() / 100.0
        self.wb_r_lbl.setText(f"{self.wb_r_val:.2f}")
        self.wb_b_lbl.setText(f"{self.wb_b_val:.2f}")

    def reset_wb(self):
        self.wb_r_val = 1.15
        self.wb_b_val = 1.05
        self.wb_r_slider.blockSignals(True)
        self.wb_b_slider.blockSignals(True)
        self.wb_r_slider.setValue(115)
        self.wb_b_slider.setValue(105)
        self.wb_r_slider.blockSignals(False)
        self.wb_b_slider.blockSignals(False)
        self.wb_r_lbl.setText("1.15")
        self.wb_b_lbl.setText("1.05")

    def trigger_calc_stretch(self):
        self.request_calc_stretch = True

    def on_stretch_toggled(self, checked):
        self.auto_stretch = checked
        self.update_lut()

    def on_bp_slider_changed(self, val):
        self.bp_spin.blockSignals(True)
        self.bp_spin.setValue(val)
        self.bp_spin.blockSignals(False)
        self.stretch_black_point = val
        self.update_lut()

    def on_bp_spin_changed(self, val):
        self.bp_slider.blockSignals(True)
        self.bp_slider.setValue(val)
        self.bp_slider.blockSignals(False)
        self.stretch_black_point = val
        self.update_lut()

    def on_wp_slider_changed(self, val):
        self.wp_spin.blockSignals(True)
        self.wp_spin.setValue(val)
        self.wp_spin.blockSignals(False)
        self.stretch_white_point = val
        self.update_lut()

    def on_wp_spin_changed(self, val):
        self.wp_slider.blockSignals(True)
        self.wp_slider.setValue(val)
        self.wp_slider.blockSignals(False)
        self.stretch_white_point = val
        self.update_lut()

    def reset_stretch(self):
        self.stretch_black_point = 0
        self.stretch_white_point = 255
        self.bp_slider.blockSignals(True)
        self.bp_spin.blockSignals(True)
        self.wp_slider.blockSignals(True)
        self.wp_spin.blockSignals(True)
        self.bp_slider.setValue(0)
        self.bp_spin.setValue(0)
        self.wp_slider.setValue(255)
        self.wp_spin.setValue(255)
        self.bp_slider.blockSignals(False)
        self.bp_spin.blockSignals(False)
        self.wp_slider.blockSignals(False)
        self.wp_spin.blockSignals(False)
        self.gamma_slider.setValue(100)
        self.stretch_check.setChecked(False)
        self.update_lut()

    def on_gamma_changed(self, val):
        self.gamma_val = val / 100.0
        self.gamma_lbl.setText(f"{self.gamma_val:.2f}")
        self.update_lut()

    def on_contrast_slider_changed(self, val):
        self.contrast_spin.blockSignals(True)
        self.contrast_spin.setValue(val)
        self.contrast_spin.blockSignals(False)
        self.on_trigger_params_changed()

    def on_contrast_spin_changed(self, val):
        self.contrast_slider.blockSignals(True)
        self.contrast_slider.setValue(val)
        self.contrast_slider.blockSignals(False)
        self.on_trigger_params_changed()

    def on_min_px_slider_changed(self, val):
        self.min_px_spin.blockSignals(True)
        self.min_px_spin.setValue(val)
        self.min_px_spin.blockSignals(False)
        self.on_trigger_params_changed()

    def on_min_px_spin_changed(self, val):
        self.min_px_slider.blockSignals(True)
        self.min_px_slider.setValue(min(val, 200))
        self.min_px_slider.blockSignals(False)
        self.on_trigger_params_changed()

    def on_trigger_params_changed(self):
        mode = self.trig_mode_combo.currentData() or "HYBRID"
        delta_th = self.delta_spin.value()
        contrast_pct = self.contrast_spin.value()
        min_px = self.min_px_spin.value()
        diff_offset = self.diff_offset_spin.value()
        pre_count = self.pre_spin.value()
        post_sec = self.post_spin.value()

        self.cam_worker.update_trigger_settings(
            mode=mode,
            delta_th=delta_th,
            contrast_pct=contrast_pct,
            min_px=min_px,
            diff_offset=diff_offset,
            pre_count=pre_count,
            post_sec=post_sec
        )

        method_str = self.stack_combo.currentText().split()[0]
        self.saver_worker.stack_method = method_str

    def open_captures_folder(self):
        os.startfile(os.path.abspath(config.OUTPUT_DIR))

    def open_cleaner_tool(self):
        try:
            from cleaner_gui import CleanerGUI
            self.cleaner_window = CleanerGUI()
            self.cleaner_window.show()
        except Exception as e:
            QMessageBox.warning(self, "Errore", f"Impossibile aprire l'utility di pulizia: {e}")

    def closeEvent(self, event):
        if self.batch_worker and self.batch_worker.isRunning():
            self.batch_worker.stop()
        self.cam_worker.stop()
        self.saver_worker.stop()
        event.accept()

# ==============================================================================
# MAIN ENTRY POINT
# ==============================================================================
def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    gui = LightningHunterGUI()
    gui.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
