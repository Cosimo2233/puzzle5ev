#pragma once
static const int CANDIDATE_BATCH = 64;
static const int CANDIDATE_INPUT = 580; // counts[4], vertices[4][8][2], deltas[64][4][2]
static const int CANDIDATE_OUTPUT = 384; // squared distances: 01,02,03,12,13,23
static const unsigned CANDIDATE_ABI = 0x434e0101;
void puzzle_candidate_scan(const float input[CANDIDATE_INPUT],
                          float output[CANDIDATE_OUTPUT], unsigned request_id,
                          unsigned count, unsigned &completed_id,
                          unsigned &abi, unsigned &status);
