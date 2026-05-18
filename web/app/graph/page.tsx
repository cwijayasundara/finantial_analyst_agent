import { redirect } from "next/navigation";

// The old JSONL-snapshot overview is gone with Kuzu (PR 4.3).
// Point users at the node-explorer — they can navigate from there.
//
// merchant::costco is the hardcoded anchor; if you don't have Costco
// transactions, change this to a merchant you do have. A future PR
// could fetch the top-spending merchant from the API and redirect
// dynamically.
export default function GraphPage() {
  redirect("/graph/merchant::costco?depth=2");
}
