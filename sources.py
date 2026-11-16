"""
FONTES DE NOTÍCIAS — é aqui que você adiciona/remove sites e assuntos.
Não precisa mexer no news_bot.py.
"""
from urllib.parse import quote_plus

# ---------------------------------------------------------------------------
# 1) Google News (busca por assunto). "when:1d" = últimas 24h.
#    O Google entrega o resultado da busca num formato próprio; você só escreve
#    os assuntos. Operadores OR e "aspas" funcionam.
# ---------------------------------------------------------------------------
GOOGLE_NEWS = [
    # Brasil
    ("BR", "gastronomia when:1d"),
    ("BR", '"food service" OR foodservice OR "bares e restaurantes" OR Abrasel when:1d'),
    ("BR", '"indústria de alimentos" OR "alimentos e bebidas" OR "alimentação fora do lar" when:1d'),
    ("BR", "chef OR restaurante OR delivery comida OR iFood when:1d"),
    # Mundo
    ("WORLD", '"food industry" OR "food trends" OR "food innovation" when:1d'),
    ("WORLD", "restaurants OR chef OR Michelin when:1d"),
    ("WORLD", '"alternative protein" OR "plant-based" OR "food tech" when:1d'),
]


def google_news_url(query: str, region: str) -> str:
    loc = "hl=pt-BR&gl=BR&ceid=BR:pt-419" if region == "BR" else "hl=en-US&gl=US&ceid=US:en"
    return f"https://news.google.com/rss/search?q={quote_plus(query)}&{loc}"


# ---------------------------------------------------------------------------
# 2) Sites lidos direto da página (sem RSS).
#    O bot abre a página de listagem, acha os links que parecem matérias e
#    abre cada matéria para pegar título, descrição e data de publicação.
#
#    name     nome que aparece na mensagem
#    region   "BR" ou "WORLD"
#    url      página que lista as notícias (home ou seção de notícias)
#    path_re  (opcional) regex que o link da matéria precisa casar, para
#             filtrar menus/anúncios. Se o site vier vazio, tente afrouxar.
# ---------------------------------------------------------------------------
WEB_SOURCES = [
    # Brasil
    {"name": "Abrasel", "region": "BR", "url": "https://abrasel.com.br/noticias/", "path_re": r"/noticias/"},
    {"name": "Mercado & Consumo", "region": "BR", "url": "https://mercadoeconsumo.com.br/", "path_re": r"/\d{2}/\d{2}/\d{4}/"},
    {"name": "Giro News", "region": "BR", "url": "https://gironews.com/food-service/", "path_re": r"/food-service/"},
    {"name": "Food Connection", "region": "BR", "url": "https://www.foodconnection.com.br/"},
    {"name": "Paladar (Estadão)", "region": "BR", "url": "https://www.estadao.com.br/paladar/", "path_re": r"/paladar/"},
    {"name": "Folha Comida", "region": "BR", "url": "https://www1.folha.uol.com.br/comida/", "path_re": r"/comida/"},
    # Mundo
    {"name": "Eater", "region": "WORLD", "url": "https://www.eater.com/"},
    {"name": "Restaurant Business", "region": "WORLD", "url": "https://www.restaurantbusinessonline.com/"},
    {"name": "Food Dive", "region": "WORLD", "url": "https://www.fooddive.com/", "path_re": r"/news/"},
    {"name": "Just Food", "region": "WORLD", "url": "https://www.just-food.com/news/"},
    {"name": "FoodNavigator", "region": "WORLD", "url": "https://www.foodnavigator.com/"},
]
