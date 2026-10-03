#include "strata/spec/controller.hpp"
#include <cstdio>
using namespace strata::spec;
static const char* nm(Source s){return s==Source::Mtp?"mtp":s==Source::Lookup?"lookup":"none";}
int main(){
  struct Sc{const char* name; CostModel m;} sc[4];
  sc[0].name="repo defaults (5070, hit .55)"; 
  sc[1].name="5070 measured hit .72";  sc[1].m.hit_rate=0.72;
  sc[2].name="3060+AVX2 est: dense x1.7, CPU x1.7, hit .72, sync 3.0, draft 1.9"; sc[2].m.hit_rate=0.72; sc[2].m.dense_ms*=1.7; sc[2].m.cpu_all_miss_ms*=1.7; sc[2].m.sync_ms=3.0; sc[2].m.mtp_draft_ms=1.9;
  sc[3].name="3060 CPU-bound est: dense x1.7, CPU x2.5, hit .72";   sc[3].m.hit_rate=0.72; sc[3].m.dense_ms*=1.7; sc[3].m.cpu_all_miss_ms*=2.5; sc[3].m.sync_ms=3.0; sc[3].m.mtp_draft_ms=1.9;
  for(auto& s: sc){
    std::printf("\n%s\n  step_ms(k): ", s.name);
    for(int k=0;k<=5;++k) std::printf("k=%d %.1f  ",k,s.m.step_ms(k,true));
    std::printf("\n  plain %.1f tok/s\n", 1000.0/s.m.step_ms(0,false));
    for(double p: {0.6,0.7,0.8,0.86,0.9,0.95}){
      Controller c(s.m);
      Choice ch;
      // set all positions to p by observing many windows with expected accept pattern: use mean-field: feed accepted = round(k*p)
      for(int i=0;i<4000;++i){ ch=c.choose(0,0,true); if(ch.source==Source::None) break; int acc = (i%100) < (int)(p*100) ? ch.k : 0; // crude
        // geometric acceptance pattern: accepted prefix length ~ min(k, geometric(p))
        int a=0; double u=(double)((i*2654435761u)%1000)/1000.0; double th=1.0; while(a<ch.k){ th*=p; if(u<th) ++a; else break; }
        (void)acc; c.observe(ch,a,0);} 
      ch=c.choose(0,0,true);
      std::printf("  p=%.2f -> %s k=%d  expected %.2f tok, %.1f tok/s\n",p,nm(ch.source),ch.k,ch.expected_tokens,1000*ch.tokens_per_ms);
    }
  }
}
