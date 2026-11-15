# The VM console

Status: implemented (1.28). Replaces the console tab built in 1.18, whose toolbar wrapped over two lines with nine
buttons of equal weight, whose screen sat in a fixed 16:10 box (empty bands beside a 4:3 guest), and whose state,
errors and warnings were spread above and below it.

## Layout

One component (`pages/VmConsole.jsx`) for the VM's Console tab and the console window (`/console/<vm>`):

1. **One toolbar, one row.** On the left, how to connect: *Graphical* (VNC), *Serial*, *SSH*, *Remote desktop* (RDP,
   now inside the console instead of a window of its own). In the middle, the connection's state in words: connected
   with the guest's resolution, connecting, reconnecting in N s, or why it ended. On the right, icon buttons with a
   tooltip, the same order everywhere: *Send keys* (Ctrl+Alt+Del first), *Clipboard*, *Display* (fit to the window,
   original size, ask the guest to follow the window), *Screenshot*, *Full screen*, *Open in a window*, and *More*
   (SPICE file, add a SPICE display, connect from your workstation, reconnect, disconnect). Buttons that do not apply
   to the mode are not shown; buttons that apply but cannot act now are disabled with a reason.
2. **The screen takes the room left.** Its height is the window's below the toolbar (measured, not guessed), the
   guest's picture is scaled to fit and centred on a dark stage, with no fixed aspect ratio. In the window and in full
   screen it takes everything.
3. **States are drawn on the stage**, never as banners around it: the VM is stopped (with *Start* for who may), the
   console is connecting, it ended (the reason, *Reconnect*, the automatic retry's countdown), SSH is for
   administrators, the remote desktop's sign-in form.
4. **The keyboard is visibly captured**: the stage gets the focus ring and a short line says that keys go to the VM
   and how to give them back (click outside, or the window's own shortcut).
5. **Help for the mode** (serial: press Enter for a prompt; SSH: root-equivalent access) is one quiet line under the
   toolbar, which can be closed and stays closed for that mode in this browser.

## Behaviour kept from before

Tickets per connection, the relay through the VM's node, automatic reconnection every 5 s up to a minute (not for
refusals), the guest's clipboard offered to copy, typing text as key presses, SPICE through virt-viewer, the
workstation panel.

## Checks

The console is tried in a real browser against a running VM of a node: each mode, resizing the window, full screen,
the stopped VM, a dropped connection and the reconnection, the remote desktop's sign-in.
