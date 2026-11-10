// Reference harness around transparency-dev/merkle (Apache-2.0, commit pinned in the study ledger).
// Reads JSON lines on stdin and answers with the library's own verdicts. It uses only the library's hasher and proof verification.
package main

import (
	"bufio"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"os"

	"github.com/transparency-dev/merkle/proof"
	"github.com/transparency-dev/merkle/rfc6962"
)

type query struct {
	ID        string   `json:"id"`
	Op        string   `json:"op"`
	Leaf      string   `json:"leaf"`
	Index     uint64   `json:"index"`
	Size      uint64   `json:"size"`
	First     uint64   `json:"first"`
	Second    uint64   `json:"second"`
	Path      []string `json:"path"`
	Root      string   `json:"root"`
	FirstHash string   `json:"first_hash"`
	SecondHash string  `json:"second_hash"`
}

func dec(s string) []byte { b, _ := hex.DecodeString(s); return b }
func decAll(ss []string) [][]byte { out := make([][]byte, len(ss)); for i, s := range ss { out[i] = dec(s) }; return out }

func main() {
	sc := bufio.NewScanner(os.Stdin)
	sc.Buffer(make([]byte, 1<<20), 1<<26)
	h := rfc6962.DefaultHasher
	for sc.Scan() {
		var q query
		if err := json.Unmarshal(sc.Bytes(), &q); err != nil { continue }
		var err error
		switch q.Op {
		case "verify_inclusion":
			err = proof.VerifyInclusion(h, q.Index, q.Size, dec(q.Leaf), decAll(q.Path), dec(q.Root))
		case "verify_consistency":
			err = proof.VerifyConsistency(h, q.First, q.Second, decAll(q.Path), dec(q.FirstHash), dec(q.SecondHash))
		}
		out := map[string]any{"id": q.ID, "ok": err == nil}
		if err != nil { out["error"] = err.Error() }
		b, _ := json.Marshal(out)
		fmt.Println(string(b))
	}
}
