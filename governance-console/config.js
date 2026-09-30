// Default settings. You can also change them in the UI (gear icon); the UI values win and are
// stored in this browser only.
window.DEMO_CONFIG = {
  // The Salesforce MCP server. The dashboard reads /audit from it and the Reset button calls /admin/reset.
  audit_url: "http://localhost:8090",

  // Chat endpoints of the two Agent Manager agents. Copy the invoke URL from each agent's page.
  // A URL that does not end in /chat gets /chat appended.
  governed:   { name: "sales-copilot",            url: "", api_key: "" },
  ungoverned: { name: "sales-copilot-ungoverned", url: "", api_key: "" },

  // The account manager who is "signed in" for the demo.
  user: { id: "AM-101", name: "Alex Rivera", title: "Senior Account Manager", region: "West" }
};
