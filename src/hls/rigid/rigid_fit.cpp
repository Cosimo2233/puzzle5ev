#include "rigid_fit.h"
#include <hls_math.h>

static bool coordinate(float value) {
    // Comparisons also reject NaN and infinities; bounded sums cannot overflow.
    return value >= -4096.0f && value <= 4096.0f;
}

void puzzle_rigid_fit(const float input[RIGID_BATCH * RIGID_INPUT_STRIDE],
                     float output[RIGID_BATCH * RIGID_OUTPUT_STRIDE],
                     unsigned request_id, unsigned count,
                     unsigned &completed_id, unsigned &abi, unsigned &status) {
#pragma HLS INTERFACE s_axilite port=return bundle=control
#pragma HLS INTERFACE s_axilite port=request_id bundle=control offset=0x10
#pragma HLS INTERFACE s_axilite port=count bundle=control offset=0x18
#pragma HLS INTERFACE s_axilite port=completed_id bundle=control offset=0x20
#pragma HLS INTERFACE s_axilite port=abi bundle=control offset=0x28
#pragma HLS INTERFACE s_axilite port=status bundle=control offset=0x30
#pragma HLS INTERFACE s_axilite port=input bundle=control offset=0x1000
#pragma HLS INTERFACE s_axilite port=output bundle=control offset=0x8000
    abi = RIGID_ABI;
    status = 0;
    if (count > RIGID_BATCH || request_id == 0) {
        status = 1;
        completed_id = request_id;
        return;
    }
    for (unsigned job = 0; job < count; ++job) {
#pragma HLS LOOP_TRIPCOUNT min=0 max=64
        const int base = job * RIGID_INPUT_STRIDE;
        const int out = job * RIGID_OUTPUT_STRIDE;
        for (int k = 0; k < RIGID_OUTPUT_STRIDE; ++k) output[out+k] = 0;
        float nf = input[base];
        if (!(nf >= 2 && nf <= RIGID_POINTS)) { output[out] = 1; continue; }
        int n = (int)nf;
        if (nf != (float)n) { output[out] = 1; continue; }
        float sx[8], sy[8], dx[8], dy[8], w[8];
        float sw = 0, scx = 0, scy = 0, dcx = 0, dcy = 0;
        bool valid = true;
        for (int i = 0; i < n; ++i) {
#pragma HLS LOOP_TRIPCOUNT min=2 max=8
            int p = base+1+5*i;
            sx[i]=input[p]; sy[i]=input[p+1]; dx[i]=input[p+2]; dy[i]=input[p+3]; w[i]=input[p+4];
            valid &= coordinate(sx[i]) && coordinate(sy[i]) && coordinate(dx[i]) && coordinate(dy[i]);
            valid &= w[i] >= 0.000001f && w[i] <= 1.0f;
            sw += w[i]; scx += w[i]*sx[i]; scy += w[i]*sy[i]; dcx += w[i]*dx[i]; dcy += w[i]*dy[i];
        }
        if (!valid) { output[out] = 1; continue; }
        scx/=sw; scy/=sw; dcx/=sw; dcy/=sw;
        float dot=0, cross=0, energy=0;
        for (int i = 0; i < n; ++i) {
#pragma HLS LOOP_TRIPCOUNT min=2 max=8
            float x=sx[i]-scx, y=sy[i]-scy, u=dx[i]-dcx, v=dy[i]-dcy;
            dot += w[i]*(x*u+y*v); cross += w[i]*(x*v-y*u);
            energy += w[i]*(x*x+y*y+u*u+v*v);
        }
        float norm = hls::sqrtf(dot*dot+cross*cross);
        // Non-unique / ill-conditioned rotation: preserve CPU SVD semantics.
        if (norm <= 0.000001f || norm <= 0.00001f*energy) { output[out]=2; continue; }
        float c=dot/norm, s=cross/norm;
        float tx=dcx-(c*scx-s*scy), ty=dcy-(s*scx+c*scy);
        float sum=0, maximum=0;
        for (int i = 0; i < n; ++i) {
#pragma HLS LOOP_TRIPCOUNT min=2 max=8
            float ex=c*sx[i]-s*sy[i]+tx-dx[i], ey=s*sx[i]+c*sy[i]+ty-dy[i];
            float squared=ex*ex+ey*ey;
            sum += w[i]*squared;
            if (squared > maximum) maximum=squared;
        }
        float angle=hls::atan2f(s,c)*57.29577951308232f;
        if (angle >= 180.0f) angle-=360.0f;
        output[out+1]=angle; output[out+2]=tx; output[out+3]=ty;
        output[out+4]=hls::sqrtf(sum/sw); output[out+5]=hls::sqrtf(maximum);
    }
    completed_id = request_id;
}
