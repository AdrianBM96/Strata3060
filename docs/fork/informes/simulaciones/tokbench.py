"""Order-of-magnitude benchmark of tools/strata_tokenizer.py's algorithm with a TOY vocabulary (a greedy BPE trained
here on the repo's own text, 20K merges): real vocab is 247,587 merges, so absolute speed differs a little; the
point is the per-piece cost with and without a memo of the BPE result per pre-token piece."""
import sys, time, collections, pathlib, importlib.util
sys.path.insert(0, "/home/user/Strata3060/tools")
sys.dont_write_bytecode = True
import strata_tokenizer as ST
root = pathlib.Path("/home/user/Strata3060")
files = sorted(list(root.glob("docs/*.md")) + list(root.glob("src/**/*.cpp")) + list(root.glob("serve/*.py")))
text = "\n".join(f.read_text(encoding="utf-8", errors="replace") for f in files)
print("corpus: %d files, %.2f MB" % (len(files), len(text) / 1e6))
# train a toy BPE on unique pre-token pieces (byte-mapped), 20K merges
pieces = collections.Counter()
rx = ST.regex.compile(ST.QWEN35_PATTERN)
for p in rx.findall(text):
    pieces["".join(ST.BYTE_TO_UNICODE[b] for b in p.encode("utf-8"))] += 1
top = [w for w, _ in pieces.most_common(12000)]
words = {w: list(w) for w in top}
merges = []
vocab = [ST.BYTE_TO_UNICODE[b] for b in range(256)]
for it in range(3000):
    cnt = collections.Counter()
    for w, sym in words.items():
        c = pieces[w]
        for i in range(len(sym) - 1): cnt[(sym[i], sym[i + 1])] += c
    if not cnt: break
    (a, b), n = cnt.most_common(1)[0]
    if n < 2: break
    merges.append(a + " " + b); vocab.append(a + b)
    for w, sym in words.items():
        i = 0; out = []
        while i < len(sym):
            if i < len(sym) - 1 and sym[i] == a and sym[i + 1] == b: out.append(a + b); i += 2
            else: out.append(sym[i]); i += 1
        words[w] = out
    if it % 1000 == 0: print("  merge", it)
print("merges:", len(merges))
vocab = list(dict.fromkeys(vocab))
tok = ST.Tokenizer(vocab, merges)
sample = text[:800_000]
t0 = time.perf_counter(); ids = tok.encode(sample); t1 = time.perf_counter()
print("baseline encode: %d chars -> %d tokens in %.2f s = %.0f tok/s" % (len(sample), len(ids), t1 - t0, len(ids) / (t1 - t0)))
# memoised variant (same algorithm, cache keyed by the byte-mapped piece)
class Memo(ST.Tokenizer):
    def __init__(self, *a, **k):
        super().__init__(*a, **k); self._memo = {}
    def _encode_plain(self, text):
        out = []; memo = self._memo; ids = self.ids
        for piece in self._re.findall(text):
            r = memo.get(piece)
            if r is None:
                mapped = "".join(ST.BYTE_TO_UNICODE[b] for b in piece.encode("utf-8"))
                r = [ids[t] for t in self._bpe(mapped)]
                if len(memo) < 400000: memo[piece] = r
            out.extend(r)
        return out
tok2 = Memo(vocab, merges)
t0 = time.perf_counter(); ids2 = tok2.encode(sample); t1 = time.perf_counter()
print("memoised encode: %.2f s = %.0f tok/s, identical ids: %s" % (t1 - t0, len(ids2) / (t1 - t0), ids2 == ids))
t0 = time.perf_counter(); ids3 = tok2.encode(sample); t1 = time.perf_counter()
print("memoised, warm second pass: %.2f s = %.0f tok/s, identical: %s" % (t1 - t0, len(ids3) / (t1 - t0), ids3 == ids))
