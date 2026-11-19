"""Track open positions and diff them against the previous run.

Two watch lists:
  banks -- Bolivian banks and financial entities (SOURCES)        -> data/jobs
  tech  -- software companies hiring LATAM engineers (TECH_SOURCES) -> data/tech

Each run:
  1. fetches every source of the watch list: job boards and hiring-platform
     feeds (evaluar.com, Greenhouse, Lever, ...), plus careers pages read with
     a small per-page rule;
  2. saves a timestamped snapshot to the data directory;
  3. compares it with the newest earlier snapshot (run n-1) and prints the
     full current list plus the differences (added / removed / changed).

A source that fails keeps its previous results (and is reported), so one
flaky site neither blocks the run nor shows up as "everything removed".

Run:  python -m week01_http.job_watch [--watch banks|tech] [--data-dir DIR] [EVALUAR_URL ...]
"""
import argparse
import json
import logging
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urljoin, urlsplit

import httpx
from parsel import Selector

from week01_http.client import create_client, get, post

DATA_DIR = Path("data/jobs")
PER_PAGE = 100

Kind = Literal["evaluar", "listings", "html", "fie", "manual",
               "greenhouse", "lever", "ashby", "workable", "smartrecruiters", "bamboohr",
               "unosquare"]


@dataclass(frozen=True)
class Source:
    """One page to watch.

    kind:
      evaluar  -- an evaluar.com job board; read via its /jm-ajax/get_listings/ endpoint
      listings -- a page with evaluar-style job cards (a.job_listing) in its HTML
      html     -- any page; postings are read with the selectors below
      fie      -- Banco FIE's page, which embeds its postings as JSON
      greenhouse, lever, ashby, workable, smartrecruiters, bamboohr
               -- that hiring platform's public job feed for `board` (the company's
                  account name there); `url` is the careers page people visit
      unosquare -- Unosquare's job page, which embeds its postings as Next.js data
      manual   -- no machine-readable list; only checked for reachability

    Selectors (html kind) are CSS, or XPath when prefixed with "xpath:".
    `item` selects one element per posting; the others are relative to it.
    `key` (optional) identifies a posting when its link is shared or unstable.
    `expect` (optional) must match the page, or the layout is assumed changed.
    `content` (optional): if it matches but no posting was read, that's an error
    (postings are present in a shape the selectors don't understand).
    `published` (optional) selects the publication date within each item;
    `detail_published` (optional) is a regex with one group that finds it in
    each posting's own page instead (one extra request per posting).
    `region` (optional) is a regex; postings whose location and title don't
    match it are dropped (for employers that hire worldwide).
    """
    name: str
    url: str
    kind: Kind = "evaluar"
    item: str = ""
    title: str = ""
    location: str = ""
    link: str = ""
    key: str = ""
    expect: str = ""
    content: str = ""
    published: str = ""
    detail_published: str = ""
    board: str = ""
    region: str = ""
    note: str = field(default="", compare=False)


SOURCES: tuple[Source, ...] = (
    # --- evaluar.com job boards ---------------------------------------------------------
    Source("Banco Unión · Evaluar", "https://bancounion.evaluar.com/"),
    Source("Banco Económico · Evaluar", "https://bancoeconomico.evaluar.com/ofertas-laborales/"),
    Source("Banco Mercantil Santa Cruz · Evaluar", "https://postulate.evaluar.com/"),
    Source("Banco Mercantil Santa Cruz · Evaluar (GPTW)", "https://bmscpostulate.evaluar.com/"),
    Source("BancoSol · Evaluar", "https://bancosol.evaluar.com/"),
    Source("BancoSol · Evaluar (internos)", "https://bancosolinternos.evaluar.com/"),
    Source("Banco Ganadero · Evaluar", "https://bancoganadero.evaluar.com/"),
    Source("Banco de Crédito de Bolivia · Evaluar", "https://bcpbolivia.evaluar.com/"),
    Source("Banco Nacional de Bolivia · Evaluar", "https://bnb.evaluar.com/"),
    Source("Banco de Desarrollo Productivo · Evaluar", "https://bdp.evaluar.com/"),
    Source("Diaconía IFD · Evaluar", "https://diaconia.evaluar.com/"),
    Source("Alianza Seguros · Evaluar", "https://alianzaseguros.evaluar.com/"),
    Source("Alianza Garantía · Evaluar", "https://alianzasgarantia.evaluar.com/"),
    Source("Bolsa Boliviana de Valores · Evaluar", "https://bbv.evaluar.com/"),
    Source("Banco FIE · Evaluar", "https://bancofie.evaluar.com/"),
    Source("Banco Ecofuturo · Evaluar", "https://bancoecofuturo.evaluar.com/"),
    Source("Banco Fortaleza · Evaluar", "https://grupofortaleza.evaluar.com/"),
    Source("Nacional Seguros · Evaluar", "https://nacionalseguros.evaluar.com/"),
    Source("Banco BISA · Evaluar", "https://bancobisa.evaluar.com/convocatorias-2/", kind="listings"),

    # --- entities' own careers pages ----------------------------------------------------
    Source("Banco FIE · web", "https://www.bancofie.com.bo/trabaja-en-banco-fie/convocatorias",
           kind="fie"),
    Source("Banco Ecofuturo · web", "https://www.bancoecofuturo.com.bo/Informacion/GEN",
           kind="html", expect="table.convocatorias-table",
           item="table.convocatorias-table tbody tr", title="td:nth-child(2)",
           location="td:nth-child(3)", key="td:nth-child(1)"),
    Source("Banco de Desarrollo Productivo · web", "https://www.bdp.com.bo/trabaja-bdp/",
           kind="html", expect="table.eae-table",
           item="xpath:(//table[contains(@class, 'eae-table')])[1]//tbody/tr",
           title="xpath:normalize-space(td[2])",
           location="xpath:substring-after(normalize-space(td[2]), ' - ')",
           link="a::attr(href)", key="xpath:normalize-space(td[2])"),
    Source("UNIVida · web", "https://www.univida.bo/index.php?mod=rrhh&view=announcementspublic",
           kind="html", expect="table",
           item="xpath://table//tr[td]", title="td:nth-child(3)",
           location="xpath:substring-after(normalize-space(td[2]), ' ')",
           link="a::attr(href)", key="td:nth-child(2)",
           detail_published=r"FECHA DE INICIO\s*(\d{1,2}/\d{1,2}/\d{4})"),
    # Drupal view; it renders no results block at all while there are no openings,
    # so the row markup is unknown: use the whole row text, and fail loudly if
    # results appear that this can't read.
    Source("ASFI · web", "https://www.asfi.gob.bo/oportunidad-de-empleo",
           kind="html", expect=".view-oportunidad-de-empleo",
           content=".view-oportunidad-de-empleo .view-content",
           item=(".view-oportunidad-de-empleo .view-content .views-row, "
                 ".view-oportunidad-de-empleo .view-content tbody tr"),
           title="xpath:.", link="a::attr(href)", key="xpath:."),
    Source("Cooperativa Catedral · web", "https://www.catedral.com.bo/trabaja-con-nosotros/",
           kind="html", expect="table.tablepress",
           item="table.tablepress tbody tr", title="td:nth-child(1)",
           location="xpath:substring-after(normalize-space(td[1]), ' - ')",
           link="td:nth-child(3) a::attr(href)", key="td:nth-child(1)"),
    Source("Bolsa Boliviana de Valores · web",
           "https://www.bbv.com.bo/acerca-de-la-bolsa/trabaja-con-nosotros/bolsa-de-trabajo/",
           kind="html", expect="section.work-content",
           item=".work-item", title="h1, h2, h3, h4, h5",
           link="a[href$='.pdf']::attr(href)", key="h1, h2, h3, h4, h5"),
    Source("Entidad de Depósito de Valores · web", "https://edv.com.bo/nuestras-vacantes/",
           kind="html", item="a.position-link", title="h4.position-title", link="xpath:@href"),

    # --- careers pages without a machine-readable list -----------------------------------
    Source("Banco Mercantil Santa Cruz · web", "https://www.bmsc.com.bo", kind="manual",
           note="links to postulate.evaluar.com"),
    Source("Banco Ganadero · web", "https://www.bg.com.bo/trabaja-con-nosotros/", kind="manual",
           note="links to bancoganadero.evaluar.com"),
    Source("Banco Fortaleza · web", "https://www.bancofortaleza.com.bo/talento-humano",
           kind="manual", note="links to grupofortaleza.evaluar.com"),
    Source("BancoSol · web", "https://www.bancosol.com.bo/trabaja-con-nosotros", kind="manual",
           note="CV form only"),
    Source("Banco PyME de la Comunidad · web", "https://www.bco.com.bo/trabaja-con-nosotros/",
           kind="manual", note="list not in the page HTML"),
    Source("IDEPRO IFD · web", "https://www.idepro.com.bo/convocatorias", kind="manual",
           note="list loaded by JavaScript"),
    Source("CIDRE IFD · web", "https://cidre.org.bo/trabaja-con-nosotros/", kind="manual",
           note="list not in the page HTML"),
    Source("FONDECO IFD · web", "https://fondeco.org/trabaja-con-nosotros/", kind="manual",
           note="application form only"),
    Source("Pro Mujer · web", "https://promujer.org/involucrate/", kind="manual",
           note="regional page; its jobs link was returning 404"),
    Source("La Boliviana Ciacruz · web", "https://www.lbc.bo/sobre-nosotros/trabaja-con-nosotros",
           kind="manual", note="no list on the page"),
    Source("Nacional Seguros · web",
           "https://nacionalseguros.com.bo/unete-a-nuestro-equipo-de-nacional-seguros",
           kind="manual", note="links to a Google Sites page"),
)

# Latin America (countries, big cities, "LatAm"/"South America"...), matched against a
# posting's location and title to keep only roles open to engineers in the region.
LATAM = (r"latam|latin|south america|central america|americas|argentin|bolivia|brazil|brasil|"
         r"chile|colombia|costa rica|ecuador|el salvador|guatemala|honduras|m[eé]xic|nicaragua|"
         r"panam|paraguay|per[uú]\b|uruguay|venezuela|dominican|puerto rico|bogot|medell|"
         r"buenos aires|c[oó]rdoba|\bcaba\b|\blima\b|quito|santiago|montevideo|s[aã]o paulo|"
         r"guadalajara|monterrey|san salvador|santo domingo|la paz|cochabamba|santa cruz|"
         r"caracas|bras[ií]lia")

TECH_SOURCES: tuple[Source, ...] = (
    # --- with Bolivian offices or Bolivia-based roles -------------------------------------
    Source("Jalasoft", "https://www.jalasoft.com/careers", kind="workable", board="jalasoft"),
    Source("Unosquare", "https://people.unosquare.com/jobs", kind="unosquare"),
    Source("CodeRoad", "https://coderoad.com/careers", kind="greenhouse", board="coderoad"),
    Source("AssureSoft", "https://www.assuresoft.com/careers/open-positions", kind="html",
           item="li.job-target", title=".job-title-card", location=".country",
           link="a::attr(href)"),
    Source("Velozient", "https://velozient.com/careers/", kind="html",
           item="a[href*='/job-description']", title="xpath:.", link="xpath:@href"),

    # --- nearshore / remote-LATAM companies ------------------------------------------------
    Source("Truelogic", "https://truelogic.io/", kind="ashby", board="truelogic"),
    Source("Azumo", "https://azumo.com/", kind="workable", board="azumo"),
    Source("Applaudo", "https://applaudo.com/", kind="smartrecruiters", board="applaudostudios"),
    Source("Santex", "https://santexgroup.com/", kind="greenhouse", board="santex"),
    Source("Teravision Technologies · Greenhouse", "https://www.teravisiontech.com/",
           kind="greenhouse", board="teravision"),
    Source("Teravision Technologies · BambooHR", "https://teravisiontech.bamboohr.com/careers",
           kind="bamboohr", board="teravisiontech"),
    Source("Gorilla Logic", "https://gorillalogic.com/", kind="bamboohr", board="gorillalogic"),
    Source("ioet", "https://www.ioet.com/", kind="bamboohr", board="ioet"),
    Source("Making Sense", "https://makingsense.com/", kind="bamboohr", board="makingsense"),
    Source("Oowlish", "https://oowlish.com/", kind="lever", board="oowlish"),
    Source("Blue Coding", "https://www.bluecoding.com/", kind="lever", board="bluecoding"),

    # --- global employers: LATAM roles only -----------------------------------------------
    Source("Nortal (ex-Nearsure)", "https://careers.nortal.com/", kind="greenhouse",
           board="nortal", region=LATAM),
    Source("Wizeline", "https://www.wizeline.ai/careers/", kind="greenhouse", board="wizeline",
           region=LATAM),
    Source("Endava", "https://www.endava.com/careers", kind="smartrecruiters", board="endava",
           region=LATAM),
    Source("Toptal", "https://www.toptal.com/careers", kind="lever", board="toptal",
           region=LATAM),

    # --- careers pages without a machine-readable list -------------------------------------
    Source("BairesDev", "https://www.bairesdev.com/join-us/", kind="manual",
           note="jobs at talent.bairesdev.com, which blocks automated clients"),
    Source("Encora (now Coforge)", "https://careers.coforge.com/coforge/", kind="manual",
           note="list loaded by JavaScript"),
    Source("Avenue Code", "https://avenuecode.com/", kind="manual",
           note="site now redirects to aircompany.ai; no careers list found"),
    Source("Jobsity", "https://careers.jobsity.com/", kind="manual",
           note="list not in the page HTML"),
)

WATCHES: dict[str, tuple[tuple[Source, ...], Path]] = {
    "banks": (SOURCES, DATA_DIR),
    "tech": (TECH_SOURCES, Path("data/tech")),
}

# Listings that are not real positions (e.g. "POSTÚLATE A NUESTRA BOLSA DE TRABAJO",
# a general CV drop). Matched against the job URL.
EXCLUDED_SLUGS = ("bolsa-de-trabajo", "se%20parte%20de%20nuestra")  # Económico CV drop, "Comunidad FIE"

# Fields compared between runs to detect a "changed" posting.
COMPARED_FIELDS = ("title", "company", "location")

NOT_SPECIFIED = "Not Specified"  # publication date the source doesn't provide

# {"source", "company", "title", "location", "published", "url", "id"};
# `published` is YYYY-MM-DD or NOT_SPECIFIED; `id` identifies the
# posting within its source (usually its URL).
Job = dict[str, str]

log = logging.getLogger(__name__)


class JobSourceError(RuntimeError):
    """Raised when a source can't be fetched or doesn't parse."""


# --- parsing ---------------------------------------------------------------------

def _clean(text: str) -> str:
    return " ".join(text.split())


def _query(node: Selector, selector: str):
    if selector.startswith("xpath:"):
        return node.xpath(selector[len("xpath:"):])
    return node.css(selector)


def to_iso_date(text: str | None) -> str:
    """YYYY-MM-DD from "2026-11-19", "2026-11-19T16:47:00", "19/11/2026" or "19-11-2026";
    NOT_SPECIFIED if it isn't a recognisable date."""
    text = (text or "").strip()
    for fmt, length in (("%Y-%m-%d", 10), ("%d/%m/%Y", 10), ("%d-%m-%Y", 10)):
        try:
            return datetime.strptime(text[:length], fmt).date().isoformat()
        except ValueError:
            continue
    return NOT_SPECIFIED


def _first_text(node: Selector, selector: str) -> str:
    """Text of the first match: attribute/text/XPath-string results as-is,
    elements as all their descendant text."""
    if not selector:
        return ""
    matches = _query(node, selector)
    if not matches:
        return ""
    first = matches[0]
    if isinstance(first.root, str):
        return _clean(first.get())
    return _clean(" ".join(first.css("::text").getall()))


def parse_listings(html: str) -> list[Job]:
    """Parse evaluar-style job cards (a.job_listing)."""
    jobs = []
    for item in Selector(html).css("a.job_listing"):
        title = _clean(item.css(".listing-title h4::text").get(""))
        company = _clean(" ".join(item.xpath(
            './/i[contains(@class, "ln-icon-Management")]/parent::li//text()').getall()))
        if not company:  # fall back to the logo's alt text
            company = _clean(item.css("img.company_logo::attr(alt)").get(""))
        # The location <li> holds the city as its own text and the country in a
        # <small> ("LA PAZ <small>(Bolivia)</small>"); keep just the city.
        location = _clean(" ".join(item.xpath(
            './/i[contains(@class, "ln-icon-Map2")]/parent::li/text()').getall()))
        jobs.append({"title": title, "company": company, "location": location,
                     "url": item.attrib.get("href", "")})
    return jobs


def parse_html(source: Source, html: str, page_url: str) -> list[Job]:
    """Read postings from any page using the source's selectors."""
    page = Selector(html)
    if source.expect and not _query(page, source.expect):
        raise JobSourceError(f"{source.name}: {source.expect!r} not found; "
                             "the page layout may have changed")
    jobs = []
    for item in _query(page, source.item):
        title = _first_text(item, source.title)
        if not title:  # header/spacer rows
            continue
        link = _first_text(item, source.link)
        url = urljoin(page_url, link) if link else page_url
        key = _first_text(item, source.key)
        jobs.append({"title": title, "company": "", "location": _first_text(item, source.location),
                     "published": to_iso_date(_first_text(item, source.published)),
                     "url": url, "id": f"{page_url}#{key}" if key else url})
    if not jobs and source.content and _query(page, source.content):
        raise JobSourceError(f"{source.name}: postings present but none could be read; "
                             "update this source's selectors")
    return jobs


def parse_fie(html: str) -> tuple[list[Job], int]:
    """Banco FIE embeds its paginated postings as JSON in an attribute of the
    element that also carries url=".../api/postulaciones/filtrar"."""
    holders = Selector(html).xpath('//*[contains(@url, "postulaciones/filtrar")]')
    if not holders:
        raise JobSourceError("Banco FIE: postings element not found; the page layout may have changed")
    for value in holders[0].attrib.values():
        if value.lstrip().startswith("{") and '"data"' in value:
            paginate = json.loads(value)
            break
    else:
        raise JobSourceError("Banco FIE: postings JSON not found; the page layout may have changed")
    jobs = [{"title": _clean(row.get("cargo") or ""),
             "company": "BANCO FIE",
             "location": _clean(row.get("nombre_departamento") or ""),
             "published": to_iso_date(row.get("desde")),
             "url": row.get("url") or ""}
            for row in paginate.get("data", [])]
    return jobs, int(paginate.get("last_page") or 1)


# --- fetching ----------------------------------------------------------------------

def _remote(location: str, remote: bool) -> str:
    """Add "Remote" to a location when the feed flags the role as remote."""
    if not remote or "remote" in location.lower():
        return location
    return f"{location} · Remote" if location else "Remote"


def parse_greenhouse(data: dict) -> list[Job]:
    return [{"title": _clean(j.get("title") or ""),
             "location": _clean((j.get("location") or {}).get("name") or ""),
             "published": to_iso_date(j.get("first_published")),
             "url": j.get("absolute_url") or ""}
            for j in data.get("jobs", [])]


def parse_lever(data: list) -> list[Job]:
    jobs = []
    for j in data:
        categories = j.get("categories") or {}
        places = categories.get("allLocations") or [categories.get("location")]
        created = j.get("createdAt")  # milliseconds since the epoch
        jobs.append({
            "title": _clean(j.get("text") or ""),
            "location": _remote(" / ".join(p for p in places if p), j.get("workplaceType") == "remote"),
            "published": (datetime.fromtimestamp(created / 1000, timezone.utc).date().isoformat()
                          if created else NOT_SPECIFIED),
            "url": j.get("hostedUrl") or ""})
    return jobs


def parse_ashby(data: dict) -> list[Job]:
    return [{"title": _clean(j.get("title") or ""),
             "location": _remote(_clean(j.get("location") or ""),
                                 bool(j.get("isRemote")) or j.get("workplaceType") == "Remote"),
             "published": to_iso_date(j.get("publishedAt")),
             "url": j.get("jobUrl") or ""}
            for j in data.get("jobs", [])]


def parse_workable(data: dict) -> list[Job]:
    """Workable lists a job once per location (same link); merge those into one posting."""
    merged: dict[str, dict] = {}
    for j in data.get("jobs", []):
        url = j.get("shortlink") or j.get("url") or ""
        entry = merged.setdefault(url, {"job": j, "places": []})
        for place in j.get("locations") or [j]:
            text = ", ".join(p for p in (place.get("city"), place.get("country")) if p)
            if text and text not in entry["places"]:
                entry["places"].append(text)
    return [{"title": _clean(e["job"].get("title") or ""),
             "location": _remote(_clean(" / ".join(e["places"])), bool(e["job"].get("telecommuting"))),
             "published": to_iso_date(e["job"].get("published_on")),
             "url": url}
            for url, e in merged.items()]


def parse_smartrecruiters(data: dict, board: str) -> list[Job]:
    jobs = []
    for j in data.get("content", []):
        place = j.get("location") or {}
        jobs.append({"title": _clean(j.get("name") or ""),
                     "location": _remote(_clean(place.get("fullLocation") or place.get("city") or ""),
                                         bool(place.get("remote"))),
                     "published": to_iso_date(j.get("releasedDate")),
                     "url": f"https://jobs.smartrecruiters.com/{board}/{j['id']}" if j.get("id") else ""})
    return jobs


def parse_bamboohr(data: dict, board: str) -> list[Job]:
    jobs = []
    for j in data.get("result", []):
        place, ats = j.get("location") or {}, j.get("atsLocation") or {}
        location = ", ".join(dict.fromkeys(  # dict.fromkeys: drop repeats, keep order
            p for p in (place.get("city") or ats.get("city"), place.get("state"), ats.get("country")) if p))
        jobs.append({"title": _clean(j.get("jobOpeningName") or ""),
                     # locationType "1" is BambooHR's remote setting
                     "location": _remote(_clean(location), bool(j.get("isRemote")) or j.get("locationType") == "1"),
                     "published": NOT_SPECIFIED,  # the public list has no dates
                     "url": f"https://{board}.bamboohr.com/careers/{j['id']}" if j.get("id") else ""})
    return jobs


def parse_unosquare(html: str) -> list[Job]:
    raw = Selector(html).css("script#__NEXT_DATA__::text").get()
    try:
        rows = json.loads(raw)["props"]["pageProps"]["allJobsData"]
    except (TypeError, ValueError, KeyError) as e:
        raise JobSourceError("Unosquare: job data not found; the page layout may have changed") from e
    return [{"title": _clean(r.get("JobTitle") or ""),
             "location": _clean(r.get("OfficeLocation") or ""),
             "published": to_iso_date(r.get("DateCreated")),
             "url": f"https://people.unosquare.com/jobs/{r['CareerOpportunityId']}"
                    if r.get("CareerOpportunityId") else ""}
            for r in rows]


def _get_json(client: httpx.Client, url: str, **params: Any) -> Any:
    response = _get_ok(client, url, params=params or None)
    try:
        return response.json()
    except ValueError as e:
        raise JobSourceError(f"{url}: response is not JSON") from e


def fetch_platform_jobs(client: httpx.Client, source: Source) -> list[Job]:
    """Postings from a hiring platform's public feed for `source.board`."""
    board = source.board
    if source.kind == "greenhouse":
        return parse_greenhouse(_get_json(client, f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs"))
    if source.kind == "lever":
        return parse_lever(_get_json(client, f"https://api.lever.co/v0/postings/{board}", mode="json"))
    if source.kind == "ashby":
        return parse_ashby(_get_json(client, f"https://api.ashbyhq.com/posting-api/job-board/{board}"))
    if source.kind == "workable":
        return parse_workable(_get_json(client, f"https://apply.workable.com/api/v1/widget/accounts/{board}"))
    if source.kind == "bamboohr":
        return parse_bamboohr(_get_json(client, f"https://{board}.bamboohr.com/careers/list"), board)
    if source.kind == "smartrecruiters":
        url = f"https://api.smartrecruiters.com/v1/companies/{board}/postings"
        jobs: list[Job] = []
        offset, total = 0, 1
        while offset < total:
            data = _get_json(client, url, limit=PER_PAGE, offset=offset)
            jobs += parse_smartrecruiters(data, board)
            total = int(data.get("totalFound") or 0)
            offset += PER_PAGE
        return jobs
    raise ValueError(f"unknown platform {source.kind!r}")


PLATFORMS = ("greenhouse", "lever", "ashby", "workable", "smartrecruiters", "bamboohr")


def _get_ok(client: httpx.Client, url: str, params: dict | None = None) -> httpx.Response:
    response = get(client, url, params=params)
    if response.is_error:
        raise JobSourceError(f"{url}: HTTP {response.status_code}")
    return response


def fetch_site_jobs(client: httpx.Client, site_url: str) -> list[Job]:
    """Every open position on one evaluar.com board (all pages of get_listings)."""
    parts = urlsplit(site_url)
    endpoint = f"{parts.scheme}://{parts.netloc}/jm-ajax/get_listings/"
    jobs: list[Job] = []
    page, max_pages = 1, 1
    while page <= max_pages:
        # POST is only how this endpoint takes its query; it changes nothing, so retrying is safe.
        response = post(client, endpoint, body={"page": page, "per_page": PER_PAGE},
                        retry_unsafe=True)
        try:
            response.raise_for_status()
            data = response.json()
        except (httpx.HTTPStatusError, ValueError) as e:
            raise JobSourceError(f"{endpoint} page {page}: unexpected response: {e}") from e

        page_jobs = parse_listings(data.get("html", ""))
        if data.get("found_jobs") and not page_jobs:
            raise JobSourceError(f"{endpoint} page {page}: jobs reported but none parsed; "
                                 "the listing layout may have changed")
        jobs += page_jobs
        max_pages = int(data.get("max_num_pages") or 1)
        page += 1
    return jobs


def _url_key(url: str) -> str:
    return url.rstrip("/")


def fetch_evaluar_dates(client: httpx.Client, site_url: str) -> dict[str, str]:
    """{posting URL: YYYY-MM-DD} for an evaluar.com board, from WordPress's REST API
    (one request per 100 postings). Dates are extra information, so any failure
    just means "no dates" rather than failing the source."""
    parts = urlsplit(site_url)
    endpoint = f"{parts.scheme}://{parts.netloc}/wp-json/wp/v2/job-listings"
    dates: dict[str, str] = {}
    page, total_pages = 1, 1
    try:
        while page <= total_pages:
            response = get(client, endpoint, params={"per_page": 100, "page": page,
                                                     "_fields": "link,date"})
            response.raise_for_status()
            for row in response.json():
                dates[_url_key(row["link"])] = to_iso_date(row.get("date"))
            total_pages = int(response.headers.get("X-WP-TotalPages") or 1)
            page += 1
    except Exception as e:  # noqa: BLE001 - dates are optional
        log.warning("%s: publication dates unavailable: %s", parts.netloc, e)
    return dates


def _detail_date(client: httpx.Client, url: str, pattern: str) -> str:
    try:
        page = Selector(_get_ok(client, url).text)
        text = _clean(" ".join(page.css("body ::text").getall()))
    except Exception as e:  # noqa: BLE001 - dates are optional
        log.warning("%s: publication date unavailable: %s", url, e)
        return NOT_SPECIFIED
    match = re.search(pattern, text, re.IGNORECASE)
    return to_iso_date(match.group(1)) if match else NOT_SPECIFIED


def fetch_source(client: httpx.Client, source: Source) -> list[Job]:
    """Fetch one source and return its postings, tagged with the source name."""
    if source.kind in ("evaluar", "listings"):
        if source.kind == "evaluar":
            jobs = fetch_site_jobs(client, source.url)
        else:
            jobs = parse_listings(_get_ok(client, source.url).text)
        if jobs:
            dates = fetch_evaluar_dates(client, source.url)
            for job in jobs:
                job["published"] = dates.get(_url_key(job["url"]), NOT_SPECIFIED)
    elif source.kind == "html":
        response = _get_ok(client, source.url)
        jobs = parse_html(source, response.text, str(response.url))
        if source.detail_published:
            for job in jobs:
                if job["url"] != source.url:  # only postings with their own page
                    job["published"] = _detail_date(client, job["url"], source.detail_published)
    elif source.kind == "fie":
        jobs, last_page, page = [], 1, 1
        while page <= last_page:
            url = source.url if page == 1 else f"{source.url}?page={page}"
            page_jobs, last_page = parse_fie(_get_ok(client, url).text)
            jobs += page_jobs
            page += 1
    elif source.kind in PLATFORMS:
        jobs = fetch_platform_jobs(client, source)
    elif source.kind == "unosquare":
        jobs = parse_unosquare(_get_ok(client, source.url).text)
    elif source.kind == "manual":
        _get_ok(client, source.url)  # only check that it's reachable
        jobs = []
    else:
        raise ValueError(f"unknown source kind {source.kind!r}")

    incomplete = [j for j in jobs if not (j["title"] and j["url"])]
    if incomplete:
        raise JobSourceError(f"{source.name}: {len(incomplete)} posting(s) missing title or link")
    for job in jobs:
        job["source"] = source.name
        job["company"] = job.get("company") or source.name.split(" · ")[0]
        job.setdefault("id", job["url"])
        job["published"] = job.get("published") or NOT_SPECIFIED
        job.setdefault("location", "")
    if source.region:
        jobs = [j for j in jobs if re.search(source.region, f"{j['location']} {j['title']}", re.IGNORECASE)]
    unique = {j["id"]: j for j in reversed(jobs)}  # same posting linked twice: keep the first
    return [j for j in jobs if unique.get(j["id"]) is j and is_position(j)]


def is_position(job: Job) -> bool:
    """False for listings that aren't real openings (see EXCLUDED_SLUGS)."""
    url = job["url"].lower()
    return not any(slug in url for slug in EXCLUDED_SLUGS)


def fetch_all(client: httpx.Client, sources: list[Source],
              workers: int = 8) -> tuple[list[Job], dict[str, str]]:
    """Fetch every source in parallel. Returns (jobs, {failed source name: error})."""
    def one(source: Source) -> tuple[Source, list[Job] | None, str]:
        try:
            jobs = fetch_source(client, source)
        except Exception as e:  # any failure is isolated to its source
            log.warning("%s: failed: %s", source.name, e)
            return source, None, f"{type(e).__name__}: {e}"
        log.info("%s: %d open position(s)", source.name, len(jobs))
        return source, jobs, ""

    jobs: list[Job] = []
    failed: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for source, source_jobs, error in pool.map(one, sources):
            if source_jobs is None:
                failed[source.name] = error
            else:
                jobs += source_jobs
    return jobs, failed


# --- snapshots & diff ----------------------------------------------------------------

def latest_snapshot(data_dir: Path) -> Path | None:
    """Newest jobs-*.json in data_dir (timestamps in the name sort chronologically)."""
    snapshots = sorted(data_dir.glob("jobs-*.json"))
    return snapshots[-1] if snapshots else None


def _source_for_url(url: str, sources: list[Source]) -> str:
    host = urlsplit(url).netloc
    for source in sources:
        if source.kind in ("evaluar", "listings") and urlsplit(source.url).netloc == host:
            return source.name
    return host


def load_snapshot(path: Path, sources: list[Source]) -> dict[str, Any]:
    """Load a snapshot, upgrading older formats (no `source`/`id` fields, `sites` list)."""
    data = json.loads(path.read_text(encoding="utf-8"))
    for job in data["jobs"]:
        job.setdefault("source", _source_for_url(job["url"], sources))
        job.setdefault("id", job["url"])
    if "sources" not in data:
        data["sources"] = sorted({_source_for_url(u, sources) for u in data.get("sites", [])})
    data.setdefault("failed", {})
    return data


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)  # atomic: never leaves a half-written snapshot


def _key(job: Job) -> tuple[str, str]:
    return job.get("source", ""), job.get("id") or job["url"]


def _differs(before: Job, after: Job) -> bool:
    # A field missing from an older snapshot (e.g. "location" before it was
    # scraped) is treated as unknown, not as a change.
    return any(f in before and f in after and before[f] != after[f] for f in COMPARED_FIELDS)


def diff_jobs(previous: list[Job], current: list[Job]) -> dict[str, list]:
    """Compare two runs by (source, id).

    The exclusion filter is applied to `previous` too, so excluding a listing
    doesn't make it show up as "removed" against an older snapshot.
    """
    prev = {_key(j): j for j in previous if is_position(j)}
    curr = {_key(j): j for j in current}
    return {
        "added": [curr[k] for k in curr if k not in prev],
        "removed": [prev[k] for k in prev if k not in curr],
        "changed": [{"before": prev[k], "after": curr[k]}
                    for k in curr if k in prev and _differs(prev[k], curr[k])],
    }


# --- report ---------------------------------------------------------------------------

@dataclass
class RunResult:
    current: list[Job]
    diff: dict[str, list] | None          # None on the first run
    failed: dict[str, str]                # source name -> error (previous results kept)
    new_sources: list[str]                # not in the previous snapshot: baseline only
    previous_path: Path | None
    report: str = ""


def _job_line(job: Job) -> str:
    where = f" [{job['location']}]" if job.get("location") else ""
    published = job.get("published") or NOT_SPECIFIED
    return f"{job['title']}{where} (published: {published})"


def format_report(result: RunResult, sources: list[Source]) -> str:
    by_source: dict[str, list[Job]] = {}
    for job in result.current:
        by_source.setdefault(job["source"], []).append(job)

    listed = [s for s in sources if s.kind != "manual"]
    lines = [f"OPEN POSITIONS ({len(result.current)}) from {len(listed)} sources", ""]
    for source in listed:
        jobs = by_source.get(source.name, [])
        status = " (FAILED, showing previous results)" if source.name in result.failed else ""
        lines.append(f"  {source.name} ({len(jobs)}){status}")
        lines.append(f"  {source.url}")
        for job in jobs:
            lines.append(f"    - {_job_line(job)}")
            lines.append(f"      {job['url']}")
        lines.append("")

    manual = [s for s in sources if s.kind == "manual"]
    if manual:
        lines.append("CHECK MANUALLY (no machine-readable list)")
        for source in manual:
            state = "UNREACHABLE" if source.name in result.failed else source.note
            lines.append(f"  - {source.name}: {source.url}  ({state})")
        lines.append("")

    if result.failed:
        lines.append(f"ERRORS ({len(result.failed)} source(s); previous results kept)")
        for name, error in result.failed.items():
            lines.append(f"  - {name}: {error}")
        lines.append("")

    lines.append("CHANGES SINCE PREVIOUS RUN")
    if result.diff is None:
        lines.append("  first run: no previous snapshot to compare against (baseline saved)")
        return "\n".join(lines)

    lines.append(f"  compared with {result.previous_path.name}")
    if result.new_sources:
        lines.append(f"  {len(result.new_sources)} new source(s), baseline saved (compared from the next run):")
        lines += [f"    · {name}" for name in result.new_sources]
    diff = result.diff
    if not any(diff.values()):
        lines.append("  no changes")
    for label, sign in (("added", "+"), ("removed", "-")):
        for job in diff[label]:
            lines.append(f"  {sign} [{label}] {job['source']}: {_job_line(job)}")
            lines.append(f"      {job['url']}")
    for change in diff["changed"]:
        before, after = change["before"], change["after"]
        edits = "; ".join(f"{f}: {before[f]!r} -> {after[f]!r}" for f in COMPARED_FIELDS
                          if f in before and f in after and before[f] != after[f])
        lines.append(f"  ~ [changed] {after['source']}: {after['title']} ({edits})")
        lines.append(f"      {after['url']}")
    return "\n".join(lines)


# --- entry point ------------------------------------------------------------------------

def _as_sources(items: list[Source | str] | tuple[Source | str, ...]) -> list[Source]:
    """Accept Source objects or bare evaluar.com board URLs."""
    return [s if isinstance(s, Source) else Source(urlsplit(s).netloc, s) for s in items]


def run(sources: list[Source | str] | tuple[Source | str, ...] = SOURCES,
        data_dir: Path = DATA_DIR, now: datetime | None = None) -> RunResult:
    """Fetch every source, save the snapshot (and diff), and return the result."""
    sources = _as_sources(sources)
    now = now or datetime.now(timezone.utc)
    # Microseconds keep two runs in the same second apart; "_" sorts after "Z", so
    # these still order correctly after older "...SSZ" snapshot names.
    stamp = now.strftime("%Y%m%dT%H%M%S_%fZ")
    previous_path = latest_snapshot(data_dir)
    previous = load_snapshot(previous_path, sources) if previous_path else None

    with closing(create_client()) as client:
        current, failed = fetch_all(client, sources)

    names = [s.name for s in sources]
    new_sources: list[str] = []
    diff = None
    if previous is not None:
        prev_jobs = previous["jobs"]
        # A failed source keeps its previous postings, so it isn't reported as "removed".
        current += [j for j in prev_jobs if j["source"] in failed]
        # Not compared: sources that failed now, sources the previous run didn't have,
        # and sources whose previous run failed with nothing to carry over.
        new_sources = [n for n in names if n not in previous["sources"]]
        never_loaded = {n for n in previous["failed"]
                        if not any(j["source"] == n for j in prev_jobs)}
        skip = set(failed) | set(new_sources) | never_loaded
        diff = diff_jobs([j for j in prev_jobs if j["source"] in names and j["source"] not in skip],
                         [j for j in current if j["source"] not in skip])

    order = {name: i for i, name in enumerate(names)}
    current.sort(key=lambda j: (order.get(j["source"], len(order)), j["title"], j["id"]))

    write_json(data_dir / f"jobs-{stamp}.json",
               {"run_at": now.isoformat(), "sources": names, "failed": failed, "jobs": current})
    if diff is not None:
        write_json(data_dir / f"diff-{stamp}.json",
                   {"run_at": now.isoformat(), "previous": previous_path.name,
                    "new_sources": new_sources, "failed": failed, **diff})

    result = RunResult(current, diff, failed, new_sources, previous_path)
    result.report = format_report(result, sources)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("boards", nargs="*",
                        help="evaluar.com board URLs to watch instead of the built-in SOURCES")
    parser.add_argument("--watch", choices=sorted(WATCHES), default="banks",
                        help="which list of sources to run (default: banks)")
    parser.add_argument("--data-dir", type=Path, default=None,
                        help="where snapshots are kept (default: data/jobs for banks, "
                             "data/tech for tech)")
    args = parser.parse_args(argv)
    sources, default_dir = WATCHES[args.watch]

    logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("week01_http.client").setLevel(logging.ERROR)  # per-retry noise
    result = run(args.boards or sources, args.data_dir or default_dir)
    print(result.report)
    return 2 if result.failed else 0  # snapshot is saved either way


if __name__ == "__main__":
    sys.exit(main())
