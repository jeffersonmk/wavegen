#!/usr/bin/env bash
# Remove o Wavegen do computador.
#
#   ./scripts/uninstall.sh          pergunta antes de cada etapa
#   ./scripts/uninstall.sh --all    remove tudo, inclusive a pasta do projeto (pede 1 confirmação)
#   ./scripts/uninstall.sh --yes    igual a --all, sem perguntar
#
# O que é removido:
#   1. atalho do menu e ícone        ~/.local/share/applications/wavegen.desktop
#                                    ~/.local/share/icons/hicolor/scalable/apps/wavegen.svg
#   2. cache (trilhas e análises)    ~/.cache/wavegen
#   3. preferências                  ~/.config/wavegen
#   4. a pasta do projeto            (código, .venv e projetos salvos dentro dela)
#
# Nunca são removidos: os vídeos que você exportou (ficam na pasta que você escolheu),
# projetos .wavegen.json salvos fora da pasta do projeto e os pacotes do sistema
# (ffmpeg, uv, portaudio).

set -euo pipefail

main() {
    local proj data cache config mode="ask"
    proj="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
    data="${XDG_DATA_HOME:-$HOME/.local/share}"
    cache="${XDG_CACHE_HOME:-$HOME/.cache}/wavegen"
    config="${XDG_CONFIG_HOME:-$HOME/.config}/wavegen"

    case "${1:-}" in
        --all) mode="all" ;;
        --yes|-y) mode="yes" ;;
        "") ;;
        -h|--help) sed -n '2,18p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; return 0 ;;
        *) echo "Opção desconhecida: $1 (use --help)" >&2; return 2 ;;
    esac

    # Segurança: só apaga a pasta se ela for mesmo o projeto do Wavegen.
    if [[ ! -f "$proj/pyproject.toml" ]] || ! grep -q '^name = "wavegen"' "$proj/pyproject.toml"; then
        echo "Erro: $proj não parece ser a pasta do Wavegen. Nada foi removido." >&2
        return 1
    fi
    if [[ "$proj" == "/" || "$proj" == "$HOME" ]]; then
        echo "Erro: pasta do projeto inválida ($proj). Nada foi removido." >&2
        return 1
    fi

    if pgrep -f "$proj/.venv/bin/" >/dev/null 2>&1 || pgrep -f "wavegen.job" >/dev/null 2>&1; then
        echo "O Wavegen parece estar aberto (ou renderizando). Feche-o antes de desinstalar." >&2
        return 1
    fi

    size() { [[ -e "$1" ]] && du -sh "$1" 2>/dev/null | cut -f1 || echo "—"; }

    echo "Wavegen — desinstalação"
    echo
    echo "  1. Atalho e ícone do menu"
    echo "  2. Cache            $cache ($(size "$cache"))"
    echo "  3. Preferências     $config ($(size "$config"))"
    echo "  4. Pasta do projeto $proj ($(size "$proj"))"
    local saved
    saved=$(find "$proj" -name '*.wavegen.json' -not -path '*/.venv/*' 2>/dev/null | wc -l)
    if (( saved > 0 )); then
        echo "     atenção: contém $saved projeto(s) .wavegen.json salvo(s)"
    fi
    echo
    echo "Os vídeos exportados e os pacotes do sistema não serão removidos."
    echo

    confirm() {   # confirm "pergunta" -> 0 = sim
        case "$mode" in
            yes) return 0 ;;
            all) return 0 ;;
        esac
        local r
        read -r -p "$1 [s/N] " r
        [[ "$r" =~ ^[sSyY]$ ]]
    }

    if [[ "$mode" == "all" ]]; then
        local r
        read -r -p "Remover TUDO acima, inclusive a pasta do projeto? [s/N] " r
        [[ "$r" =~ ^[sSyY]$ ]] || { echo "Cancelado. Nada foi removido."; return 0; }
    fi

    local removed=()

    if confirm "1. Remover o atalho e o ícone do menu?"; then
        rm -f "$data/applications/wavegen.desktop" "$data/icons/hicolor/scalable/apps/wavegen.svg"
        command -v update-desktop-database >/dev/null && update-desktop-database "$data/applications" 2>/dev/null || true
        command -v gtk-update-icon-cache >/dev/null && gtk-update-icon-cache -f -t "$data/icons/hicolor" >/dev/null 2>&1 || true
        removed+=("atalho e ícone")
    fi

    if confirm "2. Apagar o cache ($(size "$cache"))?"; then
        rm -rf -- "$cache"
        removed+=("cache")
    fi

    if confirm "3. Apagar as preferências?"; then
        rm -rf -- "$config"
        removed+=("preferências")
    fi

    local del_proj=0
    if confirm "4. Apagar a pasta do projeto inteira ($proj)?"; then
        del_proj=1
    fi

    if (( del_proj )); then
        cd /                    # sair da pasta antes de apagá-la
        rm -rf -- "$proj"
        removed+=("pasta do projeto")
    fi

    echo
    if (( ${#removed[@]} == 0 )); then
        echo "Nada foi removido."
    else
        local joined
        joined=$(printf '%s, ' "${removed[@]}")
        echo "Removido: ${joined%, }."
        echo "Se o ícone ainda aparecer no menu, saia e entre de novo na sessão."
    fi
}

main "$@"
exit $?
