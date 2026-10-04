# Vigia iPhone 15 Pro Max — NOS Portugal

Verifica a cada ~10 minutos a [loja online da NOS](https://lojaonline.nos.pt/iphone) e envia
uma **notificação push para o telemóvel** assim que o **iPhone 15 Pro Max 256GB prateado**
fica disponível. Corre gratuitamente no GitHub Actions — não precisa de computador ligado.

> A Apple não fez um 15 Pro Max "prateado": os tons prateados são **Titânio Branco** e
> **Titânio Natural**. Por omissão vigiam-se os dois. Para vigiar só um, cria a variável
> `CORES` (ver abaixo) com `branco` ou `natural`.

## Configuração (5 minutos)

1. **Instala a app ntfy** no telemóvel ([iOS](https://apps.apple.com/app/ntfy/id1625396347) /
   [Android](https://play.google.com/store/apps/details?id=io.heckel.ntfy)) e subscreve um tópico
   com um nome difícil de adivinhar, p.ex. `iphone-gabriel-8f3k2q`.
2. No GitHub, vai a **Settings → Secrets and variables → Actions → New repository secret** e cria
   `NTFY_TOPIC` com esse nome.
3. Vai a **Actions → Vigia iPhone 15 Pro Max → Run workflow**, marca *"Enviar só uma notificação de
   teste"* e corre. Deves receber a notificação no telemóvel.
4. Pronto — a partir daí corre sozinho a cada 10 minutos.

### Opcional
| Tipo | Nome | Para quê |
|---|---|---|
| Secret | `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` | Receber também por Telegram |
| Variável | `CORES` | Cores aceites, ex.: `branco` (por omissão `branco,natural,prateado,prata,silver`) |
| Variável | `INCLUIR_CAIXA_ABERTA` | `1` para aceitar unidades "Caixa Aberta" |
| Variável | `URLS_EXTRA` | URLs de produto específicos a vigiar, separados por vírgula |

## Como funciona
- Procura links de produto `iphone-15-pro-max…256gb…<cor>` nas páginas de iPhone da NOS.
- Em cada página de produto lê a disponibilidade (dados schema.org ou texto como
  "Esgotado" / "Adicionar ao carrinho").
- Notifica **uma vez** quando fica disponível; se voltar a esgotar, rearma e avisa de novo na
  próxima reposição. O estado fica em `state.json` (atualizado pelo próprio workflow).
- Se o site da NOS não responder de todo, a execução falha e o GitHub envia-te um email.

## Notas
- O GitHub pode atrasar execuções agendadas alguns minutos em horas de pico.
- O GitHub desativa workflows agendados após 60 dias sem atividade no repositório; se isso
  acontecer, basta reativá-lo no separador Actions.
- Teste local: `NTFY_TOPIC=o-teu-topico python vigia.py`
