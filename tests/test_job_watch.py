"""Tests for week01_http.job_watch (fake sites, no network)."""
import json
import re
from datetime import datetime, timezone

import pytest

from week01_http import client as client_module
from week01_http.job_watch import (
    NOT_SPECIFIED, JobSourceError, Source, diff_jobs, fetch_source, latest_snapshot, main,
    parse_fie, parse_html, parse_listings, run, to_iso_date,
)
from week01_http.client import create_client

SITE_A = "https://a.evaluar.test/"
SITE_B = "https://b.evaluar.test/ofertas-laborales/"
ENDPOINT_A = "https://a.evaluar.test/jm-ajax/get_listings/"
ENDPOINT_B = "https://b.evaluar.test/jm-ajax/get_listings/"
T1 = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)
T2 = datetime(2026, 11, 2, 12, 0, tzinfo=timezone.utc)
T3 = datetime(2026, 11, 3, 12, 0, tzinfo=timezone.utc)

A = Source("A · Evaluar", SITE_A)
B = Source("B · Evaluar", SITE_B)
TABLE_PAGE = "https://c.test/empleo"
C = Source("C · web", TABLE_PAGE, kind="html", expect="table.jobs",
           item="table.jobs tbody tr", title="td:nth-child(2)",
           location="xpath:substring-after(normalize-space(td[2]), ' - ')",
           link="a::attr(href)", key="td:nth-child(1)", content="table.jobs tbody tr")
MANUAL = Source("M · web", "https://m.test/trabaja", kind="manual", note="form only")


def listing(title, company, slug, logo_alt=None, with_company_li=True, city="LA PAZ"):
    company_li = (f'<li><i class="ln ln-icon-Management"></i> {company}</li>'
                  if with_company_li else "")
    return f"""
    <li><a href="https://x.test/trabajo/{slug}/" class="left job_listing">
        <div class="listing-logo"><img class="company_logo" alt="{logo_alt or company}"></div>
        <div class="listing-title"><h4>{title}
            </h4>
            <ul class="listing-icons">{company_li}
                <li><i class="ln ln-icon-Map2"></i> {city}
                    <small>(Bolivia)</small></li></ul></div></a></li>"""


def payload(*items, max_pages=1):
    return {"found_jobs": bool(items), "max_num_pages": max_pages, "html": "".join(items)}


def table_page(*rows):
    body = "".join(f"<tr><td>{ref}</td><td>{title}</td><td><a href='/pdf/{ref}.pdf'>Ver</a></td></tr>"
                   for ref, title in rows)
    return f"<table class='jobs'><thead><tr><th>Ref</th><th>Cargo</th></tr></thead><tbody>{body}</tbody></table>"


def titles(jobs):
    return [j["title"] for j in jobs]


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(client_module.time, "sleep", lambda s: None)


@pytest.fixture(autouse=True)
def no_dates_for_boards_a_b(httpx_mock):
    """Boards A and B have no publication dates (empty REST API) unless a test says otherwise."""
    httpx_mock.add_response(url=re.compile(r"https://[ab]\.evaluar\.test/wp-json/.*"), json=[],
                            is_optional=True, is_reusable=True)


# --- parsing -------------------------------------------------------------------

def test_parse_listings_extracts_clean_fields():
    html = listing("  ANALISTA   DE DATOS \n", "BANCO X", "analista", city="SANTA CRUZ DE LA SIERRA")
    assert parse_listings(html) == [
        {"title": "ANALISTA DE DATOS", "company": "BANCO X",
         "location": "SANTA CRUZ DE LA SIERRA",          # country in <small> is dropped
         "url": "https://x.test/trabajo/analista/"},
    ]


def test_parse_listings_falls_back_to_logo_alt_for_company():
    html = listing("CAJERO", "", "cajero", logo_alt="BANCO Y", with_company_li=False)
    assert parse_listings(html)[0]["company"] == "BANCO Y"


def test_parse_html_reads_rows_with_selectors():
    jobs = parse_html(C, table_page(("R-1", "CAJERO - POTOSI"), ("R-2", "ANALISTA - LA PAZ")), TABLE_PAGE)
    assert jobs[0] == {"title": "CAJERO - POTOSI", "company": "", "location": "POTOSI",
                       "published": NOT_SPECIFIED,
                       "url": "https://c.test/pdf/R-1.pdf", "id": f"{TABLE_PAGE}#R-1"}
    assert titles(jobs) == ["CAJERO - POTOSI", "ANALISTA - LA PAZ"]


def test_parse_html_empty_list_is_valid():
    assert parse_html(C, table_page(), TABLE_PAGE) == []


def test_parse_html_missing_expected_element_is_layout_error():
    with pytest.raises(JobSourceError, match="layout may have changed"):
        parse_html(C, "<div>redesigned</div>", TABLE_PAGE)


def test_parse_html_unreadable_rows_are_an_error():
    html = "<table class='jobs'><tbody><tr><td colspan=3><div>NEW MARKUP</div></td></tr></tbody></table>"
    with pytest.raises(JobSourceError, match="none could be read"):
        parse_html(C, html, TABLE_PAGE)


def test_parse_fie_reads_embedded_json():
    data = {"data": [{"cargo": "Analista I", "nombre_departamento": "La Paz",
                      "url": "https://fie.test/conv/ON-157"}], "last_page": 2}
    html = f"""<convocatorias :paginate='{json.dumps(data)}' url="https://fie.test/api/postulaciones/filtrar"></convocatorias>"""
    jobs, last_page = parse_fie(html)
    assert jobs == [{"title": "Analista I", "company": "BANCO FIE", "location": "La Paz",
                     "published": NOT_SPECIFIED,              # no "desde" in this row
                     "url": "https://fie.test/conv/ON-157"}]
    assert last_page == 2


# --- fetching ----------------------------------------------------------------------

def test_follows_all_pages(httpx_mock, tmp_path):
    httpx_mock.add_response(method="POST", url=ENDPOINT_A,
                            json=payload(listing("ONE", "A", "one"), max_pages=2))
    httpx_mock.add_response(method="POST", url=ENDPOINT_A,
                            json=payload(listing("TWO", "A", "two"), max_pages=2))
    result = run([A], tmp_path, now=T1)
    assert titles(result.current) == ["ONE", "TWO"]


def test_layout_change_is_reported_as_failure(httpx_mock, tmp_path):
    httpx_mock.add_response(method="POST", url=ENDPOINT_A,
                            json={"found_jobs": True, "max_num_pages": 1, "html": "<div>new</div>"})
    result = run([A], tmp_path, now=T1)
    assert "none parsed" in result.failed["A · Evaluar"]


def test_cv_pool_listing_is_excluded(httpx_mock, tmp_path):
    httpx_mock.add_response(method="POST", url=ENDPOINT_A, json=payload(
        listing("CAJERO", "A", "cajero"),
        listing("POSTÚLATE A NUESTRA BOLSA DE TRABAJO NACIONAL", "A",
                "postulate-a-nuestra-bolsa-de-trabajo-nacional"),
    ))
    assert titles(run([A], tmp_path, now=T1).current) == ["CAJERO"]


def test_mixed_source_kinds_and_manual_pages(httpx_mock, tmp_path):
    httpx_mock.add_response(method="POST", url=ENDPOINT_A, json=payload(listing("ONE", "A", "one")))
    httpx_mock.add_response(url=TABLE_PAGE, text=table_page(("R-1", "CAJERO - POTOSI")))
    httpx_mock.add_response(url=MANUAL.url, text="<form>CV</form>")
    result = run([A, C, MANUAL], tmp_path, now=T1)
    assert {(j["source"], j["title"]) for j in result.current} == {
        ("A · Evaluar", "ONE"), ("C · web", "CAJERO - POTOSI")}
    assert "CHECK MANUALLY" in result.report and "M · web: https://m.test/trabaja  (form only)" in result.report
    assert result.failed == {}


def test_same_posting_in_two_sources_is_kept_twice(httpx_mock, tmp_path):
    httpx_mock.add_response(method="POST", url=ENDPOINT_A, json=payload(listing("ONE", "A", "one")))
    httpx_mock.add_response(method="POST", url=ENDPOINT_B, json=payload(listing("ONE", "A", "one")))
    result = run([A, B], tmp_path, now=T1)
    assert [j["source"] for j in result.current] == ["A · Evaluar", "B · Evaluar"]


# --- diff ------------------------------------------------------------------------

def test_diff_jobs_added_removed_changed():
    a = {"title": "A", "company": "X", "url": "u1"}
    b = {"title": "B", "company": "X", "url": "u2"}
    b2 = {"title": "B (renamed)", "company": "X", "url": "u2"}
    c = {"title": "C", "company": "X", "url": "u3"}
    assert diff_jobs([a, b], [b2, c]) == {
        "added": [c], "removed": [a], "changed": [{"before": b, "after": b2}],
    }


def test_snapshot_without_location_does_not_mark_everything_changed():
    old = {"title": "A", "company": "X", "url": "u1"}
    new = {"title": "A", "company": "X", "location": "LA PAZ", "url": "u1"}
    assert diff_jobs([old], [new])["changed"] == []


def test_excluded_listing_in_old_snapshot_is_not_reported_removed():
    cv_pool = {"title": "POSTÚLATE", "company": "X",
               "url": "https://x.test/trabajo/postulate-a-nuestra-bolsa-de-trabajo/"}
    job = {"title": "A", "company": "X", "url": "u1"}
    assert diff_jobs([job, cv_pool], [job])["removed"] == []


# --- full runs ---------------------------------------------------------------------

def test_first_run_saves_baseline_without_diff(httpx_mock, tmp_path):
    httpx_mock.add_response(method="POST", url=ENDPOINT_A, json=payload(listing("ONE", "A", "one")))
    result = run([A], tmp_path, now=T1)
    assert result.diff is None and "first run" in result.report
    assert [p.name for p in tmp_path.iterdir()] == ["jobs-20261101T120000_000000Z.json"]


def test_second_run_compares_with_previous(httpx_mock, tmp_path):
    httpx_mock.add_response(method="POST", url=ENDPOINT_A,
                            json=payload(listing("ONE", "A", "one"), listing("TWO", "A", "two")))
    run([A], tmp_path, now=T1)
    httpx_mock.add_response(method="POST", url=ENDPOINT_A,
                            json=payload(listing("TWO", "A", "two"), listing("THREE", "A", "three")))
    result = run([A], tmp_path, now=T2)

    assert titles(result.diff["added"]) == ["THREE"]
    assert titles(result.diff["removed"]) == ["ONE"]
    assert "+ [added] A · Evaluar: THREE [LA PAZ]" in result.report
    assert latest_snapshot(tmp_path).name == "jobs-20261102T120000_000000Z.json"
    saved = json.loads((tmp_path / "diff-20261102T120000_000000Z.json").read_text(encoding="utf-8"))
    assert saved["previous"] == "jobs-20261101T120000_000000Z.json"


def test_failed_source_keeps_previous_results_and_is_not_diffed(httpx_mock, tmp_path):
    httpx_mock.add_response(method="POST", url=ENDPOINT_A, json=payload(listing("ONE", "A", "one")))
    httpx_mock.add_response(method="POST", url=ENDPOINT_B, json=payload(listing("BEE", "B", "bee")))
    run([A, B], tmp_path, now=T1)

    httpx_mock.add_response(method="POST", url=ENDPOINT_A, json=payload(listing("ONE", "A", "one"),
                                                                       listing("NEW", "A", "new")))
    httpx_mock.add_response(method="POST", url=ENDPOINT_B, status_code=404)
    result = run([A, B], tmp_path, now=T2)

    assert "B · Evaluar" in result.failed
    assert ("B · Evaluar", "BEE") in {(j["source"], j["title"]) for j in result.current}  # carried over
    assert titles(result.diff["added"]) == ["NEW"] and result.diff["removed"] == []
    assert "FAILED, showing previous results" in result.report


def test_new_source_starts_as_baseline(httpx_mock, tmp_path):
    httpx_mock.add_response(method="POST", url=ENDPOINT_A, json=payload(listing("ONE", "A", "one")))
    run([A], tmp_path, now=T1)

    httpx_mock.add_response(method="POST", url=ENDPOINT_A, json=payload(listing("ONE", "A", "one")))
    httpx_mock.add_response(method="POST", url=ENDPOINT_B, json=payload(listing("BEE", "B", "bee")))
    result = run([A, B], tmp_path, now=T2)
    assert result.new_sources == ["B · Evaluar"]
    assert not any(result.diff.values())             # B's jobs are not "added"

    httpx_mock.add_response(method="POST", url=ENDPOINT_A, json=payload(listing("ONE", "A", "one")))
    httpx_mock.add_response(method="POST", url=ENDPOINT_B, json=payload(listing("BEE", "B", "bee"),
                                                                       listing("BEE2", "B", "bee2")))
    result = run([A, B], tmp_path, now=T3)
    assert titles(result.diff["added"]) == ["BEE2"]  # compared from the next run on


def test_old_snapshot_format_is_upgraded(httpx_mock, tmp_path):
    # Format written before sources existed: no "source"/"id", "sites" list of URLs.
    old = {"run_at": T1.isoformat(), "sites": [SITE_A],
           "jobs": [{"title": "ONE", "company": "A", "location": "LA PAZ",
                     "url": "https://a.evaluar.test/trabajo/one/"}]}
    # (the fake listing's posting URL is on x.test)
    old["jobs"][0]["url"] = "https://x.test/trabajo/one/"
    (tmp_path / "jobs-20261101T120000Z.json").write_text(json.dumps(old), encoding="utf-8")

    httpx_mock.add_response(method="POST", url=ENDPOINT_A, json=payload(listing("ONE", "A", "one")))
    result = run([A], tmp_path, now=T2)
    assert result.new_sources == []                  # A is recognised from the old "sites" list
    assert result.previous_path.name == "jobs-20261101T120000Z.json"   # old name still sorts first


def test_main_exit_codes(httpx_mock, tmp_path):
    httpx_mock.add_response(method="POST", url=ENDPOINT_A, json=payload(listing("ONE", "A", "one")))
    assert main([SITE_A, "--data-dir", str(tmp_path)]) == 0
    httpx_mock.add_response(method="POST", url=ENDPOINT_A, status_code=503, is_reusable=True)
    assert main([SITE_A, "--data-dir", str(tmp_path)]) == 2   # saved, but a source failed
    assert len(list(tmp_path.glob("jobs-*.json"))) == 2


# --- publication dates --------------------------------------------------------------

D_SITE = "https://d.evaluar.test/"
D = Source("D · Evaluar", D_SITE)


def test_to_iso_date_formats():
    assert to_iso_date("2026-11-19T16:47:00") == "2026-11-19"
    assert to_iso_date("19/11/2026") == "2026-11-19"
    assert to_iso_date("19-11-2026") == "2026-11-19"
    assert to_iso_date("pronto") == NOT_SPECIFIED
    assert to_iso_date(None) == NOT_SPECIFIED


def test_evaluar_dates_come_from_rest_api_matched_by_url(httpx_mock):
    httpx_mock.add_response(method="POST", url="https://d.evaluar.test/jm-ajax/get_listings/",
                            json=payload(listing("ONE", "D", "one"), listing("TWO", "D", "two")))
    httpx_mock.add_response(url=re.compile(r"https://d\.evaluar\.test/wp-json/wp/v2/job-listings.*"),
                            json=[{"link": "https://x.test/trabajo/one", "date": "2026-11-19T16:47:00"}],
                            headers={"X-WP-TotalPages": "1"})
    with create_client() as client:
        jobs = fetch_source(client, D)
    assert {j["title"]: j["published"] for j in jobs} == {"ONE": "2026-11-19", "TWO": NOT_SPECIFIED}


def test_unavailable_dates_do_not_fail_the_source(httpx_mock):
    httpx_mock.add_response(method="POST", url="https://d.evaluar.test/jm-ajax/get_listings/",
                            json=payload(listing("ONE", "D", "one")))
    httpx_mock.add_response(url=re.compile(r"https://d\.evaluar\.test/wp-json/.*"),
                            status_code=403, is_reusable=True)
    with create_client() as client:
        assert fetch_source(client, D)[0]["published"] == NOT_SPECIFIED


def test_fie_publication_date_is_desde():
    data = {"data": [{"cargo": "Analista", "desde": "2026-11-16", "hasta": "2026-11-25",
                      "nombre_departamento": "La Paz", "url": "https://fie.test/c/1"}], "last_page": 1}
    html = f"""<x :paginate='{json.dumps(data)}' url="https://fie.test/api/postulaciones/filtrar"></x>"""
    assert parse_fie(html)[0][0]["published"] == "2026-11-16"


def test_html_source_date_from_detail_page(httpx_mock):
    src = Source("U · web", TABLE_PAGE, kind="html", item="table.jobs tbody tr",
                 title="td:nth-child(2)", link="a::attr(href)",
                 detail_published=r"FECHA DE INICIO\s*(\d{1,2}/\d{1,2}/\d{4})")
    httpx_mock.add_response(url=TABLE_PAGE, text=table_page(("R-1", "AUXILIAR"), ("R-2", "CAJERO")))
    httpx_mock.add_response(url="https://c.test/pdf/R-1.pdf", text="<p>FECHA DE INICIO 18/11/2026</p>")
    httpx_mock.add_response(url="https://c.test/pdf/R-2.pdf", text="<p>Sin fecha</p>")
    with create_client() as client:
        jobs = fetch_source(client, src)
    assert [j["published"] for j in jobs] == ["2026-11-18", NOT_SPECIFIED]


def test_html_source_without_date_is_not_specified():
    assert parse_html(C, table_page(("R-1", "CAJERO - POTOSI")), TABLE_PAGE)[0]["published"] == NOT_SPECIFIED


def test_report_shows_publication_date(httpx_mock, tmp_path):
    httpx_mock.add_response(method="POST", url=ENDPOINT_A, json=payload(listing("ONE", "A", "one")))
    report = run([A], tmp_path, now=T1).report
    assert "- ONE [LA PAZ] (published: Not Specified)" in report


# --- hiring-platform feeds (tech watch list) ----------------------------------------

from week01_http import job_watch
from week01_http.job_watch import (
    LATAM, parse_ashby, parse_bamboohr, parse_greenhouse, parse_lever, parse_smartrecruiters,
    parse_unosquare, parse_workable,
)


def test_parse_greenhouse():
    data = {"jobs": [{"title": " Data  Engineer ", "location": {"name": "Latin America"},
                      "first_published": "2026-09-15T17:16:41-04:00",
                      "absolute_url": "https://job-boards.greenhouse.io/x/jobs/1"}]}
    assert parse_greenhouse(data) == [{"title": "Data Engineer", "location": "Latin America",
                                       "published": "2026-09-15",
                                       "url": "https://job-boards.greenhouse.io/x/jobs/1"}]


def test_parse_lever_converts_millisecond_dates_and_marks_remote():
    data = [{"text": "Senior .NET Developer", "hostedUrl": "https://jobs.lever.co/x/1",
             "createdAt": 1793218659221, "workplaceType": "remote",
             "categories": {"location": "Colombia", "allLocations": ["Colombia", "Peru"]}}]
    [job] = parse_lever(data)
    assert job["location"] == "Colombia / Peru · Remote"
    assert job["published"] == "2026-10-28"


def test_parse_ashby_does_not_repeat_remote():
    data = {"jobs": [{"title": "QA", "location": "Remote LatAm", "isRemote": True,
                      "publishedAt": "2026-11-19T11:12:21.308+00:00", "jobUrl": "https://a.test/1"}]}
    assert parse_ashby(data)[0]["location"] == "Remote LatAm"
    assert parse_ashby(data)[0]["published"] == "2026-11-19"


def test_parse_workable_merges_one_entry_per_location():
    job = {"title": "DevOps", "shortlink": "https://apply.workable.com/j/A1", "telecommuting": True,
           "published_on": "2026-11-13"}
    data = {"jobs": [{**job, "locations": [{"city": None, "country": "Bolivia"}]},
                     {**job, "locations": [{"city": "Lima", "country": "Peru"}]}]}
    assert parse_workable(data) == [{"title": "DevOps", "location": "Bolivia / Lima, Peru · Remote",
                                     "published": "2026-11-13", "url": "https://apply.workable.com/j/A1"}]


def test_parse_smartrecruiters_builds_public_link():
    data = {"content": [{"id": "744", "name": "ML Engineer", "releasedDate": "2026-11-12T10:00:00Z",
                         "location": {"fullLocation": "Córdoba, Cordoba, Argentina", "remote": False}}]}
    assert parse_smartrecruiters(data, "endava") == [{
        "title": "ML Engineer", "location": "Córdoba, Cordoba, Argentina", "published": "2026-11-12",
        "url": "https://jobs.smartrecruiters.com/endava/744"}]


def test_smartrecruiters_follows_pages(httpx_mock, monkeypatch):
    monkeypatch.setattr(job_watch, "PER_PAGE", 1)
    for offset in (0, 1):
        httpx_mock.add_response(
            url=f"https://api.smartrecruiters.com/v1/companies/acme/postings?limit=1&offset={offset}",
            json={"totalFound": 2, "content": [{"id": str(offset), "name": f"JOB {offset}",
                                                "location": {"city": "Lima"}}]})
    with create_client() as client:
        jobs = fetch_source(client, Source("Acme", "https://acme.test/", kind="smartrecruiters", board="acme"))
    assert [j["title"] for j in jobs] == ["JOB 0", "JOB 1"]
    assert jobs[0]["company"] == "Acme"


def test_parse_bamboohr_has_no_dates():
    data = {"result": [{"id": "35", "jobOpeningName": "DevOps", "location": {"city": "Quito", "state": "Pichincha"},
                        "atsLocation": {"country": None}, "locationType": "1"}]}
    assert parse_bamboohr(data, "ioet") == [{"title": "DevOps", "location": "Quito, Pichincha · Remote",
                                             "published": NOT_SPECIFIED,
                                             "url": "https://ioet.bamboohr.com/careers/35"}]


def test_parse_unosquare_reads_next_data():
    data = {"props": {"pageProps": {"allJobsData": [
        {"CareerOpportunityId": 9207, "JobTitle": "Java Developer", "OfficeLocation": "Bolivia, Paraguay",
         "DateCreated": "2026-11-18T23:59:39.24303"}]}}}
    html = f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(data)}</script>'
    assert parse_unosquare(html) == [{"title": "Java Developer", "location": "Bolivia, Paraguay",
                                      "published": "2026-11-18",
                                      "url": "https://people.unosquare.com/jobs/9207"}]
    with pytest.raises(JobSourceError):
        parse_unosquare("<html></html>")


def test_region_keeps_only_latam_roles_and_duplicates_are_dropped(httpx_mock):
    httpx_mock.add_response(url="https://boards-api.greenhouse.io/v1/boards/glob/jobs", json={"jobs": [
        {"title": "Dev", "location": {"name": "Bogotá, Colombia"}, "absolute_url": "https://g.test/1"},
        {"title": "Dev", "location": {"name": "Bogotá, Colombia"}, "absolute_url": "https://g.test/1"},
        {"title": "Dev", "location": {"name": "Barcelona, Spain"}, "absolute_url": "https://g.test/2"},
        {"title": "Dev - Remote LatAm", "location": {"name": "Remote"}, "absolute_url": "https://g.test/3"},
    ]})
    src = Source("Glob", "https://glob.test/", kind="greenhouse", board="glob", region=LATAM)
    with create_client() as client:
        assert [j["url"] for j in fetch_source(client, src)] == ["https://g.test/1", "https://g.test/3"]


def test_watch_tech_uses_its_own_sources_and_folder(monkeypatch):
    calls = {}
    def fake_run(sources, data_dir):
        calls.update(sources=sources, data_dir=data_dir)
        return job_watch.RunResult([], None, {}, [], None, report="ok")
    monkeypatch.setattr(job_watch, "run", fake_run)
    assert main(["--watch", "tech"]) == 0
    assert calls == {"sources": job_watch.TECH_SOURCES, "data_dir": job_watch.Path("data/tech")}
