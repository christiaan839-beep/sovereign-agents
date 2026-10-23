// Copyright 2026 Christiaan de Wet / Sovereign Matrix
// SPDX-License-Identifier: Apache-2.0

// Command go_verifier checks one v2 receipt with the Go verifier from
// verifiable-receipts (packages/verifiable-receipts-go).
//
// Usage: go_verifier <ed25519.pem> <receipt.json>
// Prints "ok" and exits 0, or prints "rejected: <reason>" and exits 1.
// Exits 2 on a usage or file error.
package main

import (
	"encoding/json"
	"fmt"
	"os"

	vaos "github.com/christiaan839-beep/verifiable-receipts/packages/verifiable-receipts-go"
)

func main() {
	if len(os.Args) != 3 {
		fmt.Fprintln(os.Stderr, "usage: go_verifier <ed25519.pem> <receipt.json>")
		os.Exit(2)
	}
	pemBytes, err := os.ReadFile(os.Args[1])
	if err != nil {
		fmt.Fprintln(os.Stderr, "error:", err)
		os.Exit(2)
	}
	pub, err := vaos.LoadEd25519PublicKeyFromPEM(pemBytes)
	if err != nil {
		fmt.Fprintln(os.Stderr, "error:", err)
		os.Exit(2)
	}
	data, err := os.ReadFile(os.Args[2])
	if err != nil {
		fmt.Fprintln(os.Stderr, "error:", err)
		os.Exit(2)
	}
	var receipt vaos.Receipt
	if err := json.Unmarshal(data, &receipt); err != nil {
		fmt.Println("rejected:", err)
		os.Exit(1)
	}
	result := vaos.VerifyV2Receipt(receipt, pub)
	if !result.OK {
		fmt.Println("rejected:", result.Reason)
		os.Exit(1)
	}
	fmt.Println("ok")
}
