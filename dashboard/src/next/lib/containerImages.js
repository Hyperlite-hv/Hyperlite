export const NAME_RE = /^[a-zA-Z0-9][a-zA-Z0-9-]{1,62}$/;

// Any Docker Hub / OCI reference already worked through the free-text field; the gallery only
// presents the common ones. An empty key is the local Debian 12 base (fastest to create).
export const GALLERY = [
  ["", "Debian 12", "ct.g.debian"], ["ubuntu:24.04", "Ubuntu", "ct.g.ubuntu"], ["alpine:3.19", "Alpine", "ct.g.alpine"],
  ["nginx:latest", "Nginx", "ct.g.nginx"], ["httpd:latest", "Apache", "ct.g.apache"], ["postgres:16", "PostgreSQL", "ct.g.db"],
  ["mysql:8", "MySQL", "ct.g.db"], ["mariadb:11", "MariaDB", "ct.g.db"], ["mongo:latest", "MongoDB", "ct.g.doc"],
  ["redis:latest", "Redis", "ct.g.cache"], ["node:22", "Node.js", "ct.g.runtime"], ["python:3.12", "Python", "ct.g.runtime"],
  ["wordpress:latest", "WordPress", "ct.g.cms"],
];

