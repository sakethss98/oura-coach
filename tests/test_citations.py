"""Citations (core/citations.py): turn-wide numbers, clean markers, source lines, Telegram HTML."""
from core import citations

CAFFEINE = "https://www.gssiweb.org/en/sports-science-exchange/article/caffeine-and-exercise-performance-an-update"
DRINKING = ("https://www.gssiweb.org/en/sports-science-exchange/article/fluid-intake-strategies-for-optimal-"
            "hydration-and-performance-planned-drinking-vs.-drinking-to-thirst")
A = {"title": "Caffeine and Exercise Performance: An Update", "url": CAFFEINE}
B = {"title": "Fluid Intake Strategies", "url": DRINKING}


def test_numbers_run_across_the_turn_and_an_article_keeps_its_number():
    first = citations.numbered([], [A, A])
    second = citations.numbered(first, [B, A])
    assert [s["n"] for s in first] == [1, 1] and [s["n"] for s in second] == [2, 1]


def test_finish_adds_the_source_line_of_each_cited_result():
    sources = citations.numbered([], [A, B])
    assert citations.finish("A low dose works [1].", sources) == (
        f"A low dose works [1].\nSource: GSSI – Caffeine and Exercise Performance: An Update <{CAFFEINE}>")
    both = citations.finish("Low dose [1]. Drink to thirst [2].", sources).splitlines()
    assert both[1:] == [f"Source [1]: GSSI – {A['title']} <{CAFFEINE}>", f"Source [2]: GSSI – {B['title']} <{DRINKING}>"]


def test_a_pasted_url_becomes_its_marker_an_unknown_marker_goes_and_no_marker_means_no_source_line():
    sources = citations.numbered([], [A, B])
    assert citations.finish(f"Drink to thirst ({DRINKING}).", sources).splitlines()[0] == "Drink to thirst [2]."
    assert citations.finish(f"Drink to thirst. {DRINKING}", sources).splitlines()[0] == "Drink to thirst [2]."
    assert citations.finish("Fine [7].", sources) == "Fine."              # uncited: the coach's own words
    assert citations.finish("No sources.", []) == "No sources."


def test_telegram_html_escapes_everything_and_links_only_known_urls():
    text = citations.finish("Under 5 g & <1 h? Fine [1].", citations.numbered([], [A]))
    assert citations.to_html(text) == ("Under 5 g &amp; &lt;1 h? Fine [1].\n"
                                       f'Source: <a href="{CAFFEINE}">GSSI – Caffeine and Exercise Performance: '
                                       "An Update</a>")
    fake = "Source: GSSI – Click me <https://evil.example/x>"
    assert citations.to_html(fake) == "Source: GSSI – Click me &lt;https://evil.example/x&gt;"


def test_the_articles_behind_the_amounts_are_one_line_each_title_a_link():
    sources = citations.numbered([], [A])
    text = citations.finish("Water is fine.", sources, [A, B, {"title": "C & D", "url": DRINKING}])
    assert text.splitlines()[1:] == [f"Amounts: GSSI – {A['title']} <{CAFFEINE}> · {B['title']} <{DRINKING}> · "
                                     f"C & D <{DRINKING}>"]
    assert citations.to_html(text).splitlines()[1] == (
        f'Amounts: GSSI – <a href="{CAFFEINE}">{A["title"]}</a> · <a href="{DRINKING}">{B["title"]}</a> · '
        f'<a href="{DRINKING}">C &amp; D</a>')
    cited = citations.finish("A low dose works [1].", sources, [A, B]).splitlines()
    assert cited[1:] == [f"Source [1]: GSSI – {A['title']} <{CAFFEINE}>", f"Amounts: GSSI – {B['title']} <{DRINKING}>"]
    fake = f"Amounts: GSSI – Fine <{CAFFEINE}> · Click me <https://evil.example/x>"
    assert "<a " not in citations.to_html(fake)
