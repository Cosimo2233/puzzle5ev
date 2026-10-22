try {
set root [file normalize [file dirname [info script]]/../..]
open_project $root/puzzle5ev.xpr
if {[get_property PART [current_project]] ne "xczu5ev-sfvc784-2-i"} {error "Wrong part"}
if {[get_property TOP [current_fileset]] ne "system_wrapper"} {error "Wrong top"}
open_bd_design [get_files system.bd]
if {[get_property CONFIG.C_AUX_RESET_HIGH [get_bd_cells hdmi_reset]] != 0 ||
    [get_property CONFIG.CONST_VAL [get_bd_cells fan_on]] != 1 ||
    [get_bd_nets -of_objects [get_bd_pins hdmi_reset/aux_reset_in]] ne
    [get_bd_nets -of_objects [get_bd_pins fan_on/dout]]} {
    error "HDMI scanout is held in reset or auxiliary reset polarity is unverified"
}
set count 0
foreach f [get_files -all] {
    set path [string tolower [file normalize [get_property NAME $f]]]
    if {[string first "puzzle7020" $path] >= 0 || [string first "/26-e/" $path] >= 0} {error "Old source dependency: $path"}
    if {![file exists $path]} {error "Missing project file: $path"}
    incr count
}
foreach repo [get_property IP_REPO_PATHS [current_project]] {
    if {[string first [string tolower $root/] [string tolower [file normalize $repo]]] != 0} {error "External IP repository: $repo"}
}
foreach run {synth_1 impl_1} {
    if {[get_property NEEDS_REFRESH [get_runs $run]]} {error "Stale $run"}
    puts "RUN $run: [get_property STATUS [get_runs $run]]"
}
source $root/scripts/axu5ev/check_hls_abi.tcl
puts "PASS: $count project files exist; no old-workspace source dependencies; part/top/HLS ABI verified"
close_project
} on error {message options} {
    puts stderr [dict get $options -errorinfo]
    exit 1
}
