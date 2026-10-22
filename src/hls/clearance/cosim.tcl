# Compatibility entry: all AXU5EV cores use the authoritative build script.
set env(EC_AXU_COSIM) 1
source [file normalize [file dirname [info script]]/../../../scripts/axu5ev/build_hls.tcl]
