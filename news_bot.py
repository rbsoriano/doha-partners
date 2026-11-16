#!/usr/bin/env python3
"""
Food News Bot
Junta notícias de comida (Brasil + mundo) do Google News e de vários sites
(lidos direto do HTML, sem depender de RSS), remove repetidas, usa o Claude
(opcional) para escolher as mais relevantes e envia um resumo no Telegram.

Variáveis de ambiente:
  TELEGRAM_BOT_TOKEN   (obrigatória) token do @BotFather
  TELEGRAM_CHAT_ID     (obrigatória) id do grupo
  TELEGRAM_THREAD_ID   (opcional)    id do tópico, se o grupo tiver tópicos
  ANTHROPIC_API_KEY    (opcional)    ativa curadoria + tradução com Claude
  CLAUDE_MODEL         (opcional)    padrão: claude-sonnet-5-5
  MAX_ITEMS            (opcional)    notícias por envio (padrão 8)
  MAX_AGE_HOURS        (opcional)    idade máxima da notícia (padrão 36)
  ALLOW_UNDATED=1      (opcional)    aceita matérias de site sem data detectável
  DRY_RUN=1            (opcional)    só imprime, não envia nem grava histórico
"""
import hashlib
import html
import json
import re
import sys
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

import feedparser
import requests
from bs4 import BeautifulSoup
from dateutil import parser as dtparser

from sources import GOOGLE_NEWS, WEB_SOURCES, google_news_url

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
THREAD_ID = os.environ.get("TELEGRAM_THREAD_ID", "")
ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-5-5")
MAX_ITEMS = int(os.environ.get("MAX_ITEMS", "8"))
MAX_AGE_HOURS = int(os.environ.get("MAX_AGE_HOURS", "36"))
ALLOW_UNDATED = os.environ.get("ALLOW_UNDATED") == "1"
DRY_RUN = os.environ.get("DRY_RUN") == "1"
STATE_FILE = Path(os.environ.get("STATE_FILE", "seen.json"))

MAX_LINKS_PER_SITE = 20
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
}
BRT = timezone(timedelta(hours=-3))
SKIP_PATH = re.compile(
    r"/(tag|tags|categoria|categorias|category|autor|author|page|busca|search|"
    r"newsletter|login|assine|contato|sobre|politica|termos|privacy|cookies)(/|$)"
    r"|\.(jpg|jpeg|png|gif|webp|svg|pdf|mp4)$",
    re.I,
)


# ----------------------------------------------------------------------------
# Utilidades
# ----------------------------------------------------------------------------
def title_key(title: str) -> str:
    t = re.sub(r"[^a-z0-9à-ú]+", " ", title.lower()).strip()
    return "t" + hashlib.sha1(t.encode()).hexdigest()[:15]


def url_key(url: str) -> str:
    p = urlparse(url)
    host = re.sub(r"^www\d*\.", "", p.netloc.lower())
    return "u" + hashlib.sha1(f"{host}{p.path.rstrip('/')}".encode()).hexdigest()[:15]


def host_of(url: str) -> str:
    return re.sub(r"^www\d*\.", "", urlparse(url).netloc.lower())


def parse_date(value: str, region: str):
    if not value:
        return None
    try:
        d = dtparser.parse(value.strip())
    except Exception:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=BRT if region == "BR" else timezone.utc)
    d = d.astimezone(timezone.utc)
    now = datetime.now(timezone.utc)
    return min(d, now)  # datas "no futuro" viram agora


def date_from_url(url: str, region: str):
    m = re.search(r"/(20\d{2})/(\d{1,2})/(\d{1,2})(?:/|$)", url)
    if m:
        y, mo, d = map(int, m.groups())
    else:
        m = re.search(r"/(\d{2})/(\d{2})/(20\d{2})/", url)
        if not m:
            return None
        d, mo, y = map(int, m.groups())
    try:
        return datetime(y, mo, d, 12, 0, tzinfo=BRT if region == "BR" else timezone.utc).astimezone(timezone.utc)
    except ValueError:
        return None


# ----------------------------------------------------------------------------
# Coleta 1: Google News
# ----------------------------------------------------------------------------
def fetch_google() -> list[dict]:
    items = []
    for region, query in GOOGLE_NEWS:
        url = google_news_url(query, region)
        try:
            r = requests.get(url, headers=HEADERS, timeout=20)
            r.raise_for_status()
            feed = feedparser.parse(r.content)
        except Exception as e:
            print(f"[aviso] Google News falhou ({query[:30]}...): {e}", file=sys.stderr)
            continue

        n = 0
        for e in feed.entries:
            title = html.unescape(e.get("title", "")).strip()
            link = e.get("link", "")
            if not title or not link:
                continue
            source = e["source"].get("title", "") if isinstance(e.get("source"), dict) else ""
            if source and title.endswith(f" - {source}"):
                title = title[: -len(source) - 3].strip()
            ts = e.get("published_parsed") or e.get("updated_parsed")
            published = datetime(*ts[:6], tzinfo=timezone.utc) if ts else datetime.now(timezone.utc)
            items.append(
                {"region": region, "title": title, "link": link, "source": source or "Google News",
                 "description": "", "published": published}
            )
            n += 1
        print(f"[Google News] '{query[:40]}...' -> {n} itens")
    return items


# ----------------------------------------------------------------------------
# Coleta 2: sites direto do HTML
# ----------------------------------------------------------------------------
def looks_like_article(path: str) -> bool:
    """Matéria costuma ter 'slug-com-varias-palavras' no fim da URL."""
    last = path.rstrip("/").split("/")[-1]
    last = re.sub(r"\.(html?|shtml|php)$", "", last)
    return last.count("-") >= 3


def scrape_listing(src: dict) -> list[dict]:
    try:
        r = requests.get(src["url"], headers=HEADERS, timeout=20)
        r.raise_for_status()
    except Exception as e:
        print(f"[aviso] {src['name']}: não abriu a listagem ({e})", file=sys.stderr)
        return []

    soup = BeautifulSoup(r.content[:1_500_000], "html.parser")
    base_host = host_of(src["url"])
    path_re = re.compile(src["path_re"]) if src.get("path_re") else None
    found: dict[str, str] = {}

    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.startswith(("#", "mailto:", "javascript:", "tel:")):
            continue
        link = urljoin(src["url"], href).split("#")[0]
        p = urlparse(link)
        if p.scheme not in ("http", "https") or host_of(link) != base_host:
            continue
        if SKIP_PATH.search(p.path) or not looks_like_article(p.path):
            continue
        if path_re and not path_re.search(p.path):
            continue
        if url_key(link) == url_key(src["url"]):
            continue
        text = " ".join(a.get_text(" ", strip=True).split())
        hyphens = p.path.rstrip("/").split("/")[-1].count("-")
        if len(text) < 25 and hyphens < 5:
            continue
        if link not in found or len(text) > len(found[link]):
            found[link] = text

    links = list(found.items())[:MAX_LINKS_PER_SITE]
    return [
        {"region": src["region"], "title": text, "link": link, "source": src["name"],
         "description": "", "published": None}
        for link, text in links
    ]


def meta_content(soup, *names):
    for n in names:
        tag = soup.find("meta", attrs={"property": n}) or soup.find("meta", attrs={"name": n}) \
            or soup.find("meta", attrs={"itemprop": n})
        if tag and tag.get("content"):
            return tag["content"].strip()
    return ""


def find_json_key(obj, key):
    if isinstance(obj, dict):
        if key in obj and isinstance(obj[key], str):
            return obj[key]
        for v in obj.values():
            r = find_json_key(v, key)
            if r:
                return r
    elif isinstance(obj, list):
        for v in obj:
            r = find_json_key(v, key)
            if r:
                return r
    return ""


def enrich(item: dict) -> dict:
    """Abre a matéria e pega título, descrição e data de publicação."""
    try:
        r = requests.get(item["link"], headers=HEADERS, timeout=12)
        r.raise_for_status()
        soup = BeautifulSoup(r.content[:800_000], "html.parser")
    except Exception:
        return item

    title = meta_content(soup, "og:title", "twitter:title") or (soup.title.string.strip() if soup.title and soup.title.string else "")
    if title:
        parts = re.split(r"\s[\|\u2013\u2014-]\s", title)
        if len(parts) > 1 and any(item["source"].lower().split()[0] in p.lower() for p in parts[1:]) and len(parts[0]) >= 20:
            title = parts[0].strip()
        item["title"] = html.unescape(title)

    item["description"] = html.unescape(meta_content(soup, "og:description", "description", "twitter:description"))[:300]

    raw = meta_content(soup, "article:published_time", "og:article:published_time", "datePublished",
                       "pubdate", "date", "DC.date.issued", "publish-date")
    if not raw:
        t = soup.find("time", attrs={"datetime": True})
        raw = t["datetime"] if t else ""
    if not raw:
        for s in soup.find_all("script", type="application/ld+json"):
            try:
                raw = find_json_key(json.loads(s.string or ""), "datePublished")
            except Exception:
                continue
            if raw:
                break

    item["published"] = parse_date(raw, item["region"]) or date_from_url(item["link"], item["region"])
    return item


def fetch_web(seen: dict) -> list[dict]:
    stubs = []
    for src in WEB_SOURCES:
        links = scrape_listing(src)
        new = [i for i in links if url_key(i["link"]) not in seen]
        print(f"[{src['name']}] {len(links)} links na página, {len(new)} ainda não enviados")
        stubs.extend(new)

    with ThreadPoolExecutor(max_workers=8) as pool:
        enriched = list(pool.map(enrich, stubs))

    out, undated = [], 0
    for it in enriched:
        if it["published"] is None:
            undated += 1
            if not ALLOW_UNDATED:
                continue
            it["published"] = datetime.now(timezone.utc) - timedelta(hours=MAX_AGE_HOURS - 1)
        out.append(it)
    if undated:
        print(f"[aviso] {undated} matérias sem data detectável "
              f"({'incluídas' if ALLOW_UNDATED else 'ignoradas; use ALLOW_UNDATED=1 para aceitar'})")
    return out


# ----------------------------------------------------------------------------
# Junta tudo, filtra, remove repetidas
# ----------------------------------------------------------------------------
def collect(seen: dict) -> list[dict]:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=MAX_AGE_HOURS)
    result: dict[str, dict] = {}
    for it in fetch_google() + fetch_web(seen):
        if it["published"] < cutoff:
            continue
        uk, tk = url_key(it["link"]), title_key(it["title"])
        if uk in seen or tk in seen or tk in result:
            continue
        it["url_key"], it["title_key"] = uk, tk
        result[tk] = it
    return sorted(result.values(), key=lambda x: x["published"], reverse=True)


# ----------------------------------------------------------------------------
# Histórico
# ----------------------------------------------------------------------------
def load_seen() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text()).get("seen", {})
        except Exception:
            pass
    return {}


def save_seen(seen: dict) -> None:
    limit = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    STATE_FILE.write_text(json.dumps({"seen": {k: v for k, v in seen.items() if v >= limit}}, indent=1))


# ----------------------------------------------------------------------------
# Curadoria
# ----------------------------------------------------------------------------
def balanced_pick(items: list[dict], n: int) -> list[dict]:
    """Sem IA: ~60% Brasil / ~40% mundo, no máx. 2 por fonte, mais recentes primeiro."""
    per_source: dict[str, int] = {}
    br, world = [], []
    for it in items:
        if per_source.get(it["source"], 0) >= 2:
            continue
        per_source[it["source"]] = per_source.get(it["source"], 0) + 1
        (br if it["region"] == "BR" else world).append(it)
    n_br = min(len(br), round(n * 0.6))
    n_world = min(len(world), n - n_br)
    n_br = min(len(br), n - n_world)
    return br[:n_br] + world[:n_world]


def curate_with_claude(items: list[dict], n: int) -> list[dict]:
    pool = items[:50]
    now = datetime.now(timezone.utc)
    listing = [
        {"id": i, "regiao": it["region"], "titulo": it["title"], "fonte": it["source"],
         "descricao": it["description"][:200], "idade_h": round((now - it["published"]).total_seconds() / 3600)}
        for i, it in enumerate(pool)
    ]
    system = (
        "Você é editor de um boletim de notícias sobre o universo da comida (gastronomia, "
        "indústria de alimentos, food service, tendências, negócios, restaurantes, inovação). "
        "Responda SOMENTE com JSON válido."
    )
    user = (
        f"Escolha as {n} notícias mais relevantes e interessantes para quem acompanha o nicho food. "
        "Mantenha mistura de ~60% Brasil (regiao=BR) e ~40% mundo (regiao=WORLD), quando houver material. "
        "No máximo 2 notícias da mesma fonte. Evite duplicatas do mesmo assunto, receitas genéricas, "
        "clickbait, conteúdo patrocinado e o que não for sobre comida/alimentação/restaurantes.\n"
        'Para cada escolhida devolva: "id", "titulo" (português do Brasil, natural e curto) e "resumo" '
        "(uma frase de até 120 caracteres sobre por que importa; baseie-se SOMENTE no título e na "
        'descrição fornecidos, não invente fatos; use "" se não houver o que acrescentar).\n'
        'Formato: {"items":[{"id":0,"titulo":"...","resumo":"..."}]}\n\n'
        f"Notícias:\n{json.dumps(listing, ensure_ascii=False)}"
    )
    r = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={"x-api-key": ANTHROPIC_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"},
        json={"model": MODEL, "max_tokens": 2500, "system": system,
              "messages": [{"role": "user", "content": user}]},
        timeout=90,
    )
    r.raise_for_status()
    text = "".join(b.get("text", "") for b in r.json()["content"] if b["type"] == "text")
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    data = json.loads(text)

    chosen = []
    for row in data["items"]:
        idx = row.get("id")
        if not isinstance(idx, int) or not 0 <= idx < len(pool):
            continue
        it = dict(pool[idx])
        it["title"] = row.get("titulo") or it["title"]
        it["summary"] = (row.get("resumo") or "").strip()
        chosen.append(it)
    if not chosen:
        raise ValueError("Claude não devolveu itens válidos")
    return chosen[:n]


# ----------------------------------------------------------------------------
# Mensagem + Telegram
# ----------------------------------------------------------------------------
def format_digest(items: list[dict]) -> list[str]:
    now = datetime.now(BRT)
    lines = [f"🍽️ <b>Food News</b> — {now:%d/%m às %Hh}", ""]
    for region, header in (("BR", "🇧🇷 <b>Brasil</b>"), ("WORLD", "🌍 <b>Mundo</b>")):
        group = [i for i in items if i["region"] == region]
        if not group:
            continue
        lines.append(header)
        for n, it in enumerate(group, 1):
            title = html.escape(it["title"])
            link = html.escape(it["link"], quote=True)
            src = f" <i>({html.escape(it['source'])})</i>" if it["source"] else ""
            lines.append(f'{n}. <a href="{link}">{title}</a>{src}')
            if it.get("summary"):
                lines.append(f"   {html.escape(it['summary'])}")
        lines.append("")

    chunks, cur = [], ""
    for line in lines:
        if len(cur) + len(line) + 1 > 3900:
            chunks.append(cur.rstrip())
            cur = ""
        cur += line + "\n"
    if cur.strip():
        chunks.append(cur.rstrip())
    return chunks


def send_telegram(chunks: list[str]) -> None:
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    for text in chunks:
        payload = {"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
        if THREAD_ID:
            payload["message_thread_id"] = int(THREAD_ID)
        r = requests.post(url, json=payload, timeout=30)
        if not r.ok:
            raise RuntimeError(f"Telegram erro {r.status_code}: {r.text}")


# ----------------------------------------------------------------------------
def main() -> int:
    if not DRY_RUN and not (BOT_TOKEN and CHAT_ID):
        print("Defina TELEGRAM_BOT_TOKEN e TELEGRAM_CHAT_ID.", file=sys.stderr)
        return 1

    seen = load_seen()
    items = collect(seen)
    print(f"\n{len(items)} notícias novas dentro do prazo")
    if not items:
        return 0

    if ANTHROPIC_KEY:
        try:
            picked = curate_with_claude(items, MAX_ITEMS)
        except Exception as e:
            print(f"[aviso] curadoria com Claude falhou ({e}); usando seleção simples", file=sys.stderr)
            picked = balanced_pick(items, MAX_ITEMS)
    else:
        picked = balanced_pick(items, MAX_ITEMS)

    chunks = format_digest(picked)
    if DRY_RUN:
        print("\n\n".join(chunks))
        return 0

    send_telegram(chunks)
    stamp = datetime.now(timezone.utc).isoformat()
    for it in picked:
        seen[it["url_key"]] = stamp
        seen[it["title_key"]] = stamp
    save_seen(seen)
    print(f"{len(picked)} notícias enviadas")
    return 0


if __name__ == "__main__":
    sys.exit(main())
