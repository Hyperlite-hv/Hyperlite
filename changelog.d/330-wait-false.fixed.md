- A task that succeeded no longer carries the details of its audit entry as its `error` (a clone answered `"error": "clone -> copy"`): `error` is a failure's reason only, which a client of the API tests.

- Deploying a template and making a template from a VM now show in Tasks while their disk is copied; they had no task at all. Two creations or two deployments of a VM under the same name at the same time could write the same disk file: the second is now refused (409), as two clones already were.
