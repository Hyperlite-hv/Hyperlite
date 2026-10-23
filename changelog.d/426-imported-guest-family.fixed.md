- A VM created from an imported disk with an explicit system family (`guest_os`) now gets that family's devices:
  an imported Windows guest had VirtIO disks and card, which Windows without VirtIO drivers does not boot from. An
  imported disk's name that leaves its directory is refused with a 422 instead of an error 500 (#426).
