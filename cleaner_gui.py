"""
================================================================================
⚡ LIGHTNING HUNTER - UTILITY DI PULIZIA CATTURE & GESTIONE SPAZIO DISCO
================================================================================
Applicazione GUI (PyQt6) per la visualizzazione, revisione ed eliminazione rapida
delle catture elaborate.

Funzionalità:
- Elenco visuale con miniature di tutti i file JPG elaborati (e opzione SER non elaborati).
- Riconoscimento automatico e raggruppamento dei file collegati (.JPG + .TIFF + .SER).
- Selezione multipla (Ctrl+Click, Shift+Click, Ctrl+A).
- Cancellazione tramite pressione del tasto CANC (Delete) o pulsante dedicato.
- Calcolo in tempo reale dello spazio liberato su disco (MB / GB).
- Anteprima ingrandita istantanea del frame JPG.
================================================================================
"""

import sys
import os
import glob
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QListWidget, QListWidgetItem, QSplitter,
    QMessageBox, QGroupBox, QLineEdit, QCheckBox, QProgressBar,
    QFrame, QSizePolicy, QMenu
)
from PyQt6.QtGui import QPixmap, QImage, QIcon, QFont, QColor, QKeySequence, QShortcut
from PyQt6.QtCore import Qt, QSize, QTimer

import config

def format_size(bytes_val):
    """Formatta la dimensione in byte in stringa leggibile (KB, MB, GB)."""
    if bytes_val < 1024:
        return f"{bytes_val} B"
    elif bytes_val < 1024 * 1024:
        return f"{bytes_val / 1024:.1f} KB"
    elif bytes_val < 1024 * 1024 * 1024:
        return f"{bytes_val / (1024 * 1024):.1f} MB"
    else:
        return f"{bytes_val / (1024 * 1024 * 1024):.2f} GB"

class CapturePackage:
    """Rappresenta un evento catturato con tutti i file associati (JPG, TIFF, SER)."""
    def __init__(self, base_id, captures_dir):
        self.base_id = base_id
        self.captures_dir = captures_dir
        self.jpg_path = None
        self.tiff_path = None
        self.ser_path = None
        
        self.jpg_size = 0
        self.tiff_size = 0
        self.ser_size = 0
        
        self.scan_files()

    def scan_files(self):
        # 1. Ricerca JPG
        cand_jpg1 = os.path.join(self.captures_dir, f"{self.base_id}_sum.jpg")
        cand_jpg2 = os.path.join(self.captures_dir, f"{self.base_id}.jpg")
        if os.path.exists(cand_jpg1):
            self.jpg_path = cand_jpg1
            self.jpg_size = os.path.getsize(cand_jpg1)
        elif os.path.exists(cand_jpg2):
            self.jpg_path = cand_jpg2
            self.jpg_size = os.path.getsize(cand_jpg2)

        # 2. Ricerca TIFF
        cand_tiff1 = os.path.join(self.captures_dir, f"{self.base_id}_sum.tiff")
        cand_tiff2 = os.path.join(self.captures_dir, f"{self.base_id}.tiff")
        cand_tiff3 = os.path.join(self.captures_dir, f"{self.base_id}_sum.tif")
        if os.path.exists(cand_tiff1):
            self.tiff_path = cand_tiff1
            self.tiff_size = os.path.getsize(cand_tiff1)
        elif os.path.exists(cand_tiff2):
            self.tiff_path = cand_tiff2
            self.tiff_size = os.path.getsize(cand_tiff2)
        elif os.path.exists(cand_tiff3):
            self.tiff_path = cand_tiff3
            self.tiff_size = os.path.getsize(cand_tiff3)

        # 3. Ricerca SER
        cand_ser = os.path.join(self.captures_dir, f"{self.base_id}.ser")
        if os.path.exists(cand_ser):
            self.ser_path = cand_ser
            self.ser_size = os.path.getsize(cand_ser)

    @property
    def total_size(self):
        return self.jpg_size + self.tiff_size + self.ser_size

    def delete_all_files(self):
        """Elimina fisicamente tutti i file associati al pacchetto."""
        deleted_bytes = 0
        deleted_files = []
        
        for path in [self.jpg_path, self.tiff_path, self.ser_path]:
            if path and os.path.exists(path):
                try:
                    sz = os.path.getsize(path)
                    os.remove(path)
                    deleted_bytes += sz
                    deleted_files.append(path)
                except Exception as e:
                    print(f"[ERRORE ELIMINAZIONE] {path}: {e}")
        return deleted_bytes, deleted_files


class CleanerGUI(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("🧹 Fulmini - Pulizia Catture & Gestione Spazio Disco")
        self.resize(1200, 780)
        self.setMinimumSize(950, 600)

        # Icona applicazione
        icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icon.png")
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))

        self.captures_dir = os.path.abspath(config.OUTPUT_DIR)
        os.makedirs(self.captures_dir, exist_ok=True)

        self.packages = []
        self.cached_pixmaps = {}

        self.apply_dark_theme()
        self.init_ui()
        self.reload_captures()

    def apply_dark_theme(self):
        self.setStyleSheet("""
            QMainWindow { background-color: #1e1e24; color: #f5f6fa; font-family: 'Segoe UI', sans-serif; }
            QWidget { background-color: #1e1e24; color: #f5f6fa; }
            QGroupBox {
                border: 1px solid #353b48;
                border-radius: 6px;
                margin-top: 10px;
                padding-top: 12px;
                font-weight: bold;
                color: #00d2ff;
            }
            QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; }
            QPushButton {
                background-color: #2f3542;
                border: 1px solid #57606f;
                border-radius: 5px;
                padding: 6px 12px;
                font-weight: bold;
                color: #f1f2f6;
            }
            QPushButton:hover { background-color: #404b5a; border-color: #00d2ff; }
            QPushButton:pressed { background-color: #20242c; }
            QLineEdit {
                background-color: #141418;
                border: 1px solid #485460;
                border-radius: 4px;
                padding: 4px 8px;
                color: #ffffff;
            }
            QLineEdit:focus { border-color: #00d2ff; }
            QListWidget {
                background-color: #141418;
                border: 1px solid #2f3542;
                border-radius: 6px;
                padding: 4px;
                outline: none;
            }
            QListWidget::item {
                border-radius: 4px;
                padding: 6px;
                margin-bottom: 3px;
                border-bottom: 1px solid #1e2229;
            }
            QListWidget::item:hover {
                background-color: #242933;
            }
            QListWidget::item:selected {
                background-color: #2d3e50;
                border: 1px solid #00d2ff;
                color: #ffffff;
            }
            QCheckBox { spacing: 6px; }
            QCheckBox::indicator { width: 15px; height: 15px; }
        """)

    def init_ui(self):
        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        main_layout = QVBoxLayout(main_widget)
        main_layout.setContentsMargins(12, 10, 12, 10)
        main_layout.setSpacing(8)

        # --- TOP TOOLBAR ---
        top_bar = QHBoxLayout()
        self.stats_lbl = QLabel("Scansione cartella...")
        self.stats_lbl.setStyleSheet("font-size: 13px; font-weight: bold; color: #00d2ff;")
        top_bar.addWidget(self.stats_lbl)

        top_bar.addStretch()

        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("🔍 Cerca per data o nome (es. 20260828)...")
        self.filter_edit.setFixedWidth(240)
        self.filter_edit.textChanged.connect(self.apply_filter)
        top_bar.addWidget(self.filter_edit)

        self.include_unprocessed_chk = QCheckBox("Mostra anche SER non sommati")
        self.include_unprocessed_chk.setChecked(True)
        self.include_unprocessed_chk.toggled.connect(self.reload_captures)
        top_bar.addWidget(self.include_unprocessed_chk)

        self.refresh_btn = QPushButton("🔄 Ricarica")
        self.refresh_btn.clicked.connect(self.reload_captures)
        top_bar.addWidget(self.refresh_btn)

        self.open_dir_btn = QPushButton("📁 Apri Cartella")
        self.open_dir_btn.clicked.connect(self.open_captures_dir)
        top_bar.addWidget(self.open_dir_btn)

        main_layout.addLayout(top_bar)

        # --- CENTRAL SPLITTER (LISTA A SINISTRA | ANTEPRIMA A DESTRA) ---
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # COLONNA SINISTRA: LISTA CATTURE
        left_box = QGroupBox("Elenco Catture Registrate (Seleziona & premi CANC)")
        left_layout = QVBoxLayout(left_box)
        left_layout.setContentsMargins(8, 8, 8, 8)
        left_layout.setSpacing(6)

        # Barra di selezione rapida
        sel_bar = QHBoxLayout()
        self.sel_all_btn = QPushButton("☑️ Seleziona Tutti")
        self.sel_all_btn.clicked.connect(self.select_all_items)
        sel_bar.addWidget(self.sel_all_btn)

        self.desel_all_btn = QPushButton("⬜ Deseleziona")
        self.desel_all_btn.clicked.connect(self.deselect_all_items)
        sel_bar.addWidget(self.desel_all_btn)

        sel_bar.addStretch()

        self.delete_btn = QPushButton("🗑️ Elimina Selezionati (CANC)")
        self.delete_btn.setStyleSheet("background-color: #eb4d4b; color: #ffffff; font-weight: bold; padding: 6px 14px;")
        self.delete_btn.clicked.connect(self.confirm_delete_selected)
        sel_bar.addWidget(self.delete_btn)
        left_layout.addLayout(sel_bar)

        # Lista Catture
        self.list_widget = QListWidget()
        self.list_widget.setIconSize(QSize(96, 64))
        self.list_widget.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self.list_widget.itemSelectionChanged.connect(self.on_selection_changed)
        self.list_widget.itemDoubleClicked.connect(self.on_item_double_clicked)
        left_layout.addWidget(self.list_widget)

        # Scorciatoia da tastiera per il tasto CANC / DELETE
        self.del_shortcut = QShortcut(QKeySequence.StandardKey.Delete, self)
        self.del_shortcut.activated.connect(self.confirm_delete_selected)

        splitter.addWidget(left_box)

        # COLONNA DESTRA: ANTEPRIMA & DETTAGLI
        right_box = QGroupBox("Anteprima & Dettagli File")
        right_layout = QVBoxLayout(right_box)
        right_layout.setContentsMargins(10, 10, 10, 10)
        right_layout.setSpacing(8)

        self.preview_label = QLabel("Nessuna cattura selezionata")
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_label.setStyleSheet("background-color: #0d0e11; border: 2px solid #2f3542; border-radius: 8px;")
        self.preview_label.setMinimumSize(420, 280)
        self.preview_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        right_layout.addWidget(self.preview_label, stretch=3)

        # Dettagli Testuali
        self.details_label = QLabel("Seleziona una cattura dalla lista per visualizzare l'anteprima e i dettagli.")
        self.details_label.setStyleSheet("background-color: #141418; padding: 10px; border-radius: 6px; border: 1px solid #2f3542; font-size: 12px;")
        self.details_label.setTextFormat(Qt.TextFormat.RichText)
        self.details_label.setWordWrap(True)
        right_layout.addWidget(self.details_label, stretch=1)

        # Pulsanti Azione Dettaglio
        act_bar = QHBoxLayout()
        self.open_jpg_btn = QPushButton("👁️ Apri JPG")
        self.open_jpg_btn.setEnabled(False)
        self.open_jpg_btn.clicked.connect(self.open_current_jpg)
        act_bar.addWidget(self.open_jpg_btn)

        self.open_tiff_btn = QPushButton("🖼️ Apri TIFF (16-bit)")
        self.open_tiff_btn.setEnabled(False)
        self.open_tiff_btn.clicked.connect(self.open_current_tiff)
        act_bar.addWidget(self.open_tiff_btn)

        right_layout.addLayout(act_bar)

        splitter.addWidget(right_box)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)

        main_layout.addWidget(splitter)

    def reload_captures(self):
        """Scansiona la cartella captures/ e raggruppa i file per identificativo base."""
        self.packages.clear()
        self.list_widget.clear()
        self.cached_pixmaps.clear()

        if not os.path.exists(self.captures_dir):
            self.stats_lbl.setText("Cartella catture vuota o non trovata.")
            return

        all_files = os.listdir(self.captures_dir)
        
        # Identifica tutti i prefissi unici delle catture (es. lightning_YYYYMMDD_HHMMSS)
        base_ids = set()
        for f in all_files:
            lower = f.lower()
            if lower.endswith(".ser") or lower.endswith(".jpg") or lower.endswith(".tiff") or lower.endswith(".tif"):
                name = os.path.splitext(f)[0]
                if name.endswith("_sum"):
                    name = name[:-4]
                elif name.startswith("test_") or name.startswith("calibration_"):
                    continue
                base_ids.add(name)

        sorted_bases = sorted(list(base_ids), reverse=True)
        show_unprocessed = self.include_unprocessed_chk.isChecked()

        total_bytes = 0
        total_count = 0

        for base_id in sorted_bases:
            pkg = CapturePackage(base_id, self.captures_dir)
            if not show_unprocessed and not pkg.jpg_path:
                continue
            if pkg.total_size == 0:
                continue

            self.packages.append(pkg)
            total_bytes += pkg.total_size
            total_count += 1

        self.stats_lbl.setText(
            f"📊 Totale Catture: {total_count}  |  💾 Spazio Totale Occupato: {format_size(total_bytes)}"
        )

        self.apply_filter()

    def apply_filter(self):
        """Filtra la lista in base al testo inserito nella barra di ricerca."""
        query = self.filter_edit.text().strip().lower()
        self.list_widget.clear()

        for pkg in self.packages:
            if query and query not in pkg.base_id.lower():
                continue

            # Creazione testo descrittivo dell'elemento
            files_desc = []
            if pkg.jpg_path:
                files_desc.append(f"JPG: {format_size(pkg.jpg_size)}")
            if pkg.tiff_path:
                files_desc.append(f"TIFF: {format_size(pkg.tiff_size)}")
            if pkg.ser_path:
                files_desc.append(f"SER: {format_size(pkg.ser_size)}")

            files_str = " | ".join(files_desc)
            tot_str = format_size(pkg.total_size)

            status_icon = "⚡" if pkg.jpg_path else "📹"
            item_text = f"{status_icon} {pkg.base_id}\n   [{files_str}]  ➔ Totale: {tot_str}"

            item = QListWidgetItem(item_text)
            item.setData(Qt.ItemDataRole.UserRole, pkg)

            # Thumbnail anteprima se disponibile
            if pkg.jpg_path:
                if pkg.jpg_path not in self.cached_pixmaps:
                    pix = QPixmap(pkg.jpg_path)
                    if not pix.isNull():
                        thumb = pix.scaled(96, 64, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
                        self.cached_pixmaps[pkg.jpg_path] = QIcon(thumb)
                if pkg.jpg_path in self.cached_pixmaps:
                    item.setIcon(self.cached_pixmaps[pkg.jpg_path])
                item.setForeground(QColor("#2ed573"))
            else:
                item.setForeground(QColor("#ffa502"))

            self.list_widget.addItem(item)

        if self.list_widget.count() > 0:
            self.list_widget.setCurrentRow(0)
        else:
            self.on_selection_changed()

    def on_selection_changed(self):
        selected_items = self.list_widget.selectedItems()
        count = len(selected_items)

        if count == 0:
            self.preview_label.setText("Nessun elemento selezionato")
            self.preview_label.setPixmap(QPixmap())
            self.details_label.setText("Seleziona uno o più elementi dalla lista.")
            self.open_jpg_btn.setEnabled(False)
            self.open_tiff_btn.setEnabled(False)
            self.delete_btn.setText("🗑️ Elimina Selezionati (CANC)")
            return

        sel_bytes = sum(item.data(Qt.ItemDataRole.UserRole).total_size for item in selected_items)
        self.delete_btn.setText(f"🗑️ Elimina {count} Selezionati ({format_size(sel_bytes)})")

        # Visualizza anteprima del primo elemento selezionato
        first_pkg = selected_items[0].data(Qt.ItemDataRole.UserRole)
        
        if first_pkg.jpg_path and os.path.exists(first_pkg.jpg_path):
            pix = QPixmap(first_pkg.jpg_path)
            if not pix.isNull():
                scaled = pix.scaled(
                    self.preview_label.size(),
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation
                )
                self.preview_label.setPixmap(scaled)
            else:
                self.preview_label.setText("Anteprima non disponibile")
            self.open_jpg_btn.setEnabled(True)
        else:
            self.preview_label.setText("📹 Solo File Video .SER\n(Nessuna immagine JPG generata)")
            self.preview_label.setPixmap(QPixmap())
            self.open_jpg_btn.setEnabled(False)

        self.open_tiff_btn.setEnabled(bool(first_pkg.tiff_path and os.path.exists(first_pkg.tiff_path)))

        # Dettagli formattati
        det_html = f"<h3>⚡ {first_pkg.base_id}</h3>"
        det_html += f"<b>Spazio Totale Pacchetto:</b> <span style='color:#00d2ff;'>{format_size(first_pkg.total_size)}</span><br><br>"
        det_html += f"<b>File JPG:</b> {os.path.basename(first_pkg.jpg_path) if first_pkg.jpg_path else '<i>Assente</i>'} ({format_size(first_pkg.jpg_size)})<br>"
        det_html += f"<b>File TIFF (16-bit):</b> {os.path.basename(first_pkg.tiff_path) if first_pkg.tiff_path else '<i>Assente</i>'} ({format_size(first_pkg.tiff_size)})<br>"
        det_html += f"<b>File SER:</b> {os.path.basename(first_pkg.ser_path) if first_pkg.ser_path else '<i>Assente</i>'} ({format_size(first_pkg.ser_size)})<br>"
        
        if count > 1:
            det_html += f"<br><hr><b>🔹 Elementi Multipli Selezionati:</b> {count} catture (<span style='color:#eb4d4b;'>{format_size(sel_bytes)} totali</span>)"

        self.details_label.setText(det_html)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # Riaggiorna l'anteprima alla dimensione della label
        selected_items = self.list_widget.selectedItems()
        if selected_items:
            pkg = selected_items[0].data(Qt.ItemDataRole.UserRole)
            if pkg.jpg_path and os.path.exists(pkg.jpg_path):
                pix = QPixmap(pkg.jpg_path)
                if not pix.isNull():
                    scaled = pix.scaled(
                        self.preview_label.size(),
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation
                    )
                    self.preview_label.setPixmap(scaled)

    def select_all_items(self):
        self.list_widget.selectAll()

    def deselect_all_items(self):
        self.list_widget.clearSelection()

    def on_item_double_clicked(self, item):
        pkg = item.data(Qt.ItemDataRole.UserRole)
        if pkg.jpg_path and os.path.exists(pkg.jpg_path):
            os.startfile(pkg.jpg_path)
        elif pkg.tiff_path and os.path.exists(pkg.tiff_path):
            os.startfile(pkg.tiff_path)

    def open_current_jpg(self):
        selected_items = self.list_widget.selectedItems()
        if selected_items:
            pkg = selected_items[0].data(Qt.ItemDataRole.UserRole)
            if pkg.jpg_path and os.path.exists(pkg.jpg_path):
                os.startfile(pkg.jpg_path)

    def open_current_tiff(self):
        selected_items = self.list_widget.selectedItems()
        if selected_items:
            pkg = selected_items[0].data(Qt.ItemDataRole.UserRole)
            if pkg.tiff_path and os.path.exists(pkg.tiff_path):
                os.startfile(pkg.tiff_path)

    def open_captures_dir(self):
        if os.path.exists(self.captures_dir):
            os.startfile(self.captures_dir)

    def confirm_delete_selected(self):
        """Chiede conferma ed elimina tutti i file associati agli elementi selezionati."""
        selected_items = self.list_widget.selectedItems()
        if not selected_items:
            QMessageBox.information(self, "Nessuna selezione", "Seleziona almeno una cattura da eliminare.")
            return

        count = len(selected_items)
        packages_to_delete = [item.data(Qt.ItemDataRole.UserRole) for item in selected_items]
        total_bytes = sum(p.total_size for p in packages_to_delete)

        num_jpg = sum(1 for p in packages_to_delete if p.jpg_path)
        num_tiff = sum(1 for p in packages_to_delete if p.tiff_path)
        num_ser = sum(1 for p in packages_to_delete if p.ser_path)

        msg = (
            f"Sei sicuro di voler eliminare definitivamente <b>{count} catture</b>?<br><br>"
            f"Verranno rimossi dal disco i file collegati:<br>"
            f"• <b>{num_jpg}</b> file .JPG<br>"
            f"• <b>{num_tiff}</b> file .TIFF (16-bit)<br>"
            f"• <b>{num_ser}</b> file video .SER<br><br>"
            f"💾 <b>Spazio che verrà liberato: <span style='color:#eb4d4b;'>{format_size(total_bytes)}</span></b>"
        )

        reply = QMessageBox.warning(
            self,
            "Conferma Eliminazione Definitiva",
            msg,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel
        )

        if reply == QMessageBox.StandardButton.Yes:
            deleted_bytes_total = 0
            for pkg in packages_to_delete:
                del_bytes, _ = pkg.delete_all_files()
                deleted_bytes_total += del_bytes

            self.reload_captures()
            self.statusBar().showMessage(
                f"✅ Eliminate {count} catture con successo! Liberati {format_size(deleted_bytes_total)} su disco.",
                4000
            )


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    win = CleanerGUI()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
