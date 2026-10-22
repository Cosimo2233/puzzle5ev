#include "rigid_fit.h"
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <limits>

static float input[RIGID_BATCH*RIGID_INPUT_STRIDE];
static float output[RIGID_BATCH*RIGID_OUTPUT_STRIDE];
static void require(bool ok, const char *what) { if (!ok) { std::printf("FAIL %s\n",what); std::exit(1); } }
int main() {
    unsigned done=0, abi=0, status=0;
    // Maximum batch, all supported point counts, nonuniform weights, wraparound.
    for (int j=0;j<RIGID_BATCH;++j) {
        int n=2+j%7, base=j*RIGID_INPUT_STRIDE;
        input[base]=(float)n;
        double angle=(-180.0+j*5.7)*3.141592653589793/180.0;
        for(int i=0;i<n;++i) {
            double x=3.7*i*i-22, y=17.2*i+3;
            int p=base+1+5*i;
            input[p]=x; input[p+1]=y;
            input[p+2]=std::cos(angle)*x-std::sin(angle)*y+63;
            input[p+3]=std::sin(angle)*x+std::cos(angle)*y-74;
            input[p+4]=0.2f+0.1f*i;
        }
    }
    for(unsigned request=1;request<=2;++request) {
        puzzle_rigid_fit(input,output,request,RIGID_BATCH,done,abi,status);
        require(done==request && abi==RIGID_ABI && status==0,"header");
        for(int j=0;j<RIGID_BATCH;++j) {
            int out=j*6;
            require(output[out]==0,"fit status");
            double diff=std::remainder(output[out+1]-(-180.0+j*5.7),360.0);
            require(std::fabs(diff)<0.002,"angle");
            require(std::fabs(output[out+2]-63)<0.002 && std::fabs(output[out+3]+74)<0.002,"translation");
            require(output[out+4]<0.002 && output[out+5]<0.002,"residual");
        }
    }
    input[0]=1;
    input[RIGID_INPUT_STRIDE]=3;
    for(int i=0;i<3;++i) for(int k=0;k<5;++k) input[RIGID_INPUT_STRIDE+1+5*i+k]=1;
    input[2*RIGID_INPUT_STRIDE+1]=std::numeric_limits<float>::quiet_NaN();
    puzzle_rigid_fit(input,output,3,3,done,abi,status);
    require(done==3 && output[0]==1 && output[6]==2 && output[12]==1,"invalid/degenerate");
    puzzle_rigid_fit(input,output,4,65,done,abi,status);
    require(done==4 && status==1,"batch overflow");
    puzzle_rigid_fit(input,output,5,0,done,abi,status);
    require(done==5 && status==0,"empty probe");
    puzzle_rigid_fit(input,output,0,0,done,abi,status);
    require(done==0 && status==1,"zero request");
    FILE *vectors=std::fopen("rigid_vectors.txt","r");
    require(vectors!=NULL,"SVD golden vectors available");
    int jobs=0;
    require(std::fscanf(vectors,"%d",&jobs)==1 && jobs==64,"golden count");
    double expected[64][5];
    for(int j=0;j<jobs;++j) {
        int n=0, base=j*RIGID_INPUT_STRIDE;
        require(std::fscanf(vectors,"%d",&n)==1,"golden points");
        input[base]=n;
        for(int i=0;i<5*n;++i) require(std::fscanf(vectors,"%f",&input[base+1+i])==1,"golden input");
        for(int k=0;k<5;++k) require(std::fscanf(vectors,"%lf",&expected[j][k])==1,"golden output");
    }
    std::fclose(vectors);
    puzzle_rigid_fit(input,output,6,jobs,done,abi,status);
    require(done==6 && status==0,"golden batch");
    double maximum_error=0;
    for(int j=0;j<jobs;++j) {
        require(output[j*6]==0,"golden fit status");
        for(int k=0;k<5;++k) {
            double error=output[j*6+1+k]-expected[j][k];
            if(k==0) error=std::remainder(error,360.0);
            maximum_error=std::fmax(maximum_error,std::fabs(error));
            if(std::fabs(error)>=0.01) std::printf("job %d field %d error %g\n",j,k,error);
            require(std::fabs(error)<0.01,"float64 SVD comparison (0.01 mm/degree)");
        }
    }
    std::printf("SVD golden maximum absolute field error: %.9g\n",maximum_error);
    std::puts("PASS rigid fit protocol and numeric cases");
    return 0;
}
