// Typing text into a VM's graphical console as key presses. A VNC server has no shared clipboard without an agent in
// the guest, so pasting a password into a login prompt means typing it: each character becomes its X11 keysym
// (what noVNC sends), Enter and Tab their own keys.
export const MAX_TYPED = 2000;
const XK_RETURN = 0xff0d;
const XK_TAB = 0xff09;

export function textToKeysyms(text, lookup) {
  const out = [];
  for (const ch of String(text).slice(0, MAX_TYPED)) {
    if (ch === "\r") continue;
    if (ch === "\n") out.push(XK_RETURN);
    else if (ch === "\t") out.push(XK_TAB);
    else out.push(lookup(ch.codePointAt(0)));
  }
  return out;
}

// Key combinations the browser or the workstation would catch before the VM (Ctrl+Alt+F2 switches the local
// console, Alt+Tab the local window...): sent to the VM from a menu instead.
export const KEY_COMBOS = [
  { id: "ctrlAltF1", keys: [[0xffe3, "ControlLeft"], [0xffe9, "AltLeft"], [0xffbe, "F1"]] },
  { id: "ctrlAltF2", keys: [[0xffe3, "ControlLeft"], [0xffe9, "AltLeft"], [0xffbf, "F2"]] },
  { id: "ctrlAltF7", keys: [[0xffe3, "ControlLeft"], [0xffe9, "AltLeft"], [0xffc4, "F7"]] },
  { id: "altTab", keys: [[0xffe9, "AltLeft"], [0xff09, "Tab"]] },
  { id: "altF4", keys: [[0xffe9, "AltLeft"], [0xffc1, "F4"]] },
  { id: "super", keys: [[0xffeb, "MetaLeft"]] },
  { id: "printScreen", keys: [[0xff61, "PrintScreen"]] },
];

// Presses the keys in order, then releases them in reverse order.
export function sendCombo(rfb, combo) {
  combo.keys.forEach(([sym, code]) => rfb.sendKey(sym, code, true));
  [...combo.keys].reverse().forEach(([sym, code]) => rfb.sendKey(sym, code, false));
}
