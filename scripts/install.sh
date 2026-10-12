#!/usr/bin/env bash
# Instala o Wavegen para o usuário atual.
#
#   ./scripts/install.sh            instala (ou atualiza) em ~/.config/wavegen/app  [padrão]
#   ./scripts/install.sh --here     usa esta própria pasta (modo desenvolvimento, não copia nada)
#   ./scripts/install.sh --dest DIR instala em outra pasta
#   ./scripts/install.sh --remove   só remove o atalho do menu
#
# O que ele faz:
#   1. copia o app (código, ícone, scripts) para ~/.config/wavegen/app
#   2. cria o ambiente Python lá dentro (uv sync → ~/.config/wavegen/app/.venv)
#   3. cria o atalho "Wavegen" no menu de aplicativos, com o ícone
#
# Rodar de novo atualiza a instalação (os arquivos antigos do app são substituídos).
# Preferências (~/.config/wavegen/settings.json) e cache (~/.cache/wavegen) são mantidos.

set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG="${XDG_CONFIG_HOME:-$HOME/.config}/wavegen"
DEST="$CONFIG/app"
DATA="${XDG_DATA_HOME:-$HOME/.local/share}"
APPS="$DATA/applications"
ICONS="$DATA/icons/hicolor/scalable/apps"

case "${1:-}" in
    --here) DEST="$SRC" ;;
    --dest) DEST="$(realpath -m "${2:?informe a pasta depois de --dest}")" ;;
    --remove)
        rm -f "$APPS/wavegen.desktop" "$ICONS/wavegen.svg"
        command -v update-desktop-database >/dev/null && update-desktop-database "$APPS" 2>/dev/null || true
        echo "Atalho removido. (Para remover tudo: ./scripts/uninstall.sh)"
        exit 0 ;;
    -h|--help) sed -n '2,16p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    "") ;;
    *) echo "Opção desconhecida: $1 (use --help)" >&2; exit 2 ;;
esac

# ---------------------------------------------------------------- verificações
if [[ ! -f "$SRC/pyproject.toml" ]] || ! grep -q '^name = "wavegen"' "$SRC/pyproject.toml"; then
    echo "Erro: rode este script de dentro da pasta do Wavegen." >&2
    exit 1
fi
missing=()
for cmd in uv ffmpeg ffprobe; do
    command -v "$cmd" >/dev/null || missing+=("$cmd")
done
if (( ${#missing[@]} )); then
    echo "Erro: falta instalar: ${missing[*]}" >&2
    echo "      sudo pacman -S --needed ffmpeg uv portaudio" >&2
    exit 1
fi
if pgrep -f "$DEST/.venv/bin/" >/dev/null 2>&1; then
    echo "O Wavegen está aberto. Feche-o e rode o script de novo." >&2
    exit 1
fi

version=$(sed -n 's/^version = "\(.*\)"/\1/p' "$SRC/pyproject.toml")
echo "Wavegen $version"

# ---------------------------------------------------------------- 1. copiar
if [[ "$DEST" != "$SRC" ]]; then
    case "$DEST" in "$HOME"|"/"|"$CONFIG") echo "Pasta de destino inválida: $DEST" >&2; exit 1 ;; esac
    if [[ -d "$DEST" && ! -f "$DEST/pyproject.toml" && -n "$(ls -A "$DEST")" ]]; then
        echo "Erro: $DEST já existe e não é uma instalação do Wavegen. Nada foi alterado." >&2
        exit 1
    fi
    echo "1/3 Copiando o app para $DEST…"
    mkdir -p "$DEST"
    # substitui só os arquivos do app; mantém .venv para a atualização ser rápida
    rm -rf "$DEST/src" "$DEST/data" "$DEST/scripts"
    cp -r "$SRC/src" "$SRC/data" "$SRC/scripts" "$DEST/"
    cp "$SRC/pyproject.toml" "$SRC/uv.lock" "$SRC/README.md" "$DEST/"
    [[ -f "$SRC/LICENSE" ]] && cp "$SRC/LICENSE" "$DEST/"
    find "$DEST/src" -name __pycache__ -type d -prune -exec rm -rf {} +
    echo "$SRC" > "$DEST/.instalado-de"
else
    echo "1/3 Usando a própria pasta do projeto ($DEST)"
fi

# ---------------------------------------------------------------- 2. ambiente Python
echo "2/3 Preparando o ambiente Python (uv sync)…"
(cd "$DEST" && uv sync -q --no-dev)

# ---------------------------------------------------------------- 3. atalho
echo "3/3 Criando o atalho no menu…"
mkdir -p "$APPS" "$ICONS"
cp "$DEST/data/wavegen.svg" "$ICONS/wavegen.svg"
cat > "$APPS/wavegen.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Wavegen
GenericName=Vídeos musicais com waveform
Comment=Imagem ou vídeo + músicas → vídeo com visualizador de áudio para o YouTube
Exec="$DEST/.venv/bin/wavegen" %F
Icon=$ICONS/wavegen.svg
Terminal=false
Categories=AudioVideo;Video;
MimeType=application/json;
StartupWMClass=wavegen
EOF
command -v update-desktop-database >/dev/null && update-desktop-database "$APPS" 2>/dev/null || true
command -v gtk-update-icon-cache >/dev/null && gtk-update-icon-cache -f -t "$DATA/icons/hicolor" >/dev/null 2>&1 || true

echo
echo "Pronto! Abra \"Wavegen\" no menu de aplicativos."
echo "  App instalado em: $DEST"
echo "  Pelo terminal:    \"$DEST/.venv/bin/wavegen\""
if [[ "$DEST" != "$SRC" ]]; then
    echo "  Para atualizar:   rode ./scripts/install.sh de novo a partir da versão nova."
fi
