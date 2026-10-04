#!/usr/bin/env python3
"""Vigia de stock: iPhone 15 Pro Max 256GB (prateado) na loja online da NOS.

Corre periodicamente (GitHub Actions) e envia uma notificação push quando o
modelo fica disponível. Só usa a biblioteca padrão do Python.

Configuração por variáveis de ambiente (todas opcionais excepto o canal de envio):
  NTFY_TOPIC          tópico do ntfy.sh para onde vão as notificações
  NTFY_SERVER         servidor ntfy (por omissão https://ntfy.sh)
  TELEGRAM_BOT_TOKEN  / TELEGRAM_CHAT_ID   envio alternativo por Telegram
  CORES               cores aceites, separadas por vírgula
                      (por omissão "branco,natural,prateado,prata,silver")
  INCLUIR_CAIXA_ABERTA  "1" para aceitar também unidades "caixa aberta"
  URLS_EXTRA          URLs de produto adicionais a verificar (separados por vírgula)
  STATE_FILE          ficheiro de estado (por omissão state.json)
  TESTE               "1" envia uma notificação de teste e sai
"""
from __future__ import annotations

import gzip
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

BASE = os.getenv("NOS_BASE", "https://lojaonline.nos.pt")

# Páginas onde procurar links para o produto. A lista de /iphone é carregada por
# JavaScript, por isso é aberta num navegador real (Playwright) quando disponível.
PAGINAS_DESCOBERTA = [
    f"{BASE}/iphone",
    f"{BASE}/iphone-prestacoes",
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/128.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
    "Accept-Language": "pt-PT,pt;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip",
}

SEM_STOCK = [
    "esgotado", "indisponível", "indisponivel", "sem stock", "fora de stock",
    "sem disponibilidade", "avise-me", "avisa-me", "notificar-me",
    "produto não disponível", "brevemente",
]
COM_STOCK = [
    "adicionar ao carrinho", "adicionar ao cesto", "comprar agora",
    "comprar já", "em stock", "disponível para entrega", "entrega em",
]


def log(msg: str) -> None:
    print(f"[{datetime.now(timezone.utc):%Y-%m-%d %H:%M:%S}Z] {msg}", flush=True)


_navegador = None


def _abrir_navegador():
    """Abre o Chromium (Playwright) uma vez; devolve None se não estiver instalado."""
    global _navegador
    if _navegador is None and os.getenv("SEM_NAVEGADOR") != "1":
        try:
            from playwright.sync_api import sync_playwright
            pw = sync_playwright().start()
            browser = pw.chromium.launch()
            contexto = browser.new_context(user_agent=HEADERS["User-Agent"], locale="pt-PT")
            _navegador = (pw, browser, contexto)
        except Exception as e:
            log(f"Navegador indisponível, a usar HTTP simples: {e}")
            _navegador = False
    return _navegador or None


def fechar_navegador() -> None:
    if _navegador:
        pw, browser, _ = _navegador
        browser.close()
        pw.stop()


def obter_renderizado(url: str) -> str | None:
    """Abre a página num navegador real, para apanhar conteúdo carregado por JavaScript."""
    nav = _abrir_navegador()
    if not nav:
        return None
    pagina = nav[2].new_page()
    try:
        resp = pagina.goto(url, wait_until="domcontentloaded", timeout=45000)
        if resp and resp.status == 404:
            log(f"HTTP 404 em {url}")
            return ""
        try:
            pagina.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            pass
        # Desce a página para forçar o carregamento de listas "lazy".
        for _ in range(6):
            pagina.mouse.wheel(0, 4000)
            pagina.wait_for_timeout(700)
        hrefs = pagina.eval_on_selector_all("a[href]", "els => els.map(e => e.href)")
        extra = "".join(f'<a href="{h}"></a>' for h in hrefs)
        return pagina.content() + extra
    except Exception as e:
        log(f"Erro no navegador em {url}: {e}")
        return None
    finally:
        pagina.close()


def obter(url: str, tentativas: int = 3) -> str | None:
    renderizado = obter_renderizado(url)
    if renderizado == "":
        return None
    if renderizado:
        return renderizado
    for i in range(tentativas):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=30) as r:
                dados = r.read()
                if r.headers.get("Content-Encoding") == "gzip":
                    dados = gzip.decompress(dados)
                charset = r.headers.get_content_charset() or "utf-8"
                return dados.decode(charset, errors="replace")
        except urllib.error.HTTPError as e:
            log(f"HTTP {e.code} em {url}")
            if e.code == 404:
                return None
        except Exception as e:  # rede, timeout, etc.
            log(f"Erro em {url}: {e}")
        time.sleep(2 ** (i + 1))
    return None


def normalizar(texto: str) -> str:
    return urllib.parse.unquote(html.unescape(texto)).lower()


def links_produto(pagina: str) -> set[str]:
    encontrados = set()
    for href in re.findall(r"""["'](?:https?://[^/"'\s]+)?(/produto/[^"'\s<>\\]+)""", pagina):
        encontrados.add(BASE + html.unescape(href).split("#")[0])
    # Também apanha URLs escapados dentro de JSON (\/produto\/...)
    for href in re.findall(r"(\\/produto\\/[^\"'\s<>]+?)(?=\\?[\"'\s<>]|$)", pagina):
        encontrados.add(BASE + href.replace("\\/", "/"))
    return encontrados


def corresponde(url: str, cores: list[str], caixa_aberta: bool) -> bool:
    slug = normalizar(url)
    if "iphone-15-pro-max" not in slug or "256gb" not in slug:
        return False
    if "caixa-aberta" in slug and not caixa_aberta:
        return False
    return any(c in slug for c in cores)


def disponibilidade(pagina: str) -> tuple[str, str]:
    """Devolve ("disponivel" | "esgotado" | "desconhecido", motivo)."""
    # 1) Dados estruturados schema.org (JSON-LD / microdata)
    m = re.findall(r"schema\.org/(InStock|OutOfStock|SoldOut|PreOrder|BackOrder|LimitedAvailability|Discontinued)", pagina)
    if m:
        if any(x in ("InStock", "LimitedAvailability", "PreOrder") for x in m):
            return "disponivel", f"schema.org: {sorted(set(m))}"
        return "esgotado", f"schema.org: {sorted(set(m))}"
    for chave in ("isAvailable", "available", "inStock", "hasStock"):
        m2 = re.search(rf'"{chave}"\s*:\s*(true|false)', pagina)
        if m2:
            return ("disponivel" if m2.group(1) == "true" else "esgotado"), f"{chave}={m2.group(1)}"

    # 2) Texto visível
    texto = re.sub(r"<script.*?</script>|<style.*?</style>", " ", pagina, flags=re.S | re.I)
    texto = normalizar(re.sub(r"<[^>]+>", " ", texto))
    sem = [k for k in SEM_STOCK if k in texto]
    com = [k for k in COM_STOCK if k in texto]
    if sem and not com:
        return "esgotado", f"texto: {sem}"
    if com and not sem:
        return "disponivel", f"texto: {com}"
    if com and sem:
        # botão de compra presente e também algum aviso; tratar com cautela
        return "desconhecido", f"sinais mistos: com={com} sem={sem}"
    return "desconhecido", "sem sinais reconhecidos"


def titulo(pagina: str, url: str) -> str:
    m = re.search(r"<title>(.*?)</title>", pagina, re.S | re.I)
    if m:
        return html.unescape(m.group(1)).strip().split("|")[0].strip()
    return urllib.parse.unquote(url.rsplit("/", 1)[-1])


def preco(pagina: str) -> str | None:
    m = re.search(r'"price"\s*:\s*"?([\d.,]+)', pagina)
    return f"{m.group(1)} €" if m else None


def notificar(titulo_msg: str, corpo: str, url: str | None = None) -> bool:
    enviado = False
    topico = os.getenv("NTFY_TOPIC")
    if topico:
        servidor = os.getenv("NTFY_SERVER", "https://ntfy.sh").rstrip("/")
        corpo_json = {
            "topic": topico,
            "title": titulo_msg,
            "message": corpo,
            "priority": 5,
            "tags": ["iphone", "rotating_light"],
        }
        if url:
            corpo_json["click"] = url
            corpo_json["actions"] = [{"action": "view", "label": "Abrir loja", "url": url}]
        try:
            req = urllib.request.Request(
                servidor, data=json.dumps(corpo_json).encode(),
                headers={"Content-Type": "application/json"}, method="POST")
            urllib.request.urlopen(req, timeout=30).read()
            log("Notificação ntfy enviada")
            enviado = True
        except Exception as e:
            log(f"Falha ntfy: {e}")

    token, chat = os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    if token and chat:
        texto = f"{titulo_msg}\n\n{corpo}" + (f"\n{url}" if url else "")
        dados = urllib.parse.urlencode({"chat_id": chat, "text": texto}).encode()
        try:
            urllib.request.urlopen(f"https://api.telegram.org/bot{token}/sendMessage", data=dados, timeout=30).read()
            log("Notificação Telegram enviada")
            enviado = True
        except Exception as e:
            log(f"Falha Telegram: {e}")

    if not enviado:
        log("AVISO: nenhuma notificação enviada (configure NTFY_TOPIC ou TELEGRAM_*)")
    return enviado


def carregar_estado(caminho: str) -> dict:
    try:
        with open(caminho, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def guardar_estado(caminho: str, estado: dict) -> None:
    with open(caminho, "w", encoding="utf-8") as f:
        json.dump(estado, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")


def main() -> int:
    if os.getenv("TESTE") == "1":
        ok = notificar("🔔 Teste do vigia iPhone",
                       "Se recebeste isto, as notificações estão a funcionar.", f"{BASE}/iphone")
        return 0 if ok else 1

    cores = [c.strip().lower() for c in os.getenv("CORES", "branco,natural,prateado,prata,silver").split(",") if c.strip()]
    caixa_aberta = os.getenv("INCLUIR_CAIXA_ABERTA") == "1"
    caminho_estado = os.getenv("STATE_FILE", "state.json")
    estado = carregar_estado(caminho_estado)

    candidatos: set[str] = set(estado.get("urls_conhecidos", []))
    candidatos |= {u.strip() for u in os.getenv("URLS_EXTRA", "").split(",") if u.strip()}
    paginas_ok = 0
    for pag_url in PAGINAS_DESCOBERTA:
        pag = obter(pag_url)
        if pag:
            paginas_ok += 1
            todos = links_produto(pag)
            novos = {u for u in todos if corresponde(u, cores, caixa_aberta)}
            modelos = sorted({m for u in todos for m in re.findall(r"iphone-\d+[a-z-]*?(?=-5g|-\d+(?:gb|tb))", normalizar(u))})
            log(f"{pag_url}: {len(todos)} produtos na página, {len(novos)} correspondem. Modelos: {', '.join(modelos) or '-'}")
            candidatos |= novos

    # Remove duplicados que só diferem na query string
    por_caminho: dict[str, str] = {}
    for u in candidatos:
        por_caminho.setdefault(u.split("?")[0], u)
    candidatos = set(por_caminho.values())

    if paginas_ok == 0:
        # O site não respondeu (bloqueio, manutenção...). Falhar faz o GitHub avisar por email.
        log("ERRO: não foi possível aceder a nenhuma página da loja NOS.")
        return 1

    if not candidatos:
        log("Nenhuma página do iPhone 15 Pro Max 256GB nas cores pedidas foi encontrada (ainda não listado ou sem stock).")

    notificados: dict = estado.get("notificados", {})
    resultados = {}
    algum_disponivel = False
    for url in sorted(candidatos):
        pag = obter(url)
        if pag is None:
            continue
        estado_prod, motivo = disponibilidade(pag)
        nome, valor = titulo(pag, url), preco(pag)
        resultados[url] = {"nome": nome, "estado": estado_prod, "motivo": motivo, "preco": valor}
        log(f"{estado_prod.upper():12} {nome} ({valor or 's/ preço'}) — {motivo}")

        chave = url.split("?")[0]
        if estado_prod == "disponivel":
            algum_disponivel = True
            if chave not in notificados:
                corpo = f"{nome}\nPreço: {valor or 'ver no site'}\nCorre! Está disponível na loja NOS."
                if notificar("📱 iPhone 15 Pro Max 256GB DISPONÍVEL na NOS!", corpo, url):
                    notificados[chave] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        elif estado_prod == "esgotado" and chave in notificados:
            # Voltou a esgotar: rearmar para avisar na próxima reposição.
            log(f"{nome} voltou a esgotar; vigia rearmado.")
            notificados.pop(chave)

    estado["urls_conhecidos"] = sorted(set(estado.get("urls_conhecidos", [])) | {u.split("?")[0] for u in resultados})
    estado["notificados"] = notificados
    estado["ultimo_resultado"] = resultados
    guardar_estado(caminho_estado, estado)
    log("Disponível!" if algum_disponivel else "Ainda não disponível.")
    return 0


if __name__ == "__main__":
    try:
        codigo = main()
    finally:
        fechar_navegador()
    sys.exit(codigo)
