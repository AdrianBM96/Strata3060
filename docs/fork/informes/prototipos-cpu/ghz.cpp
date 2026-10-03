#include <chrono>
#include <cstdio>
int main(){
  auto t0=std::chrono::steady_clock::now();
  unsigned long long x=1;
  for(long i=0;i<2000000000L;i++){ asm volatile("add $1, %0" : "+r"(x)); }
  auto t1=std::chrono::steady_clock::now();
  double s=std::chrono::duration<double>(t1-t0).count();
  printf("dependent adds: %.2f GHz (x=%llu)\n", 2e9/s/1e9, x);
}
