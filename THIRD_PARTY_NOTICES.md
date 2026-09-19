# Third-party notices

Hyperlite includes or depends on third-party software. Each component is provided under its own license, which applies to that component and is not replaced by the Hyperlite license.

## Vendored in this repository

| Component | Location | License |
|---|---|---|
| noVNC (HTML5 VNC client) | `dashboard/public/novnc/core/` | MPL-2.0 (see the header of each file). Source: https://github.com/novnc/noVNC |
| pako (zlib port, used by noVNC) | `dashboard/public/novnc/vendor/pako/` | MIT and Zlib. Source: https://github.com/nodeca/pako |
| xterm.js and its fit addon | `dashboard/public/xterm/` | MIT. Source: https://github.com/xtermjs/xterm.js |

The exact upstream versions of these vendored copies were not recorded when they were added. When updating them, record the version here and include the upstream license files.

## Dependencies installed from package registries

Python packages are listed in `requirements.txt` and JavaScript packages in `dashboard/package.json` and `dashboard/package-lock.json`. Their licenses are those published by their authors on PyPI and npm (mainly MIT, BSD, Apache-2.0 and LGPL for `libvirt-python`).

## Fonts

The dashboard loads the *Archivo* and *IBM Plex Mono* fonts from Google Fonts at runtime (SIL Open Font License 1.1). They are not redistributed in this repository.
