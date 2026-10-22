#pragma once
static const int CLEARANCE_POINTS = 8;
static const int CLEARANCE_INPUT = 76;
static const int CLEARANCE_SAMPLES = 401;
static const unsigned CLEARANCE_ABI = 0x43440101;
void puzzle_clearance_scan(const float input[CLEARANCE_INPUT],
                          float output[CLEARANCE_SAMPLES], unsigned request_id,
                          unsigned &completed_id, unsigned &abi, unsigned &status);
