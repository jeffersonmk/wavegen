#!/usr/bin/env bash
# Instala (ou remove) o atalho do Wavegen no menu de aplicativos do usuário.
#   ./scripts/install-desktop.sh            instala
#   ./scripts/install-desktop.sh --remove   remove
set -euo pipefail

PROJ="$(cd "$(dirname "$0")/.." && pwd)"
APPS="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
ICONS="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor/scalable/apps"

if [[ "${1:-}" == "--remove" ]]; then
    rm -f "$APPS/wavegen.desktop" "$ICONS/wavegen.svg"
    command -v update-desktop-database >/dev/null && update-desktop-database "$APPS" || true
    echo "Atalho removido."
    exit 0
fi

UV="$(command -v uv || true)"
if [[ -z "$UV" ]]; then
    echo "Erro: 'uv' não encontrado. Instale com: sudo pacman -S uv" >&2
    exit 1
fi

echo "Preparando o ambiente Python (uv sync)…"
(cd "$PROJ" && "$UV" sync -q)

mkdir -p "$APPS" "$ICONS"
cp "$PROJ/data/wavegen.svg" "$ICONS/wavegen.svg"
cat > "$APPS/wavegen.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Wavegen
GenericName=Vídeos musicais com waveform
Comment=Imagem + músicas → vídeo com visualizador de áudio para o YouTube
Exec="$PROJ/.venv/bin/wavegen" %F
Icon=$ICONS/wavegen.svg
Terminal=false
Categories=AudioVideo;Video;
MimeType=application/json;
StartupWMClass=wavegen
EOF
command -v update-desktop-database >/dev/null && update-desktop-database "$APPS" || true
echo "Pronto: procure por \"Wavegen\" no menu de aplicativos."
