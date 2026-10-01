import json


def _store():
    from app.repositories import registry

    return registry.objects().sync


def set_container_ssh_user(container_name, username):
    _store().set_container_ssh_user(container_name, username)


def get_container_ssh_user(container_name):
    return _store().container_ssh_user(container_name)


def delete_container_ssh_user(container_name):
    _store().delete_container_ssh_user(container_name)


# Application containers (see container_builder.read_image_config): the image, the process settings and the
# address given to the container, so that a clone or a restore can be defined the same way.


def set_container_app(container_name, image, spec, ip, network):
    _store().set_container_app(container_name, image, json.dumps(spec), ip, network)


def get_container_app(container_name):
    row = _store().container_app(container_name)
    if not row:
        return None
    return {"image": row["image"], "spec": json.loads(row["spec"]), "ip": row["ip"], "network": row["network"]}


def delete_container_app(container_name):
    _store().delete_container_app(container_name)


# Storage pool holding a container's filesystem, when not the default location.


def set_container_storage(container_name, pool, base_dir):
    _store().set_container_storage(container_name, pool, str(base_dir))


def get_container_storage(container_name):
    row = _store().container_storage(container_name)
    return {"pool": row["pool"], "base_dir": row["base_dir"]} if row else None


def delete_container_storage(container_name):
    _store().delete_container_storage(container_name)
