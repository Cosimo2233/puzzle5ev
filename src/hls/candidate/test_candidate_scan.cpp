#include "candidate_scan.h"
#include <cmath>
#include <fstream>
#include <iostream>
#include <limits>
int main() {
    std::ifstream f("candidate_vectors.txt");int cases=0;f>>cases;
    if(!f || cases<1)return 1;
    float input[CANDIDATE_INPUT],output[CANDIDATE_OUTPUT];
    unsigned completed=0,abi=0,status=0,count=0;float maximum=0;
    for(int c=0;c<cases;++c) {
        f>>count;for(int i=0;i<CANDIDATE_INPUT;++i)f>>input[i];
        puzzle_candidate_scan(input,output,c+1,count,completed,abi,status);
        if(status || abi!=CANDIDATE_ABI || completed!=(unsigned)c+1)return 2;
        for(unsigned i=0;i<count*6;++i) {
            float expected;f>>expected;
            if(expected<0){if(output[i]!=-1)return 3;continue;}
            float error=std::fabs(std::sqrt(output[i])-expected);
            if(!std::isfinite(output[i]) || output[i]<0 || error>0.005f) {
                std::cerr<<"case "<<c<<" result "<<i<<" error "<<error<<"\n";return 4;
            }
            if(error>maximum)maximum=error;
        }
    }
    for(unsigned invalid: {0u,65u}) {
        puzzle_candidate_scan(input,output,200,invalid,completed,abi,status);
        if(status!=1 || completed!=200)return 5;
    }
    input[4]=std::numeric_limits<float>::quiet_NaN();
    puzzle_candidate_scan(input,output,201,1,completed,abi,status);
    if(status!=1 || completed!=201)return 6;
    std::cout<<"PASS "<<cases<<" batches; max distance error "<<maximum<<" mm\n";
}
