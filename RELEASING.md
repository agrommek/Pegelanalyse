# Release erstellen

1. Sicherstellen, dass alle gewünschten Änderungen auf `main` gepusht sind.

2. Git-Tag setzen und pushen:
   ```bash
   git tag v1.2.3
   git push origin v1.2.3
   ```

3. GitHub Actions baut automatisch die Executables für Linux, Windows und macOS.
   Fortschritt unter: **Actions** → neuester Workflow-Run.

4. Nach erfolgreichem Build erscheint der Release automatisch unter **Releases**
   mit den drei Binaries als Anhänge:
   - `pegelanalyse-linux`
   - `pegelanalyse-windows.exe`
   - `pegelanalyse-macos`

## Tag wieder entfernen (falls nötig)

```bash
git tag -d v1.2.3              # lokal löschen
git push origin :refs/tags/v1.2.3  # auf GitHub löschen
```

Den zugehörigen GitHub Release dann manuell auf github.com unter **Releases** löschen.

## Versionsnummern

Schema: `vMAJOR.MINOR.PATCH` (Beispiele: `v1.0.0`, `v1.2.3`, `v2.0.0`)
