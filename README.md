# Pegelanalyse

Visualisierung von Schallpegelmessungen aus CSV-Dateien eines Sound Level Meters,
wahlweise kombiniert mit zugehörigen WAV-Audioaufnahmen.

## Unterstützte Geräte

Die Dateien können direkt und ohne Konvertierung verwendet werden:

- **Sound Level Meter:** .txt-Exportdateien (CSV-Format mit Metadaten-Header)
- **Recorder:** WAV-Dateien vom Denon DN-700R (96 kHz, 24-bit PCM, Stereo)

Mehrere WAV-Dateien einer Aufnahme-Session (z.B. automatisch aufgeteilte Dateien)
werden als ein zusammenhängender Stream verarbeitet.

## Was die App tut

- Liest CSV-Dateien vom Sound Level Meter (Schallpegel in dB(A), bis ca. 30.000 Messwerte)
- Berechnet den gleitenden Leq (äquivalenter Dauerschallpegel) über ein konfigurierbares Zeitfenster
- Liest optional WAV-Aufnahmen und berechnet den Leq in dBFS
- Richtet beide Signale zeitlich aufeinander aus
- Erzeugt einen Dual-Achsen-Plot (dB(A) links, dBFS rechts) und zeigt ihn an bzw. speichert ihn als PNG/SVG

## Verwendung

### GUI-Modus

Die App ohne Argumente starten, es öffnet sich ein Fenster:

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
3. Felder prüfen / anpassen
4. **Generiere Plot** klicken

**Parameter im GUI-Modus:**

- **Audio-Start:** Uhrzeit, zu der die WAV-Aufnahme gestartet wurde (Format `HH:MM:SS`). Die App nutzt diesen Wert, um das Audiosignal zeitlich mit der CSV-Messkurve zu synchronisieren. Nur relevant wenn WAV-Dateien geladen sind.

- **Integrationszeit:** Zeitfenster in Sekunden, über das die Pegel gemittelt werden. Kleinere Werte zeigen kurzfristige Schwankungen deutlicher, grössere Werte glätten die Kurve stärker. Standard: 20 s.

- **Export-Dateiname:** Basisname (ohne Endung) für gespeicherte Dateien. Wird automatisch aus dem Datum der CSV-Datei vorausgefüllt.

- **PNG / SVG:** Aktiviert den automatischen Export des Plots als Bilddatei. Ohne aktivierte Checkbox wird der Plot nur angezeigt, nicht gespeichert.

### CLI-Modus

```
python pegelanalyse.py [Optionen]
```

| Option | Beschreibung | Standard |
|---|---|---|
| `--csv FILE` | CSV-Datei vom Sound Level Meter | |
| `--wav FILE [FILE …]` | WAV-Datei(en) der Aufnahme | |
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

**Hinweise zu den Executables:**

- **Dateigrösse:** Die Binaries sind ca. 70–100 MB gross. Python-Interpreter, NumPy, Matplotlib, Pillow, tkinter und alle weiteren Abhängigkeiten sind in einer einzigen Datei gebündelt, kein separates Installationspaket nötig.

- **Startzeit:** Beim ersten Start entpackt die App ihr internes Bundle in ein temporäres Verzeichnis. Das dauert je nach Hardware und Plattform 5–15 Sekunden, danach öffnet sich das Fenster. Das ist normales Verhalten bei PyInstaller-Executables im `--onefile`-Modus.

- **Konsolenfenster im GUI-Modus (Windows):** Unter Windows öffnet sich beim Start ein Konsolenfenster, auch wenn man direkt in den GUI-Modus startet. Das ist ein bewusster Kompromiss: dasselbe Binary wird sowohl als GUI- als auch als CLI-Werkzeug genutzt, und CLI-Ausgaben (Fortschrittsanzeige beim WAV-Einlesen, Fehlermeldungen) müssen sichtbar sein. Ein reines GUI-Binary würde CLI-Output komplett unterdrücken.

- **Sicherheitswarnung unter macOS:** Beim ersten Start erscheint möglicherweise die Meldung *„Pegelanalyse kann nicht geöffnet werden, weil der Entwickler nicht verifiziert werden kann."* Die App ist nicht mit einem Apple-Entwicklerzertifikat signiert. Umgehung: Rechtsklick auf die Datei → **Öffnen** → im Dialog nochmals **Öffnen** klicken. Dieser Schritt ist nur einmalig nötig.

- **Sicherheitswarnung unter Windows:** Windows SmartScreen kann beim ersten Start eine Warnung anzeigen (*„Windows hat Ihren PC geschützt"*), da das Binary keinen bekannten Herausgeber hat. Klick auf **Weitere Informationen** → **Trotzdem ausführen** umgeht die Warnung.

### Aus dem Quellcode

Python 3.11 oder neuer wird benötigt.

```bash
pip install -r requirements.txt
python pegelanalyse.py --help
```

## Warum die Pegel niedriger aussehen als erwartet

Die App berechnet den **Leq** (äquivalenter Dauerschallpegel), den energetischen Mittelwert über das gesamte Integrationszeitfenster. Das ist der physikalisch korrekte RMS-Wert: alle Samples im Fenster gehen gleichgewichtet ein, also auch die leisen Momente zwischen Sprachbeiträgen, Musikpausen oder Umgebungsgeräuschen.

Die Pegelanzeige an einem Mischpult, einem Recorder oder einer DAW funktioniert anders: sie zeigt den **momentanen** Pegel mit sehr kurzer Integrationszeit (typisch 10–300 ms) und reagiert dadurch auf jeden Lautstärkespitzenwert sofort. Das sieht deutlich "heisser" aus.

Wenn dieselbe Aufnahme im Recorder bei −12 dBFS zu stehen scheint, der Leq-Plot hier aber −30 dBFS zeigt, ist beides richtig. Es sind nur unterschiedliche Fragen: *Wie laut war die lauteste Stelle?* gegenüber *Wie viel Energie war im Durchschnitt über 20 Sekunden vorhanden?*

Für Lärmschutz-Beurteilungen und akustische Messungen ist der Leq die massgebliche Grösse.

## Abhängigkeiten

| Paket | Zweck |
|---|---|
| [NumPy](https://numpy.org) | Effizientes Einlesen von 24-bit WAV-Daten |
| [Matplotlib](https://matplotlib.org) | Plot-Erzeugung |
| [tkinterdnd2](https://github.com/pmgagne/tkinterdnd2) | Drag & Drop in der GUI |

---

Dieses Projekt wurde mit Unterstützung von [Claude Code](https://claude.ai/code) (Anthropic Claude Sonnet 4.6) entwickelt.
