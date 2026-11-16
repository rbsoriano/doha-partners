# Food News Bot 🍽️

Junta notícias de comida (Brasil + mundo) do **Google News** e de **vários sites lidos direto do HTML**
(Abrasel, Mercado & Consumo, Giro News, Food Connection, Paladar, Folha Comida, Eater, Restaurant Business,
Food Dive, Just Food, FoodNavigator), escolhe as melhores e manda no seu grupo do Telegram
às 08h, 12h e 18h (Brasília). Roda de graça no GitHub Actions.

## Arquivos
| Arquivo | Para quê |
|---|---|
| `news_bot.py` | o bot (coleta, filtro, curadoria, envio) |
| `sources.py` | **lista de sites e assuntos — é o que você edita** |
| `requirements.txt` | dependências |
| `.github/workflows/food-news.yml` | agenda no GitHub Actions |
| `seen.json` | criado automaticamente: histórico para não repetir notícia |

## Colocar no ar
1. Crie um repositório **privado** no GitHub e envie todos os arquivos (mantenha a pasta `.github`).
2. *Settings → Secrets and variables → Actions → New repository secret*:
   - `TELEGRAM_BOT_TOKEN` — token do @BotFather
   - `TELEGRAM_CHAT_ID` — id do grupo (número negativo)
   - `ANTHROPIC_API_KEY` — *opcional, recomendado*: o Claude escolhe as melhores, traduz do inglês e resume
3. Aba **Actions → Food News Bot → Run workflow** para testar na hora.
4. Abra a execução e leia o log: cada fonte mostra quantos links achou. Fonte com `0 links` = ajustar em `sources.py`.

> ⚠️ Nunca coloque o token dentro dos arquivos. Se ele vazou, use `/revoke` no @BotFather.

## Como o bot lê um site sem RSS
1. Abre a página de listagem (`url` em `sources.py`) e separa os links que parecem matérias
   (mesmo domínio, endereço com "slug-com-varias-palavras", fora de menus/tags/categorias).
2. Abre cada matéria nova e extrai título, descrição e **data de publicação**
   (meta tags, JSON-LD, `<time>` ou a data na URL).
3. Descarta o que é mais velho que 36h, o que já foi enviado e títulos repetidos entre fontes.

## Se um site não funcionar
- **`0 links`**: o site carrega a lista por JavaScript ou bloqueou o acesso (alguns bloqueiam IPs de servidor).
  Tente outra página de listagem do mesmo site (uma seção específica) ou afrouxe/remova o `path_re`.
- **Muitas "matérias sem data"**: o site não expõe a data. Defina a variável `ALLOW_UNDATED=1` no workflow.
- Um site fora do ar nunca derruba o bot: ele só registra o aviso e segue com as outras fontes.

## Personalizar
- **Adicionar site:** acrescente um bloco em `WEB_SOURCES` no `sources.py`.
- **Assuntos do Google News:** edite `GOOGLE_NEWS` no `sources.py`.
- **Horários:** `cron` no workflow (UTC; Brasília = UTC-3).
- **Quantidade / idade máxima:** `MAX_ITEMS` e `MAX_AGE_HOURS`.
- **Grupo com tópicos:** variável `TELEGRAM_THREAD_ID`.

## Testar no computador
```bash
pip install -r requirements.txt
DRY_RUN=1 python news_bot.py     # mostra a mensagem, não envia nem grava histórico
```
