#include "clearance_scan.h"

static float point_segment(float px, float py, float ax, float ay,
                           float ex, float ey, float inverse_length) {
#pragma HLS INLINE
    float dx=px-ax, dy=py-ay;
    float t=(dx*ex+dy*ey)*inverse_length;
    t=t<0 ? 0 : (t>1 ? 1 : t);
    dx-=t*ex; dy-=t*ey;
    return dx*dx+dy*dy;
}

// Convex polygons only, in positive signed-area order. Relative translations
// avoid needlessly large world coordinates. No geometry is accepted here:
// PS uses these distances only to reject coarse candidates, then verifies GEOS.
static float pair_distance(const float x[4][8], const float y[4][8],
                           const float ex[4][8], const float ey[4][8],
                           const float inv[4][8], const int n[4],
                           const float cx[4], const float cy[4],
                           int a,int b,float alpha) {
#pragma HLS INLINE off
    float dx=alpha*(cx[a]-cx[b]), dy=alpha*(cy[a]-cy[b]);
    float best=1.0e20f;
    bool separated=false;
    for(int side=0;side<2;++side) {
        int edge_poly=side==0?a:b, point_poly=side==0?b:a;
        float ox=side==0?dx:-dx, oy=side==0?dy:-dy;
        for(int i=0;i<8;++i) {
            bool outside=true;
            for(int j=0;j<8;++j) {
#pragma HLS PIPELINE II=2
                if(i<n[edge_poly] && j<n[point_poly]) {
                    float ax=x[edge_poly][i]+ox, ay=y[edge_poly][i]+oy;
                    float px=x[point_poly][j], py=y[point_poly][j];
                    float cross=ex[edge_poly][i]*(py-ay)-ey[edge_poly][i]*(px-ax);
                    if(cross>=0)outside=false;
                    float d=point_segment(px,py,ax,ay,ex[edge_poly][i],ey[edge_poly][i],inv[edge_poly][i]);
                    if(d<best)best=d;
                }
            }
            if(i<n[edge_poly] && outside)separated=true;
        }
    }
    return separated?best:0.0f;
}

void puzzle_clearance_scan(const float input[CLEARANCE_INPUT],
                          float output[CLEARANCE_SAMPLES], unsigned request_id,
                          unsigned &completed_id, unsigned &abi, unsigned &status) {
#pragma HLS INTERFACE s_axilite port=return bundle=control
#pragma HLS INTERFACE s_axilite port=request_id bundle=control offset=0x10
#pragma HLS INTERFACE s_axilite port=completed_id bundle=control offset=0x20
#pragma HLS INTERFACE s_axilite port=abi bundle=control offset=0x28
#pragma HLS INTERFACE s_axilite port=status bundle=control offset=0x30
#pragma HLS INTERFACE s_axilite port=input bundle=control offset=0x1000
#pragma HLS INTERFACE s_axilite port=output bundle=control offset=0x2000
#pragma HLS ALLOCATION instances=pair_distance limit=2 function
    abi=CLEARANCE_ABI; status=0;
    int n[4];float x[4][8],y[4][8],ex[4][8],ey[4][8],inv[4][8],cx[4],cy[4];
#pragma HLS ARRAY_PARTITION variable=n complete
#pragma HLS ARRAY_PARTITION variable=x complete dim=1
#pragma HLS ARRAY_PARTITION variable=y complete dim=1
#pragma HLS ARRAY_PARTITION variable=ex complete dim=1
#pragma HLS ARRAY_PARTITION variable=ey complete dim=1
#pragma HLS ARRAY_PARTITION variable=inv complete dim=1
    bool valid=request_id!=0;
    for(int p=0;p<4;++p) {
        float count=input[p];
        n[p]=(count>=3 && count<=8)?(int)count:3;
        valid &= count==(float)n[p];
        cx[p]=input[4+2*p];cy[p]=input[5+2*p];
        valid &= cx[p]>=-512 && cx[p]<=512 && cy[p]>=-512 && cy[p]<=512;
        for(int i=0;i<8;++i) {
            x[p][i]=input[12+16*p+2*i];y[p][i]=input[13+16*p+2*i];
            if(i<n[p])valid &= x[p][i]>=-512 && x[p][i]<=512 && y[p][i]>=-512 && y[p][i]<=512;
        }
        for(int i=0;i<8;++i) {
            int j=i+1==n[p]?0:i+1;
            if(i<n[p]) {
                ex[p][i]=x[p][j]-x[p][i];ey[p][i]=y[p][j]-y[p][i];
                float squared=ex[p][i]*ex[p][i]+ey[p][i]*ey[p][i];
                valid &= squared>=1;
                inv[p][i]=squared>=1?1.0f/squared:0;
            } else {ex[p][i]=0;ey[p][i]=0;inv[p][i]=0;}
        }
        for(int i=0;i<8;++i) {
            int j=i+1==n[p]?0:i+1;
            if(i<n[p])valid &= ex[p][i]*ey[p][j]-ey[p][i]*ex[p][j]>0;
        }
    }
    if(!valid){status=1;completed_id=request_id;return;}
    for(int k=0;k<CLEARANCE_SAMPLES;++k) {
        float alpha=(float)k/400.0f;
        float d01=pair_distance(x,y,ex,ey,inv,n,cx,cy,0,1,alpha);
        float d02=pair_distance(x,y,ex,ey,inv,n,cx,cy,0,2,alpha);
        float d03=pair_distance(x,y,ex,ey,inv,n,cx,cy,0,3,alpha);
        float d12=pair_distance(x,y,ex,ey,inv,n,cx,cy,1,2,alpha);
        float d13=pair_distance(x,y,ex,ey,inv,n,cx,cy,1,3,alpha);
        float d23=pair_distance(x,y,ex,ey,inv,n,cx,cy,2,3,alpha);
        float best=d01;
        if(d02<best)best=d02;if(d03<best)best=d03;if(d12<best)best=d12;
        if(d13<best)best=d13;if(d23<best)best=d23;
        output[k]=best;
    }
    completed_id=request_id;
}
