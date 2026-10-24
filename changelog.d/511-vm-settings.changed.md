- A VM's processors, memory, resource limits, CPU pinning and advanced hardware (disk options, boot order, memory
  ballooning, machine type) are written by `hyperlite-daemon` instead of the API (#511, step 3, VM domain part 2a).
  The daemon checks the fields again and changes the VM's definition itself. The answers are unchanged.
