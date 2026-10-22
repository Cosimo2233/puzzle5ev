# Independent UltraScale+ target.
set root [file normalize [file dirname [info script]]/../..]
set part xczu5ev-sfvc784-2-i
if {[info exists env(EC_AXU_HLS_CORES)]} {
    foreach selected [split $env(EC_AXU_HLS_CORES) ,] {
        if {$selected ni {preprocess rigid clearance candidate}} {error "Unknown HLS core: $selected"}
    }
}
foreach {name top folder source test vectors} {
    preprocess puzzle_preprocess {} preprocess.cpp test_preprocess.cpp {}
    rigid puzzle_rigid_fit rigid rigid_fit.cpp test_rigid_fit.cpp rigid_vectors.txt
    clearance puzzle_clearance_scan clearance clearance_scan.cpp test_clearance_scan.cpp clearance_vectors.txt
    candidate puzzle_candidate_scan candidate candidate_scan.cpp test_candidate_scan.cpp candidate_vectors.txt
} {
    if {[info exists env(EC_AXU_HLS_CORES)] && $name ni [split $env(EC_AXU_HLS_CORES) ,]} {continue}
    set input [file join $root src hls $folder]
    set output [file join $root build axu5ev hls_$name]
    open_project -reset $output
    set_top $top
    add_files [file join $input $source]
    add_files -tb [file join $input $test]
    if {$vectors ne ""} {add_files -tb [file join $input $vectors]}
    open_solution -reset solution -flow_target vivado
    set_part $part
    create_clock -period 10
    if {$name in {clearance candidate}} {config_compile -pipeline_loops 0}
    csim_design
    csynth_design
    if {[info exists env(EC_AXU_COSIM)] && $env(EC_AXU_COSIM) eq "1"} {cosim_design -rtl verilog}
    export_design -format ip_catalog
    close_project
}
source $root/scripts/axu5ev/check_hls_abi.tcl
exit
