# rosdep-with-aur
Rosdep keys for arch linux packages, including AUR packages

Just drop `10-arch.list` into `/etc/ros/rosdep/sources.list.d` and unlock a few more rosdep keys from the official Arch repos and the AUR

## Generate rosdep keys

Execute `scripts/check-missing.py` in the root directory to update `arch-with-aur.yaml`

## TODOs, Ideas, Roadmap

 * REP 111 compliance (fix complains regarding pip)
 * generate rosdep yaml file automatically with GitHub actions or other CI
 * Repology is slow at this scale. Consider matching files contained in the packages
 * As Repology operates on projects instead of packages, way too many packages may be emitted (e.g. Qt)
 * Create separate lists for official repositories and AUR
 * Merge information from previous rosdep list to keep manual changes
