#include <ap_int.h>
#include <ap_axi_sdata.h>
#include <hls_stream.h>
typedef ap_axiu<32,0,0,0> rgb_word;
typedef ap_axiu<8,0,0,0> mask_word;
void puzzle_preprocess(hls::stream<rgb_word>&,hls::stream<mask_word>&,
    unsigned,unsigned,unsigned,unsigned,unsigned,unsigned,unsigned&);
#include <vector>
#include <cassert>
#include <cstdio>
static std::vector<unsigned> spatial(const std::vector<unsigned>& in,int w,int h,int op) {
    std::vector<unsigned> out(w*h);
    for(int y=0;y<h;y++) for(int x=0;x<w;x++) {
        unsigned n=0;
        for(int dy=-1;dy<=1;dy++) for(int dx=-1;dx<=1;dx++)
            if(y+dy>=0 && y+dy<h && x+dx>=0 && x+dx<w) n+=in[(y+dy)*w+x+dx];
        out[y*w+x]=op==0 ? n>=5 : op==1 ? n==9 : n>0;
    }
    return out;
}
int main() {
    unsigned seed=17;
    const int sizes[][2]={{1,1},{2,7},{32,21},{292,207},{1280,720},{7,3}};
    for(unsigned test=0;test<6;test++) {
        int w=sizes[test][0],h=sizes[test][1];
        hls::stream<rgb_word> src; hls::stream<mask_word> dst;
        std::vector<unsigned> ref(w*h);
        for(int i=0;i<w*h;i++) {
            seed=1664525*seed+1013904223;
            unsigned b=seed&255,g=(seed>>8)&255,r=(seed>>16)&255;
            if(test==3 && i%w>40 && i%w<200 && i/w>40 && i/w<160) b=g=r=240;
            rgb_word v;v.data=b|(g<<8)|(r<<16);v.keep=15;v.strb=15;v.last=(i==w*h-1);src.write(v);
            unsigned rg=r>g?r:g,br=b>rg?b:rg;
            ref[i]=!(b>=45 && b*256>rg*300) && br>=30;
        }
        for(int op: {0,1,2,2,1}) ref=spatial(ref,w,h,op);
        unsigned completed=0;
        puzzle_preprocess(src,dst,w,h,300,45,30,test+1,completed);
        assert(completed==test+1 && src.empty());
        for(int i=0;i<w*h;i++) {
            auto v=dst.read();assert(v.data==(ref[i]?255:0));
            assert(v.last==(i==w*h-1) && v.keep==1);
        }
        assert(dst.empty());
    }
    puts("PASS: six frames, borders, changing dimensions, pixel reference, TLAST");
}
