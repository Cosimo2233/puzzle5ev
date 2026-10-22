#include "candidate_scan.h"

static float cross(float ax,float ay,float bx,float by) {
#pragma HLS INLINE
    return ax*by-ay*bx;
}

static bool contains(const float x[8],const float y[8],int n,float px,float py) {
#pragma HLS INLINE off
    bool inside=false;
    for(int i=0;i<8;++i) {
#pragma HLS PIPELINE II=2
        if(i<n) {
            int j=i+1==n?0:i+1;
            float ex=x[j]-x[i],ey=y[j]-y[i];
            float c=cross(ex,ey,px-x[i],py-y[i]);
            if((y[i]>py)!=(y[j]>py))
                if((ey>0 && c>0)||(ey<0 && c<0))inside=!inside;
        }
    }
    return inside;
}

static bool intersects(float ax,float ay,float bx,float by,
                       float cx,float cy,float dx,float dy) {
#pragma HLS INLINE
    float c1=cross(bx-ax,by-ay,cx-ax,cy-ay),c2=cross(bx-ax,by-ay,dx-ax,dy-ay);
    float c3=cross(dx-cx,dy-cy,ax-cx,ay-cy),c4=cross(dx-cx,dy-cy,bx-cx,by-cy);
    bool sides=((c1<=0 && c2>=0)||(c1>=0 && c2<=0)) &&
               ((c3<=0 && c4>=0)||(c3>=0 && c4<=0));
    float aminx=ax<bx?ax:bx,amaxx=ax>bx?ax:bx,aminy=ay<by?ay:by,amaxy=ay>by?ay:by;
    float bminx=cx<dx?cx:dx,bmaxx=cx>dx?cx:dx,bminy=cy<dy?cy:dy,bmaxy=cy>dy?cy:dy;
    return sides && aminx<=bmaxx && bminx<=amaxx && aminy<=bmaxy && bminy<=amaxy;
}

static float distance(const float x[4][8],const float y[4][8],
                      const float ex[4][8],const float ey[4][8],
                      const float inv[4][8],const int n[4],
                      int a,int b,float dx,float dy) {
#pragma HLS INLINE off
    // Translate A relative to B. Ray crossing works for either winding and
    // concave polygons; SAT from the legacy convex-only scan is not used.
    if(n[a]==0 || n[b]==0)return -1.0f;
    bool overlap=contains(x[b],y[b],n[b],x[a][0]+dx,y[a][0]+dy) ||
                 contains(x[a],y[a],n[a],x[b][0]-dx,y[b][0]-dy);
    float best=1.0e20f;
    for(int side=0;side<2;++side) {
        int e=side==0?a:b,p=side==0?b:a;
        float ox=side==0?dx:-dx,oy=side==0?dy:-dy;
        for(int i=0;i<8;++i) {
            for(int j=0;j<8;++j) {
#pragma HLS PIPELINE II=2
                if(i<n[e] && j<n[p]) {
                    int next=j+1==n[p]?0:j+1;
                    float ax=x[e][i]+ox,ay=y[e][i]+oy,px=x[p][j],py=y[p][j];
                    float vx=px-ax,vy=py-ay;
                    float t=(vx*ex[e][i]+vy*ey[e][i])*inv[e][i];
                    t=t<0?0:(t>1?1:t);
                    vx-=t*ex[e][i];vy-=t*ey[e][i];
                    float d=vx*vx+vy*vy;
                    if(d<best)best=d;
                    if(side==0 && intersects(ax,ay,ax+ex[e][i],ay+ey[e][i],
                                             px,py,x[p][next],y[p][next]))overlap=true;
                }
            }
        }
    }
    return overlap?0.0f:best;
}

void puzzle_candidate_scan(const float input[CANDIDATE_INPUT],
                          float output[CANDIDATE_OUTPUT], unsigned request_id,
                          unsigned count,unsigned &completed_id,unsigned &abi,unsigned &status) {
#pragma HLS INTERFACE s_axilite port=return bundle=control
#pragma HLS INTERFACE s_axilite port=request_id bundle=control offset=0x10
#pragma HLS INTERFACE s_axilite port=count bundle=control offset=0x18
#pragma HLS INTERFACE s_axilite port=completed_id bundle=control offset=0x20
#pragma HLS INTERFACE s_axilite port=abi bundle=control offset=0x28
#pragma HLS INTERFACE s_axilite port=status bundle=control offset=0x30
#pragma HLS INTERFACE s_axilite port=input bundle=control offset=0x1000
#pragma HLS INTERFACE s_axilite port=output bundle=control offset=0x2000
    abi=CANDIDATE_ABI;status=0;
    bool valid=request_id!=0 && count>=1 && count<=CANDIDATE_BATCH;
    int n[4];float x[4][8],y[4][8],ex[4][8],ey[4][8],inv[4][8];
#pragma HLS ARRAY_PARTITION variable=n complete
#pragma HLS ARRAY_PARTITION variable=x complete dim=1
#pragma HLS ARRAY_PARTITION variable=y complete dim=1
#pragma HLS ARRAY_PARTITION variable=ex complete dim=1
#pragma HLS ARRAY_PARTITION variable=ey complete dim=1
#pragma HLS ARRAY_PARTITION variable=inv complete dim=1
    bool ended=false;
    for(int p=0;p<4;++p) {
        float k=input[p];
        n[p]=(k>=3 && k<=8)?(int)k:0;
        valid &= k==(float)n[p] && (!ended || n[p]==0) && (p>=2 || n[p]!=0);
        ended |= n[p]==0;
        for(int i=0;i<8;++i) {
            x[p][i]=input[4+16*p+2*i];y[p][i]=input[5+16*p+2*i];
            if(i<n[p])valid &= x[p][i]>=-512 && x[p][i]<=512 && y[p][i]>=-512 && y[p][i]<=512;
        }
        for(int i=0;i<8;++i) {
            int j=i+1==n[p]?0:i+1;
            if(i<n[p]) {
                ex[p][i]=x[p][j]-x[p][i];ey[p][i]=y[p][j]-y[p][i];
                float sq=ex[p][i]*ex[p][i]+ey[p][i]*ey[p][i];
                valid &= sq>=1.0e-6f;
                inv[p][i]=sq>=1.0e-6f?1.0f/sq:0;
            }else{ex[p][i]=0;ey[p][i]=0;inv[p][i]=0;}
        }
    }
    if(!valid){status=1;completed_id=request_id;return;}
    // Validate the whole active input before producing any results.
    for(unsigned k=0;k<count;++k)
        for(int p=0;p<8;++p) {
#pragma HLS PIPELINE II=1
            float v=input[68+8*k+p];valid &= v>=-128 && v<=128;
        }
    if(!valid){status=1;completed_id=request_id;return;}
    for(unsigned k=0;k<count;++k) {
        int pair=0;
        for(int a=0;a<4;++a)for(int b=a+1;b<4;++b) {
            float dx=input[68+8*k+2*a]-input[68+8*k+2*b];
            float dy=input[69+8*k+2*a]-input[69+8*k+2*b];
            output[6*k+pair++]=distance(x,y,ex,ey,inv,n,a,b,dx,dy);
        }
    }
    completed_id=request_id;
}
