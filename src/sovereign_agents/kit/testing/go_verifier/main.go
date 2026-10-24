// Copyright 2026 Christiaan de Wet / Sovereign Matrix
// SPDX-License-Identifier: Apache-2.0

// Command go_verifier checks one v2 receipt with the Go verifier from
// verifiable-receipts (packages/verifiable-receipts-go).
//
// Usage: go_verifier <ed25519.pem> <receipt.json>
// Prints "ok" and exits 0, or prints "rejected: <reason>" and exits 1,
// a public key the module refuses to load included. Exits 2 on a usage
// or file error.
package main

import (
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
	data, err := os.ReadFile(os.Args[2])
	if err != nil {
		fmt.Fprintln(os.Stderr, "error:", err)
		os.Exit(2)
	}
	// The Python verifier rejects a receipt under a key it refuses to
	// load (a small-order point, say); this one does the same.
	pub, err := vaos.LoadEd25519PublicKeyFromPEM(pemBytes)
	if err != nil {
		fmt.Println("rejected:", err)
		os.Exit(1)
	}
	// ParseReceipt counts the text's depth before encoding/json reads it,
	// so a receipt nested deeper than 64 is rejected at any depth.
	receipt, err := vaos.ParseReceipt(data)
	if err != nil {
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
