"""The API documentation inside the dashboard: signed-in users only, and whom an administrator chose."""


def test_administrators_only_by_default(client, auth_headers):
    admin = auth_headers("alice")
    viewer = auth_headers("olga", role="observateur")
    assert client.get("/api-docs/acces", headers=admin).json() == {"acces": "admins", "autorise": True}
    assert client.get("/api-docs/acces", headers=viewer).json() == {"acces": "admins", "autorise": False}
    schema = client.get("/api-docs/schema", headers=admin)
    assert schema.status_code == 200 and "/vms" in schema.json()["paths"]
    assert client.get("/api-docs/schema", headers=viewer).status_code == 403
    assert client.get("/api-docs/schema").status_code == 401  # never without a session


def test_an_administrator_opens_it_to_everyone_or_closes_it(client, auth_headers):
    admin = auth_headers("alice")
    viewer = auth_headers("olga", role="observateur")
    assert client.put("/api-docs/acces", json={"acces": "tous"}, headers=viewer).status_code == 403
    assert client.put("/api-docs/acces", json={"acces": "tous"}, headers=admin).json()["acces"] == "tous"
    assert client.get("/api-docs/schema", headers=viewer).status_code == 200
    client.put("/api-docs/acces", json={"acces": "desactive"}, headers=admin)
    assert client.get("/api-docs/schema", headers=admin).status_code == 403
    assert client.put("/api-docs/acces", json={"acces": "public"}, headers=admin).status_code == 422

