# ⚡ Fulmini

Software per Windows per la ripresa e il salvataggio automatico di fulmini durante i temporali utilizzando camere astronomiche **ZWO ASI** (es. ASI294MC Pro, ASI585MC, ASI678MC, ASI178, ASI120 e simili).

---

## 📌 Cosa fa in pratica

Quando c'è un temporale, filmare ore di video continuo riempirebbe il disco in pochi minuti con decine di gigabyte di buio inutile.  
**Fulmini** risolve il problema tenendo sempre in memoria RAM un buffer circolare degli ultimi fotogrammi:

1. **Ascolto continuo senza scrivere su disco**: la camera gira a piena velocità (fino a 60+ FPS con binning 2x2) memorizzando gli ultimi 5-10 fotogrammi solo in RAM.
2. **Rilevamento del lampo (Trigger differenziale)**: confronta il fotogramma attuale con quelli precedenti (es. 6 frame fa). Se compaiono punti ad alto contrasto (una scarica, un ramo di fulmine o un puntatore laser) o c'è un lampo diffuso, il software fa scattare l'acquisizione. Non scatta sulle luci fisse (lampadine, lampioni).
3. **Salvataggio immediato del video grezzo (.SER)**: salva sul disco l'inizio del fulmine (preso dal buffer in RAM) e continua a registrare per il tempo impostato (es. 1 o 2 secondi). Durante la registrazione non fa calcoli pesanti: scrive e basta.
4. **Somma delle immagini a 16 bit (.TIFF)**: a fine sessione, con un pulsante, elabora tutti i video `.SER` registrati e genera le immagini finali composte in **TIFF a 16-bit reali** (con bilanciamento del bianco e matrice colore calibrata per togliere la dominante verdastra dei sensori Bayer) più una comoda anteprima `.JPG`.
5. **Pulizia disco facile**: include un'interfaccia dedicata per vedere le foto e cancellare con il tasto **CANC** sia l'anteprima JPG che il TIFF e il video SER pesante associato in un colpo solo.

---

## 🚀 Come si lancia

Basta fare doppio clic sui file `.bat` presenti nella cartella:

| File Batch | Cosa fa |
| :--- | :--- |
| **`run_gui.bat`** | **Programma principale con Interfaccia Grafica**: anteprima video live, regolazione esposizione/gain, stretch, trigger e galleria. |
| **`cleaner.bat`** | **Utility di pulizia**: mostra la galleria dei file, lo spazio occupato e permette di cancellare con il tasto `CANC` tutti i file collegati (JPG + TIFF + SER). |
| **`calibrate.bat`** | **Calibrazione colore**: per inquadrare un ColorChecker a monitor e calcolare la matrice colore personalizzata salvandola in `camera_calibration.json`. |
| **`stack_all.bat`** | Elabora da riga di comando tutti i file `.SER` presenti nella cartella `captures/` e crea i relativi TIFF. |
| **`run.bat`** | Versione minimale da terminale senza interfaccia grafica. |
| **`test_camera.bat`** | Diagnostica e test degli FPS effettivi su porta USB 3.0. |

---

## 🛠️ Requisiti e Installazione

1. **Sistema Operativo**: Windows 10 / 11 (64-bit).
2. **Python**: Python 3.10 o successivo installato (con opzione *"Add Python to PATH"* spuntata durante l'installazione).
3. **Camera**: Qualsiasi camera USB 3.0 / USB 2.0 ZWO ASI collegata al PC (la libreria `ASICamera2.dll` è già inclusa nella cartella del progetto).

Per installare le librerie necessarie, apri il terminale nella cartella del programma e dai:
```bash
pip install -r requirements.txt
```

Librerie utilizzate: `zwoasi`, `opencv-python`, `PyQt6`, `numpy`, `tifffile`, `Pillow`.

---

## ⚙️ Parametri principali spiegati semplice

Dalla finestra principale (`run_gui.bat`), nella scheda **⚡ Trigger**:

* **Modalità Trigger**: 
  * `Ibrido (Consigliato)`: scatta sia se vede rami di fulmine / punti ad alto contrasto, sia se c'è un flash diffuso nel cielo.
  * `Solo Punti Accesi`: ideale per scariche filiformi o test con puntatore laser.
  * `Solo Delta Medio`: scatta solo se l'intera inquadratura aumenta di luminosità media.
* **Soglia Contrasto (%)**: di quanto deve essere più luminoso il punto rispetto a prima per essere considerato un lampo (default: `+60%`).
* **Punti Minimi (Pixel)**: quanti pixel devono accendersi contemporaneamente per far scattare la registrazione (default: `20 px`).
* **Frame di Riferimento**: quanti fotogrammi indietro nel tempo andare a guardare per fare il confronto (default: `6 frame`).
* **Frame Pre-Trigger**: quanti fotogrammi precedenti al lampo recuperare dalla RAM prima dello scatto (default: `6 frame`).
* **Durata Post-Trigger**: per quanti secondi continuare a registrare dopo che è scattato il fulmine (default: `1.5 s`).

---

## 📁 Struttura dei File Salvati

Tutti i file vengono salvati automaticamente nella sottocartella `captures/`:

* `lightning_YYYYMMDD_HHMMSS.ser`: Video grezzo non compresso della sequenza.
* `lightning_YYYYMMDD_HHMMSS_sum.tiff`: Immagine a 16-bit (Massima intensità / Max-Stack) con profilo colore calibrato.
* `lightning_YYYYMMDD_HHMMSS_sum.jpg`: Immagine compressa per anteprima rapida su smartphone o social.

---

## 🧹 Gestione dello Spazio su Disco

I file video `.SER` non compressi a piena risoluzione possono pesare da 200 MB a 500 MB ciascuno.  
Aprendo **`cleaner.bat`** (oppure cliccando sul tasto **`🧹 Pulizia`** nella GUI principale) puoi scorrere le foto salvate:
* Seleziona le catture venute male o i falsi scatti (puoi fare selezione multipla con `Ctrl` o `Shift`).
* Premi il tasto **`CANC`**: il programma ti mostrerà quanti GB stai liberando ed eliminerà in automatico il JPG, il TIFF e il pesante file video SER corrispondente.

---

## 🎨 Calibrazione Colore per Sensori Astronomici One-Shot-Color

I sensori astronomici hanno una risposta spettrale grezza (prevalenza di verde) diversa da quella delle normali macchine fotografiche reflex.  
Il programma include una pipeline colore a 3 stadi:
1. Bilanciamento del bianco ($WB_R = 1.15, WB_B = 1.05$).
2. Matrice di correzione $3 \times 3$ per eliminare il crosstalk spettrale dei filtri Bayer.
3. Curva tonale e saturazione regolabile.

Se vuoi calibrare la tua camera specifica, avvia **`calibrate.bat`**, inquadra a monitor il target a 24 colori e premi *Calcola Matrice*: verrà generato il file `camera_calibration.json` che verrà caricato in automatico sia dall'interfaccia di acquisizione che dallo stacker.
