#include <immintrin.h>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <thread>
#include <vector>
#include <cstring>
int main(int argc,char**argv){
  int nt=argc>1?atoi(argv[1]):1; size_t bytes=1ull<<30;
  std::vector<std::thread> th; std::vector<double> s(nt);
  std::vector<unsigned char*> b(nt);
  for(int i=0;i<nt;i++){ b[i]=(unsigned char*)aligned_alloc(4096,bytes); memset(b[i],1,bytes);}
  auto t0=std::chrono::steady_clock::now();
  for(int i=0;i<nt;i++) th.emplace_back([&,i]{ __m256i acc=_mm256_setzero_si256(); for(int r=0;r<3;r++) for(size_t o=0;o<bytes;o+=32) acc=_mm256_add_epi8(acc,_mm256_load_si256((__m256i*)(b[i]+o))); s[i]=_mm256_extract_epi8(acc,0);});
  for(auto&t:th)t.join();
  double sec=std::chrono::duration<double>(std::chrono::steady_clock::now()-t0).count();
  printf("%d threads read: %.1f GB/s total\n",nt,3.0*bytes*nt/sec/1e9);
}
