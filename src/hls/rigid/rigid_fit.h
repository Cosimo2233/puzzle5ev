#pragma once

// ABI 1: AXI-Lite resident batch; coordinates in mm, positive angle clockwise
// in image coordinates (y down). No scaling or reflection is fitted.
const unsigned RIGID_ABI = 0x52470101;
const int RIGID_BATCH = 64;
const int RIGID_POINTS = 8;
const int RIGID_INPUT_STRIDE = 41; // count, then 8 * (sx, sy, tx, ty, weight)
const int RIGID_OUTPUT_STRIDE = 6; // status, degrees, tx, ty, weighted RMS, max error

void puzzle_rigid_fit(const float input[RIGID_BATCH * RIGID_INPUT_STRIDE],
                     float output[RIGID_BATCH * RIGID_OUTPUT_STRIDE],
                     unsigned request_id, unsigned count,
                     unsigned &completed_id, unsigned &abi, unsigned &status);
