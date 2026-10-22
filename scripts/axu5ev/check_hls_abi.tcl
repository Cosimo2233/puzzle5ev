# Usable in Vivado Tcl as well as Vitis HLS; verify actual exported driver maps.
set abi_root [file normalize [file dirname [info script]]/../..]
foreach {abi_name abi_top abi_map} {
    preprocess puzzle_preprocess {ADDR_WIDTH_DATA 0x10 ADDR_HEIGHT_DATA 0x18 ADDR_RATIO_DATA 0x20 ADDR_BLUE_MIN_DATA 0x28 ADDR_BRIGHTNESS_MIN_DATA 0x30 ADDR_FRAME_ID_DATA 0x38 ADDR_COMPLETED_FRAME_ID_DATA 0x40}
    rigid puzzle_rigid_fit {ADDR_INPUT_R_BASE 0x4000 ADDR_OUTPUT_R_BASE 0x8000 ADDR_REQUEST_ID_DATA 0x0010 ADDR_COUNT_DATA 0x0018 ADDR_COMPLETED_ID_DATA 0x0020 ADDR_ABI_DATA 0x0028 ADDR_STATUS_DATA 0x0030}
    clearance puzzle_clearance_scan {ADDR_REQUEST_ID_DATA 0x0010 ADDR_COMPLETED_ID_DATA 0x0020 ADDR_ABI_DATA 0x0028 ADDR_STATUS_DATA 0x0030 ADDR_INPUT_R_BASE 0x1000 ADDR_OUTPUT_R_BASE 0x2000 DEPTH_INPUT_R 76 DEPTH_OUTPUT_R 401}
} {
    set abi_path $abi_root/build/axu5ev/hls_$abi_name/solution/impl/ip/drivers/${abi_top}_v1_0/src/x${abi_top}_hw.h
    set abi_fd [open $abi_path r]
    set abi_text [read $abi_fd]
    close $abi_fd
    foreach {abi_reg abi_value} $abi_map {
        set abi_macro X[string toupper $abi_top]_CONTROL_$abi_reg
        if {![regexp "#define ${abi_macro} +${abi_value}\\y" $abi_text]} {
            error "HLS ABI mismatch: $abi_macro must be $abi_value"
        }
    }
    puts "PASS HLS ABI: $abi_name"
}
