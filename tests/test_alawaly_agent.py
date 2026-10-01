"""Tests for Alawaly Real Estate voice assistant and catalog knowledge base."""

from src import catalog, flow_say
from src.ui_bridge import absolute_url
from src.voice_agent.assistant import Assistant
from src.voice_agent.navigation_tools import NavigationTools
from src.voice_agent.project_tools import ProjectTools


def test_catalog_load():
    cat = catalog.load_catalog()
    assert cat is not None
    assert "company" in cat
    assert "projects" in cat
    assert len(cat["projects"]) >= 8
    assert len(cat["latest_news"]) >= 5


def test_company_facts():
    company = catalog.get_company()
    assert company["name"] == "العوالي العقارية"
    assert company["founded_year"] == 2011
    assert company["headquarters"] == "جدة"
    assert "سليمان بن رميح الرميح" in company["ceo"]["name"]
    assert "16 مليار" in company["stats"]["total_portfolio_value"]


def test_search_projects_by_city():
    riyadh_projects = catalog.search_projects(city="الرياض")
    assert len(riyadh_projects) >= 4
    for p in riyadh_projects:
        assert p["city"] == "الرياض"

    jeddah_projects = catalog.search_projects(city="جدة")
    assert len(jeddah_projects) >= 4
    for p in jeddah_projects:
        assert p["city"] == "جدة"


def test_search_projects_by_type():
    res = catalog.search_projects(property_type="سكني")
    assert len(res) >= 4
    for p in res:
        assert "سكن" in p["property_type_ar"]

    comm = catalog.search_projects(property_type="مكاتب")
    assert len(comm) >= 3


def test_project_details_akam_homes():
    p = catalog.get_project("آكام هومز")
    assert p is not None
    assert p["slug"] == "akam-homes"
    assert p["city"] == "جدة"
    assert p["total_units"] == 750
    assert p["unit_specs"]["bedrooms"] == 4
    assert p["unit_specs"]["area_sqm"] == 206.84


def test_project_details_alawaly_tower():
    p = catalog.get_project("العوالي تاور")
    assert p is not None
    assert p["slug"] == "alawaly-tower"
    assert p["city"] == "الرياض"
    assert p["total_units"] == 192


def test_page_resolution():
    home = catalog.resolve_page("الرئيسية")
    assert home is not None
    assert home[0] == "/ar"

    about = catalog.resolve_page("عن العوالي")
    assert about is not None
    assert about[0] == "/ar/about"

    projects = catalog.resolve_page("مشاريعنا")
    assert projects is not None
    assert projects[0] == "/ar/projects"

    contact = catalog.resolve_page("العناية بالعملاء")
    assert contact is not None
    assert contact[0] == "/ar/contact"

    news = catalog.resolve_page("المركز الاعلامي")
    assert news is not None
    assert news[0] == "/ar/news/latest"

    interest = catalog.resolve_page("سجل اهتمامك")
    assert interest is not None
    assert interest[0] == "sidebar:interest"


def test_greeting_text():
    norm = catalog.normalize_ar(flow_say.GREETING_SAY_AR)
    assert "العوالي العقاريه" in norm
    assert "2011" in norm
    assert "مساعدك الذكي" in norm


def test_assistant_classes():
    assert issubclass(Assistant, ProjectTools)
    assert issubclass(Assistant, NavigationTools)


def test_navigate_urls_are_absolute():
    assert absolute_url("/ar/about") == "https://www.alawaly.sa/ar/about"
    assert absolute_url("/ar/project/akam-homes") == "https://www.alawaly.sa/ar/project/akam-homes"
    assert absolute_url("https://alawaly-project.vercel.app/") == "https://alawaly-project.vercel.app/"
