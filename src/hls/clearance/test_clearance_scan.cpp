#include "clearance_scan.h"
#include <cmath>
#include <fstream>
#include <iostream>
#include <limits>
int main() {
    std::ifstream f("clearance_vectors.txt"); int cases=0;f>>cases;
    if(!f || cases<1)return 1;
    float input[CLEARANCE_INPUT], output[CLEARANCE_SAMPLES];
    unsigned completed=0,abi=0,status=0;
    float maximum=0;
    for(int c=0;c<cases;++c) {
        for(int i=0;i<CLEARANCE_INPUT;++i)f>>input[i];
        puzzle_clearance_scan(input,output,c+1,completed,abi,status);
        if(status || abi!=CLEARANCE_ABI || completed!=(unsigned)c+1)return 2;
        for(int k=0;k<CLEARANCE_SAMPLES;++k) {
            float expected;f>>expected;
            float error=std::fabs(std::sqrt(output[k])-expected);
            if(!std::isfinite(output[k]) || output[k]<0 || error>0.005f) {
                std::cerr<<"case "<<c<<" sample "<<k<<" error "<<error<<"\n";return 3;
            }
            if(error>maximum)maximum=error;
        }
    }
    input[0]=2;
    puzzle_clearance_scan(input,output,100,completed,abi,status);
    if(status!=1 || completed!=100)return 4;
    input[0]=4;input[12]=std::numeric_limits<float>::quiet_NaN();
    puzzle_clearance_scan(input,output,101,completed,abi,status);
    if(status!=1 || completed!=101)return 5;
    puzzle_clearance_scan(input,output,0,completed,abi,status);
    if(status!=1 || completed!=0)return 6;
    std::cout<<"PASS "<<cases<<" scans; max distance error "<<maximum<<" mm\n";
    return 0;
}
