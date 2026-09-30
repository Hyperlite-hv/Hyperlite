"""Swagger for the accounts an administrator chose: a single-use ticket from the dashboard button opens /docs, whose
cookie is checked against the setting at each page; /docs and /openapi.json are never public."""


def _open(client, headers):
    r = client.post("/api-docs/ticket", headers=headers)
    assert r.status_code == 200, r.text
    return client.get(r.json()["url"], follow_redirects=False)


def test_administrators_open_swagger_by_default_and_nobody_without_the_button(client, auth_headers):
    admin = auth_headers("alice")
    viewer = auth_headers("olga", role="observateur")
    assert client.get("/api-docs/acces", headers=viewer).json() == {"acces": "admins", "autorise": False}
    assert client.post("/api-docs/ticket", headers=viewer).status_code == 403
    assert client.get("/docs").status_code == 403 and client.get("/openapi.json").status_code == 403

    r = _open(client, admin)
    assert r.status_code == 303 and r.headers["location"] == "/docs"
    cookie = r.cookies.get("hl_docs")
    assert (
        cookie
        and "httponly" in r.headers["set-cookie"].lower()
        and "samesite=strict" in r.headers["set-cookie"].lower()
    )
    page = client.get("/docs", cookies={"hl_docs": cookie})
    assert page.status_code == 200 and "/swagger/swagger-ui-bundle.js" in page.text  # served here, no CDN
    schema = client.get("/openapi.json", cookies={"hl_docs": cookie}).json()
    assert "/vms" in schema["paths"] and "JetonAPI" in schema["components"]["securitySchemes"]
    # "Authorize" takes a user name and password, or an API token.
    assert {"JetonAPI": []} in schema["paths"]["/vms"]["get"]["security"]


def test_a_ticket_is_used_once(client, auth_headers):
    admin = auth_headers("alice")
    url = client.post("/api-docs/ticket", headers=admin).json()["url"]
    assert client.get(url, follow_redirects=False).status_code == 303
    assert client.get(url, follow_redirects=False).status_code == 403


def test_withdrawing_the_access_cuts_an_open_swagger(client, auth_headers):
    admin = auth_headers("alice")
    viewer = auth_headers("olga", role="observateur")
    assert client.put("/api-docs/acces", json={"acces": "tous"}, headers=viewer).status_code == 403
    client.put("/api-docs/acces", json={"acces": "tous"}, headers=admin)
    cookie = _open(client, viewer).cookies.get("hl_docs")
    assert client.get("/openapi.json", cookies={"hl_docs": cookie}).status_code == 200
    client.put("/api-docs/acces", json={"acces": "admins"}, headers=admin)
    assert client.get("/openapi.json", cookies={"hl_docs": cookie}).status_code == 403
    assert client.put("/api-docs/acces", json={"acces": "public"}, headers=admin).status_code == 422
