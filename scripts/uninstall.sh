#!/usr/bin/env bash
# Remove o Wavegen do computador.
#
#   ./scripts/uninstall.sh          pergunta antes de cada etapa
#   ./scripts/uninstall.sh --all    remove tudo (pede uma única confirmação)
#   ./scripts/uninstall.sh --yes    igual a --all, sem perguntar
#
# O que pode ser removido:
#   1. atalho do menu e ícone        ~/.local/share/applications/wavegen.desktop
#                                    ~/.local/share/icons/hicolor/scalable/apps/wavegen.svg
#   2. o app instalado               ~/.config/wavegen/app (código + ambiente Python)
#   3. cache (trilhas e análises)    ~/.cache/wavegen
#   4. preferências                  ~/.config/wavegen/settings.json
#   5. a pasta do código-fonte       de onde o app foi instalado (ex.: ~/Projects/wavegen)
#
# Nunca são removidos: os vídeos que você exportou, projetos .wavegen.json salvos fora das
# pastas acima e os pacotes do sistema (ffmpeg, uv, portaudio).

set -euo pipefail

# Tudo fica dentro de main(): o bash lê o script enquanto executa, e este script pode apagar
# a própria pasta. Com main(), o arquivo inteiro já foi lido antes de qualquer remoção.
main() {
    local here config app data cache mode="ask"
    here="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
    config="${XDG_CONFIG_HOME:-$HOME/.config}/wavegen"
    app="$config/app"
    data="${XDG_DATA_HOME:-$HOME/.local/share}"
    cache="${XDG_CACHE_HOME:-$HOME/.cache}/wavegen"

    case "${1:-}" in
        --all) mode="all" ;;
        --yes|-y) mode="yes" ;;
        "") ;;
        -h|--help) sed -n '2,18p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; return 0 ;;
        *) echo "Opção desconhecida: $1 (use --help)" >&2; return 2 ;;
    esac

    is_wavegen() { [[ -f "$1/pyproject.toml" ]] && grep -q '^name = "wavegen"' "$1/pyproject.toml"; }

    # código-fonte: esta pasta (se não for o app instalado) ou a registrada na instalação
    local src="" cand
    if [[ "$here" != "$app" ]] && is_wavegen "$here"; then
        src="$here"
    elif [[ -f "$app/.instalado-de" ]]; then
        cand="$(cat "$app/.instalado-de")"
        if is_wavegen "$cand"; then src="$cand"; fi
    fi
    local d
    for d in "$src" "$app"; do
        if [[ -n "$d" && ( "$d" == "/" || "$d" == "$HOME" || "$d" == "$config" ) ]]; then
            echo "Erro: pasta inválida ($d). Nada foi removido." >&2
            return 1
        fi
    done

    if pgrep -f "$app/.venv/bin/" >/dev/null 2>&1 || pgrep -f "wavegen.job" >/dev/null 2>&1 \
       || { [[ -n "$src" ]] && pgrep -f "$src/.venv/bin/" >/dev/null 2>&1; }; then
        echo "O Wavegen parece estar aberto (ou renderizando). Feche-o antes de desinstalar." >&2
        return 1
    fi

    size() { [[ -e "$1" ]] && du -sh "$1" 2>/dev/null | cut -f1 || echo "não existe"; }

    echo "Wavegen — desinstalação"
    echo
    echo "  1. Atalho e ícone do menu"
    echo "  2. App instalado     $app ($(size "$app"))"
    echo "  3. Cache             $cache ($(size "$cache"))"
    echo "  4. Preferências      $config/settings.json"
    if [[ -n "$src" ]]; then
        echo "  5. Código-fonte      $src ($(size "$src"))"
        local saved
        saved=$(find "$src" -name '*.wavegen.json' -not -path '*/.venv/*' 2>/dev/null | wc -l)
        if (( saved > 0 )); then
            echo "     atenção: contém $saved projeto(s) .wavegen.json salvo(s)"
        fi
    fi
    echo
    echo "Os vídeos exportados e os pacotes do sistema não serão removidos."
    echo

    if [[ "$mode" == "all" ]]; then
        local r
        read -r -p "Remover TUDO acima? [s/N] " r
        [[ "$r" =~ ^[sSyY]$ ]] || { echo "Cancelado. Nada foi removido."; return 0; }
    fi

    confirm() {   # confirm "pergunta" -> 0 = sim
        [[ "$mode" != "ask" ]] && return 0
        local r
        read -r -p "$1 [s/N] " r
        [[ "$r" =~ ^[sSyY]$ ]]
    }

    local removed=()

    if confirm "1. Remover o atalho e o ícone do menu?"; then
        rm -f "$data/applications/wavegen.desktop" "$data/icons/hicolor/scalable/apps/wavegen.svg"
        command -v update-desktop-database >/dev/null && update-desktop-database "$data/applications" 2>/dev/null || true
        command -v gtk-update-icon-cache >/dev/null && gtk-update-icon-cache -f -t "$data/icons/hicolor" >/dev/null 2>&1 || true
        removed+=("atalho e ícone")
    fi

    if [[ -e "$app" ]] && confirm "2. Remover o app instalado ($(size "$app"))?"; then
        if is_wavegen "$app"; then
            cd /
            rm -rf -- "$app"
            removed+=("app instalado")
        else
            echo "   $app não parece ser o Wavegen; mantido."
        fi
    fi

    if [[ -e "$cache" ]] && confirm "3. Apagar o cache ($(size "$cache"))?"; then
        rm -rf -- "$cache"
        removed+=("cache")
    fi

    if [[ -e "$config/settings.json" ]] && confirm "4. Apagar as preferências?"; then
        rm -f -- "$config/settings.json" "$config/settings.json.tmp"
        removed+=("preferências")
    fi
    rmdir "$config" 2>/dev/null || true          # só some se tiver ficado vazia

    if [[ -n "$src" && -e "$src" ]] && confirm "5. Apagar a pasta do código-fonte ($src)?"; then
        cd /
        rm -rf -- "$src"
        removed+=("código-fonte")
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
