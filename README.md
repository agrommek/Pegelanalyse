# Pegelanalyse

Visualisierung von Schallpegelmessungen aus CSV-Dateien eines Sound Level Meters,
wahlweise kombiniert mit zugehörigen WAV-Audioaufnahmen.

## Was die App tut

- Liest CSV-Dateien vom Sound Level Meter (Schallpegel in dB(A), bis ~30.000 Messwerte)
- Berechnet den gleitenden Leq (äquivalenter Dauerschallpegel) über ein konfigurierbares Zeitfenster
- Liest optional WAV-Aufnahmen (96 kHz, 24-bit PCM, Stereo; auch mehrere Dateien) und berechnet den Leq in dBFS
- Richtet beide Signale zeitlich aufeinander aus
- Erzeugt einen Dual-Achsen-Plot (dB(A) links, dBFS rechts) und zeigt ihn an und/oder speichert ihn als PNG/SVG

## Verwendung

### GUI-Modus

Die App ohne Argumente starten — es öffnet sich ein Fenster:

```
python pegelanalyse.py
```

oder mit der vorkompilierten Executable:

```
pegelanalyse.exe          # Windows
./pegelanalyse            # Linux / macOS
```

Im Fenster:
1. CSV-Datei per Drag & Drop in die linke Zone ziehen
2. WAV-Datei(en) per Drag & Drop in die rechte Zone ziehen (mehrere gleichzeitig oder nacheinander möglich)
3. Felder prüfen / anpassen (Audio-Start, Integrationszeit, Export-Dateiname, PNG/SVG)
4. **Generiere Plot** klicken

### CLI-Modus

```
python pegelanalyse.py [Optionen]
```

| Option | Beschreibung | Standard |
|---|---|---|
| `--csv FILE` | CSV-Datei vom Sound Level Meter | — |
| `--wav FILE [FILE …]` | WAV-Datei(en) der Aufnahme | — |
| `--audio-start HH:MM:SS` | Startzeitpunkt der Audioaufnahme | `09:55:00` |
| `--integration-time SEK` | Integrationszeit für Leq in Sekunden | `20` |
| `--save-png` | Plot als PNG speichern | nein |
| `--save-svg` | Plot als SVG speichern | nein |
| `--export-basename NAME` | Dateiname ohne Endung | Datum aus CSV |

Mindestens `--csv` oder `--wav` muss angegeben werden.

**Beispiele:**

```bash
# Nur CSV
python pegelanalyse.py --csv messung.txt

# CSV + zwei WAV-Dateien, Ergebnis speichern
python pegelanalyse.py \
  --csv messung.txt \
  --wav aufnahme1.wav aufnahme2.wav \
  --audio-start 10:05:30 \
  --integration-time 30 \
  --save-png --save-svg \
  --export-basename 2026-02-15
```

## Installation

### Vorkompilierte Executables

Fertige Executables für Windows, Linux und macOS stehen unter
[Releases](../../releases) bzw. als Build-Artefakte unter
[Actions](../../actions) zum Download bereit. Kein Python erforderlich.

### Aus dem Quellcode

Python 3.11 oder neuer wird benötigt.

```bash
pip install -r requirements.txt
python pegelanalyse.py --help
```

## Abhängigkeiten

| Paket | Zweck |
|---|---|
| [NumPy](https://numpy.org) | Effizientes Einlesen von 24-bit WAV-Daten |
| [Matplotlib](https://matplotlib.org) | Plot-Erzeugung |
| [tkinterdnd2](https://github.com/pmgagne/tkinterdnd2) | Drag & Drop in der GUI |
