"""Certifications: the team list, one person's certifications (record, renew, correct), and the catalogue in Admin."""

from typing import Annotated

from fastapi import APIRouter, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse

from competence.core import admin, services
from competence.core import certifications as certs
from competence.web.app import ConnDep
from competence.web.routes.pages import clean_team, query_link, render, see_other
from competence.web.schemas import CertificationForm, RecordForm

router = APIRouter(include_in_schema=False)


def _404(e: services.NotFound) -> HTTPException:
    return HTTPException(404, str(e))


def _context() -> dict:
    return {"status_labels": certs.STATUS_LABELS, "expiring_days": certs.EXPIRING_DAYS}


# ---------- team and person ----------


@router.get("/certifications")
def team_page(request: Request, conn: ConnDep, team: Annotated[int | None, Query()] = None) -> HTMLResponse:
    roles = {r.id: r.name for r in admin.list_roles(conn)}
    team, teams = clean_team(conn, team)
    return render(
        request,
        "certifications.html",
        {
            "t": certs.team_certifications(conn, team_id=team),
            "roles": roles,
            "team": team,
            "teams": teams,
            "link": query_link({"team": team}),
            **_context(),
        },
    )


def _person_page(request, conn, person_id: int, values: dict | None = None, error=None, status_code=200):
    try:
        pc = certs.person_certifications(conn, person_id)
    except services.NotFound as e:
        raise _404(e) from e
    roles = {r.id: r.name for r in admin.list_roles(conn)}
    return render(
        request,
        "person_certifications.html",
        {
            "pc": pc,
            "roles": roles,
            "choices": certs.list_certifications(conn, include_retired=False),
            "v": values or {},
            "error": error,
            **_context(),
        },
        status_code,
    )


@router.get("/people/{person_id}/certifications")
def person_page(request: Request, conn: ConnDep, person_id: int) -> HTMLResponse:
    return _person_page(request, conn, person_id)


@router.post("/people/{person_id}/certifications")
def add_record(request: Request, conn: ConnDep, person_id: int, form: Annotated[RecordForm, Form()]):
    try:
        certs.add_record(conn, person_id, form.certification_id, form.obtained_on, form.expires_on, form.notes)
    except services.NotFound as e:
        raise _404(e) from e
    except services.Invalid as e:
        return _person_page(request, conn, person_id, form.model_dump(), str(e), 400)
    return see_other(f"/people/{person_id}/certifications#cert-{form.certification_id}")


@router.post("/certifications/records/{record_id}/delete")
def delete_record(conn: ConnDep, record_id: int):
    try:
        r = certs.delete_record(conn, record_id)
    except services.NotFound as e:
        raise _404(e) from e
    return see_other(f"/people/{r.person_id}/certifications#cert-{r.certification_id}")


# ---------- catalogue (Admin) ----------


def _admin_page(request, conn, error=None, status_code=200):
    return render(
        request,
        "admin/certifications.html",
        {
            "certs": certs.list_certifications(conn),
            "roles": {r.id: r.name for r in admin.list_roles(conn)},
            "error": error,
        },
        status_code,
    )


def _cert_form(request, conn, values: dict, cert=None, error=None, status_code=200):
    return render(
        request,
        "admin/certification_form.html",
        {
            "cert": cert,
            "v": values,
            "error": error,
            "roles": [r for r in admin.list_roles(conn) if r.retired_on is None or r.id in values.get("role_id", [])],
        },
        status_code,
    )


def _cert_or_404(conn, cert_id: int):
    try:
        return certs.get_certification(conn, cert_id)
    except services.NotFound as e:
        raise _404(e) from e


@router.get("/admin/certifications")
def admin_page(request: Request, conn: ConnDep) -> HTMLResponse:
    return _admin_page(request, conn)


@router.get("/admin/certifications/new")
def new_certification(request: Request, conn: ConnDep) -> HTMLResponse:
    return _cert_form(request, conn, {"name": "", "description": "", "valid_months": None, "role_id": []})


@router.post("/admin/certifications")
def create_certification(request: Request, conn: ConnDep, form: Annotated[CertificationForm, Form()]):
    try:
        cid = certs.add_certification(conn, form.name, form.description, form.valid_months, form.role_id)
    except services.Invalid as e:
        return _cert_form(request, conn, form.model_dump(), error=str(e), status_code=400)
    return see_other(f"/admin/certifications#cert-{cid}")


@router.get("/admin/certifications/{cert_id}/edit")
def edit_certification(request: Request, conn: ConnDep, cert_id: int) -> HTMLResponse:
    c = _cert_or_404(conn, cert_id)
    return _cert_form(
        request,
        conn,
        {"name": c.name, "description": c.description, "valid_months": c.valid_months, "role_id": list(c.required_by)},
        cert=c,
    )


@router.post("/admin/certifications/{cert_id}")
def update_certification(request: Request, conn: ConnDep, cert_id: int, form: Annotated[CertificationForm, Form()]):
    c = _cert_or_404(conn, cert_id)
    try:
        certs.update_certification(conn, cert_id, form.name, form.description, form.valid_months, form.role_id)
    except services.Invalid as e:
        return _cert_form(request, conn, form.model_dump(), cert=c, error=str(e), status_code=400)
    return see_other(f"/admin/certifications#cert-{cert_id}")


@router.post("/admin/certifications/{cert_id}/retire")
def retire_certification(conn: ConnDep, cert_id: int):
    _cert_or_404(conn, cert_id)
    certs.retire_certification(conn, cert_id)
    return see_other(f"/admin/certifications#cert-{cert_id}")


@router.post("/admin/certifications/{cert_id}/unretire")
def unretire_certification(conn: ConnDep, cert_id: int):
    _cert_or_404(conn, cert_id)
    certs.unretire_certification(conn, cert_id)
    return see_other(f"/admin/certifications#cert-{cert_id}")


@router.post("/admin/certifications/{cert_id}/delete")
def delete_certification(request: Request, conn: ConnDep, cert_id: int):
    _cert_or_404(conn, cert_id)
    try:
        certs.delete_certification(conn, cert_id)
    except services.Invalid as e:
        return _admin_page(request, conn, str(e), 400)
    return see_other("/admin/certifications")
