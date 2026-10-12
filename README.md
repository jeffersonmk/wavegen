# Wavegen — vídeos musicais com waveform animado

Aplicativo para Linux que junta **uma imagem ou um vídeo + uma ou mais músicas** e gera um
**vídeo completo com visualizador de áudio** (waveform/espectro) sincronizado, pronto para o
YouTube (MP4 · H.264 · AAC). Também exporta o visualizador **com fundo transparente**
(ProRes 4444 / PNG em .mov) para usar como sobreposição no DaVinci Resolve Free ou Studio.

## Instalação

Dependências do sistema (Arch/CachyOS):

```bash
sudo pacman -S --needed ffmpeg uv portaudio
```

- `ffmpeg`: obrigatório (lê os áudios, monta a trilha e codifica o vídeo).
- `uv`: cria o ambiente Python com as bibliotecas (PySide6-Essentials, numpy, sounddevice).
- `portaudio`: som da pré-visualização. Sem ele o app funciona, mas o preview fica mudo.
- Opcional, para usar a GPU AMD: `libva-mesa-driver` (VAAPI). Confira com `vainfo`.

### Passo a passo

1. Instale as dependências do sistema (comando acima).
2. Entre na pasta do projeto:
   ```bash
   cd ~/Projects/resolve-waveforms
   ```
3. Crie o atalho no menu (o script também roda `uv sync`, que instala as bibliotecas
   Python em `.venv`):
   ```bash
   ./scripts/install-desktop.sh
   ```
4. Abra **Wavegen** no menu de aplicativos. Se o ícone não aparecer logo, saia e entre de
   novo na sessão.

Para atualizar, veja a seção [Atualização](#atualização).

## Atualização

O app roda direto da pasta do projeto, então atualizar é trocar os arquivos dessa pasta e
rodar o script de instalação de novo. Seus projetos `.wavegen.json` e preferências continuam
valendo.

### Passo a passo

1. **Feche o Wavegen** e espere terminar qualquer renderização.
2. (Recomendado) Faça uma cópia de segurança dos projetos salvos dentro da pasta do app, se
   houver:
   ```bash
   cd ~/Projects/resolve-waveforms
   find . -name '*.wavegen.json' -not -path './.venv/*'
   ```
3. **Traga a versão nova** para a pasta, de um destes jeitos:
   - Alterações feitas direto na pasta (por exemplo, pelo assistente): os arquivos já estão
     no lugar. Siga para o passo 4.
   - Pelo GitHub, quando o projeto estiver lá:
     ```bash
     cd ~/Projects/resolve-waveforms
     git pull
     ```
   - Por um arquivo .zip: extraia por cima da pasta, mantendo `.venv` (se quiser, apague a
     pasta `src/` antes, para não sobrar arquivo antigo).
4. **Atualize as bibliotecas e o atalho** (instala as dependências novas e reaplica o ícone):
   ```bash
   cd ~/Projects/resolve-waveforms
   ./scripts/install-desktop.sh
   ```
5. (Opcional) Confira se está tudo certo:
   ```bash
   uv run python -m unittest discover -s tests
   ```
   O final deve mostrar `OK`.
6. Abra o **Wavegen** pelo menu. A versão aparece em **Ajuda → Sobre**.

### Dúvidas comuns

- **Preciso apagar o cache?** Não. As trilhas e análises em cache são refeitas sozinhas
  quando o formato muda. Se algo parecer estranho, use **Ajuda → Limpar cache de áudio**.
- **O ícone do menu não mudou:** saia e entre de novo na sessão.
- **Deu erro de biblioteca ao abrir:** apague o ambiente Python e recrie:
  ```bash
  cd ~/Projects/resolve-waveforms
  rm -rf .venv
  ./scripts/install-desktop.sh
  ```
- **Quero recomeçar do zero:** rode `./scripts/uninstall.sh` (veja a seção Desinstalação) e
  instale de novo.

## Desinstalação

Use o script, que pergunta antes de cada etapa:

```bash
cd ~/Projects/resolve-waveforms
./scripts/uninstall.sh
```

Ele oferece remover, um de cada vez:

1. o atalho e o ícone do menu;
2. o cache de trilhas e análises (`~/.cache/wavegen`);
3. as preferências (`~/.config/wavegen`);
4. a pasta do projeto inteira (código, `.venv` e projetos `.wavegen.json` salvos
   dentro dela; o script avisa quantos existem).

Para remover tudo de uma vez: `./scripts/uninstall.sh --all` (pede uma única confirmação).
O script se recusa a rodar com o app aberto ou renderizando, e só apaga a pasta se ela for
mesmo a do Wavegen.

Nunca são removidos: os vídeos exportados, projetos salvos fora da pasta do projeto e os
pacotes do sistema. Se quiser remover os pacotes também (e nada mais os usar):

```bash
sudo pacman -Rs uv portaudio      # o ffmpeg costuma ser usado por outros programas
```

Para tirar só o atalho do menu: `./scripts/install-desktop.sh --remove`.

## Executar

- Pelo menu de aplicativos: **Wavegen**.
- Pelo terminal: `uv run wavegen` (ou `uv run wavegen meu.wavegen.json` para abrir um projeto).
- Sem interface: `uv run wavegen render --project meu.wavegen.json --out video.mp4`
  (opcional `--sample INICIO DURACAO` para renderizar só um trecho).

## Como usar (fluxo da janela)

1. **Fundo (imagem ou vídeo)** (coluna esquerda): “Escolher imagem ou vídeo…” ou arraste o
   arquivo para a janela. Imagens: PNG, JPG/JPEG, WebP, BMP e TIFF. Vídeos: MP4, MOV, MKV,
   WebM, AVI, GIF e outros que o FFmpeg leia. Nada é deformado; na aba **Fundo** você escolhe
   entre *Preencher a tela* (corta as sobras; ajuste a posição do recorte e o zoom) e
   *Imagem inteira* (sem cortes; as faixas vazias recebem o fundo desfocado ou uma cor).
   Também dá para escurecer o fundo.
   **Vídeo de fundo:** toca sem som (o áudio é só o das músicas). Se for mais curto que a
   playlist, a opção **“Repetir o vídeo em loop até a última faixa terminar”** (aba Fundo,
   ligada por padrão) faz ele recomeçar até o fim; desligada, o último quadro fica parado.
   Se for mais longo, é cortado no fim da última música. O painel avisa qual caso se aplica.
2. **Músicas**: “＋ Adicionar” ou arrastar arquivos (MP3, WAV, FLAC, AAC, M4A, OGG, Opus…).
   A ordem da lista é a ordem do vídeo; reordene arrastando ou com ↑ ↓. A duração de cada faixa
   e o total aparecem embaixo. Na aba **Áudio**: tocar em sequência (padrão) ou embaralhado,
   volume geral, crossfade, intervalo entre faixas, fade-in/fade-out.
3. **Estilo**: escolha um dos 11 visualizadores na galeria.
4. **Personalizar**: os controles mudam conforme o estilo (cores, gradiente, opacidade, brilho,
   barras, espessura, raio…), além de posição/tamanho (números ou **arrastando o retângulo
   na pré-visualização**: arraste para mover, puxe cantos/bordas para redimensionar,
   Shift = ajuste fino) e comportamento (sensibilidade, resposta, suavidade).
5. **Pré-visualização**: ▶ (ou Espaço) toca a trilha real com o visualizador animado. O preview
   é desenhado em resolução menor, mas com o mesmo código e as mesmas proporções do vídeo final.
6. **Exportar**: resolução, fps, formato, qualidade, CPU/GPU, pasta e nome. O tamanho
   mostrado é uma **estimativa**.
7. **Testar 15 s** renderiza um trecho a partir da posição atual, para conferir o resultado
   final. **Renderizar vídeo** gera o arquivo completo. Dá para acompanhar o progresso,
   o tempo decorrido e o restante, e cancelar a qualquer momento.

**Lista de faixas para o YouTube:** o botão “Lista de faixas para o YouTube…” (embaixo da lista
de músicas, ou no menu Arquivo) mostra o tempo em que cada música começa no vídeo, na ordem
final (considerando intervalo, crossfade e embaralhamento). Dá para editar os títulos (o padrão
vem das tags *artista – título* da música ou do nome do arquivo), escolher se a troca durante um
crossfade é marcada no início ou no meio da transição, numerar, **Copiar** e **Salvar .txt**. Na
aba Exportar, a opção “Salvar também a lista de faixas” (ligada por padrão) grava
`<nome do vídeo> - lista de faixas.txt` ao lado do vídeo. O app avisa quando a lista não atende
às regras de capítulos do YouTube (começar em 0:00, 3 ou mais faixas, cada uma com 10 s ou
mais). No terminal: `uv run wavegen tracklist projeto.wavegen.json`.

Projetos são salvos em JSON (`Arquivo → Salvar projeto`, extensão `.wavegen.json`). Eles
guardam caminhos, ordem das músicas, estilo, parâmetros, posição e configurações. A mídia não
é copiada para dentro do projeto. Se algum arquivo foi movido, o app avisa e pede para
localizá-lo (e procura os outros arquivos na mesma pasta nova). As preferências de exportação,
as pastas recentes e o tamanho da janela ficam em `~/.config/wavegen/settings.json`.

## Estilos

| Estilo | Dados usados | Desenho |
|---|---|---|
| Barras verticais | espectro (bandas) | barras que crescem da base, do topo ou do centro |
| Espectro espelhado | espectro | barras para cima e para baixo do eixo central |
| Linha de onda | forma de onda (amostras) | osciloscópio contínuo, suavizado |
| Onda circular | forma de onda + volume | onda fechada em círculo que pulsa |
| Espectro circular | espectro + volume | barras radiais em volta de um anel |
| Linha minimalista | espectro | traço fino e discreto |
| Espectro de frequência | espectro | analisador em blocos (estilo LED) com picos |
| Onda simétrica | espectro | forma espelhada preenchida, em camadas |
| Waveform preenchido | envoltória da música | forma de onda rolando como num editor de áudio, trecho futuro mais apagado, linha de reprodução |
| Visualizador neon | espectro | linhas/barras com brilho (glow) |
| Vinil com capa | envoltória (batidas ou volume) | capa quadrada com **imagem personalizada**, disco de vinil girando (33⅓/45 rpm, rótulo em cor ou com a capa) e barras arredondadas que nascem no disco e correm para o lado |

No **Vinil com capa**, a capa é escolhida em Personalizar → Capa (vazio = usa a imagem de fundo; com fundo em vídeo, uma capa padrão). O grupo **Nome e contador** mostra o nome da faixa que está tocando (das tags ou do nome do arquivo, ou o título editado na Lista de faixas), o número da faixa (03 / 12) e um contador com barra de progresso: tempo/duração, tempo restante ou decorrido. Dá para escolher a posição (nome em cima e contador embaixo, tudo em cima ou tudo embaixo), tamanho, cores e sombra; o nome entra deslizando a cada troca de faixa. Nomes longos diminuem a fonte para caber. Para um efeito de “green screen” como nos vídeos de exemplo, deixe o fundo sem imagem com a cor verde (aba Fundo), ou exporte em formato transparente para o Resolve.

Para criar um estilo novo, faça uma subclasse de `Style` em `src/wavegen/styles/`, declare os
`params` (`ParamSpec`) e implemente `draw()`. Ele aparece na galeria e o painel de controles é
gerado automaticamente.

## Formatos de exportação

| Formato | Uso | Observação |
|---|---|---|
| MP4 · H.264 + AAC (padrão) | YouTube | `yuv420p`, BT.709, `+faststart`; *Alta qualidade* = CRF 18 / AAC 320k; *Arquivo menor* = CRF 23 / AAC 192k |
| MOV · ProRes 4444 com alfa | DaVinci Resolve (sobreposição) | sem imagem de fundo, áudio PCM 24 bits; arquivos grandes |
| MOV · PNG com alfa | Resolve/edição sem perdas | ainda maior |

Predefinições: 1920×1080, 1280×720, 3840×2160, 1080×1920 (vertical) e 1080×1080 (quadrado);
24, 25, 29,97, 30 (padrão), 59,94 e 60 fps.

**Áudio:** nada é aplicado automaticamente (nem normalização, nem compressão). As faixas são
montadas numa única trilha em 32 bits float a 48 kHz, codificada **uma vez** em AAC. Com uma
única música AAC/M4A, sem volume nem fades, o áudio original é **copiado sem recodificar**.

### GPU AMD (VAAPI)

A opção *GPU (VAAPI)* usa `h264_vaapi` para a **codificação** do vídeo. O desenho do waveform
continua na CPU (com vários processos em paralelo), então não é “renderização por GPU”
completa. O app testa a GPU de verdade antes de habilitar a opção. Se ela não estiver
disponível ou falhar durante a renderização, você recebe uma mensagem clara e a oferta de
renderizar de novo pela CPU. Os arquivos VAAPI saem maiores (~2×) para a mesma qualidade
visual.

Medido neste PC (RX 9060 XT), trilha de 7min26s em 1080p30: **CPU 2min34s (155 MB)**,
**GPU 1min47s (312 MB)**.

## Solução de problemas

- **Ajuda → Diagnóstico do sistema** mostra a versão do FFmpeg, os encoders, o teste VAAPI
  e o estado do áudio. O botão **Diagnóstico** (rodapé) abre o log técnico com as mensagens
  completas do FFmpeg.
- *“FFmpeg não encontrado”*: `sudo pacman -S ffmpeg`.
- *GPU indisponível*: instale `libva-mesa-driver`, confira com `vainfo` se aparece
  `VAEntrypointEncSlice` para H.264, e se o usuário tem acesso a `/dev/dri/renderD128`
  (grupo `render`). Enquanto isso, use CPU.
- *Preview mudo*: instale `portaudio`; veja o log.
- *Pouco espaço em disco*: o app verifica antes de começar e avisa.
- *Arquivo corrompido ou não suportado*: a música não entra na lista e o motivo é mostrado.
- O cache das trilhas montadas fica em `~/.cache/wavegen` (**Ajuda → Limpar cache de áudio**).

## Testes

```bash
uv run python -m unittest discover -s tests -v       # 49 testes (~15 s)
QT_QPA_PLATFORM=offscreen uv run python tests/gui_smoke.py PASTA_SAIDA IMAGEM AUDIO...   # interface
```

Os testes cobrem: duração da playlist (sequência, intervalo, crossfade), trilha amostra por
amostra igual ao original (sem silêncio extra, sem normalização), fades/volume, sincronia
(cliques caem no quadro certo a 30/60 fps), determinismo (1 processo = N processos, quadro a
quadro), os estilos serem diferentes entre si e reagirem ao áudio, preview proporcional ao
vídeo final, parâmetros do FFmpeg (caminhos com espaços, `-` e `:`), renderização real de MP4
(streams, duração, áudio = vídeo), cancelamento sem arquivo parcial e projeto salvo/carregado.

## Arquitetura

```
src/wavegen/
  ffmpeg.py      FFmpeg/ffprobe: localização, sondagem, teste VAAPI, erros traduzidos
  playlist.py    faixas, configurações de áudio, montagem da trilha única (cache)
  analysis.py    análise do áudio (256 bandas, nível, envoltória) + suavização determinística
  styles/        biblioteca de estilos (base.py, spectrum.py, waves.py)
  compositor.py  fundo + visualizador num quadro (o mesmo código no preview e na exportação)
  export.py      predefinições, comando de codificação, estimativa de tamanho
  parallel.py    desenho dos quadros em vários processos (memória compartilhada)
  job.py         renderização em processo separado, progresso em JSON, verificação final
  project.py     projeto (.wavegen.json) e preferências
  gui/           interface PySide6: janela, preview interativo, player, widgets
```
