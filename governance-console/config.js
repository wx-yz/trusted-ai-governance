// Default settings. You can also change them in the UI (gear icon); the UI values win and are
// stored in this browser only.
window.DEMO_CONFIG = {
  // The Orders & Payments MCP server. The dashboard reads /audit from it and the Reset button calls /admin/reset.
  audit_url: "http://localhost:8090",

  // Chat endpoints of the two Agent Manager agents. Copy the invoke URL from each agent's page.
  // A URL that does not end in /chat gets /chat appended.
  governed:   { name: "support-agent",            url: "", api_key: "" },
  ungoverned: { name: "support-agent-ungoverned", url: "", api_key: "" },

  // The customer who is "signed in" for the demo.
  user: { id: "CUST-1001", name: "Maya Chen", tier: "Summit member", company: "Northwind Outfitters" }
};
