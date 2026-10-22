#include <ap_int.h>
#include <ap_axi_sdata.h>
#include <hls_stream.h>

typedef ap_axiu<32,0,0,0> rgb_word;
typedef ap_axiu<8,0,0,0> mask_word;
typedef ap_uint<1> bit;
static const int MAX_WIDTH = 1280;

static void threshold(hls::stream<rgb_word>& src, hls::stream<bit>& dst,
                      int width, int height, unsigned ratio, unsigned blue_min, unsigned brightness_min) {
    for (int i=0; i<width*height; ++i) {
#pragma HLS PIPELINE II=1
        rgb_word word = src.read();
        unsigned b=word.data.range(7,0), g=word.data.range(15,8), r=word.data.range(23,16);
        unsigned rg = r > g ? r : g;
        unsigned brightness = b > rg ? b : rg;
        bool blue = b >= blue_min && b*256 > rg*ratio;
        dst.write(!blue && brightness >= brightness_min);
    }
}

// OP: 0 majority, 1 erosion, 2 dilation. ID creates independent line memories.
template<int OP, int ID>
static void window3(hls::stream<bit>& src, hls::stream<bit>& dst, int width, int height) {
    bit rows[2][MAX_WIDTH+2];
#pragma HLS ARRAY_PARTITION variable=rows complete dim=1
#pragma HLS BIND_STORAGE variable=rows type=ram_2p impl=bram
    bit window[3][3];
#pragma HLS ARRAY_PARTITION variable=window complete dim=0
    for (int y=0; y<height+2; ++y) {
        for (int x=0; x<width+2; ++x) {
#pragma HLS PIPELINE II=1
            bit p = (y>0 && y<=height && x>0 && x<=width) ? src.read() : bit(0);
            bit a = y>=2 ? rows[0][x] : bit(0);
            bit b = y>=1 ? rows[1][x] : bit(0);
            rows[0][x] = b;
            rows[1][x] = p;
            for (int row=0; row<3; ++row) {
#pragma HLS UNROLL
                window[row][0] = x ? window[row][1] : bit(0);
                window[row][1] = x ? window[row][2] : bit(0);
            }
            window[0][2]=a; window[1][2]=b; window[2][2]=p;
            unsigned count=0;
            for (int row=0; row<3; ++row) {
#pragma HLS UNROLL
                for (int col=0; col<3; ++col) {
#pragma HLS UNROLL
                    count += window[row][col];
                }
            }
            if (y>=2 && x>=2) dst.write(OP==0 ? count>=5 : OP==1 ? count==9 : count!=0);
        }
    }
}

static void pack(hls::stream<bit>& src, hls::stream<mask_word>& dst, int count) {
    for (int i=0; i<count; ++i) {
#pragma HLS PIPELINE II=1
        mask_word word;
        word.data=src.read() ? 255 : 0; word.keep=1; word.strb=1;
        word.last=(i==count-1); // DMA packet is one frame, NOT one scan line.
        dst.write(word);
    }
}

static void pipeline(hls::stream<rgb_word>& src, hls::stream<mask_word>& dst,
    unsigned width, unsigned height, unsigned ratio, unsigned blue_min, unsigned brightness_min) {
    hls::stream<bit> s0, s1, s2, s3, s4, s5;
#pragma HLS STREAM variable=s0 depth=4096
#pragma HLS STREAM variable=s1 depth=4096
#pragma HLS STREAM variable=s2 depth=4096
#pragma HLS STREAM variable=s3 depth=4096
#pragma HLS STREAM variable=s4 depth=4096
#pragma HLS STREAM variable=s5 depth=4096
#pragma HLS DATAFLOW
    threshold(src,s0,width,height,ratio,blue_min,brightness_min);
    window3<0,0>(s0,s1,width,height);
    window3<1,1>(s1,s2,width,height);
    window3<2,2>(s2,s3,width,height);
    window3<2,3>(s3,s4,width,height);
    window3<1,4>(s4,s5,width,height);
    pack(s5,dst,width*height);
}

void puzzle_preprocess(hls::stream<rgb_word>& src, hls::stream<mask_word>& dst,
    unsigned width, unsigned height, unsigned ratio, unsigned blue_min,
    unsigned brightness_min, unsigned frame_id, unsigned& completed_frame_id) {
#pragma HLS INTERFACE axis port=src
#pragma HLS INTERFACE axis port=dst
#pragma HLS INTERFACE s_axilite port=width bundle=control offset=0x10
#pragma HLS INTERFACE s_axilite port=height bundle=control offset=0x18
#pragma HLS INTERFACE s_axilite port=ratio bundle=control offset=0x20
#pragma HLS INTERFACE s_axilite port=blue_min bundle=control offset=0x28
#pragma HLS INTERFACE s_axilite port=brightness_min bundle=control offset=0x30
#pragma HLS INTERFACE s_axilite port=frame_id bundle=control offset=0x38
#pragma HLS INTERFACE s_axilite port=completed_frame_id bundle=control offset=0x40
#pragma HLS INTERFACE s_axilite port=return bundle=control
    if (width<1 || width>1280 || height<1 || height>720 || ratio>1023 || blue_min>255 || brightness_min>255) {
        completed_frame_id=0xffffffff;
        return;
    }
    pipeline(src,dst,width,height,ratio,blue_min,brightness_min);
    completed_frame_id=frame_id;
}
