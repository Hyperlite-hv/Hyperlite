"""A container's web terminal is root with the host's capabilities (no user namespace): administrators only, as the
VM one (GHSA-527j-fwg2-7646). An operator of the container keeps its console log."""


def test_an_operator_of_a_container_gets_no_web_terminal(client, auth_headers, database):
    with database.get_conn() as db:
        db.execute(
            "INSERT INTO acl (subject_type, subject_id, role, resource_type, resource_id) "
            "VALUES ('user', 'bob', 'operator', 'container', 'web')"
        )
        db.commit()
    bob = auth_headers("bob", "viewer")
    r = client.post("/containers/web/terminal-ticket", headers=bob)
    assert r.status_code == 403
