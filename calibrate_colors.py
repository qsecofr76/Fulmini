# ==============================================================================
# STRUMENTO DI CALIBRAZIONE COLORE VIA TARGET SU MONITOR - ZWO ASI
# CON TARGET NUMERATO (1-4), FREEZE FRAME PER ALLINEAMENTO FERMO & ORIENTAMENTO
# ==============================================================================
import os
import sys
import json
import time
import threading
import logging
import numpy as np
import cv2

logging.getLogger().setLevel(logging.ERROR)
import zwoasi as asi

from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer, QPointF, QRectF
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QPushButton, QSlider, QSpinBox, QDoubleSpinBox, QGroupBox,
    QMessageBox, QSplitter, QFrame, QProgressBar, QDialog
)
from PyQt6.QtGui import QImage, QPixmap, QPainter, QColor, QPen, QBrush, QFont, QMouseEvent

import config

# ==============================================================================
# COLOR CHECKER TARGET (24 COLORI STANDARD MACBETH / CIE sRGB)
# ==============================================================================
MACBETH_PATCHES = [
    # Riga 1: Colori naturali
    ("Dark Skin",     [115,  82,  68]),
    ("Light Skin",    [194, 150, 130]),
    ("Blue Sky",      [ 98, 122, 157]),
    ("Foliage",       [ 87, 108,  67]),
    ("Blue Flower",   [133, 128, 177]),
    ("Bluish Green",  [103, 189, 170]),
    # Riga 2: Colori saturi
    ("Orange",        [214, 126,  44]),
    ("Purplish Blue", [ 80,  91, 166]),
    ("Moderate Red",  [193,  90,  99]),
    ("Purple",        [ 94,  60, 108]),
    ("Yellow Green",  [157, 188,  64]),
    ("Orange Yellow", [224, 163,  46]),
    # Riga 3: Primari & Secondari
    ("Blue",          [ 56,  61, 150]),
    ("Green",         [ 70, 148,  73]),
    ("Red",           [175,  54,  60]),
    ("Yellow",        [231, 199,  31]),
    ("Magenta",       [187,  86, 149]),
    ("Cyan",          [  8, 133, 161]),
    # Riga 4: Scala di grigi (100% a 0%)
    ("White (95%)",   [243, 243, 242]),
    ("Neutral 8 (80%)",[200, 200, 200]),
    ("Neutral 6.5(65%)",[160, 160, 160]),
    ("Neutral 5 (50%)",[122, 122, 121]),
    ("Neutral 3.5(35%)",[ 85,  85,  85]),
    ("Black (20%)",   [ 52,  52,  52])
]

# ==============================================================================
# WIDGET TARGET MONITOR CON ANGOLI NUMERATI (1, 2, 3, 4)
# ==============================================================================
class ColorTargetWidget(QWidget):
    def __init__(self):
        super().__init__()
        self.setMinimumSize(480, 320)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        w = self.width()
        h = self.height()
        painter.fillRect(0, 0, w, h, QColor(10, 10, 10))

        margin = 35
        grid_w = w - 2 * margin
        grid_h = h - 2 * margin
        
        cols, rows = 6, 4
        cell_w = grid_w / cols
        cell_h = grid_h / rows
        padding = 3

        idx = 0
        for r in range(rows):
            for c in range(cols):
                name, rgb = MACBETH_PATCHES[idx]
                x = margin + c * cell_w + padding
                y = margin + r * cell_h + padding
                pw = cell_w - 2 * padding
                ph = cell_h - 2 * padding

                painter.fillRect(int(x), int(y), int(pw), int(ph), QColor(rgb[0], rgb[1], rgb[2]))
                painter.setPen(QPen(QColor(0, 0, 0), 1))
                painter.drawRect(int(x), int(y), int(pw), int(ph))
                idx += 1

        # 4 Angoli con Numeri ed Etichette Colorate Corrispondenti
        corners_info = [
            (margin, margin, QColor(255, 71, 87), "1 (TL)"),
            (w - margin, margin, QColor(46, 213, 115), "2 (TR)"),
            (w - margin, h - margin, QColor(30, 144, 255), "3 (BR)"),
            (margin, h - margin, QColor(255, 165, 2), "4 (BL)")
        ]
        
        for cx, cy, col, text in corners_info:
            painter.setPen(QPen(QColor(255, 255, 255), 2))
            painter.setBrush(QBrush(col))
            painter.drawEllipse(int(cx - 14), int(cy - 14), 28, 28)
            painter.setPen(QPen(QColor(0, 0, 0), 1))
            painter.setFont(QFont("Arial", 11, QFont.Weight.Bold))
            painter.drawText(QRectF(cx - 14, cy - 14, 28, 28), Qt.AlignmentFlag.AlignCenter, text[0])

# ==============================================================================
# WIDGET CAMERA INTERATTIVO CON MANIGLIE PER I 4 ANGOLI
# ==============================================================================
class InteractiveCameraWidget(QLabel):
    corners_changed = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.setMinimumSize(480, 340)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setStyleSheet("background-color: #000000; border: 2px solid #2d3436; border-radius: 6px;")
        
        # 4 Angoli normalizzati (0.0 a 1.0)
        # [Top-Left 1, Top-Right 2, Bottom-Right 3, Bottom-Left 4]
        self.corners_norm = [
            [0.20, 0.20],
            [0.80, 0.20],
            [0.80, 0.80],
            [0.20, 0.80]
        ]
        self.active_handle = -1
        self.pixmap_rect = QRectF()
        self.current_pixmap = None
        self.is_frozen = False

    def set_camera_pixmap(self, pixmap, is_frozen=False):
        self.current_pixmap = pixmap
        self.is_frozen = is_frozen
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        if not self.current_pixmap or self.current_pixmap.isNull():
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "In attesa video camera...")
            return

        scaled = self.current_pixmap.scaled(self.size(), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.FastTransformation)
        px = (self.width() - scaled.width()) / 2
        py = (self.height() - scaled.height()) / 2
        self.pixmap_rect = QRectF(px, py, scaled.width(), scaled.height())
        painter.drawPixmap(int(px), int(py), scaled)

        # Coordinate schermo dei 4 angoli
        pts_screen = []
        for nx, ny in self.corners_norm:
            sx = self.pixmap_rect.x() + nx * self.pixmap_rect.width()
            sy = self.pixmap_rect.y() + ny * self.pixmap_rect.height()
            pts_screen.append(QPointF(sx, sy))

        # Disegna il poligono della griglia (linee ciano)
        pen_poly = QPen(QColor(0, 210, 255), 2, Qt.PenStyle.SolidLine)
        painter.setPen(pen_poly)
        for i in range(4):
            painter.drawLine(pts_screen[i], pts_screen[(i + 1) % 4])

        # Disegna linee interne della griglia 6x4
        pen_grid = QPen(QColor(0, 210, 255, 120), 1, Qt.PenStyle.DashLine)
        painter.setPen(pen_grid)
        for c in range(1, 6):
            frac = c / 6.0
            p_top = pts_screen[0] + frac * (pts_screen[1] - pts_screen[0])
            p_bot = pts_screen[3] + frac * (pts_screen[2] - pts_screen[3])
            painter.drawLine(p_top, p_bot)
        for r in range(1, 4):
            frac = r / 4.0
            p_left = pts_screen[0] + frac * (pts_screen[3] - pts_screen[0])
            p_right = pts_screen[1] + frac * (pts_screen[2] - pts_screen[1])
            painter.drawLine(p_left, p_right)

        # Disegna le 4 maniglie numerate identiche al Target
        handle_colors = [QColor(255, 71, 87), QColor(46, 213, 115), QColor(30, 144, 255), QColor(255, 165, 2)]
        handle_labels = ["1", "2", "3", "4"]
        
        for i, pt in enumerate(pts_screen):
            painter.setBrush(QBrush(handle_colors[i]))
            painter.setPen(QPen(QColor(255, 255, 255), 2))
            painter.drawEllipse(pt, 12, 12)
            painter.setPen(QPen(QColor(0, 0, 0), 1))
            painter.setFont(QFont("Arial", 10, QFont.Weight.Bold))
            painter.drawText(QRectF(pt.x() - 12, pt.y() - 12, 24, 24), Qt.AlignmentFlag.AlignCenter, handle_labels[i])

        # Badge di stato congelato
        if self.is_frozen:
            painter.setBrush(QBrush(QColor(238, 82, 83, 200)))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRoundedRect(int(px + 10), int(py + 10), 180, 26, 4, 4)
            painter.setPen(QPen(QColor(255, 255, 255)))
            painter.setFont(QFont("Arial", 10, QFont.Weight.Bold))
            painter.drawText(int(px + 18), int(py + 27), "📸 FOTOGRAMMA CONGELATO")

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton and self.pixmap_rect.width() > 0:
            pos = event.position()
            min_dist = 35.0
            closest = -1
            for i, (nx, ny) in enumerate(self.corners_norm):
                sx = self.pixmap_rect.x() + nx * self.pixmap_rect.width()
                sy = self.pixmap_rect.y() + ny * self.pixmap_rect.height()
                dist = np.hypot(pos.x() - sx, pos.y() - sy)
                if dist < min_dist:
                    min_dist = dist
                    closest = i

            if closest != -1:
                self.active_handle = closest
            else:
                nx = float(np.clip((pos.x() - self.pixmap_rect.x()) / self.pixmap_rect.width(), 0.0, 1.0))
                ny = float(np.clip((pos.y() - self.pixmap_rect.y()) / self.pixmap_rect.height(), 0.0, 1.0))
                dists = [np.hypot(nx - c[0], ny - c[1]) for c in self.corners_norm]
                idx_min = int(np.argmin(dists))
                self.corners_norm[idx_min] = [nx, ny]
                self.active_handle = idx_min
                self.corners_changed.emit()
                self.update()

    def mouseMoveEvent(self, event: QMouseEvent):
        if self.active_handle != -1 and self.pixmap_rect.width() > 0:
            pos = event.position()
            nx = float(np.clip((pos.x() - self.pixmap_rect.x()) / self.pixmap_rect.width(), 0.0, 1.0))
            ny = float(np.clip((pos.y() - self.pixmap_rect.y()) / self.pixmap_rect.height(), 0.0, 1.0))
            self.corners_norm[self.active_handle] = [nx, ny]
            self.corners_changed.emit()
            self.update()

    def mouseReleaseEvent(self, event: QMouseEvent):
        self.active_handle = -1

    def reset_corners_to_center(self):
        self.corners_norm = [
            [0.20, 0.20],
            [0.80, 0.20],
            [0.80, 0.80],
            [0.20, 0.80]
        ]
        self.corners_changed.emit()
        self.update()

# ==============================================================================
# WORKER ACQUISIZIONE LIVE CAMERA (DEBAYER RG2BGR -> Ch0=R, Ch1=G, Ch2=B)
# ==============================================================================
class CalibCameraWorker(QThread):
    frame_ready = pyqtSignal(np.ndarray, float, float)
    error_occurred = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.running = False
        self.camera = None
        self.exposure_ms = 8.0
        self.gain = 5
        self.latest_frame = None
        self.lock = threading.Lock()

    def run(self):
        dll_path = os.path.abspath(config.SDK_DLL_PATH)
        try:
            asi.init(dll_path)
        except Exception as e:
            self.error_occurred.emit(f"Errore SDK ZWO: {e}")
            return

        if asi.get_num_cameras() == 0:
            self.error_occurred.emit("Nessuna camera ZWO trovata via USB.")
            return

        try:
            self.camera = asi.Camera(0)
            props = self.camera.get_camera_property()
            w = props['MaxWidth'] // 2
            h = props['MaxHeight'] // 2
            w -= (w % 8)
            h -= (h % 2)
            
            self.camera.set_image_type(asi.ASI_IMG_RAW8)
            self.camera.set_roi_format(w, h, 2, asi.ASI_IMG_RAW8)
            self.camera.set_control_value(asi.ASI_EXPOSURE, int(self.exposure_ms * 1000))
            self.camera.set_control_value(asi.ASI_GAIN, self.gain)
            self.camera.set_control_value(asi.ASI_BANDWIDTHOVERLOAD, 90)
            self.camera.start_video_capture()
        except Exception as e:
            self.error_occurred.emit(f"Errore connessione: {e}")
            return

        self.running = True
        while self.running:
            try:
                raw_bytes = self.camera.capture_video_frame(timeout=800)
            except Exception:
                time.sleep(0.01)
                continue

            if raw_bytes is None:
                continue

            frame = np.frombuffer(raw_bytes, dtype=np.uint8).reshape((h, w))
            # Debayering corretto: Ch0=Rosso, Ch1=Verde, Ch2=Blu
            rgb = cv2.cvtColor(frame, cv2.COLOR_BayerRG2BGR)

            mean_b = float(np.mean(frame[::8, ::8]))
            max_b = float(np.max(frame[::8, ::8]))

            with self.lock:
                self.latest_frame = rgb.copy()

            self.frame_ready.emit(rgb, mean_b, max_b)

        try:
            self.camera.stop_video_capture()
            self.camera.close()
        except Exception:
            pass

    def update_exposure(self, ms):
        self.exposure_ms = ms
        if self.camera and self.running:
            try:
                self.camera.set_control_value(asi.ASI_EXPOSURE, int(ms * 1000))
            except Exception:
                pass

    def update_gain(self, g):
        self.gain = g
        if self.camera and self.running:
            try:
                self.camera.set_control_value(asi.ASI_GAIN, g)
            except Exception:
                pass

    def get_snapshot(self):
        with self.lock:
            return self.latest_frame.copy() if self.latest_frame is not None else None

    def stop(self):
        self.running = False
        self.wait(2000)

# ==============================================================================
# FINESTRA DI DEBUG VISIVO
# ==============================================================================
class DebugResultDialog(QDialog):
    def __init__(self, raw_warped, corrected_warped, parent=None):
        super().__init__(parent)
        self.setWindowTitle("🔍 Debug Calibrazione Colore - Confronto Prima / Dopo")
        self.resize(1100, 520)
        self.setStyleSheet("background-color: #121418; color: #dfe4ea; font-family: 'Segoe UI', Arial;")

        layout = QVBoxLayout(self)

        info_lbl = QLabel("<b>CONFRONTO VISIVO CALIBRAZIONE</b> (Verifica la corrispondenza dei 24 colori rispetto al Target)")
        info_lbl.setStyleSheet("color: #00d2ff; font-size: 13px; margin-bottom: 6px;")
        layout.addWidget(info_lbl)

        grid = QGridLayout()

        # 1. Target Riferimento
        target_box = QGroupBox("1. Target Ground Truth (sRGB)")
        tb_layout = QVBoxLayout(target_box)
        gt_img = self.generate_ground_truth_image(360, 240)
        lbl_gt = QLabel()
        lbl_gt.setPixmap(QPixmap.fromImage(gt_img))
        tb_layout.addWidget(lbl_gt)
        grid.addWidget(target_box, 0, 0)

        # 2. Immagine Raw Camera
        raw_box = QGroupBox("2. Immagine Raw Camera (Acquisita)")
        rb_layout = QVBoxLayout(raw_box)
        lbl_raw = QLabel()
        raw_resized = cv2.resize(raw_warped, (360, 240))
        q_raw = QImage(raw_resized.data, 360, 240, 360 * 3, QImage.Format.Format_RGB888)
        lbl_raw.setPixmap(QPixmap.fromImage(q_raw))
        rb_layout.addWidget(lbl_raw)
        grid.addWidget(raw_box, 0, 1)

        # 3. Immagine Corretta con Matrice
        corr_box = QGroupBox("3. Immagine Corretta (Matrice + WB)")
        cb_layout = QVBoxLayout(corr_box)
        lbl_corr = QLabel()
        corr_resized = cv2.resize(corrected_warped, (360, 240))
        q_corr = QImage(corr_resized.data, 360, 240, 360 * 3, QImage.Format.Format_RGB888)
        lbl_corr.setPixmap(QPixmap.fromImage(q_corr))
        cb_layout.addWidget(lbl_corr)
        grid.addWidget(corr_box, 0, 2)

        layout.addLayout(grid)

        # Pulsanti
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        close_btn = QPushButton("OK - Chiudi Debug")
        close_btn.setStyleSheet("background-color: #0984e3; color: #ffffff; font-weight: bold; padding: 8px 20px; border-radius: 5px;")
        close_btn.clicked.connect(self.accept)
        btn_layout.addWidget(close_btn)
        layout.addLayout(btn_layout)

    def generate_ground_truth_image(self, w, h):
        img = np.zeros((h, w, 3), dtype=np.uint8)
        cw = w / 6
        ch = h / 4
        idx = 0
        for r in range(4):
            for c in range(6):
                name, rgb = MACBETH_PATCHES[idx]
                y1, y2 = int(r * ch + 2), int((r + 1) * ch - 2)
                x1, x2 = int(c * cw + 2), int((c + 1) * cw - 2)
                img[y1:y2, x1:x2] = rgb
                idx += 1
        return QImage(img.data, w, h, w * 3, QImage.Format.Format_RGB888)

# ==============================================================================
# FINESTRA PRINCIPALE CALIBRAZIONE
# ==============================================================================
class ColorCalibrationApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("🎨 Fulmini - Calibrazione Colore & Matrice Sensore ZWO ASI")
        self.resize(1440, 890)

        # Icona applicazione
        icon_path = config.get_resource_path("icon.png")
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))

        self.last_calib_result = None
        self.latest_max_b = 0.0
        self.last_live_frame = None
        self.frozen_frame = None
        self.is_frozen = False
        self.last_raw_warped = None
        self.last_corrected_warped = None

        self.apply_theme()
        self.init_ui()
        self.start_worker()

    def apply_theme(self):
        self.setStyleSheet("""
            QMainWindow, QWidget { background-color: #121418; color: #dfe4ea; font-family: 'Segoe UI', Arial; font-size: 12px; }
            QGroupBox { border: 1px solid #2d3436; border-radius: 6px; margin-top: 6px; font-weight: bold; color: #00d2ff; padding: 8px; background-color: #181b22; }
            QPushButton { background-color: #2b303c; border: 1px solid #3d4454; border-radius: 5px; padding: 7px 12px; font-weight: bold; color: #ffffff; }
            QPushButton:hover { background-color: #3b4252; border-color: #00d2ff; }
            QSlider::groove:horizontal { height: 4px; background: #2b303c; border-radius: 2px; }
            QSlider::sub-page:horizontal { background: #00d2ff; border-radius: 2px; }
            QSlider::handle:horizontal { background: #ffffff; border: 1px solid #00d2ff; width: 14px; margin: -5px 0; border-radius: 7px; }
            QProgressBar { background-color: #14171d; border: 1px solid #2d3436; border-radius: 3px; text-align: center; color: #ffffff; font-weight: bold; font-size: 11px; }
            QProgressBar::chunk { background-color: #00d2ff; border-radius: 2px; }
        """)

    def init_ui(self):
        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        main_layout = QVBoxLayout(main_widget)
        main_layout.setContentsMargins(10, 10, 10, 10)

        header_box = QGroupBox("📋 Procedura Calibrazione Semplificata (Punti 1-4 & Freeze Frame)")
        h_layout = QVBoxLayout(header_box)
        h_layout.addWidget(QLabel("1. <b>Inquadra il Target a monitor</b> e regola l'esposizione finché la barra è <b>VERDE</b>."))
        h_layout.addWidget(QLabel("2. Clicca su <b>📸 Congela Fotogramma (Freeze)</b> per bloccare lo scatto fermo senza vibrazioni."))
        h_layout.addWidget(QLabel("3. <b>Fai combaciare i 4 cerchietti 1🔴, 2🟢, 3🔵, 4🟡 con i rispettivi numeri sul Target</b> (gestisce rotazioni e telescopio)."))
        h_layout.addWidget(QLabel("4. Clicca su <b>⚡ Calcola Matrice Colore</b> e poi su <b>💾 Salva</b>."))
        main_layout.addWidget(header_box)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        # 1. PANNELLO SINISTRA: TARGET A MONITOR & ANTEPRIMA RADDRIZZATA
        target_container = QWidget()
        target_vbox = QVBoxLayout(target_container)
        target_vbox.addWidget(QLabel("<b>TARGET COLORATO DI RIFERIMENTO (Da Inquadrare a Monitor)</b>"))
        self.target_widget = ColorTargetWidget()
        target_vbox.addWidget(self.target_widget, stretch=2)

        # Riquadro Anteprima Live Scontornata
        crop_box = QGroupBox("🔍 Anteprima 24 Colori Scontornati")
        crop_layout = QVBoxLayout(crop_box)
        self.warped_preview_lbl = QLabel("Allinea i punti 1, 2, 3, 4 per vedere i 24 colori...")
        self.warped_preview_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.warped_preview_lbl.setStyleSheet("background-color: #000000; border: 1px solid #00d2ff; border-radius: 4px;")
        self.warped_preview_lbl.setFixedHeight(140)
        crop_layout.addWidget(self.warped_preview_lbl)
        target_vbox.addWidget(crop_box, stretch=1)

        splitter.addWidget(target_container)

        # 2. PANNELLO DESTRA: INQUADRATURA CAMERA & CONTROLLI
        cam_container = QWidget()
        cam_vbox = QVBoxLayout(cam_container)
        
        top_cam_bar = QHBoxLayout()
        top_cam_bar.addWidget(QLabel("<b>INQUADRATURA CAMERA (Trascina 1, 2, 3, 4)</b>"))
        top_cam_bar.addStretch()
        
        self.freeze_btn = QPushButton("📸 Congela Fotogramma (Freeze)")
        self.freeze_btn.setStyleSheet("background-color: #ee5253; color: #ffffff; font-weight: bold;")
        self.freeze_btn.setCheckable(True)
        self.freeze_btn.clicked.connect(self.toggle_freeze_frame)
        top_cam_bar.addWidget(self.freeze_btn)

        self.reset_pts_btn = QPushButton("↺ Ripristina Punti al Centro")
        self.reset_pts_btn.clicked.connect(self.on_reset_corners)
        top_cam_bar.addWidget(self.reset_pts_btn)
        cam_vbox.addLayout(top_cam_bar)

        self.cam_widget = InteractiveCameraWidget()
        self.cam_widget.corners_changed.connect(self.update_warped_live)
        cam_vbox.addWidget(self.cam_widget, stretch=1)

        # Barra livello luce
        level_layout = QHBoxLayout()
        level_layout.addWidget(QLabel("Livello Picco Luce:"))
        self.level_bar = QProgressBar()
        self.level_bar.setRange(0, 255)
        self.level_bar.setValue(0)
        self.level_bar.setFixedHeight(18)
        level_layout.addWidget(self.level_bar)
        self.level_status_lbl = QLabel("Regola Esposizione")
        self.level_status_lbl.setFixedWidth(140)
        self.level_status_lbl.setStyleSheet("font-weight: bold; color: #00d2ff;")
        level_layout.addWidget(self.level_status_lbl)
        cam_vbox.addLayout(level_layout)

        # Controlli Esposizione
        ctrl_box = QGroupBox("Regolazione Esposizione & Guadagno Live")
        c_grid = QGridLayout(ctrl_box)
        c_grid.addWidget(QLabel("Esposizione (ms):"), 0, 0)
        self.exp_slider = QSlider(Qt.Orientation.Horizontal)
        self.exp_slider.setRange(1, 40)
        self.exp_slider.setValue(8)
        self.exp_slider.valueChanged.connect(self.on_exp_changed)
        c_grid.addWidget(self.exp_slider, 0, 1)
        self.exp_lbl = QLabel("8 ms")
        c_grid.addWidget(self.exp_lbl, 0, 2)

        c_grid.addWidget(QLabel("Guadagno (Gain):"), 1, 0)
        self.gain_slider = QSlider(Qt.Orientation.Horizontal)
        self.gain_slider.setRange(0, 60)
        self.gain_slider.setValue(5)
        self.gain_slider.valueChanged.connect(self.on_gain_changed)
        c_grid.addWidget(self.gain_slider, 1, 1)
        self.gain_lbl = QLabel("5")
        c_grid.addWidget(self.gain_lbl, 1, 2)
        cam_vbox.addWidget(ctrl_box)

        # Pulsanti Azione
        btn_layout = QHBoxLayout()
        self.calib_btn = QPushButton("⚡ Calcola Matrice Colore")
        self.calib_btn.setStyleSheet("background-color: #0984e3; font-size: 13px; padding: 10px;")
        self.calib_btn.clicked.connect(self.perform_calibration)
        btn_layout.addWidget(self.calib_btn)

        self.debug_btn = QPushButton("🔍 Mostra Debug Visivo")
        self.debug_btn.setEnabled(False)
        self.debug_btn.clicked.connect(self.show_debug_window)
        btn_layout.addWidget(self.debug_btn)

        self.save_btn = QPushButton("💾 Salva in 'camera_calibration.json'")
        self.save_btn.setStyleSheet("background-color: #10ac84; font-size: 13px; padding: 10px;")
        self.save_btn.setEnabled(False)
        self.save_btn.clicked.connect(self.save_calibration_file)
        btn_layout.addWidget(self.save_btn)
        cam_vbox.addLayout(btn_layout)

        # Risultati
        self.results_label = QLabel("Stato: Inquadra il target, clicca su 'Congela Fotogramma' e allinea i punti 1, 2, 3, 4.")
        self.results_label.setStyleSheet("color: #00d2ff; font-weight: bold; padding: 4px;")
        cam_vbox.addWidget(self.results_label)

        splitter.addWidget(cam_container)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)
        main_layout.addWidget(splitter)

    def start_worker(self):
        self.worker = CalibCameraWorker()
        self.worker.frame_ready.connect(self.on_frame_received)
        self.worker.error_occurred.connect(self.on_error)
        self.worker.start()

    def on_frame_received(self, rgb, mean_b, max_b):
        self.last_live_frame = rgb.copy()
        
        # Se non è congelato, aggiorna il video live
        if not self.is_frozen:
            self.latest_max_b = max_b
            self.level_bar.setValue(int(max_b))
            
            if max_b >= 230:
                self.level_bar.setStyleSheet("QProgressBar::chunk { background-color: #ff4757; }")
                self.level_status_lbl.setText("⚠️ TROPPO LUMINOSO!")
                self.level_status_lbl.setStyleSheet("color: #ff4757; font-weight: bold;")
            elif max_b >= 80 and max_b < 230:
                self.level_bar.setStyleSheet("QProgressBar::chunk { background-color: #2ed573; }")
                self.level_status_lbl.setText("✅ LIVELLO OTTIMALE")
                self.level_status_lbl.setStyleSheet("color: #2ed573; font-weight: bold;")
            else:
                self.level_bar.setStyleSheet("QProgressBar::chunk { background-color: #ffa502; }")
                self.level_status_lbl.setText("Troppo Scuro")
                self.level_status_lbl.setStyleSheet("color: #ffa502; font-weight: bold;")

            h, w, ch = rgb.shape
            q_img = QImage(rgb.data, w, h, ch * w, QImage.Format.Format_RGB888)
            pix = QPixmap.fromImage(q_img)
            self.cam_widget.set_camera_pixmap(pix, is_frozen=False)
            self.update_warped_live()

    def toggle_freeze_frame(self, checked):
        if checked:
            if self.last_live_frame is not None:
                self.frozen_frame = self.last_live_frame.copy()
                self.is_frozen = True
                self.freeze_btn.setText("▶️ Sblocca (Torna a Live)")
                self.freeze_btn.setStyleSheet("background-color: #10ac84; color: #ffffff; font-weight: bold;")
                
                h, w, ch = self.frozen_frame.shape
                q_img = QImage(self.frozen_frame.data, w, h, ch * w, QImage.Format.Format_RGB888)
                self.cam_widget.set_camera_pixmap(QPixmap.fromImage(q_img), is_frozen=True)
                self.results_label.setText("📷 Fotogramma bloccato! Posiziona con precisione i 4 punti 1, 2, 3, 4.")
                self.update_warped_live()
        else:
            self.is_frozen = False
            self.frozen_frame = None
            self.freeze_btn.setText("📸 Congela Fotogramma (Freeze)")
            self.freeze_btn.setStyleSheet("background-color: #ee5253; color: #ffffff; font-weight: bold;")
            self.results_label.setText("Video live ripreso.")

    def update_warped_live(self):
        active_frame = self.frozen_frame if (self.is_frozen and self.frozen_frame is not None) else self.last_live_frame
        if active_frame is None:
            return

        h, w = active_frame.shape[:2]
        pts_norm = self.cam_widget.corners_norm
        src_pts = np.array([
            [pts_norm[0][0] * w, pts_norm[0][1] * h],
            [pts_norm[1][0] * w, pts_norm[1][1] * h],
            [pts_norm[2][0] * w, pts_norm[2][1] * h],
            [pts_norm[3][0] * w, pts_norm[3][1] * h]
        ], dtype=np.float32)

        dst_w, dst_h = 240, 160
        dst_pts = np.array([[0, 0], [dst_w, 0], [dst_w, dst_h], [0, dst_h]], dtype=np.float32)
        
        try:
            M = cv2.getPerspectiveTransform(src_pts, dst_pts)
            warped = cv2.warpPerspective(active_frame, M, (dst_w, dst_h))
            q_warp = QImage(warped.data, dst_w, dst_h, dst_w * 3, QImage.Format.Format_RGB888)
            self.warped_preview_lbl.setPixmap(QPixmap.fromImage(q_warp).scaled(
                self.warped_preview_lbl.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.FastTransformation
            ))
        except Exception:
            pass

    def on_reset_corners(self):
        self.cam_widget.reset_corners_to_center()

    def on_exp_changed(self, val):
        self.exp_lbl.setText(f"{val} ms")
        self.worker.update_exposure(float(val))

    def on_gain_changed(self, val):
        self.gain_lbl.setText(str(val))
        self.worker.update_gain(int(val))

    def perform_calibration(self):
        active_frame = self.frozen_frame if (self.is_frozen and self.frozen_frame is not None) else self.worker.get_snapshot()
        if active_frame is None:
            QMessageBox.warning(self, "Attenzione", "Nessun frame disponibile per la calibrazione.")
            return

        self.results_label.setText("Campionamento 24 tessere e calcolo matrice in corso...")
        QApplication.processEvents()

        h, w = active_frame.shape[:2]
        pts_norm = self.cam_widget.corners_norm
        src_pts = np.array([
            [pts_norm[0][0] * w, pts_norm[0][1] * h],
            [pts_norm[1][0] * w, pts_norm[1][1] * h],
            [pts_norm[2][0] * w, pts_norm[2][1] * h],
            [pts_norm[3][0] * w, pts_norm[3][1] * h]
        ], dtype=np.float32)

        dst_w, dst_h = 600, 400
        dst_pts = np.array([[0, 0], [dst_w, 0], [dst_w, dst_h], [0, dst_h]], dtype=np.float32)
        M = cv2.getPerspectiveTransform(src_pts, dst_pts)
        warped = cv2.warpPerspective(active_frame, M, (dst_w, dst_h))
        self.last_raw_warped = warped.copy()

        # Campionamento delle 24 tessere colorate
        cols, rows = 6, 4
        cw = dst_w / cols
        ch_h = dst_h / rows
        
        measured_rgb = []
        reference_rgb = []

        idx = 0
        for r in range(rows):
            for c in range(cols):
                cx = int(c * cw + cw / 2)
                cy = int(r * ch_h + ch_h / 2)
                rad_x = int(cw * 0.18)
                rad_y = int(ch_h * 0.18)
                
                patch_crop = warped[cy - rad_y : cy + rad_y, cx - rad_x : cx + rad_x]
                avg_r = float(np.mean(patch_crop[:, :, 0]))
                avg_g = float(np.mean(patch_crop[:, :, 1]))
                avg_b = float(np.mean(patch_crop[:, :, 2]))

                measured_rgb.append([avg_r, avg_g, avg_b])
                reference_rgb.append(MACBETH_PATCHES[idx][1])
                idx += 1

        measured_np = np.array(measured_rgb, dtype=np.float32)
        reference_np = np.array(reference_rgb, dtype=np.float32)

        # Bilanciamento del Bianco (WB) dalle 6 tessere di grigio (indici 18-23)
        gray_measured = measured_np[18:24]
        max_measured_gray = np.max(gray_measured)
        scale_factor = 230.0 / max(max_measured_gray, 10.0)
        
        mean_gray_r = np.mean(gray_measured[:, 0])
        mean_gray_g = np.mean(gray_measured[:, 1])
        mean_gray_b = np.mean(gray_measured[:, 2])

        calc_wb_r = float(np.clip(mean_gray_g / max(mean_gray_r, 1.0), 0.5, 2.5))
        calc_wb_b = float(np.clip(mean_gray_g / max(mean_gray_b, 1.0), 0.5, 2.5))

        # Solver Regolarizzato Tikhonov Ridge Regression
        wb_applied = measured_np.copy()
        wb_applied[:, 0] *= calc_wb_r
        wb_applied[:, 2] *= calc_wb_b
        
        norm_meas = wb_applied * scale_factor

        lambda_reg = 0.12
        X = norm_meas
        Y = reference_np
        
        XtX = X.T @ X
        XtY = X.T @ Y
        reg_matrix = XtX + lambda_reg * np.eye(3) * np.trace(XtX) / 3.0
        target_with_prior = XtY + lambda_reg * (np.trace(XtX) / 3.0) * np.eye(3)
        
        ccm_transposed = np.linalg.solve(reg_matrix, target_with_prior)
        calculated_ccm = ccm_transposed.T

        # Preservazione luminanza
        for row in range(3):
            r_sum = np.sum(calculated_ccm[row])
            if r_sum > 0.1:
                calculated_ccm[row] /= r_sum

        # Immagine corretta
        wb_full_warped = warped.astype(np.float32)
        wb_full_warped[:, :, 0] *= calc_wb_r
        wb_full_warped[:, :, 2] *= calc_wb_b
        wb_full_warped = np.clip(wb_full_warped, 0, 255).astype(np.uint8)
        self.last_corrected_warped = cv2.transform(wb_full_warped, calculated_ccm)

        calibrated_test = cv2.transform(np.expand_dims(wb_applied, axis=0), calculated_ccm)[0]
        delta_error = float(np.mean(np.abs(calibrated_test - reference_np)))

        self.last_calib_result = {
            "camera_model": "ZWO ASI294MC Pro",
            "calibration_date": time.strftime("%Y-%m-%d %H:%M:%S"),
            "white_balance_gains": {
                "WB_R": round(calc_wb_r, 3),
                "WB_G": 1.000,
                "WB_B": round(calc_wb_b, 3)
            },
            "color_correction_matrix_3x3": np.round(calculated_ccm, 4).tolist(),
            "mean_color_error_delta": round(delta_error, 2),
            "measured_grayscale_response": {
                "R": [round(float(v), 1) for v in gray_measured[:, 0]],
                "G": [round(float(v), 1) for v in gray_measured[:, 1]],
                "B": [round(float(v), 1) for v in gray_measured[:, 2]]
            }
        }

        self.results_label.setText(
            f"✅ Calibrazione Riuscita! WB_R: {calc_wb_r:.2f} | WB_B: {calc_wb_b:.2f} | Errore Residuo Δ: {delta_error:.1f}"
        )
        self.save_btn.setEnabled(True)
        self.debug_btn.setEnabled(True)

        self.show_debug_window()

    def show_debug_window(self):
        if self.last_raw_warped is not None and self.last_corrected_warped is not None:
            dlg = DebugResultDialog(self.last_raw_warped, self.last_corrected_warped, self)
            dlg.exec()

    def save_calibration_file(self):
        if not self.last_calib_result:
            return

        json_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "camera_calibration.json")
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(self.last_calib_result, f, indent=4)

        QMessageBox.information(
            self,
            "Salvataggio Riuscito",
            f"File di calibrazione salvato in:\n{json_path}\n\n"
            f"La GUI e lo stacker useranno automaticamente questo profilo!"
        )

    def on_error(self, err_msg):
        QMessageBox.critical(self, "Errore Camera", err_msg)

    def closeEvent(self, event):
        self.worker.stop()
        event.accept()

# ==============================================================================
# MAIN ENTRY POINT
# ==============================================================================
def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    win = ColorCalibrationApp()
    win.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
